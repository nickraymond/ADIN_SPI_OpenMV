# S31 clip grab — RESOLVED via mp4.py

**2026-09-07, second attempt: the comparison is delivered.**
`COMPARE_hd_crops.png` is a 1:1 centre-crop of MJPEG q90 against
H.264 at 8 / 16 / 32 Mbps, all four verified as real images
(luma stddev 38.8-44.7; a garbage frame measures 4-8).

**What fixed it: `mp4.py`, PR #3247's own supported route.** It muxes
to AVCC with SPS/PPS out-of-band in the `avcC` box, pulled from the
encoder instance itself. All three MP4s decode with zero ffmpeg
warnings. The raw Annex-B path remains unexplained and is recorded
below — but it is NOT a proven PR defect, and the working route is
the one the PR documents.

**One gotcha worth carrying:** `mp4.Mp4(...)` defaults to
`buffer_size=262144`, and an HD IDR here is ~293 KB, so the default
raises `ValueError: access unit larger than buffer_size`. Pass
`buffer_size=2*1024*1024` for HD.

---

## The earlier raw-Annex-B attempt, and why it failed

**Status 2026-09-07: the visual comparison is NOT delivered.** The MJPEG q90
reference decodes correctly; the three H.264 clips do not, and the board
stopped answering the REPL before I could iterate. Numbers are unaffected —
they come from the encoder's own hardware readback, not from decoding.

## What is wrong

The captured Annex-B streams are **structurally perfect**:

```
off        1  size       26  type  7 SPS
off       27  size        9  type  8 PPS
off       36  size   293360  type  5 IDR
off   293396  size   180427  type  1 P-slice
...  32 NALs, 4,692,042 bytes
```

…and the IDR still will not decode:

```
[h264] top block unavailable for requested intra mode -1
[h264] error while decoding MB 0 0
```

Frame 1 decodes to **uniform grey (stddev 0.0)**; the P-frames then accumulate
real residual onto that blank reference, so stddev creeps 3.9 → 6.4 over the
clip while a correct frame measures ~38. The whole clip is therefore garbage,
at 8, 16 and 32 Mbps alike.

## Three wrong turns worth not repeating

1. **PNG file size is not proof of a decoded frame.** The garbage frames wrote
   1.0–1.4 MB PNGs — noise compresses poorly — and I took that as success.
   Only `ImageStat` stddev exposed them. Trust artifacts, and check the
   artifact's *content*, not its size.
2. **`encode()` emits only the slice NAL** (verified by parsing NAL types:
   type 5, then 1,1,1…). The Annex-B file must begin with `sps_pps()`.
3. **The SPS/PPS must come from the encoder instance that produced the clip.**
   In bitrate mode `py_codec` sets `qpHdr = -1`, so rate control picks
   `pic_init_qp` from the first frame's statistics; a separately constructed
   encoder at the same bitrate emits a *different* PPS (`…ee02b248` vs
   `…ee027248`), and decoding with it gives a washed-out low-contrast image
   rather than an error.

## What has NOT been ruled out

- **My use of the raw path.** PR #3247's own testing validated MP4s written by
  `mp4.py`, which muxes to AVCC with out-of-band SPS/PPS — a different path
  from the raw Annex-B byte stream used here. The next attempt should use
  `mp4.py`, which is the PR's supported route.
- **A defect in the draft PR's Annex-B output.** Possible, not established.
  Do not report this upstream until the `mp4.py` path has been tried.

## Bench state

The N6 refused the raw REPL twice (45 s of port silence between attempts, one
attempt each, no polling). Contact was stopped there per the bench rule. This
board had **zero** such refusals before today (S30 session's count), so it is
either the draft-PR firmware or the repeated `mpremote mount` sessions. It
needs a power cut — `pi/field/power_cycle.py` on nereus002, which refuses on a
flat battery; the rig was on the charger at ~5.1 V.
