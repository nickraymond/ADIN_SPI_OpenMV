# make_ref_scene.py -- host-side: turn a full-res reference photo into the
# AE3 sensor's actual output geometries, for the reference-scene encode bench.
#
# The AE3 sensor (id 0x7936) letterboxes to 16:10: QVGA->320x200,
# VGA->640x400, HD->1280x800. We keep the scene's full ROI: center-crop the
# source to 16:10 (no scene content lost horizontally), then downsample
# spatial density only. Color goes out as 24-bit BMP, mono as binary PGM --
# both load natively on OpenMV via image.Image().
#
# Usage:  python3 bench/make_ref_scene.py <source.jpg> <outdir> [label]
# Then:   ssh pi 'mpremote mount <outdir dir on pi> run ae3_ref_scene_bench.py'

import sys
import os
from PIL import Image

SIZES = ((320, 200), (640, 400), (1280, 800))
ASPECT = 16 / 10


def crop_16_10(img):
    """Center-crop to 16:10 keeping full width if source is taller (4:3)."""
    w, h = img.size
    if w / h > ASPECT:          # too wide: trim sides
        new_w = int(round(h * ASPECT))
        x0 = (w - new_w) // 2
        return img.crop((x0, 0, x0 + new_w, h))
    new_h = int(round(w / ASPECT))  # too tall: trim top/bottom
    y0 = (h - new_h) // 2
    return img.crop((0, y0, 0 + w, y0 + new_h))


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: make_ref_scene.py <source.jpg> <outdir> [label]")
    src_path, outdir = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else "ref"
    os.makedirs(outdir, exist_ok=True)

    src = Image.open(src_path)
    src = src.convert("RGB")
    cropped = crop_16_10(src)
    print("source %s %dx%d -> cropped %dx%d (16:10)"
          % (os.path.basename(src_path), *src.size, *cropped.size))

    for w, h in SIZES:
        small = cropped.resize((w, h), Image.LANCZOS)
        color_path = os.path.join(outdir, "%s_color_%dx%d.bmp" % (label, w, h))
        small.save(color_path)
        mono_path = os.path.join(outdir, "%s_mono_%dx%d.pgm" % (label, w, h))
        small.convert("L").save(mono_path)
        print("  wrote %s (%d B), %s (%d B)"
              % (color_path, os.path.getsize(color_path),
                 mono_path, os.path.getsize(mono_path)))


if __name__ == "__main__":
    main()
