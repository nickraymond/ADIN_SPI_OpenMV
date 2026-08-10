# ae3_ref_scene_bench.py -- encode benchmark against a stored reference
# scene (e.g. synthetic coral reef) instead of the live camera feed.
#
# Companion to ae3_video_bench.py: same modes/qualities, but bytes/frame and
# bpp reflect a REPRESENTATIVE scene, not whatever the bench camera sees.
# Encode times on loaded images are valid (encoder cost is per-pixel);
# capture time is not measured here (no sensor involved).
#
# Run with the ref images visible at /remote via mpremote mount:
#   mpremote connect <dev> mount <dir-with-bmps-pgms> run ae3_ref_scene_bench.py
# Files expected (from make_ref_scene.py): <label>_color_WxH.bmp,
# <label>_mono_WxH.pgm for 320x200 / 640x400 / 1280x800.

import image
import time
import gc

LABEL = "ref"
BASE = "/remote"
SIZES = ((320, 200), (640, 400), (1280, 800))
QUALITIES = (15, 35, 50, 75, 90)
ENC_REPS = 5


def bench_image(path, w, h):
    try:
        img = image.Image(path, copy_to_fb=True)
    except Exception as e:
        print("SKIP %s: %r" % (path, e))
        return
    if img.width() != w or img.height() != h:
        print("SKIP %s: loaded %dx%d, expected %dx%d"
              % (path, img.width(), img.height(), w, h))
        return
    for q in QUALITIES:
        gc.collect()
        nbytes = 0
        t0 = time.ticks_us()
        for _ in range(ENC_REPS):
            jpg = img.to_jpeg(quality=q, copy=True)
            nbytes = jpg.size()
            del jpg
        us = time.ticks_diff(time.ticks_us(), t0)
        enc_ms = (us / ENC_REPS) / 1000.0
        bpp = (nbytes * 8.0) / (w * h)
        fps = 1000.0 / max(enc_ms, 0.001)
        mbps = (nbytes * 8.0 * fps) / 1e6
        print("%-18s %-3d %9d %7.3f %8.2f %7.1f %11.2f"
              % (path.rsplit("/", 1)[-1], q, nbytes, bpp, enc_ms, fps, mbps))
        gc.collect()


def main():
    print("=" * 70)
    print("AE3 reference-scene encode benchmark (no sensor)")
    gc.collect()
    print("free heap at start: %d bytes" % gc.mem_free())
    print("=" * 70)
    hdr = ("%-18s %-3s %9s %7s %8s %7s %11s"
           % ("file", "q", "bytes/fr", "bpp", "enc ms", "max fps", "Mbps@maxfps"))
    print(hdr)
    print("-" * len(hdr))
    for w, h in SIZES:
        bench_image("%s/%s_color_%dx%d.bmp" % (BASE, LABEL, w, h), w, h)
        bench_image("%s/%s_mono_%dx%d.pgm" % (BASE, LABEL, w, h), w, h)
    print("-" * len(hdr))
    print("NOTE: max fps here is encoder-limited only (no capture, no SPI tx).")
    print("Copy this table into DESIGN.md next to the dark-room table.")


if __name__ == "__main__":
    main()
