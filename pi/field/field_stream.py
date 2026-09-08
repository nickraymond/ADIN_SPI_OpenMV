#!/usr/bin/env python3
"""Three live camera streams on one page: IMX708 | AE3 | N6, left to right.

The field rig's first demo. It is a FORK of the S8 two-board viewer, not an
extension of it: that viewer's job is comparing two boards running a detector,
so most of its 1,400 lines are model loading, LAB blob thresholds and per-class
counting -- all permanently empty here. What it got RIGHT is imported rather
than copied (``SerialBoard``, ``reader_loop``, ``supervise``, ``Latest``, the
reconnect backoff), because those encode measured bench lessons this rig
inherits wholesale:

* ``SerialBoard``, never ``mpremote run``, in the data path -- mpremote
  accumulates and rescans its own output, so a stream decayed from ~20 fps to
  under 2 while the board's per-stage timings stayed flat at 38.5 ms.
* The reconnect backoff is (2, 5, 10, 20, 30) s and NOT flat: repeated
  raw-REPL attaches are themselves how the AE3 gets wedged.
* Liveness is measured and displayed (``stale_s`` + a red banner), never
  inferred -- a frozen stream and a motionless scene look identical.

Board ports are resolved by ASKING each board its role (``pi/field/discover``),
so swapping in a new AE3 or N6 needs no config edit anywhere.
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
sys.path.insert(0, _HERE)

from n6_stream_host import (build_board_script_text, reader_loop,  # noqa: E402
                            supervise, QuietServer)
import discover as discovery                                       # noqa: E402
from sources import SourceView, supervise_csi                      # noqa: E402
import netinfo                                                     # noqa: E402

#: Left-to-right panel order, fixed by Nick: IMX708 first, N6 on the far right.
LAYOUT = ("IMX", "AE3", "N6")

#: VGA-class per camera. The OpenMV sensors letterbox to 16:10 at every size
#: (measured: QVGA = 320x200, VGA = 640x400, HD = 1280x800), so "VGA" is 640x400
#: on the boards and a true 640x480 on the IMX708. They are not the same
#: rectangle and the page does not pretend otherwise.
#: What each framesize means per camera. The three sensors are NOT the same
#: rectangle and the page must not pretend otherwise:
#:   AE3  letterboxes 16:10  -> VGA 640x400,  HD 1280x800
#:   N6   letterboxes 16:10 at VGA, 16:9 at HD -> 640x400, 1280x720
#:   IMX708 is free to pick anything from a 4608x2592 sensor
#: The IMX is matched to the N6's rectangle rather than given its own, so the
#: three panels frame roughly the same scene -- Nick's "keep the relative ROI
#: the same as it makes sense". It is NOT dumbed down to the AE3: the IMX and
#: N6 run their real resolution and the AE3 lands where its hardware lands.
CSI_SIZES = {"VGA": (640, 480), "HD": (1280, 720)}
CSI_SIZE = CSI_SIZES["VGA"]

#: The AE3 cannot hold 15 fps at HD -- its measured streaming ceiling is
#: ~3.6 fps HD mono (S23 GOLD). That is not a bug to hide; it is the
#: performance difference this tool exists to show, so the page reports
#: SET fps beside ACTUAL fps and lets the gap speak.

#: Upper bound for the board script's run length, in seconds.
#:
#: MEASURED THE HARD WAY 2026-09-06: passing 1e9 crashed BOTH boards with
#: ``OverflowError: overflow converting long int to machine word`` inside
#: ``time.ticks_add(time.ticks_ms(), int(MAX_SECONDS * 1000))``. MicroPython's
#: ticks arithmetic takes a delta within +/- ticks_period/2 -- 2**29 ms
#: (~6.2 days) on these ports -- and 1e9 s is 1e12 ms, far outside it.
#:
#: This cannot simply be "forever": ``#D`` (bounded run complete) makes the
#: supervisor set quit and return PERMANENTLY, so whatever we pick is a real
#: stream lifetime, not a formality. 3 days sits at roughly half the ticks
#: limit -- long enough that no bench or field session reaches it, with
#: enough margin that a port with a smaller ticks_period is still safe.
MAX_STREAM_SECONDS = 259200

#: Per-camera frame-rate CEILINGS (Nick, 2026-09-06: "set the max fps for
#: IMX at 30, N6 at 15. Keep that cpu cool!").
#:
#: These are not the hardware limits -- measured unpaced at HD, the IMX708
#: reached 57 fps (42.95 Mb/s) and the N6 16.1 fps mono. The rig ran at
#: 948 mA and 63.4 degC doing it, on a Pi Zero 2 W in an enclosure with no
#: convection. So the cap is a THERMAL and power budget, not a capability
#: statement, and the page still reports SET vs ACTUAL so the difference
#: stays visible rather than looking like a limitation.
#:
#: The AE3 is uncapped because it cannot reach any of these: 2.6 fps at HD
#: colour, 5.3 mono. Capping it would only mislead.
FPS_CEILING = {"IMX708": 30.0, "N6": 15.0, "AE3": None}


def capped_fps(label, requested, ceiling=None):
    """The rate this camera may actually run at."""
    cap = (ceiling or FPS_CEILING).get(label)
    if cap is None or requested <= 0:
        return requested
    return min(requested, cap)


def board_cfg(framesize, quality, pace_ms, pixfmt="RGB565"):
    """Config for a plain video stream: no model, no blobs, no overlay.

    This is the same shape the proven ``hil-aiming`` recipe uses
    (``--no-detect --no-blobs``), which is the one recipe written to run on
    ANY bench state -- exactly what a field rig wants.
    """
    return {
        "framesize": framesize,
        "quality": quality,
        "pace_ms": pace_ms,
        "pixfmt": pixfmt,
        "max_seconds": MAX_STREAM_SECONDS,
        "max_frames": 0,
        "detect": False,
        "blobs": False,
        "overlay": False,
        "model": "",
        "model_kind": "auto",
        "threshold": 0.4,
        "blob_pixels": 150,
        "blob_area": 150,
        "tune": False,
        "blob_label": "blob",
        "blob_scan": "codes",
    }


def build_views(found, csi_camera=0, want=None):
    """One SourceView per camera present, in LAYOUT order.

    A missing camera still gets a panel, showing WHY it is missing. Dropping
    it from the page would make a two-camera rig look like a correct
    three-camera rig with a narrow layout.
    """
    views = []
    for role in LAYOUT:
        if role == "IMX":
            v = SourceView("IMX708", "csi", "camera %d" % csi_camera)
            v.want = dict(want or {}, w=(want or {}).get("csi_w"),
                          h=(want or {}).get("csi_h"))
            views.append(v)
        else:
            info = found.get(role)
            view = SourceView(role, "serial", info["port"] if info else "")
            view.want = dict(want or {})
            if info:
                view.stats.board = info.get("machine", "")
            else:
                view.stats.status = "not found -- no board reported role %s" % role
                view.state["alive"] = False
            views.append(view)
    return views


def page(views):
    """Three panels, click one to fill the window.

    Layout notes, all from Nick's review of the first cut (2026-09-06):
    the per-camera detail was a wall of changing text that was hard to read,
    so it is a fixed-row TABLE now -- the rows never move, only the numbers.
    The "target" line is gone (it was a device path nobody reads mid-test),
    and SET is shown beside ACTUAL for both resolution and frame rate,
    because the gap between them is the measurement.
    """
    panels = "\n".join(
        '<figure class="p" id="p%d" onclick="focusPanel(%d)" '
        'title="click to enlarge">'
        '<figcaption>%s<span class="hint">click to enlarge</span></figcaption>'
        '<div class="ban" id="b%d"></div>'
        '<img src="/s/%d/stream" alt="%s"/>'
        '<table class="st"><tbody>'
        '<tr><th>resolution</th><td id="r%d">&mdash;</td></tr>'
        '<tr><th>frame rate</th><td id="f%d">&mdash;</td></tr>'
        '<tr><th>stream rate</th><td id="m%d">&mdash;</td></tr>'
        '<tr><th>status</th><td id="s%d" class="stat">&mdash;</td></tr>'
        '</tbody></table></figure>'
        % (i, i, v.label, i, i, v.label, i, i, i, i)
        for i, v in enumerate(views))
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Field rig &mdash; three cameras</title><style>
 :root{--bg:#111;--card:#181818;--line:#2a2a2a;--fg:#ddd;--mut:#8a949e;--acc:#8fd0ff}
 body{background:var(--bg);color:var(--fg);margin:0;padding:12px;
      font:13px/1.45 ui-monospace,Menlo,Consolas,monospace}
 header{display:flex;justify-content:space-between;align-items:flex-start;
        gap:12px;margin:0 0 10px}
 h1{font-size:15px;margin:0;color:#fff;font-weight:600}
 #net{text-align:right;font-size:12px;color:var(--mut);white-space:nowrap}
 #net b{font-weight:600}
 .excellent{color:#7fd48a}.good{color:#7fd48a}.weak{color:#e6c15a}
 .poor{color:#ef8a8a}.unknown{color:var(--mut)}.wired{color:#7fd48a}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
 .p{flex:1 1 320px;min-width:290px;margin:0;background:var(--card);
    border:1px solid var(--line);border-radius:6px;padding:8px;cursor:zoom-in}
 figcaption{font-size:13px;color:var(--acc);margin:0 0 6px;
            display:flex;justify-content:space-between;align-items:baseline}
 .hint{color:var(--mut);font-size:11px;font-weight:400}
 img{width:100%;display:block;background:#000;border-radius:3px}
 table.st{width:100%;border-collapse:collapse;margin-top:6px;font-size:12px}
 table.st th{text-align:left;font-weight:400;color:var(--mut);
             padding:1px 8px 1px 0;white-space:nowrap;width:5.8em}
 table.st td{padding:1px 0;color:var(--fg)}
 td.stat{color:var(--mut)}
 .ban{display:none;margin:0 0 6px;padding:4px 6px;border-radius:3px;
      background:#5c1a1a;color:#ffdede;font-weight:600}
 .ban.on{display:block}
 .lag{color:#e6c15a}
 /* Fullscreen: the clicked panel fills the window, the others are hidden.
    Kept as a class on <body> so one Escape handler undoes it. */
 body.zoom .p{display:none}
 body.zoom .p.big{display:block;flex:1 1 100%;cursor:zoom-out}
 body.zoom .p.big img{max-height:78vh;object-fit:contain}
</style></head><body>
<header>
  <h1>Field rig &mdash; IMX708 &middot; AE3 &middot; N6</h1>
  <div id="net">link &hellip;</div>
</header>
<div class="row">__PANELS__</div>
<script>
let zoomed = null;
function focusPanel(i){
  const el = document.getElementById('p'+i);
  if (zoomed === i){ unzoom(); return; }
  document.querySelectorAll('.p').forEach(p => p.classList.remove('big'));
  el.classList.add('big'); document.body.classList.add('zoom'); zoomed = i;
}
function unzoom(){
  document.body.classList.remove('zoom');
  document.querySelectorAll('.p').forEach(p => p.classList.remove('big'));
  zoomed = null;
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') unzoom(); });

const fmt = (v, s) => (v === null || v === undefined) ? '\u2014' : v + (s||'');

async function tick(){
  try{
    const rows = await (await fetch('/api/sources')).json();
    rows.forEach((j,i)=>{
      const R=document.getElementById('r'+i), F=document.getElementById('f'+i),
            M=document.getElementById('m'+i), S=document.getElementById('s'+i),
            b=document.getElementById('b'+i);
      if(!R) return;
      R.textContent = fmt(j.res) + (j.framesize? '  ('+j.framesize+')' : '');
      // SET beside ACTUAL. A camera that cannot hold the requested rate is
      // reporting a hardware limit, not failing -- so the gap is shown
      // plainly rather than hidden behind one number.
      const set = j.set_fps, act = j.fps;
      const slow = (set && act && act < set * 0.8);
      F.innerHTML = fmt(act) + ' actual <span class="' + (slow?'lag':'') +
                    '">/ ' + fmt(set) + ' set</span>';
      M.textContent = fmt(j.mbps,' Mb/s') + '   ' + fmt(j.kb_frame,' kB/frame');
      S.textContent = j.status;
      const stale = (j.stale_s===null||j.stale_s===undefined)||j.stale_s>3;
      b.className = 'ban'+(stale?' on':'');
      b.textContent = (j.stale_s===null||j.stale_s===undefined)
        ? 'NOT LIVE \u2014 no frame yet'
        : 'NOT LIVE \u2014 last frame '+j.stale_s+'s ago';
    });
  }catch(e){}
  try{
    const n = await (await fetch('/api/net')).json();
    const el = document.getElementById('net');
    // The link is shown because a weak one makes every camera look bad --
    // but note the fps above is counted ON THE PI, so a bad link stutters
    // the picture without moving those numbers.
    if (n.wired){
      el.innerHTML = 'link <b class="wired">'+n.iface+' wired</b>';
    } else {
      el.innerHTML = 'link <b>'+n.iface+'</b> '+(n.ssid? '&middot; '+n.ssid : '')+
        ' &middot; <b class="'+n.grade+'">'+fmt(n.signal_dbm,' dBm')+
        ' '+n.grade+'</b>'+
        (n.tx_bitrate_mbps? ' &middot; '+n.tx_bitrate_mbps+' Mb/s tx' : '');
    }
  }catch(e){}
}
setInterval(tick,1000); tick();
</script></body></html>""".replace("__PANELS__", panels)


