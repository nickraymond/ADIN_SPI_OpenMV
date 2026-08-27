"""Shared GT loader for the labeler-vs-labeler HIL eval (S8, E3 decision).

Ground truth = the reviewed=true rows of Nick's hand-labeled HIL stills
(stills_v1 + stills_v2 under ~/nereus_ml/datasets/hil_monterey — the same
186-frame set E10 merged and deployed to the bench). Box format in
labels.jsonl is the label-GUI convention: [ci, x_tl, y_tl, w, h, area_px]
in native 1920x1080 pixels (hil_stills.py prelabel writes it, the GUI
reviews it).

CAVEAT that rides every number scored against this GT: the boxes were
CORRECTED FROM YOLOX-S AUTO-BOXES (hil_stills.py prelabel), so box
geometry anchors toward YOLOX-S. RF-DETR must win by a clear margin
before a quantitative claim is made.
"""
import json
import math
from pathlib import Path

HIL = Path.home() / "nereus_ml" / "datasets" / "hil_monterey"
SETS = ("stills_v1", "stills_v2")

# sqrt(area) px bins at 1920x1080 — chosen from the measured GT
# distribution (p10 49 px / p50 76 / p90 124); "<48" is the small tail
SIZE_BINS = [("all", 0, 1e9), ("<48", 0, 48), ("48-64", 48, 64),
             ("64-96", 64, 96), (">=96", 96, 1e9)]


def load_reviewed():
    """-> sorted [(name, abs_path, w, h, [[x,y,w,h], ...]), ...],
    reviewed rows only, across both sets. Names are unique (clip stems
    differ per set); a duplicate name is a merge bug -> loud fail."""
    rows = {}
    for s in SETS:
        base = HIL / s
        for line in open(base / "labels.jsonl"):
            r = json.loads(line)
            if not r.get("reviewed"):
                continue
            name = Path(r["file"]).name
            if name in rows:
                raise SystemExit(f"FAIL: duplicate still name {name} "
                                 f"across {SETS} — set merge is broken")
            path = base / r["file"]
            if not path.exists():
                raise SystemExit(f"FAIL: {path} missing (labels.jsonl "
                                 f"references it)")
            rows[name] = (str(path), r["w"], r["h"],
                          [[b[1], b[2], b[3], b[4]] for b in r["boxes"]])
    return [(n, *rows[n]) for n in sorted(rows)]


def coco_gt(rows):
    images, anns = [], []
    aid = 0
    for i, (name, _path, w, h, boxes) in enumerate(rows):
        images.append({"id": i, "file_name": name, "width": w, "height": h})
        for (x, y, bw, bh) in boxes:
            anns.append({"id": aid, "image_id": i, "category_id": 0,
                         "bbox": [x, y, bw, bh], "area": bw * bh,
                         "iscrowd": 0})
            aid += 1
    return {"images": images, "annotations": anns,
            "categories": [{"id": 0, "name": "urchin"}]}


def clip_of(name):
    """monterey_01_f0015 -> monterey_01; img_8811_f0042 -> img_8811."""
    return name.rsplit("_f", 1)[0]


def sqrt_area(bbox):
    return math.sqrt(max(0.0, bbox[2] * bbox[3]))
