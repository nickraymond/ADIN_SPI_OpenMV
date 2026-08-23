#!/usr/bin/env python3
"""Harvest species-classifier crops from the REVIEWED GBIF labels.

Reads ~/nereus_ml/datasets/gbif_inat/labels.jsonl (the label-GUI working
copy: only frames Nick has reviewed count; excluded frames have zero
boxes and contribute nothing) and cuts 25%-margin crops per box into
autobox_<tag>/crops_reviewed/<species>/. Replaces the raw auto-crop
harvest as the stage-2 training source.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/harvest_crops.py [--tag v1]
"""
import argparse
import json
from pathlib import Path

import cv2

GBIF = Path.home() / "nereus_ml" / "datasets" / "gbif_inat"
MARGIN = 0.25
SPECIES = ["purple", "red"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1")
    args = ap.parse_args()
    out = GBIF / f"autobox_{args.tag}" / "crops_reviewed"
    counts = {}
    for sp in SPECIES:
        (out / sp).mkdir(parents=True, exist_ok=True)
        counts[sp] = 0
    skipped = 0
    recs = [json.loads(l) for l in open(GBIF / "labels.jsonl")]
    for r in recs:
        if not r.get("reviewed") or not r["boxes"]:
            continue
        img = cv2.imread(str(GBIF / r["file"]))
        if img is None:
            skipped += 1
            continue
        h0, w0 = img.shape[:2]
        stem = Path(r["file"]).stem
        for k, (ci, x0, y0, bw, bh, *_) in enumerate(r["boxes"]):
            sp = SPECIES[ci] if ci < len(SPECIES) else None
            if sp is None:
                skipped += 1
                continue
            mx, my = bw * MARGIN, bh * MARGIN
            x1, y1 = max(0, int(x0 - mx)), max(0, int(y0 - my))
            x2, y2 = min(w0, int(x0 + bw + mx)), min(h0, int(y0 + bh + my))
            if x2 - x1 < 12 or y2 - y1 < 12:
                skipped += 1
                continue
            cv2.imwrite(str(out / sp / f"{stem}_{k}.jpg"),
                        img[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 92])
            counts[sp] += 1
    print(f"crops_reviewed: {counts}, skipped {skipped} -> {out}")


if __name__ == "__main__":
    main()
