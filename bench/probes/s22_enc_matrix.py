# s22_enc_matrix.py -- S22 bite 2, measurement window (Nick approved):
# what do subsampling and quality actually buy on THIS encoder for the
# reef reference scene? Times `to_jpeg` exactly as the bridge calls it
# (image.Image(path) -> to_jpeg(quality=..., copy=True), plus the
# subsampling kwarg under test), on the ref assets demo_up already
# stages at /flash/ref_scene/. No sensor, no HE, no chain -- run with a
# neutral /flash/main.py per ae3-board-access.
#
# Output: one row per (mode x subsampling x q): median encode us of
# REPS runs + encoded bytes + the fps ceiling the encode time implies.
# The table goes to /flash/s22_enc.txt AND stdout; the artifact is the
# authority (CLAUDE.md rule 4).
#
# Modes measured are the ones Nick's targets ride on: VGA color (15 fps
# target), HD mono (5-6 fps target), HD color (honesty row), VGA mono
# (the near-miss 13.27), QVGA color (control vs S0's 19.7 ms).
#
# Usage:
#     mpremote connect <by-id> run bench/probes/s22_enc_matrix.py

import time

LOG = "/flash/s22_enc.txt"
REF = "/flash/ref_scene"
REPS = 5

# (label, asset, color?)
MODES = [
    ("qvga-color", "ref_color_320x200.bmp", True),
    ("vga-color", "ref_color_640x400.bmp", True),
    ("vga-mono", "ref_mono_640x400.pgm", False),
    ("hd-color", "ref_color_1280x800.bmp", True),
    ("hd-mono", "ref_mono_1280x800.pgm", False),
]
QS = (35, 50, 60)


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(msg + "\n")
            f.flush()
    except Exception:
        pass
    print(msg)


def median(v):
    s = sorted(v)
    return s[len(s) // 2]


def main():
    import gc
    import image

    # AUTO row included deliberately: it is what the bridge ships today
    # (q<=35 -> 420, q<60 -> 422, else 444 -- jpege.c), so the delta
    # rows read directly as "what a one-kwarg change buys".
    subs = [("auto", image.JPEG_SUBSAMPLING_AUTO),
            ("420", image.JPEG_SUBSAMPLING_420),
            ("422", image.JPEG_SUBSAMPLING_422),
            ("444", image.JPEG_SUBSAMPLING_444)]

    log("=" * 64)
    log("S22 enc matrix -- reef refs, to_jpeg(quality, subsampling), "
        "%d reps, median" % REPS)
    log("%-11s %-5s q%-3s %9s %8s %7s" % ("mode", "sub", "", "enc_us",
                                          "bytes", "fps"))
    for (label, asset, is_color) in MODES:
        path = REF + "/" + asset
        gc.collect()
        try:
            img = image.Image(path)
        except Exception as e:
            log("%-11s SKIP: %r" % (label, e))
            continue
        # Mono: subsampling is forced 444 by the encoder for grayscale;
        # measure only AUTO (the knob does not exist for these modes).
        mode_subs = subs if is_color else subs[:1]
        for (sname, sval) in mode_subs:
            for q in QS:
                times = []
                nbytes = 0
                ok = True
                for _ in range(REPS):
                    gc.collect()
                    t0 = time.ticks_us()
                    try:
                        jpg = img.to_jpeg(quality=q, copy=True,
                                          subsampling=sval)
                    except Exception as e:
                        log("%-11s %-5s q%-3d ERROR: %r"
                            % (label, sname, q, e))
                        ok = False
                        break
                    dt = time.ticks_diff(time.ticks_us(), t0)
                    times.append(dt)
                    nbytes = len(jpg.bytearray()) if hasattr(jpg, "bytearray") \
                        else jpg.size()
                    del jpg
                if not ok:
                    continue
                med = median(times)
                log("%-11s %-5s q%-3d %9d %8d %7.2f"
                    % (label, sname, q, med, nbytes, 1e6 / med))
        del img
        gc.collect()
    log("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import sys
        log("PYTHON EXCEPTION: %r" % (e,))
        try:
            with open(LOG, "a") as f:
                sys.print_exception(e, f)
        except Exception:
            pass
