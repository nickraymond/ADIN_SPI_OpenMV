#!/usr/bin/env python3
"""HDR bracket on RAW: -EV / 0 / +EV merged in linear space, per board.

Why raw and not JPEG: the merge divides each frame by its own exposure so
every frame becomes an estimate of the same scene radiance. A ratio only
means something when the numbers are proportional to photons -- so on a
gamma-encoded JPEG the arithmetic is WRONG, not merely imprecise. That is
the difference between a real HDR and a cosmetic one.

Pipeline: bracket raw -> divide each by ITS OWN measured exposure ->
weighted merge to radiance -> tonemap -> demosaic -> WB -> gamma.
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "s28"))

import composite                                     # noqa: E402
import discover as discovery                         # noqa: E402
from raw_still import finish                         # noqa: E402


def ev_ladder(base_us, stops=(-2.0, 0.0, 2.0), frame_time_us=None):
    """Exposures for the EV rungs, clamped to what the frame time allows.

    These sensors CLAMP exposure to the current frame time minus a margin
    (S28 measured it on the PAG7936), so a +2 EV request can silently come
    back unchanged -- which would make two 'different' frames identical and
    the merge meaningless. Clamping here makes the limit visible instead.
    """
    out = []
    for ev in stops:
        us = int(base_us * (2.0 ** ev))
        if frame_time_us:
            us = min(us, int(frame_time_us * 0.95))
        out.append(max(us, 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stops", default="-2,0,2")
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--boards", default="AE3,N6")
    ap.add_argument("--out", default=os.path.expanduser("~/hdr_stills"))
    args = ap.parse_args(argv)

    np = composite._np()
    from PIL import Image
    import s28_stack
    stops = [float(s) for s in args.stops.split(",")]
    run_dir = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    print("HDR bracket: stops=%s %s -> %s" % (stops, args.framesize, run_dir),
          flush=True)

    found, _ = discovery.discover()
    for role in [r.strip() for r in args.boards.split(",") if r.strip()]:
        info = found.get(role)
        if not info:
            print("  %s: not found" % role, flush=True)
            continue
        # First pass: one frame to learn the metered exposure.
        probe, geom, base_us = composite.board_bracket(
            info["port"], [0], run_dir, role + "_probe", size=args.framesize)
        if not base_us:
            base_us = 8000
            print("  %s: could not read metered exposure, assuming %d us"
                  % (role, base_us), flush=True)
        time.sleep(40)                    # settle before the second attach
        exps = ev_ladder(base_us, stops)
        frames, geom, _ = composite.board_bracket(
            info["port"], exps, run_dir, role, size=args.framesize)
        if len(frames) < 2 or not geom:
            print("  %s: only %d bracket frame(s)" % (role, len(frames)),
                  flush=True)
            continue

        got = [f["got_us"] for f in frames]
        spread = (max(got) / min(got)) if min(got) > 0 else 0
        print("  %-4s base=%d us  requested=%s  actual=%s  spread=%.1fx"
              % (role, base_us, exps, got, spread), flush=True)
        if spread < 1.5:
            print("     WARNING: exposures barely differ -- the sensor "
                  "clamped them, so this merge is not a real HDR",
                  flush=True)

        radiance = composite.merge_bracket(frames, geom)
        merged = composite.tonemap(radiance)
        Image.fromarray(finish(s28_stack.demosaic(
            np.clip(merged, 0, 255).astype(np.uint8), "BGGR"), gamma=False)
        ).save(os.path.join(run_dir, "%s_hdr.jpg" % role), quality=95)
        # The metered single frame, same pipeline, for comparison.
        mid = min(frames, key=lambda f: abs(f["got_us"] - base_us))
        a = np.frombuffer(open(mid["path"], "rb").read(),
                          dtype=np.uint8)[:geom[0] * geom[1]].reshape(
                              geom[1], geom[0])
        Image.fromarray(finish(s28_stack.demosaic(a, "BGGR"))).save(
            os.path.join(run_dir, "%s_single.jpg" % role), quality=95)
        print("     wrote %s_single.jpg and %s_hdr.jpg" % (role, role),
              flush=True)
    print("artifacts: %s" % run_dir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
