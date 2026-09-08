#!/usr/bin/env python3
"""The HD answer Nick asked for: ~30 fps, max quality, N6.

Reads the measured ladder in results/hd_quality_ladder.json and pairs MJPEG
against hardware H.264 by INTRA-FRAME SIZE, not by nominal quality number.

Why intra-size pairing. "q70" in JPEG and quality=70 in H.264 (which maps to
a fixed QP via 51 - q*51/100) are unrelated scales, so a same-nominal-q table
compares nothing. An H.264 IDR frame is an intra-coded still at the same
resolution -- the closest thing the codec has to a JPEG -- so two intra frames
of equal size are, to first order, equal quality. The bias is known and it
runs AGAINST H.264 here: H.264 intra is more efficient than JPEG at equal
bytes, so at matched intra size the H.264 clip looks slightly BETTER, and the
true equal-quality ratio is therefore at least what this prints.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LADDER = os.path.join(HERE, "results", "hd_quality_ladder.json")

CLIP_S, CLIP_FPS, HOUR = 5.0, 30.0, 3600.0


def load():
    d = json.load(open(LADDER))
    mj = sorted([r for r in d["results"] if r["codec"] == "mjpeg"],
                key=lambda r: r["quality"])
    h264 = sorted([r for r in d["results"] if r["codec"] == "h264"],
                  key=lambda r: r["quality"])
    return d, mj, h264


def match_by_intra(h264, target_bytes):
    """The H.264 cell whose intra frame is closest to target_bytes."""
    return min(h264, key=lambda r: abs(r["intra_bytes_mean"] - target_bytes))


def main():
    d, mj, h264 = load()
    info = [i for i in d["info"] if "max_size_probe" in i]
    dims = info[0]["max_size_probe"]["HD"] if info else "?"
    print("=" * 78)
    print("N6 HD, hardware encoders, measured on nereus002 2026-09-07")
    print("=" * 78)
    print("  frame size delivered : %s -- the PAG7936 is 16:10, so csi.HD is"
          % dims)
    print("                         1280x800, NOT 1280x720")
    print("  scene                : static indoor (H.264's best case)")
    print("  clip basis           : %.0f s at %.0f fps = %d frames"
          % (CLIP_S, CLIP_FPS, int(CLIP_S * CLIP_FPS)))
    print()

    print("MJPEG ladder (the N6's hardware JPEG quantises quality to")
    print("quality/10, so q90 and q95 are the SAME setting -- source:")
    print("ports/stm32/stm_jpeg.c:138 `.qLevel = quality / 10`)")
    print("  %5s  %12s  %9s  %8s" % ("q", "B/frame", "enc ms", "fps"))
    for r in mj:
        print("  %5d  %12d  %9.2f  %8.1f"
              % (r["quality"], r["bytes_per_frame"], r["encode_ms_per_frame"],
                 r["achieved_fps"]))
    print()

    print("H.264 ladder, intra vs inter split")
    print("  %5s  %12s  %12s  %12s  %8s  %7s"
          % ("q", "clip B/frame", "intra B", "inter B", "enc ms", "fps"))
    for r in h264:
        print("  %5d  %12d  %12d  %12d  %8.2f  %7.1f"
              % (r["quality"], r["bytes_per_frame"], r["intra_bytes_mean"],
                 r["inter_bytes_mean"], r["encode_ms_per_frame"],
                 r["achieved_fps"]))
    print()

    print("=" * 78)
    print("PAIRED BY INTRA SIZE -- the decision table")
    print("=" * 78)
    print("  %-22s %12s %12s %8s %7s" % ("", "B/frame", "5 s clip", "fps", "1/hour"))
    for m in mj:
        h = match_by_intra(h264, m["bytes_per_frame"])
        frames = CLIP_S * CLIP_FPS
        for label, r in (("MJPEG q%d" % m["quality"], m),
                         ("  H.264 q%d (matched)" % h["quality"], h)):
            clip = r["bytes_per_frame"] * frames
            print("  %-22s %12d %9.1f MB %8.1f %6.0f kbps"
                  % (label, r["bytes_per_frame"], clip / 1e6,
                     r["achieved_fps"], clip * 8 / HOUR / 1e3))
        ratio = m["bytes_per_frame"] / h["bytes_per_frame"]
        saved = (m["bytes_per_frame"] - h["bytes_per_frame"]) * frames
        intra_gap = 100.0 * (h["intra_bytes_mean"] - m["bytes_per_frame"]) / m["bytes_per_frame"]
        verdict = "" if h["achieved_fps"] >= 30 else "   <-- H.264 MISSES 30 fps"
        print("  %-22s ratio %.2fx   saves %.1f MB/clip   (intra match %+.1f%%)%s"
              % ("", ratio, saved / 1e6, intra_gap, verdict))
        print()

    print("Read this with three caveats, all of which push the SAME way:")
    print("  1. The scene is STATIC (confirmed by the S30 session that owned the")
    print("     rig). That is H.264's best case. Motion and suspended particulate")
    print("     make the ratio WORSE, never better.")
    print("  2. At high quality, H.264's inter frames are nearly as large as its")
    print("     intra frames (see the ladder). At a fixed low QP the encoder")
    print("     faithfully codes SENSOR NOISE, which is uncorrelated frame to")
    print("     frame -- so the inter-frame saving that makes H.264 famous is")
    print("     largely spent. This is the marine-snow problem in miniature.")
    print("  3. Loop fps is free-running, not clamped to 30. Frames arriving")
    print("     FASTER than 30 fps are closer together in time and correlate")
    print("     MORE, which again flatters H.264.")


if __name__ == "__main__":
    main()
