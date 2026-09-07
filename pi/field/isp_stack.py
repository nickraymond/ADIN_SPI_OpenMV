#!/usr/bin/env python3
"""Stack N raw frames ON THE BOARD, then run the result through its own
debayer/gamma. Nick's idea, 2026-09-06.

The point: we were throwing away the board's ISP work to get linear data,
then re-doing demosaic/WB/gamma badly on the host. This keeps the linear
maths where it must be (the averaging) and gives the board's own pipeline
CLEAN data to finish -- best of both.

It also removes the reason raw was stills-only: the board ships ONE
finished JPEG (~85 kB) instead of N raw frames at ~1.37 MB each. A 16-frame
stack goes from ~22 MB on the wire to ~85 kB.
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import composite                                     # noqa: E402
import discover as discovery                         # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--boards", default="AE3,N6")
    ap.add_argument("--settle", type=float, default=40.0)
    ap.add_argument("--out", default=os.path.expanduser("~/isp_stack_runs"))
    args = ap.parse_args(argv)

    run_dir = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    print("on-board ISP stack: N=%d %s -> %s"
          % (args.n, args.framesize, run_dir), flush=True)

    found, _ = discovery.discover()
    for role in [r.strip() for r in args.boards.split(",") if r.strip()]:
        info = found.get(role)
        if not info:
            print("  %s: not found" % role, flush=True)
            continue
        # N=1 first: the control, through the identical code path, so the
        # comparison isolates stacking rather than the pipeline.
        t0 = time.time()
        one, i1 = composite.board_isp_stack(info["port"], 1, run_dir,
                                            role + "_x1", size=args.framesize,
                                            quality=args.quality)
        time.sleep(args.settle)
        many, iN = composite.board_isp_stack(info["port"], args.n, run_dir,
                                             role, size=args.framesize,
                                             quality=args.quality)
        heaps = iN.get("heap") or []
        print("  %-4s N=%d  stack=%s ms  debayer=%s ms  wall=%.1fs  "
              "heap %s -> %s  x1=%s  xN=%s"
              % (role, args.n, iN.get("stack_ms"), iN.get("debayer_ms"),
                 time.time() - t0,
                 heaps[0] if heaps else "?", heaps[-1] if heaps else "?",
                 os.path.basename(one) if one else "FAILED",
                 os.path.basename(many) if many else "FAILED"), flush=True)
        if many:
            print("     shipped %d bytes (vs ~%d MB as %d raw frames)"
                  % (os.path.getsize(many),
                     int(args.n * 1.37), args.n), flush=True)
        time.sleep(args.settle)
    print("artifacts: %s" % run_dir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