def make_handler(views):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _body(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/" or path.startswith("/index"):
                self._body(page(views).encode(), "text/html; charset=utf-8")
                return
            if path == "/api/sources":
                self._body(json.dumps([v.snapshot() for v in views]).encode(),
                           "application/json")
                return
            if path == "/api/net":
                self._body(json.dumps(netinfo.net_status()).encode(),
                           "application/json")
                return
            if path == "/healthz":
                # The workbench health-gates LIVE on this answering 200.
                self._body(b"ok", "text/plain")
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "s" and parts[1].isdigit():
                idx = int(parts[1])
                if not (0 <= idx < len(views)):
                    self.send_error(404)
                    return
                view = views[idx]
                if parts[2] == "stats.json":
                    self._body(json.dumps(view.snapshot()).encode(),
                               "application/json")
                elif parts[2] == "frame.jpg":
                    frame, _ = view.latest.get()
                    if frame:
                        self._body(frame, "image/jpeg")
                    else:
                        self.send_error(503, "no frame yet")
                elif parts[2] == "stream":
                    self._mjpeg(view)
                else:
                    self.send_error(404)
                return
            self.send_error(404)

        def _mjpeg(self, view):
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = -1
            try:
                while True:
                    frame, seq = view.latest.get()
                    if frame and seq != last:
                        last = seq
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(frame))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


