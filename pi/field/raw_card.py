#!/usr/bin/env python3
"""Workbench card: RAW stacking and HDR bracketing, side by side, per camera.

Serves immediately and captures on a thread -- capture takes minutes at HD
and the workbench health-gates LIVE on this page answering within 60 s.
(Learned the hard way this evening: a capture-then-serve order got SIGINT'd
mid-merge and reported a failure that never happened.)

Both experiments in one card, because they are the two halves of the same
question and you want to see them against the same scene:

  STACK   N frames at ONE locked exposure, averaged in LINEAR space.
          Noise falls ~sqrt(N). Costs a short burst; safe on static subjects.
  BRACKET -EV/0/+EV, each divided by ITS OWN measured exposure and merged
          to radiance. Buys dynamic range. Costs a LONG exposure on the
          bright rung, so it smears anything that moves.
"""

import argparse
import http.server
import os
import socketserver
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "s28"))

import composite                                     # noqa: E402
import discover as discovery                         # noqa: E402
from raw_still import finish                         # noqa: E402
from hdr_still import ev_ladder                      # noqa: E402


def parse_stops(spec):
    """"2" -> [-2, 0, 2]; "-2,0,2" -> the same list, given explicitly.

    The card sends the short form because the recipe schema forbids commas
    in a param value; the CLI keeps the explicit form for asymmetric ladders.
    """
    if "," in spec:
        return [float(x) for x in spec.split(",") if x.strip()]
    n = abs(float(spec))
    return [-n, 0.0, n]


def _page(title, body):
    return """<!doctype html><meta charset="utf-8">
<title>%s</title><style>
body{background:#111;color:#ddd;font:13px/1.6 ui-monospace,Menlo,monospace;margin:0;padding:14px}
h1{font-size:15px;color:#fff;margin:0 0 4px}.sub{color:#8a949e;margin:0 0 12px}
.c{background:#181818;border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin:0 0 12px}
h2{font-size:13px;color:#8fd0ff;margin:0 0 8px}
.pair{display:flex;gap:10px;flex-wrap:wrap}
figure{margin:0;flex:1 1 360px}figcaption{color:#8a949e;margin:0 0 4px}
img{width:100%%;border-radius:3px;background:#000}
.meta{color:#8a949e;margin:6px 0 0}.bad{color:#ef8a8a}.warn{color:#e6c15a}
img{cursor:zoom-in}
a.dl{color:#8fd0ff;text-decoration:none;border:1px solid #2a4a5a;
     border-radius:3px;padding:0 5px;margin-left:6px;font-size:11px}
a.dl:hover{background:#1d3040}
/* Lightbox: click any image for a 1:1 look. Scrollable, because at full
   resolution the point is to inspect pixels, not to fit the screen. */
#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.94);
    z-index:99;overflow:auto;padding:10px;text-align:center}
#lb.on{display:block}
#lb img{max-width:none;cursor:zoom-out}
#lb .hint{color:#8a949e;position:fixed;top:8px;left:12px;font-size:12px}
</style><h1>%s</h1>%s
<div id="lb" onclick="this.className=''"><div class="hint">click anywhere or
press Esc to close &middot; scroll to pan at full resolution</div>
<img id="lbimg"></div>
<script>
function zoom(el){
  document.getElementById('lbimg').src = el.getAttribute('src');
  document.getElementById('lb').className = 'on';
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.getElementById('lb').className = '';
});
</script>""" % (title, title, body)


def _progress(path, msg):
    with open(path, "w") as fh:
        fh.write(_page("RAW composite",
                       '<meta http-equiv="refresh" content="10">'
                       '<p class="sub">%s</p>' % msg))


def _png(jpg_path):
    """Write a full-resolution PNG beside the JPEG and return its name.

    PNG because the download is for pixel-peeping and further processing --
    re-encoding a JPEG to share it would add a second generation of loss on
    top of the one already there.
    """
    from PIL import Image
    png = jpg_path[:-4] + ".png"
    if not os.path.exists(png):
        Image.open(jpg_path).save(png, optimize=True)
    return os.path.basename(png)


