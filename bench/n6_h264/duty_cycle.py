#!/usr/bin/env python3
"""Duty-cycle arithmetic for the N6 MJPEG -> H.264 question (S31, desk-only).

Answers the question docs/N6_H264_SPEC.md sec 4 insists on: not "how much
smaller is the file" but "at what duty cycle does the win pay for itself".

Every input is labelled MEASURED or DERIVED. Nothing here touches hardware;
the only measured inputs are the S30/S29 numbers quoted in the spec.

Run:  python3 bench/n6_h264/duty_cycle.py
"""

# --- Inputs -----------------------------------------------------------------

# MEASURED (S30 probe on nereus002, quoted in docs/N6_H264_SPEC.md sec 3).
# The probe excludes USB transfer, so this is the camera's own capture+encode
# ceiling. VGA q30 on the N6.
N6_VGA_Q30_BYTES_PER_FRAME = 13.7 * 1024
N6_VGA_Q30_ENCODE_FPS = 68.6

# MEASURED (S29, IMX708 720p15 on nereus002): the only MJPEG->H.264 ratio this
# project owns. Different sensor, different encoder, different scene -- an
# anchor, not a prediction for the N6.
S29_MJPEG_MBPS, S29_H264_MBPS = 26.43, 6.60

# DERIVED: prediction band for the N6's H.264 win at equal perceptual quality.
# Low end = a scene that defeats inter-frame prediction (suspended particulate,
# surge, rolling gain). High end = a near-static benthic scene, which is what a
# moored urchin camera actually sees. The S29 ratio sits inside this band.
RATIO_LOW, RATIO_MID, RATIO_HIGH = 3.0, 4.0, 8.0

# Nick's stated need.
CLIP_SECONDS, CLIP_FPS = 5.0, 30.0

# SPEC sec "Link + stream budget": T1L sustained video budget.
T1L_VIDEO_BUDGET_MBPS = 8.0


def mbps(bytes_total, seconds):
    return bytes_total * 8 / seconds / 1e6


def kbps(bytes_total, seconds):
    return bytes_total * 8 / seconds / 1e3


# The spec quotes clip payloads in MiB (1.95 MB for the 5 s VGA clip); keep the
# same convention so the two documents cannot be read as disagreeing.
def mib(bytes_total):
    return bytes_total / (1024 * 1024)


# --- 1. The instantaneous rate while recording ------------------------------

mjpeg_clip_bytes = N6_VGA_Q30_BYTES_PER_FRAME * CLIP_FPS * CLIP_SECONDS
mjpeg_inst_mbps = mbps(mjpeg_clip_bytes, CLIP_SECONDS)

print("=" * 72)
print("N6 VGA q30, 30 fps -- while recording          [MEASURED bytes/frame]")
print("=" * 72)
print(f"  bytes/frame              {N6_VGA_Q30_BYTES_PER_FRAME/1024:8.1f} KB")
print(f"  instantaneous bitrate    {mjpeg_inst_mbps:8.2f} Mbps")
print(f"  {CLIP_SECONDS:.0f} s clip payload         {mib(mjpeg_clip_bytes):8.2f} MiB")
print(f"  S29 anchor ratio         {S29_MJPEG_MBPS/S29_H264_MBPS:8.2f}x  "
      f"({S29_MJPEG_MBPS} -> {S29_H264_MBPS} Mbps, IMX708 720p15) [MEASURED]")
print()

# --- 2. One clip per hour: the case actually on the table -------------------

HOUR = 3600.0
print("=" * 72)
print("One 5 s clip per hour (duty cycle 0.139%)     [DERIVED from measured]")
print("=" * 72)
print(f"{'codec':<26}{'clip':>11}{'sustained':>13}{'saved':>12}")
mj_sus = kbps(mjpeg_clip_bytes, HOUR)
print(f"{'MJPEG q30 (measured)':<26}{mib(mjpeg_clip_bytes):9.2f} MiB{mj_sus:11.2f} kbps{'--':>12}")
for name, r in (("H.264 pessimistic", RATIO_LOW),
                ("H.264 S29-anchored", RATIO_MID),
                ("H.264 optimistic", RATIO_HIGH)):
    b = mjpeg_clip_bytes / r
    s = kbps(b, HOUR)
    print(f"{name + f' ({r:.0f}x)':<26}{mib(b):9.2f} MiB{s:11.2f} kbps{mj_sus - s:9.2f} kbps")
print()
print(f"  Ceiling on the saving: {mj_sus:.2f} kbps. You cannot save more than")
print( "  the entire MJPEG budget, and at this duty cycle that is the whole prize.")
print()

# --- 3. The crossover: duty cycle vs absolute saving ------------------------

print("=" * 72)
print("Crossover -- duty cycle needed to save a given sustained rate")
print("=" * 72)
print(f"{'saving target':<18}" + "".join(f"{f'at {r:.0f}x':>16}" for r in
                                          (RATIO_LOW, RATIO_MID, RATIO_HIGH)))
