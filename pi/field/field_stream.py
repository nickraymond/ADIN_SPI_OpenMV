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

#: Left-to-right panel order, fixed by Nick: IMX708 first, N6 on the far right.
LAYOUT = ("IMX", "AE3", "N6")

#: VGA-class per camera. The OpenMV sensors letterbox to 16:10 at every size
#: (measured: QVGA = 320x200, VGA = 640x400, HD = 1280x800), so "VGA" is 640x400
#: on the boards and a true 640x480 on the IMX708. They are not the same
#: rectangle and the page does not pretend otherwise.
CSI_SIZE = (640, 480)

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


def board_cfg(framesize, quality, pace_ms):
    """Config for a plain video stream: no model, no blobs, no overlay.

    This is the same shape the proven ``hil-aiming`` recipe uses
    (``--no-detect --no-blobs``), which is the one recipe written to run on
    ANY bench state -- exactly what a field rig wants.
    """
    return {
        "framesize": framesize,
        "quality": quality,
        "pace_ms": pace_ms,
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


def build_views(found, csi_camera=0):
    """One SourceView per camera present, in LAYOUT order.

    A missing camera still gets a panel, showing WHY it is missing. Dropping
    it from the page would make a two-camera rig look like a correct
    three-camera rig with a narrow layout.
    """
    views = []
    for role in LAYOUT:
        if role == "IMX":
            views.append(SourceView("IMX708", "csi", "camera %d" % csi_camera))
        else:
            info = found.get(role)
            view = SourceView(role, "serial", info["port"] if info else "")
            if info:
                view.stats.board = info.get("machine", "")
            else:
                view.stats.status = "not found -- no board reported role %s" % role
                view.state["alive"] = False
            views.append(view)
    return views


def page(views):
    """Side-by-side page, one panel per camera, in LAYOUT order."""
    panels = "\n".join(
        '<div class="p"><h2>%s</h2><div class="ban" id="b%d"></div>'
        '<img src="/s/%d/stream" alt="%s"/>'
        '<pre id="s%d">connecting&hellip;</pre></div>'
        % (v.label, i, i, v.label, i) for i, v in enumerate(views))
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Field rig &mdash; three cameras</title><style>
 body{background:#111;color:#ddd;font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:0;padding:12px}
 h1{font-size:15px;margin:0 0 10px;color:#fff;font-weight:600}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
 .p{flex:1 1 320px;min-width:300px;background:#181818;border:1px solid #2a2a2a;border-radius:6px;padding:8px}
 h2{font-size:13px;margin:0 0 6px;color:#8fd0ff}
 img{width:100%;display:block;background:#000;border-radius:3px}
 pre{margin:6px 0 0;white-space:pre-wrap;color:#aaa;font-size:12px}
 .ban{display:none;margin:0 0 6px;padding:4px 6px;border-radius:3px;
      background:#5c1a1a;color:#ffdede;font-weight:600}
 .ban.on{display:block}
</style></head><body>
<h1>Field rig &mdash; IMX708 &middot; AE3 &middot; N6</h1>
<div class="row">__PANELS__</div>
<script>
// Liveness is polled and DISPLAYED. The <img> keeps showing the last frame
// when a camera dies, so the banner is the only thing that can tell the
// difference between a live still scene and a dead stream.
async function tick(){
  try{
    const rows = await (await fetch('/api/sources')).json();
    rows.forEach((j,i)=>{
      const s=document.getElementById('s'+i), b=document.getElementById('b'+i);
      if(!s) return;
      s.textContent =
        'status  '+j.status+'\\n'+
        'fps     '+j.fps+'   '+j.mbps+' Mbps   '+j.kb_frame+' kB/frame\\n'+
        'frames  '+j.frames+'   reconnects '+j.reconnects+'   resyncs '+j.resyncs+'\\n'+
        'target  '+j.target+
        (j.board? '\\nboard   '+j.board : '')+
        (j.junk && j.junk.length? '\\nlast    '+j.junk[j.junk.length-1] : '');
      const stale = (j.stale_s===null||j.stale_s===undefined)||j.stale_s>3;
      b.className = 'ban'+(stale?' on':'');
      b.textContent = (j.stale_s===null||j.stale_s===undefined)
        ? 'NOT LIVE \\u2014 no frame yet'
        : 'NOT LIVE \\u2014 last frame '+j.stale_s+'s ago';
    });
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
    ap.add_argument("--csi-width", type=int, default=CSI_SIZE[0])
    ap.add_argument("--csi-height", type=int, default=CSI_SIZE[1])
    ap.add_argument("--csi-camera", type=int, default=0)
    ap.add_argument("--attach-settle", type=float, default=REFUSAL_SETTLE_S,
                    help="seconds of port silence before retrying a board "
                         "that refused the raw REPL (default: %(default)s)")
    ap.add_argument("--no-csi", action="store_true",
                    help="serial boards only (for a rig with no CSI camera)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    pace_ms = int(1000.0 / args.fps) if args.fps > 0 else 0

    found, problems = discover_boards(args.attach_settle)

    views = build_views(found, args.csi_camera)
    if args.no_csi:
        views = [v for v in views if v.kind != "csi"]

    cfg = board_cfg(args.framesize, args.quality, pace_ms)
    script_text = build_board_script_text(cfg)

    threads = []
    for view in views:
        if view.kind == "csi":
            t = threading.Thread(
                target=supervise_csi, daemon=True,
                args=(view, args.csi_width, args.csi_height, args.fps,
                      args.quality, args.csi_camera))
        elif view.target:
            t = threading.Thread(
                target=supervise, daemon=True,
                args=(view.target, script_text, view.latest, view.stats,
                      view.state))
        else:
            continue        # missing board: its panel already says so
        t.start()
        threads.append(t)

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
