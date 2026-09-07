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
</style><h1>%s</h1>%s""" % (title, title, body)


def _progress(path, msg):
    with open(path, "w") as fh:
        fh.write(_page("RAW composite",
                       '<meta http-equiv="refresh" content="10">'
                       '<p class="sub">%s</p>' % msg))


def run_all(args, run_dir, index):
    import numpy as np
    from PIL import Image
    import s28_stack

    found, _ = discovery.discover()
    roles = [r.strip() for r in args.boards.split(",") if r.strip()]
    cards = []

    for role in roles:
        info = found.get(role)
        if not info:
            cards.append('<div class="c"><h2>%s</h2>'
                         '<p class="bad">board not found</p></div>' % role)
            continue
        figs, meta = [], []

        if args.mode in ("stack", "both"):
            _progress(index, "%s: capturing %d raw frames for the stack&hellip;"
                      % (role, args.n))
            paths, geom = composite.board_raw_burst(
                info["port"], args.n, run_dir, role, size=args.framesize)
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
            probe, geom, base_us = composite.board_bracket(
                info["port"], [0], run_dir, role + "_p", size=args.framesize)
            time.sleep(args.settle)
            base_us = base_us or 8000
            exps = ev_ladder(base_us, parse_stops(args.stops))
            frames, geom, _ = composite.board_bracket(
                info["port"], exps, run_dir, role, size=args.framesize)
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

        import base64

        def uri(p):
            with open(p, "rb") as fh:
                return "data:image/jpeg;base64," + base64.b64encode(
                    fh.read()).decode()

        pair = "".join('<figure><figcaption>%s</figcaption>'
                       '<img src="%s"></figure>' % (cap, uri(p))
                       for cap, p in figs)
        cards.append('<div class="c"><h2>%s</h2><div class="pair">%s</div>'
                     '<p class="meta">%s</p></div>'
                     % (role, pair, " &middot; ".join(meta)))

    with open(index, "w") as fh:
        fh.write(_page("RAW composite &mdash; %s" % args.mode,
                       '<p class="sub">Raw is LINEAR: stacking averages real '
                       'photon counts and bracketing divides by exposure. '
                       'Both are only valid in this domain.</p>'
                       + "\n".join(cards)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", default="both",
                    choices=("stack", "bracket", "both"))
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--stops", default="2",
                    help="+/- N stops around metered; also accepts an "
                         "explicit comma list like -2,0,2")
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--boards", default="AE3,N6")
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
