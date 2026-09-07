#!/usr/bin/env python3
"""RAW stills: 1 frame vs N stacked, on the boards. Stills only, by design.

Answers "what does shooting raw actually buy me, and how must it be
processed to look better" -- with the pipeline in the CORRECT order, which
is the part that makes the difference visible:

    capture raw (LINEAR: no debayer, no WB, no gamma)
      -> mean-stack in linear          <- averaging real photon counts
      -> demosaic                      <- interpolate AFTER averaging
      -> white balance (grey world)
      -> gamma encode for display      <- only now is it a picture

Doing gamma first, or demosaicing first, is what makes a composite look
"barely different" -- the arithmetic lands in the wrong space and the win
is compressed out of the shadows, which is exactly where stacking helps.

Raw is stills-only here for a measured reason: one HD Bayer frame is
1,024,000 bytes, ~1.37 MB as base64 on the wire, against ~85 kB for the
same frame as JPEG. That is ~16x the bytes on the slowest link in the rig.
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


def white_balance(rgb):
    """Grey-world WB. Raw has NO white balance applied -- without this the
    image is strongly green, because a Bayer array has twice as many green
    photosites as red or blue."""
    np = composite._np()
    out = rgb.astype(np.float32)
    means = [out[:, :, c].mean() or 1.0 for c in range(3)]
    target = sum(means) / 3.0
    for c in range(3):
        out[:, :, c] *= target / means[c]
    return np.clip(out, 0, 255)


def finish(rgb_linear, gamma=True):
    """WB then gamma. The last two steps, in that order."""
    np = composite._np()
    out = white_balance(rgb_linear)
    if gamma:
        out = composite.to_display(out / 255.0)
    return np.clip(out, 0, 255).astype(np.uint8)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--boards", default="AE3,N6")
    ap.add_argument("--out", default=os.path.expanduser("~/raw_stills"))
    args = ap.parse_args(argv)

    np = composite._np()
    from PIL import Image
    run_dir = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    print("raw stills: N=%d %s -> %s" % (args.n, args.framesize, run_dir),
          flush=True)

    found, _ = discovery.discover()
    for role in [r.strip() for r in args.boards.split(",") if r.strip()]:
        info = found.get(role)
        if not info:
            print("  %s: not found" % role, flush=True)
            continue
        t0 = time.time()
        paths, geom = composite.board_raw_burst(info["port"], args.n,
                                                run_dir, role,
                                                size=args.framesize)
        if len(paths) < 2 or not geom:
            print("  %s: only %d raw frame(s) captured" % (role, len(paths)),
                  flush=True)
            continue
        single, stacked = composite.stack_raw(paths, geom)
        Image.fromarray(finish(single)).save(
            os.path.join(run_dir, "%s_raw_single.jpg" % role), quality=95)
        Image.fromarray(finish(stacked)).save(
            os.path.join(run_dir, "%s_raw_stacked.jpg" % role), quality=95)

        # Noise, measured on the LINEAR data before any gamma -- the number
        # gamma would otherwise flatter.
        frames = [np.frombuffer(open(p, "rb").read(),
                                dtype=np.uint8)[:geom[0] * geom[1]]
                  .reshape(geom[1], geom[0]).astype(np.float32)[::4, ::4]
                  for p in paths]
        sigma1 = float(np.mean(np.std(np.stack(frames, 0), axis=0)))
        half = len(frames) // 2
        m1 = np.mean(np.stack(frames[:half], 0), axis=0)
        m2 = np.mean(np.stack(frames[half:], 0), axis=0)
        sigmaN = float(np.mean(np.std(np.stack([m1, m2], 0), axis=0)))
        print("  %-4s %dx%d  N=%d  %.1fs  sigma 1frame=%.3f  %d-frame=%.3f  "
              "improvement=%.2fx" % (role, geom[0], geom[1], len(paths),
                                     time.time() - t0, sigma1, half, sigmaN,
                                     sigma1 / sigmaN if sigmaN else 0),
              flush=True)
    print("artifacts: %s" % run_dir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