#: Seconds of TOTAL port silence before re-attaching a board that just
#: refused. The ae3-board-access rule is 60 s and longer than the 30 s
#: quiet-exit, because the clock restarts on any contact -- so this wait is
#: only useful if nothing touches the port during it.
REFUSAL_SETTLE_S = 60.0


def discover_boards(settle_s=REFUSAL_SETTLE_S, sleep=time.sleep,
                    discover=None):
    """Find boards by role, with ONE retry after a raw-REPL refusal.

    Two facts collide at start-up. Discovery has to attach to identify a
    board, and then the stream supervisor attaches again moments later --
    and repeated raw-REPL attaches are precisely how the AE3 gets wedged
    (roughly 4-6 after a teardown and it refuses below the Python level,
    curable only by a power cycle). Measured here 2026-09-06: a start
    immediately after a board crash produced
    ``could not enter raw repl`` and the AE3 dropped out of the run.

    So: one pass; and if a role is missing *because a port refused* rather
    than because it is absent, wait out the full silence and try ONCE more.
    Never a loop -- polling resets the very quiet-exit timer being waited on,
    which is how a previous session hung forever.
    """
    discover = discover or discovery.discover
    print("discovering boards (asking each one its role)...", flush=True)
    found, problems = discover()
    missing = discovery.require(found)
    # "no answer" is a refusal (board present, port busy/grumpy); an absent
    # device never gets probed at all, so it produces no problem line.
    if missing and problems:
        print("  refusal on first pass; %gs of TOTAL silence, then ONE retry"
              % settle_s, flush=True)
        for problem in problems:
            print("  ! %s" % problem, flush=True)
        sleep(settle_s)
        found, problems = discover()
    for role in discovery.ROLES:
        info = found.get(role)
        print("  %-4s %s" % (role, info["port"] if info else "MISSING"),
              flush=True)
    for problem in problems:
        print("  ! %s" % problem, flush=True)
    return found, problems


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--framesize", default="VGA",
                    help="OpenMV capture size (VGA = 640x400 letterboxed)")
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--quality", type=int, default=50)
    ap.add_argument("--csi-width", type=int, default=0,
                    help="override the CSI width (0 = follow --framesize)")
    ap.add_argument("--csi-height", type=int, default=0,
                    help="override the CSI height (0 = follow --framesize)")
    ap.add_argument("--csi-camera", type=int, default=0)
    ap.add_argument("--colour", default="color", choices=("color", "mono"),
                    help="board pixel format. mono (GRAYSCALE) exists to "
                         "measure the AE3's real HD ceiling -- this SoC has "
                         "no hardware JPEG, so colour costs a convert plus "
                         "3x the DCT work")
    ap.add_argument("--attach-settle", type=float, default=REFUSAL_SETTLE_S,
                    help="seconds of port silence before retrying a board "
                         "that refused the raw REPL (default: %(default)s)")
    ap.add_argument("--no-csi", action="store_true",
                    help="serial boards only (for a rig with no CSI camera)")
    # The recipe param mechanism renders --<key> <value>, so a bare store_true
    # cannot be a card toggle. nereus000 has no CSI camera at all (it is the
    # HIL rig -- boards only), and a dead third panel there reads as a broken
    # demo rather than an absent sensor.
    ap.add_argument("--csi", choices=("on", "off"), default="on",
                    help="include the CSI camera panel (default: %(default)s)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    pace_ms = int(1000.0 / args.fps) if args.fps > 0 else 0
    csi_w, csi_h = CSI_SIZES.get(args.framesize.upper(), CSI_SIZE)
    if args.csi_width:
        csi_w = args.csi_width
    if args.csi_height:
        csi_h = args.csi_height

    # SERVE BEFORE DISCOVERING. A refused board sends discover_boards into
    # its 60 s silence wait, and the workbench health-gates LIVE on this page
    # answering within 60 s -- so discover-then-serve gets SIGINT'd mid-wait
    # and the whole viewer dies because ONE board was grumpy. Measured
    # 2026-09-07 with the AE3 refusing: two safety mechanisms fighting.
    views = build_views({}, args.csi_camera,
                        want={"fps": args.fps, "framesize": args.framesize,
                              "csi_w": csi_w, "csi_h": csi_h})
    if args.no_csi or args.csi == "off":
        views = [v for v in views if v.kind != "csi"]
    for v in views:
        if v.kind == "serial":
            v.stats.status = "discovering board..."

    pixfmt = "GRAYSCALE" if args.colour == "mono" else "RGB565"

    def script_for(label):
        """Each board gets its OWN pace: the caps differ per camera."""
        f = capped_fps(label, args.fps)
        ms = int(1000.0 / f) if f > 0 else 0
        return build_board_script_text(board_cfg(args.framesize, args.quality,
                                                 ms, pixfmt))

    threads = []

    def spawn(view):
        vfps = capped_fps(view.label, args.fps)
        if vfps != args.fps:
            print("  %s capped to %g fps (thermal budget)"
                  % (view.label, vfps), flush=True)
        view.want["fps"] = vfps
        if view.kind == "csi":
            t = threading.Thread(
                target=supervise_csi, daemon=True,
                args=(view, csi_w, csi_h, vfps,
                      args.quality, args.csi_camera))
        elif view.target:
            t = threading.Thread(
                target=supervise, daemon=True,
                args=(view.target, script_for(view.label), view.latest,
                      view.stats, view.state))
        else:
            return          # missing board: its panel already says so
        t.start()
        threads.append(t)

    def bring_up():
        """Discover, then attach -- off the main thread so the page is up."""
        found, problems = discover_boards(args.attach_settle)
        for problem in problems:
            print("  ! %s" % problem, flush=True)
        for view in views:
            if view.kind == "serial":
                info = found.get(view.label)
                if info:
                    view.target = info["port"]
                    view.stats.board = info.get("machine", "")
                    view.stats.status = "attaching..."
                else:
                    view.stats.status = ("not found -- no board reported "
                                         "role %s" % view.label)
                    view.state["alive"] = False
            spawn(view)

    threading.Thread(target=bring_up, daemon=True).start()

    srv = QuietServer((args.bind, args.http_port), make_handler(views))

    def stop(signum, frame):
        # SIGINT/SIGTERM unwind through the clean path. SIGKILL is banned on
        # this bench -- it skipped the board teardown and took the N6 off the
        # USB bus entirely, needing a physical replug.
        print("\nstopping...", flush=True)
        for v in views:
            v.state["quit"] = True
            b = v.state.get("board")
            if b is not None:
                try:
                    b.stop() if hasattr(b, "stop") else b.terminate()
                except Exception:       # noqa: BLE001
                    pass
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("serving http://%s:%d/  (%d panels: %s)"
          % (args.bind, args.http_port, len(views),
             ", ".join(v.label for v in views)), flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
