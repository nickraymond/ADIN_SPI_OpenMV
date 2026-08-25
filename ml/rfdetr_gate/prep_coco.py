#!/usr/bin/env python3
"""E3 gate step 1: corpus_v2 (repo jsonl) -> RF-DETR's COCO layout.

RF-DETR trains from a dataset dir of train/ and valid/ each holding
_annotations.coco.json plus the images. Images are SYMLINKED, never
copied — the corpus is ~45 GB and already on this disk.

  ~/nereus_ml/venvs/rfdetr/bin/python ml/rfdetr_gate/prep_coco.py \
      [--corpus ~/nereus_ml/datasets/corpus_v2] \
      [--out ~/nereus_ml/datasets/rfdetr_corpus_v2]

Box convention in the jsonl (ml/yolox_urchin/data.py): boxes rows are
[class_id, x, y, w, h, ...] in image pixels, top-left origin — already
COCO's xywh. Densest-frame check rides along: RF-DETR's 300 queries
must exceed the max GT boxes per image (TRACKER flagged ~130).
"""
import argparse
import json
import os
from pathlib import Path


def convert(split_jsonl, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    images, anns = [], []
    aid = 0
    max_boxes = 0
    for i, ln in enumerate(open(split_jsonl)):
        r = json.loads(ln)
        src = Path(r["file"])
        # unique flat name: source dirs collide on bare basenames
        name = f"{i:06d}_{src.name}"
        link = out_dir / name
        if not link.exists():
            os.symlink(src, link)
        images.append({"id": i, "file_name": name,
                       "width": int(r["w"]), "height": int(r["h"])})
        max_boxes = max(max_boxes, len(r["boxes"]))
        for b in r["boxes"]:
            _ci, x, y, w, h = b[:5]
            anns.append({"id": aid, "image_id": i, "category_id": 1,
                         "bbox": [float(x), float(y), float(w), float(h)],
                         "area": float(w) * float(h), "iscrowd": 0})
            aid += 1
    coco = {"images": images, "annotations": anns,
            "categories": [{"id": 1, "name": "urchin",
                            "supercategory": "none"}]}
    json.dump(coco, open(out_dir / "_annotations.coco.json", "w"))
    return len(images), aid, max_boxes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=str(Path.home() /
                    "nereus_ml/datasets/corpus_v2"))
    ap.add_argument("--out", default=str(Path.home() /
                    "nereus_ml/datasets/rfdetr_corpus_v2"))
    args = ap.parse_args()
    corpus, out = Path(args.corpus).expanduser(), Path(args.out).expanduser()
    overall_max = 0
    for split, sub in (("train.jsonl", "train"), ("val.jsonl", "valid")):
        n, a, mx = convert(corpus / split, out / sub)
        overall_max = max(overall_max, mx)
        print(f"{sub}: {n} images, {a} boxes, densest {mx}")
    # the query-capacity check TRACKER asked for, loud
    assert overall_max < 300, (
        f"densest frame has {overall_max} boxes >= RF-DETR's 300 queries "
        f"— raise num_queries or the gate under-detects by construction")
    print(f"densest frame {overall_max} < 300 queries — OK")


if __name__ == "__main__":
    main()