def _fig(cap, path):
    """One figure: click to enlarge, with a full-res PNG download link."""
    jpg = os.path.basename(path)
    png = _png(path)
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    return ('<figure><figcaption>%s '
            '<a class="dl" href="%s" download>PNG %dx%d</a></figcaption>'
            '<img src="%s" alt="%s" onclick="zoom(this)"></figure>'
            % (cap, png, w, h, jpg, cap))


def attach_retry(fn, label, settle_s, sleep=time.sleep, tries=2):
    """Run a board operation, retrying ONCE after real silence on a refusal.

    "could not enter raw repl" means the board is present but will not drop
    to the REPL -- usually because something touched the port too recently.
    The cure is silence, not persistence: repeated attaches are themselves
    how the AE3 gets wedged, so this retries exactly once and then gives up.
    """
    last = None
    for i in range(tries):
        try:
            return fn(), None
        except Exception as exc:                     # noqa: BLE001
            last = exc
            if "raw repl" in str(exc).lower() and i < tries - 1:
                print("  %s: %s -- %gs of silence, ONE retry"
                      % (label, exc, settle_s), flush=True)
                sleep(settle_s)
                continue
            break
    return None, last


def imx_card(args, run_dir, index):
    """STACK and BRACKET on the IMX708, through the same linear pipeline.

    Same maths as the boards -- the only difference is the container: the
    IMX hands back a DNG rather than plain Bayer bytes, so it needs a
    decode step the boards do not.
    """
    from PIL import Image
    from raw_still import finish
    figs, meta = [], []

    if args.mode in ("stack", "both"):
        _progress(index, "IMX708: metering, then %d raw frames&hellip;" % args.n)
        shutter, gain = composite.imx_meter()
        shots = composite.imx_raw_capture(args.n, run_dir, shutter_us=shutter,
                                          gain=gain, tag="IMX")
        if len(shots) >= 2:
            first, mean, scale = composite.imx_raw_stack(
                [s["path"] for s in shots])
            p1 = os.path.join(run_dir, "IMX_single.jpg")
            p2 = os.path.join(run_dir, "IMX_stack.jpg")
            # Flatten the 4.14x centre-to-corner falloff the raw path
            # leaves in (rpicam's ISP would normally do this from the
            # tuning file). Applied to BOTH so the comparison is fair.
            ls = composite.lens_shading
            Image.fromarray(finish(composite.imx_demosaic(
                ls(first, args.shading), scale))).save(p1, quality=95)
            Image.fromarray(finish(composite.imx_demosaic(
                ls(mean, args.shading), scale))).save(p2, quality=95)
            figs += [("1 raw frame", p1),
                     ("stacked x%d (linear)" % len(shots), p2)]
            meta.append("stack: %d raw frames, shutter %s us"
                        % (len(shots), shutter))
        else:
            meta.append("stack FAILED: %d raw frame(s)" % len(shots))

    if args.mode in ("bracket", "both"):
        _progress(index, "IMX708: bracketing exposures&hellip;")
        base, gain = composite.imx_meter()
        base = base or 8000
        exps = ev_ladder(base, parse_stops(args.stops))
        frames = []
        for us in exps:
            got = composite.imx_raw_capture(1, run_dir, shutter_us=us,
                                            gain=gain, tag="IMXev%d" % us)
            if got:
                # `or` is WRONG here: an unreadable exposure comes back as
                # -1, which is truthy, so every frame was skipped as
                # "us <= 0" and the merge divided an empty accumulator
                # ("'<=' not supported between NoneType and int").
                # rpicam honours --shutter precisely within sensor limits,
                # so the requested value is a sound fallback -- unlike the
                # boards, where the sensor clamps and the readback is the
                # only truth.
                read = got[0].get("got_us")
                frames.append({"path": got[0]["path"],
                               "want_us": us,
                               "got_us": read if (read and read > 0) else us})
        if len(frames) >= 2:
            got_list = [f["got_us"] for f in frames]
            spread = max(got_list) / min(got_list) if min(got_list) else 0
            rad = imx_merge_bracket(frames)
            p3 = os.path.join(run_dir, "IMX_hdr.jpg")
            rad = composite.lens_shading(rad, args.shading)
            Image.fromarray(finish(composite.imx_demosaic(
                rad, float(rad.max() or 1.0)), gamma=True)).save(p3, quality=95)
            figs.append(("HDR merge (%.0fx range)" % spread, p3))
            meta.append("bracket: requested %s / actual %s us"
                        % (exps, got_list))
            if spread < 1.5:
                meta.append('<span class="warn">exposures barely differ '
                            '&mdash; not a real HDR</span>')
        else:
            meta.append("bracket FAILED: %d frame(s)" % len(frames))

    pair = "".join(_fig(cap, p) for cap, p in figs)
    return ('<div class="c"><h2>IMX708</h2><div class="pair">%s</div>'
            '<p class="meta">%s</p></div>' % (pair, " &middot; ".join(meta)))


