# refcard — Reef Reference Card V1 color analyzer

Measure a camera's color reproduction against the **Nereus Reef Reference
Card V1** — true ground truth, not the N6 (which has its own ISP color
error). Built 2026-09-02 during the AE3-vs-N6 image-quality
investigation (SPEC §Camera SENSOR and ISP).

## What it does

1. Detects the card's four **36H11 AprilTags** (ids 0=TL, 1=TR, 2=BL,
   3=BR) and solves a homography from the card to the image.
2. Samples every patch (glare-robust median) and reports per-patch
   **ΔE76** vs the card's known sRGB values.
3. Fits a **3×4 color-correction matrix (CCM)** in linear light from the
   clean patches and re-scores — the "how good can this camera's color
   get" number — and writes a CCM-corrected image.

## Run

```bash
~/nereus_ml/venvs/fomo/bin/python bench/refcard/refcard_analyze.py \
  --image ref_AE3.jpg --label AE3 --out-dir /tmp/refcard
```

Writes `<label>_report.json` (per-patch table + fitted CCM),
`<label>_overlay.jpg` (sample points + per-patch ΔE), and
`<label>_corrected.jpg` (the CCM applied to the whole frame).

Needs `opencv-contrib-python-headless` (AprilTag detection) + numpy in
the venv. The math core (homography, sRGB↔Lab, ΔE, CCM) is pure numpy
and host-tested: `python -m pytest bench/refcard/test_refcard.py -q`.

## Reading the numbers

- **Absolute ΔE carries illuminant error** — the card is lit by ambient
  light (not a calibrated illuminant) and the camera white-balances. The
  **CCM-corrected ΔE** is the fair "achievable color" read; the raw ΔE is
  dominated by exposure/WB.
- The **AE3-vs-N6 comparison at the same moment** is robust (identical
  conditions).
- Glare on the laminate corrupts individual patches — flagged per patch
  (`clipped` ≥ 0.25 → excluded from the CCM fit and marked `GLARE`).

## Card spec

`refcard_v1.json` — extracted from
`Nereus_Reef_Reference_Card_V1_11x17_RGB_vector_crop_bleed.pdf`: the four
tag-corner centers and every patch's card-coordinate center + true sRGB
value (4 grays, 7 coral tones, 6 vivid colors). Card coordinate system is
1417×472, origin top-left. Regenerate from the vector file if the card
design changes (V2 exists; this tool targets the V1 Nick has in hand).
