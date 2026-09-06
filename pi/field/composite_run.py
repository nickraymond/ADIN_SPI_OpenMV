#!/usr/bin/env python3
"""Composite demo: STACK and BRACKET on all three cameras, one HTML report.

Serves a page comparing, per camera, a SINGLE frame against the composite,
with the measured noise ladder beside it. Wrapped as a workbench card so it
is one click.

Run order per camera: meter -> lock -> burst -> merge -> render. The lock is
reported, not assumed (S28's rule: a burst whose settings moved is not a
stack, and 'it looked fine' is how that gets missed).
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

import composite                                        # noqa: E402
import discover as discovery                            # noqa: E402


def _write_progress(path, args, state):
    """A holding page so the card can go LIVE while capture runs."""
    body = ("FAILED" in state) and '<p class="bad">%s</p>' % state or (
        '<p>Capturing <b>%d</b> frames per camera at %s, merge=<b>%s</b>&hellip;'
        '</p><p class="sub">On a Pi Zero 2 W this takes a few minutes at HD. '
        'The page refreshes itself.</p>' % (args.n, args.framesize, args.merge))
    with open(path, "w") as fh:
        fh.write("""<!doctype html><meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Composite &mdash; %s</title><style>
body{background:#111;color:#ddd;font:13px/1.6 ui-monospace,Menlo,monospace;padding:16px}
b{color:#8fd0ff}.sub{color:#8a949e}.bad{color:#ef8a8a}
</style><h1 style="font-size:15px;color:#fff">Composite &mdash; %s</h1>%s"""
                 % (args.mode, args.mode, body))


def build_report(results, mode, out_html):
    """One page: per camera, single vs composite + the noise numbers."""
    import base64

    def uri(path):
        with open(path, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()

    cards = []
    for r in results:
        if r.get("error"):
            cards.append(
                '<div class="c"><h2>%s</h2><p class="bad">%s</p></div>'
                % (r["label"], r["error"]))
            continue
        ladder = "".join(
            "<tr><td>%d</td><td>%.3f</td><td>%.2fx</td></tr>"
            % (k, s, (r["ladder"][0][1] / s) if s else 0)
            for k, s in r["ladder"])
        cards.append("""<div class="c"><h2>%s</h2>
<div class="pair">
  <figure><figcaption>single frame</figcaption><img src="%s"></figure>
  <figure><figcaption>composite (%s, N=%d)</figcaption><img src="%s"></figure>
</div>
<table><tr><th>frames</th><th>temporal &sigma;</th><th>improvement</th></tr>
%s</table>
<p class="meta">%s</p></div>""" % (
            r["label"], uri(r["single"]), mode, r["n"], uri(r["composite"]),
            ladder, r.get("note", "")))

    html = """<!doctype html><meta charset="utf-8">
<title>Composite &mdash; %s</title><style>
body{background:#111;color:#ddd;font:13px/1.5 ui-monospace,Menlo,monospace;margin:0;padding:14px}
h1{font-size:15px;color:#fff;margin:0 0 4px}.sub{color:#8a949e;margin:0 0 12px}
.c{background:#181818;border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin:0 0 12px}
h2{font-size:13px;color:#8fd0ff;margin:0 0 8px}
.pair{display:flex;gap:10px;flex-wrap:wrap}
figure{margin:0;flex:1 1 340px}figcaption{color:#8a949e;margin:0 0 4px}
img{width:100%%;border-radius:3px;background:#000}
table{border-collapse:collapse;margin:8px 0 0;font-size:12px}
th,td{text-align:left;padding:1px 14px 1px 0;color:#ccc}th{color:#8a949e;font-weight:400}
.meta{color:#8a949e;margin:6px 0 0}.bad{color:#ef8a8a}
</style><h1>Composite &mdash; %s</h1>
<p class="sub">Left: one frame. Right: the composite. Lower temporal &sigma; is
less noise. Improvement is relative to a single frame &mdash; stacking should
track &radic;N.</p>
%s""" % (mode, mode, "\n".join(cards))
    with open(out_html, "w") as fh:
        fh.write(html)
    return out_html


def run_stack(args, out_dir):
    """Mode A: N frames at one locked exposure, merged."""
    results = []
    found, _ = discovery.discover()

    shutter, gain = composite.imx_meter(args.width, args.height)
    imx = composite.imx_capture(args.n, out_dir, args.width, args.height,
                                shutter_us=shutter, gain=gain)
    jobs = [("IMX708", imx,
             "locked shutter=%s us gain=%s" % (shutter, gain))]
    for role in ("AE3", "N6"):
        info = found.get(role)
        if not info:
            results.append({"label": role, "error": "board not found"})
            continue
        paths = composite.board_burst(info["port"], args.n, out_dir, role,
                                      size=args.framesize,
                                      quality=args.quality)
        jobs.append((role, paths, "AE/AWB frozen on the board"))

    for label, paths, note in jobs:
        if len(paths) < 2:
            results.append({"label": label,
                            "error": "only %d frame(s) captured" % len(paths)})
            continue
        frames = [composite.decode_jpeg(open(p, "rb").read()) for p in paths]
        merged = composite.stack_frames(frames, args.merge)
        comp_path = os.path.join(out_dir, "%s_composite.jpg" % label)
        _save(merged, comp_path)
        results.append({"label": label, "n": len(frames), "single": paths[0],
                        "composite": comp_path,
                        "ladder": composite.noise_ladder(frames, args.merge),
                        "note": note})
    return results


def _save(arr, path):
    import numpy as np
    from PIL import Image
    a = np.clip(arr, 0, 255).astype("uint8")
    Image.fromarray(a).save(path, quality=92)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", default="stack", choices=("stack", "bracket"))
    ap.add_argument("--n", type=int, default=8, help="frames per burst")
    ap.add_argument("--merge", default="mean",
                    choices=("mean", "median", "sigma"))
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--out", default=os.path.expanduser("~/composite_runs"))
    ap.add_argument("--http-port", type=int, default=8094)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--once", action="store_true",
                    help="capture and write the report, do not serve")
    args = ap.parse_args(argv)

    run_dir = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    print("composite: mode=%s N=%d -> %s" % (args.mode, args.n, run_dir),
          flush=True)

    def capture():
        if args.mode == "bracket":
            print("BRACKET mode is not implemented yet -- see the card notes.",
                  flush=True)
            return [{"label": "all",
                     "error": "bracket mode not implemented yet"}]
        return run_stack(args, run_dir)

    if args.once:
        results = capture()
        for r in results:
            print("  %-7s %s" % (r["label"], r.get("error") or
                                 "N=%d ladder=%s" % (r["n"], r["ladder"])),
                  flush=True)
        build_report(results, args.mode, os.path.join(run_dir, "index.html"))
        return 0

    # SERVE FIRST, capture second. Capture + merge on a Pi Zero 2 W takes
    # minutes at HD, and the workbench health-gates LIVE on this page
    # answering within 60 s -- so a capture-then-serve order gets SIGINT'd
    # mid-merge and the card reports a failure that never happened.
    # (S28's compare card learned the same lesson: serve -> capture ->
    # report -> serve.)
    index = os.path.join(run_dir, "index.html")
    _write_progress(index, args, "starting")
    os.chdir(run_dir)

    def worker():
        try:
            results = capture()
            for r in results:
                print("  %-7s %s" % (r["label"], r.get("error") or
                                     "N=%d ladder=%s" % (r["n"], r["ladder"])),
                      flush=True)
            build_report(results, args.mode, index)
            print("report ready", flush=True)
        except Exception as exc:                        # noqa: BLE001
            # A failed capture must SAY so on the page; a stale "capturing"
            # forever is the frozen-stream failure in another costume.
            _write_progress(index, args, "FAILED: %s" % exc)
            print("composite FAILED: %s" % exc, flush=True)

    threading.Thread(target=worker, daemon=True).start()

    handler = http.server.SimpleHTTPRequestHandler

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with S((args.bind, args.http_port), handler) as srv:
        print("serving http://%s:%d/index.html"
              % (args.bind, args.http_port), flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
