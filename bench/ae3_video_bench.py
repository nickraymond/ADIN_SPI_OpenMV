# ae3_video_bench.py -- OpenMV AE3 video-ceiling benchmark
#
# Measures the three numbers that actually decide your max video quality:
#   1. JPEG encode throughput (software encoder on the 400 MHz M55) -> fps ceiling
#   2. REAL bits-per-pixel for YOUR underwater scene -> bitrate and file size
#   3. Free RAM -> how long a clip you can buffer before you must stream
#
# Run from OpenMV IDE with the camera pointed at a representative scene
# (the actual deployment scene if possible -- bpp is scene-dependent and
# that is the whole point of measuring instead of guessing).
#
# Copy the printed table out; it is the ground truth for every size estimate.

import sensor
import time
import gc

CLIP_SECONDS = 5.0
WARMUP_FRAMES = 10
MEASURE_FRAMES = 12

# (label, sensor framesize attr name) -- missing ones are skipped automatically
RESOLUTIONS = [
    ("160x120   QQVGA", "QQVGA"),
    ("320x240   QVGA",  "QVGA"),
    ("640x480   VGA",   "VGA"),
    ("800x600   SVGA",  "SVGA"),
    ("1280x720  HD",    "HD"),
    ("1280x768  WXGA",  "WXGA"),
]

QUALITIES = [15, 35, 50, 75, 90]

PIXFORMATS = [
    ("color", "RGB565"),
    ("mono",  "GRAYSCALE"),
]


def human(n):
    if n >= 1048576:
        return "%.2f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n


def bench_one(fs_attr, pf_attr, quality):
    """Return (w, h, bytes_per_frame, encode_ms, capture_ms) or None."""
    fs = getattr(sensor, fs_attr, None)
    pf = getattr(sensor, pf_attr, None)
    if fs is None or pf is None:
        return None

    try:
        sensor.reset()
        sensor.set_pixformat(pf)
        sensor.set_framesize(fs)
        sensor.skip_frames(time=1500)
    except Exception:
        return None

    for _ in range(WARMUP_FRAMES):
        sensor.snapshot()

    gc.collect()
    total_bytes = 0
    total_enc = 0
    total_cap = 0

    for _ in range(MEASURE_FRAMES):
        t0 = time.ticks_us()
        img = sensor.snapshot()
        t1 = time.ticks_us()
        w, h = img.width(), img.height()
        # fw >= 1.28 renamed compressed() -> to_jpeg(copy=True)
        if hasattr(img, "to_jpeg"):
            jpg = img.to_jpeg(quality=quality, copy=True)
        else:
            jpg = img.compressed(quality=quality)
        t2 = time.ticks_us()

        total_cap += time.ticks_diff(t1, t0)
        total_enc += time.ticks_diff(t2, t1)
        total_bytes += jpg.size()
        del jpg
        gc.collect()

    n = float(MEASURE_FRAMES)
    return (w, h,
            total_bytes / n,
            (total_enc / n) / 1000.0,
            (total_cap / n) / 1000.0)


def main():
    print("=" * 78)
    print("OpenMV AE3 video ceiling benchmark")
    print("clip length: %.1f s   frames measured per point: %d"
          % (CLIP_SECONDS, MEASURE_FRAMES))
    gc.collect()
    print("free heap at start: %s" % human(gc.mem_free()))
    print("=" * 78)

    hdr = ("%-17s %-6s %-3s %9s %7s %8s %7s %10s %11s"
           % ("resolution", "fmt", "q", "bytes/fr", "bpp",
              "enc ms", "max fps", "5s @maxfps", "Mbps@maxfps"))
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for res_label, fs_attr in RESOLUTIONS:
        for pf_label, pf_attr in PIXFORMATS:
            for q in QUALITIES:
                r = bench_one(fs_attr, pf_attr, q)
                if r is None:
                    continue
                w, h, bpf, enc_ms, cap_ms = r
                px = w * h
                bpp = (bpf * 8.0) / px
                # encoder-limited fps (capture usually overlaps in practice)
                max_fps = 1000.0 / max(enc_ms, 0.001)
                max_fps = min(max_fps, 120.0)  # sensor ceiling
                clip = bpf * max_fps * CLIP_SECONDS
                mbps = (bpf * 8.0 * max_fps) / 1e6

                print("%-17s %-6s %-3d %9d %7.3f %8.2f %7.1f %10s %11.2f"
                      % (res_label, pf_label, q, int(bpf), bpp,
                         enc_ms, max_fps, human(clip), mbps))
                rows.append((res_label, pf_label, q, bpf, bpp,
                             enc_ms, max_fps))
                gc.collect()

    print("-" * len(hdr))
    gc.collect()
    free = gc.mem_free()
    print("free heap after sweep: %s" % human(free))
    print()
    print("BUFFERING CEILING")
    print("  A clip you buffer in RAM before sending must fit in free heap.")
    print("  free heap = %s  ->  at 5 s that is %.2f Mbps sustained."
          % (human(free), (free * 8.0 / 1e6) / CLIP_SECONDS))
    print("  Anything above that must be streamed out as you encode.")
    print()
    print("HOW TO READ THIS")
    print("  bpp is scene-dependent -- this is YOUR scene, trust it over any")
    print("  generic table. MJPEG has no interframe compression, so:")
    print("      clip bytes = bytes/fr  x  fps  x  seconds")
    print("  Pick any two of {resolution, fps, quality}; the third is forced.")


main()
