# Roboflow Tier-1b projects — license captures (S26 bite 1)

Captured 2026-08-21 by reading each public Universe page logged-out
(pages render a "LICENSE" block; text below is what the page displays,
verbatim). Full export/download requires a Roboflow account + API key —
that step is Nick's hands.

## sea-urchin/urchin-detector (the purple/red set)

- Page LICENSE block: `CC BY 4.0`
- Images: 74 · Object Detection · 1 dataset version · updated "a year ago"
- **CLASSES (10)**: `0, 1, 18, 26, 3, 7, Black Sea urchin, Purple Sea
  Urchin, Red Sea Urchin, White Sea Urchin` — six numeric junk classes
  alongside the four color classes; label hygiene is worse than the
  research row implied. Bite-2 spot-check required before trusting it
  as the purple/red seed.

## new-workspace-dex2x/sea-urchin-body-5bmiy

- Page LICENSE block: `CC BY 4.0`
- Images: 2,273 · Instance Segmentation · 10 versions · updated
  "4 months ago"
- CLASSES (4): `Collector urchin, Purple Sea Urchin, sea urchin body,
  sea urchin shell`

## roboflow-100/underwater-objects-5v7p8 (RF100)

- Page LICENSE block: `CC BY 4.0`
- Images: 7,600 · 5 classes: `starfish, echinus, holothurian, scallop,
  waterweeds` · yolov5 model attached (mAP@50 69.9%)
- Lineage confirmed on-page: "originally created by Yimin Chen",
  current project `workspace-txxpz/underwater-detection`; part of RF100
  (`github.com/roboflow-ai/roboflow-100-benchmark`) → URPC family.

## sakana/urchins-cjlib — GONE

- 2026-08-21, logged-out: "Project Not Found ... does not exist, has
  been deleted, or is not shared with you." This was NOAA yolo11n's
  named training set (~1.7k imgs, CC-BY per the research file and per
  the yolo11n README). Recheck once Nick has an account; otherwise move
  to the dead-ends list.

## Not yet captured

- Spectral Labs Marine (818 det) — search-results listing only; needs
  the concrete project URL pinned down before a license can be captured.