def imx_merge_bracket(frames):
    """Weighted linear-radiance merge over decoded DNGs (same maths as the
    boards' merge_bracket, but the frames arrive as DNG rather than bytes)."""
    np = composite._np()
    num = den = None
    for f in frames:
        a, scale = composite.imx_raw_load(f["path"])
        us = float(f["got_us"] or f["want_us"])
        if us <= 0:
            continue
        norm = a / (scale or 1.0)
        wt = 1.0 - np.abs((norm - 0.5) / 0.5) ** 2
        np.clip(wt, 0.0, 1.0, out=wt)
        wt[norm >= 0.99] = 0.0          # clipped: no information
        wt[norm <= 0.005] = 0.0         # below the noise floor
        contrib = wt * (a / us)
        num = contrib if num is None else num + contrib
        den = wt if den is None else den + wt
        del a, norm, wt, contrib
    if num is None or den is None:
        raise RuntimeError(
            "bracket merge got no usable frames -- every exposure was <= 0. "
            "Check the per-frame exposure readback.")
    den[den <= 0] = 1e-6
    return num / den


def run_all(args, run_dir, index):
    import numpy as np
    from PIL import Image
    import s28_stack

    found, _ = discovery.discover()
    roles = [r.strip() for r in args.boards.split(",") if r.strip()]
    cards = []

    if "IMX" in roles:
        roles = [r for r in roles if r != "IMX"]
        # The IMX needs no board at all -- a wedged AE3 must never stop it.
        try:
            cards.append(imx_card(args, run_dir, index))
        except Exception as exc:                     # noqa: BLE001
            cards.append('<div class="c"><h2>IMX708</h2>'
                         '<p class="bad">%s</p></div>' % exc)

    for role in roles:
        try:
            cards.append(board_card(args, run_dir, index, found, role))
        except Exception as exc:                     # noqa: BLE001
            cards.append('<div class="c"><h2>%s</h2><p class="bad">%s</p>'
                         '</div>' % (role, exc))

    with open(index, "w") as fh:
        fh.write(_page("RAW composite &mdash; %s" % args.mode,
                       '<p class="sub">Raw is LINEAR: stacking averages real '
                       'photon counts and bracketing divides by exposure. '
                       'Both are only valid in this domain.</p>'
                       + "\n".join(cards)))


