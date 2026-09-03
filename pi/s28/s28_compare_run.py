#!/usr/bin/env python3
"""s28_compare_run.py — S28 bite 2: the one-click stacking-compare card.

The workbench recipe's argv (card `s28-stack-compare`). One click:

  1. serve a page IMMEDIATELY on --serve-port (so the workbench health
     check passes and the browser opens) — a "capturing…" placeholder
     that auto-refreshes,
  2. capture a locked BAYER burst of whatever the AE3 is pointed at
     (a reference card / static scene under steady light) via
     s28_burst_capture --plan stack,
  3. run s28_compare to write the report straight into the served page
     (single vs mean/median/sigma-clip, the √N noise ladder, and the
     flicker check that says whether the light was steady enough),
  4. keep serving until Stop (SIGINT/SIGTERM) → shut the server, exit.

A capture failure (e.g. the AE3 in its raw-repl refusal) is shown ON the
page with the recovery step, never a silent dead card. The board is used
only during step 2 and released before serving, so Stop is just a server
shutdown. Exit code: 0 clean, 1 capture failed (page shows why).
"""
import argparse
import http.server
import os
import signal
import socketserver
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

PLACEHOLDER = """<!doctype html><meta charset=utf-8>
<meta http-equiv=refresh content=3>
<title>S28 stacking compare</title>
<body style="font:14px system-ui;background:#111;color:#eee;margin:40px">
<h2>S28 frame-stacking compare</h2>
<p>Capturing a locked 16-frame BAYER burst and analysing it…</p>
<p style=color:#999>This page refreshes every 3 s; the report appears
when the burst is done (~30–60 s).</p></body>"""


def error_page(msg):
    return ("""<!doctype html><meta charset=utf-8>
<title>S28 stacking compare — capture failed</title>
<body style="font:14px system-ui;background:#111;color:#eee;margin:40px">
<h2>S28 stacking compare — capture failed</h2>
<pre style="color:#f88;white-space:pre-wrap">%s</pre>
<p style=color:#999>Most often the AE3 is in its raw-repl refusal
(bite-R): a Pi reboot clears it, then click the card again. Check the
board is aimed at a static, steadily-lit scene.</p></body>""" % msg)


def serve(serve_dir, port, stop_evt):
    os.chdir(serve_dir)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in ("/", ""):
                self.path = "/index.html"
            return super().do_GET()

    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    stop_evt.wait()
    httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="/dev/serial/by-id/"
                    "usb-OpenMV_OpenMV_Camera_0829c14000000000-if00")
    ap.add_argument("--serve-port", type=int, default=8093)
    ap.add_argument("--out-root", default=os.path.expanduser("~/s28_runs"))
    ap.add_argument("--frames", default="16")     # workbench [params] enum
    args = ap.parse_args()

    serve_dir = os.path.join(os.path.expanduser(args.out_root),
                             "compare_live")
    os.makedirs(serve_dir, exist_ok=True)
    index = os.path.join(serve_dir, "index.html")
    open(index, "w").write(PLACEHOLDER)

    stop_evt = threading.Event()

    def _stop(sig, frm):
        stop_evt.set()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    srv = threading.Thread(target=serve,
                           args=(serve_dir, args.serve_port, stop_evt),
                           daemon=True)
    srv.start()
    print("serving S28 compare on :%d" % args.serve_port, flush=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(os.path.expanduser(args.out_root),
                           "compare_%s" % stamp)
    rc = 0
    try:
        cap = subprocess.run(
            [sys.executable, "-u", "pi/s28/s28_burst_capture.py",
             "--port", args.port, "--out", run_dir, "--plan", "stack",
             "--n", str(int(args.frames)), "--workbench", "none",
             "--scene", "workbench card, %s" % stamp],
            cwd=_ROOT, capture_output=True, text=True, timeout=240)
        if cap.returncode != 0 or not os.path.isdir(
                os.path.join(run_dir, "frames", "stack_bayer_vga")):
            raise RuntimeError("burst capture failed (rc=%d)\n%s"
                               % (cap.returncode, cap.stderr[-1500:]))
        comp = subprocess.run(
            [sys.executable, "-u", "pi/s28/s28_compare.py",
             "--run", run_dir, "--stage", "stack_bayer_vga",
             "--out", index],
            cwd=_ROOT, capture_output=True, text=True, timeout=120)
        if comp.returncode != 0 or not os.path.isfile(index):
            raise RuntimeError("compare failed (rc=%d)\n%s"
                               % (comp.returncode, comp.stderr[-1500:]))
        print(comp.stdout, flush=True)
        print("report ready at :%d" % args.serve_port, flush=True)
    except Exception as e:
        open(index, "w").write(error_page(str(e)))
        print("CAPTURE/COMPARE FAILED: %s" % e, file=sys.stderr,
              flush=True)
        rc = 1

    # serve until Stop
    stop_evt.wait()
    srv.join(timeout=5)
    return rc


if __name__ == "__main__":
    sys.exit(main())
