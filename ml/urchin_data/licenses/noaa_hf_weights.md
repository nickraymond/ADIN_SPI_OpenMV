# NOAA HuggingFace urchin detectors — license capture (S26 bite 1)

Captured 2026-08-21 from the HF model repos + their READMEs (both
READMEs archived next to the weights in
`~/nereus_ml/datasets/noaa_hf_weights/archives/`).

## akridge/yolo11n-sea-urchin-detector

- HF repo metadata: **no license tag** (verified via
  `https://huggingface.co/api/models/akridge/yolo11n-sea-urchin-detector`).
- README (verbatim, dataset section):
  `- **License**: CC BY 4.0` /
  `- **URL**: https://universe.roboflow.com/sakana/urchins-cjlib/dataset/1`
- **The named training set is GONE**: that Roboflow URL returns
  "Project Not Found" as of 2026-08-21 (checked logged-out). The CC-BY
  claim about the training data is no longer independently verifiable.
- Base model is Ultralytics YOLO11 → the weights are arguably
  AGPL-3.0-derived even without a tag.

## NMFS-OSI/yolo11x-sea-urchin-detector

- HF repo metadata tag (verbatim): `license:agpl-3.0`; README
  frontmatter: `license: agpl-3.0`, `base_model: Ultralytics/YOLO11`.
- README names its training data as Roboflow "Diad 3"
  (`universe.roboflow.com/diad1/diad-3/dataset/6`), "License: CC BY
  4.0", 5,000 images — NOT the Sakana set the research file grouped
  both models under.

**Consequence:** both models are benchmark-only (AGPL lineage) — fine
as the strategy's step-4 free baseline, nothing from them ships in a
product.

Weights on disk (sha256 in `../manifests/noaa_hf_weights.json`):
`yolo11n_urchin_trained.pt` 5,540,153 B ·
`yolo11x_urchin_trained.pt` 114,461,312 B.