for target_kbps in (10.0, 100.0, 1000.0):
    row = f"{target_kbps:>7.0f} kbps      "
    for r in (RATIO_LOW, RATIO_MID, RATIO_HIGH):
        # saving = inst_rate * duty * (1 - 1/r)
        duty = (target_kbps / 1e3) / (mjpeg_inst_mbps * (1 - 1 / r))
        secs_per_hour = duty * HOUR
        if duty > 1.0:
            row += f"{'not reachable':>16}"
        else:
            row += f"{secs_per_hour:12.0f} s/h"
    print(row)
print()
print("  Read this as: to save even 100 kbps sustained you must record")
print("  minutes of video per hour, not one 5 s clip.")
print()

# --- 4. Where H.264 is enabling rather than merely cheaper ------------------

print("=" * 72)
print("Where H.264 changes feasibility, not cost")
print("=" * 72)
# DERIVED: scale the measured VGA bits-per-pixel to other resolutions at the
# same quality setting. bpp is roughly flat-to-falling with resolution at a
# fixed quantiser, so this is an upper-ish bound, and it is NOT a measurement.
bpp = N6_VGA_Q30_BYTES_PER_FRAME * 8 / (640 * 480)
print(f"  measured VGA q30 density {bpp:.3f} bpp   [MEASURED]")
print(f"{'resolution':<14}{'fps':>5}{'MJPEG':>12}{'H.264 4x':>12}   fits 8 Mbps T1L?")
for label, w, h, fps in (("QVGA", 320, 240, 30), ("VGA", 640, 480, 30),
                         ("720p", 1280, 720, 24), ("720p", 1280, 720, 30)):
    mj = bpp * w * h * fps / 1e6
    h264 = mj / RATIO_MID
    verdict = ("MJPEG fits" if mj <= T1L_VIDEO_BUDGET_MBPS else
               ("H.264 ONLY" if h264 <= T1L_VIDEO_BUDGET_MBPS else "neither"))
    print(f"{label:<14}{fps:>5}{mj:10.2f} Mbps{h264:9.2f} Mbps   {verdict}")
print()
print("  All rows except the VGA measurement are DERIVED by bpp scaling.")
print("  The 720p rows are the cell SPEC.md already called out as needing")
print("  H.264 ('Public 720p stream -- needs H.264 (N6, non-goal)').")


# --- 5. Does the encoder fit? -----------------------------------------------
# Allocation sizes read out of the vendored driver, not guessed:
#   drivers/vc8000/src/common/encasiccontroller_v2.c  EncAsicMemAlloc_V2()
#   drivers/vc8000/src/h264/H264Init.c:487            DOUBLE_BUFFER -> 2 lum, 2 chr
#   ports/stm32/modules/py_codec.c (PR #3247)         out_buf = w*h*3/2 + 4096
# encOutputMbInfoDebug_s is 56 B (drivers/vc8000/include/enccommon.h:195-215).

N6_FREE_HEAP_VGA_MB = 25.6   # MEASURED, docs/N6_H264_SPEC.md sec 3

def encoder_footprint(w, h, num_ref_lum=2, num_ref_chr=2):
    mb_total = ((w + 15) // 16) * ((h + 15) // 16)
    parts = {
        "ref luma": num_ref_lum * mb_total * 16 * 16,
        "ref chroma": num_ref_chr * mb_total * 2 * 8 * 8,
        "CABAC ctx": 52 * 2 * 464,
        "MV/MB info": mb_total * 56,
        "NAL size tbl": ((4 * ((h + 15) // 16 + 4)) + 7) & ~7,
        "segment map": (mb_total * 4 + 63) // 64 * 8,
        "output buf": w * h * 3 // 2 + 4096,
    }
    return mb_total, parts


print()
print("=" * 72)
print("Encoder footprint vs the N6's free heap        [DERIVED from driver src]")
print("=" * 72)
print(f"{'resolution':<14}{'macroblocks':>13}{'encoder RAM':>14}{'% of free heap':>16}")
for label, w, h in (("QVGA", 320, 240), ("VGA", 640, 480),
                    ("720p", 1280, 720), ("1080p", 1920, 1080)):
    mb_total, parts = encoder_footprint(w, h)
    total = sum(parts.values())
    pct = 100.0 * total / (N6_FREE_HEAP_VGA_MB * 1024 * 1024)
    print(f"{label:<14}{mb_total:>13}{total/(1024*1024):11.2f} MiB{pct:14.1f} %")
print()
mb_total, parts = encoder_footprint(640, 480)
print("  VGA breakdown:")
for k, v in parts.items():
    print(f"    {k:<16}{v/1024:9.1f} KiB")
print()
print("  ST's community guidance that 1080p 'won't fit into the internal memory'")
print("  applies to STM32Cube examples that allocate from internal SRAM. The")
print("  OpenMV N6 has DRAM and EWLMallocLinear asks for UMA_FAST with UMA_MAYBE,")
print("  i.e. prefer-fast-fall-back-to-DRAM, so that limit should not bind here.")
print("  UNVERIFIED on hardware -- see the bench plan.")