def board_card(args, run_dir, index, found, role):
    """One board's stack + bracket. Raises only what the caller should show."""
    import numpy as np
    from PIL import Image
    import s28_stack
    from raw_still import finish

    for role in [role]:
        info = found.get(role)
        if not info:
            return ('<div class="c"><h2>%s</h2>'
                    '<p class="bad">board not found</p></div>' % role)
        figs, meta = [], []

        if args.mode in ("stack", "both"):
            _progress(index, "%s: capturing %d raw frames for the stack&hellip;"
                      % (role, args.n))
            got, err = attach_retry(
                lambda: composite.board_raw_burst(
                    info["port"], args.n, run_dir, role,
                    size=args.framesize),
                role + " stack", args.settle)
            paths, geom = got if got else ([], None)
            if err:
                meta.append("stack FAILED: %s" % err)
            if len(paths) >= 2 and geom:
                single, stacked = composite.stack_raw(paths, geom)
                p1 = os.path.join(run_dir, "%s_single.jpg" % role)
                p2 = os.path.join(run_dir, "%s_stack.jpg" % role)
                Image.fromarray(finish(single)).save(p1, quality=95)
                Image.fromarray(finish(stacked)).save(p2, quality=95)
                figs += [("1 raw frame", p1),
                         ("stacked x%d (linear)" % len(paths), p2)]
                meta.append("stack: %d frames at %dx%d"
                            % (len(paths), geom[0], geom[1]))
            else:
                meta.append("stack FAILED: %d frame(s)" % len(paths))
            time.sleep(args.settle)

        if args.mode in ("bracket", "both"):
            _progress(index, "%s: bracketing exposures&hellip;" % role)
            got, err = attach_retry(
                lambda: composite.board_bracket(
                    info["port"], [0], run_dir, role + "_p",
                    size=args.framesize),
                role + " meter", args.settle)
            if err:
                meta.append("bracket FAILED: %s" % err)
                got = None
            probe, geom, base_us = got if got else (None, None, None)
            time.sleep(args.settle)
            base_us = base_us or 8000
            exps = ev_ladder(base_us, parse_stops(args.stops))
            frames = []
            got2, err2 = attach_retry(
                lambda: composite.board_bracket(
                    info["port"], exps, run_dir, role, size=args.framesize),
                role + " bracket", args.settle)
            frames, geom, _ = got2 if got2 else ([], geom, None)
            if err2:
                meta.append("bracket FAILED: %s" % err2)
            if len(frames) >= 2 and geom:
                got = [f["got_us"] for f in frames]
                spread = max(got) / min(got) if min(got) else 0
                rad = composite.merge_bracket(frames, geom)
                merged = composite.tonemap(rad)
                p3 = os.path.join(run_dir, "%s_hdr.jpg" % role)
                Image.fromarray(finish(s28_stack.demosaic(
                    np.clip(merged, 0, 255).astype(np.uint8), "BGGR"),
                    gamma=False)).save(p3, quality=95)
                figs.append(("HDR merge (%.0fx range)" % spread, p3))
                meta.append("bracket: requested %s / actual %s us" % (exps, got))
                if spread < 1.5:
                    meta.append('<span class="warn">exposures barely differ '
                                '&mdash; the sensor clamped them, so this is '
                                'NOT a real HDR</span>')
            else:
                meta.append("bracket FAILED: %d frame(s)" % len(frames))
            time.sleep(args.settle)

        pair = "".join(_fig(cap, p) for cap, p in figs)
        return ('<div class="c"><h2>%s</h2><div class="pair">%s</div>'
                '<p class="meta">%s</p></div>'
                % (role, pair, " &middot; ".join(meta)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", default="both",
                    choices=("stack", "bracket", "both"))
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--stops", default="2",
                    help="+/- N stops around metered; also accepts an "
                         "explicit comma list like -2,0,2")
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--boards", default="IMX,AE3,N6",
                    help="IMX = the CSI camera; AE3/N6 = the boards")
    ap.add_argument("--shading", type=float, default=1.0,
                    help="lens-shading correction strength, 0 = off "
                         "(IMX708 only; measured 4.14x centre:corner)")
    ap.add_argument("--settle", type=float, default=40.0,
                    help="seconds of port silence between board attaches")
    ap.add_argument("--out", default=os.path.expanduser("~/raw_card_runs"))
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8095)
    args = ap.parse_args(argv)

    run_dir = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    index = os.path.join(run_dir, "index.html")
    _progress(index, "starting&hellip;")
    os.chdir(run_dir)
    print("raw card: mode=%s -> %s" % (args.mode, run_dir), flush=True)

    def worker():
        try:
            run_all(args, run_dir, index)
            print("report ready", flush=True)
        except Exception as exc:                     # noqa: BLE001
            _progress(index, '<span class="bad">FAILED: %s</span>' % exc)
            print("raw card FAILED: %s" % exc, flush=True)

    threading.Thread(target=worker, daemon=True).start()

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with S((args.bind, args.http_port),
           http.server.SimpleHTTPRequestHandler) as srv:
        print("serving http://%s:%d/index.html" % (args.bind, args.http_port),
              flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
