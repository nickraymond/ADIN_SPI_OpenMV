# s23_enc_golden.py -- S23 bite 1: the golden-image gate for the MVE
# color-convert patch. Same harness as s22_enc_matrix (reef refs staged
# at /flash/ref_scene, to_jpeg exactly as the bridge calls it, neutral
# /flash/main.py per ae3-board-access), plus a sha256 per row.
#
# Protocol: run ONCE on the stock build (hashes = the golden), flash the
# patched build, run AGAIN. The patch claims BIT-IDENTICAL arithmetic,
# so every color-row hash must match exactly -- a single differing hash
# fails the gate and blocks any on-chain use of the patched build.
# Mono rows must match trivially (the patch never touches grayscale).
# The encode-time delta between the two runs IS the (a) measurement.
#
# Output rows: mode sub q enc_us bytes sha256[:16], to /flash/
# s23_golden.txt AND stdout; the on-flash artifact is the authority.
#
# Usage:
#     mpremote connect <by-id> run bench/probes/s23_enc_golden.py

import time

LOG = "/flash/s23_golden.txt"
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
QS = (35, 50, 60, 90)


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
    import hashlib
    import binascii

    # 420/422/444 forced rows cover every branch the bridge can reach
    # (bite 0 ships forced 420; 422/444 prove the shared converter is
    # exact for the other subsampling paths too). Mono rides AUTO --
    # the knob does not exist for grayscale.
    subs = [("420", image.JPEG_SUBSAMPLING_420),
            ("422", image.JPEG_SUBSAMPLING_422),
            ("444", image.JPEG_SUBSAMPLING_444)]

    log("=" * 64)
    log("S23 enc golden -- reef refs, sha256 per row, %d reps median"
        % REPS)
    log("%-11s %-4s q%-3s %9s %8s  %s" % ("mode", "sub", "", "enc_us",
                                          "bytes", "sha256_16"))
    for (label, asset, is_color) in MODES:
        path = REF + "/" + asset
        gc.collect()
        try:
            img = image.Image(path)
        except Exception as e:
            log("%-11s SKIP: %r" % (label, e))
            continue
        mode_subs = subs if is_color else \
            [("auto", image.JPEG_SUBSAMPLING_AUTO)]
        for (sname, sval) in mode_subs:
            for q in QS:
                times = []
                digest = ""
                nbytes = 0
                ok = True
                for _ in range(REPS):
                    gc.collect()
                    t0 = time.ticks_us()
                    try:
                        jpg = img.to_jpeg(quality=q, copy=True,
                                          subsampling=sval)
                    except Exception as e:
                        log("%-11s %-4s q%-3d ERROR: %r"
                            % (label, sname, q, e))
                        ok = False
                        break
                    dt = time.ticks_diff(time.ticks_us(), t0)
                    times.append(dt)
                    b = jpg.bytearray()
                    nbytes = len(b)
                    digest = binascii.hexlify(
                        hashlib.sha256(b).digest()).decode()[:16]
                    del jpg
                if not ok:
                    continue
                log("%-11s %-4s q%-3d %9d %8d  %s"
                    % (label, sname, q, median(times), nbytes, digest))
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
