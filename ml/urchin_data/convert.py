"""S26 -> S8 bite E: per-source converters to the B2/B3 labels.jsonl format.

Format (verified against ml/fomo/relabel.py on main, 2026-08-21):
  one JSON object per line:
    {"file": <path relative to the jsonl's dir>, "w": int, "h": int,
     "classes": [...], "boxes": [[class_idx, x0, y0, w, h, pixels], ...]}
  boxes are ABSOLUTE integer pixels, top-left origin, exactly 6 fields.
  `pixels` here = box area (w*h): sources ship boxes not masks, and the
  trainer never reads b[5] (checked train.py) — it is informational.

Convention (agreed with the S8 session, 2026-08-21):
  - backbone sources (urchinbot, duo, rf100): classes=["urchin"], all
    urchin-species boxes -> ci=0; non-urchin classes dropped (counted).
  - species sources (rb74): classes=["purple_urchin","red_urchin"];
    black/white urchin classes dropped (counted).
  - converters are the species source of truth; re-run with a different
    class map for any two-class variant. Never patch labels.jsonl.

Each converter ends with a self-audit line: per-class box counts +
min-side p10/median + %<24px, to be checked against
ml/urchin_data/manifests/.

Usage: python convert.py {urchinbot|duo|rf100|rb74|all}
"""
import ast
import csv
import glob
import json
import os
import sys

DS = os.path.expanduser("~/nereus_ml/datasets")


def write_jsonl(path, recs):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {path}: {len(recs)} records")


def audit(name, recs, dropped=None):
    per = {}
    sides = []
    for r in recs:
        for b in r["boxes"]:
            per[r["classes"][b[0]]] = per.get(r["classes"][b[0]], 0) + 1
            sides.append(min(b[3], b[4]))
    sides.sort()
    n = len(sides)
    med = sides[n // 2] if n else 0
    p10 = sides[n // 10] if n else 0
    lt24 = 100 * sum(1 for s in sides if s < 24) / n if n else 0
    print(f"AUDIT {name}: boxes={per} total={n} min-side px p10={p10} median={med} <24px={lt24:.1f}%"
          + (f" dropped={dropped}" if dropped else ""))


def conv_urchinbot():
    """CSV (richer than the YOLO txts) -> single-class. Extra CSV columns
    (species, conf, depth, lat/lon, campaign) stay in the manifest side,
    NOT in labels.jsonl (S8's ask)."""
    recs = []
    for r in csv.DictReader(open(f"{DS}/urchinbot/archives/Complete_urchin_dataset.csv")):
        W, H = int(r["width"]), int(r["height"])
        boxes = []
        for t in ast.literal_eval(r["boxes"] or "[]"):
            cx, cy, w, h = t[2] * W, t[3] * H, t[4] * W, t[5] * H
            bw, bh = int(round(w)), int(round(h))
            boxes.append([0, int(round(cx - w / 2)), int(round(cy - h / 2)), bw, bh, bw * bh])
        recs.append({"file": f"images/im{r['id']}.JPG", "w": W, "h": H,
                     "classes": ["urchin"], "boxes": boxes})
    write_jsonl(f"{DS}/urchinbot/labels.jsonl", recs)
    audit("urchinbot", recs)


def conv_duo():
    """COCO instances_{train,test}.json (NOT val — byte-copy of test; NOT
    the YOLO labels/ dir — its class ids are silently remapped). echinus
    only -> ci=0."""
    root = f"{DS}/duo/extracted/DUO/DUO"
    recs, dropped = [], 0
    for split in ("train", "test"):
        d = json.load(open(f"{root}/annotations/instances_{split}.json"))
        echinus = {c["id"] for c in d["categories"] if c["name"] == "echinus"}
        by_img = {}
        for a in d["annotations"]:
            if a["category_id"] in echinus:
                x, y, w, h = a["bbox"]
                bw, bh = int(round(w)), int(round(h))
                by_img.setdefault(a["image_id"], []).append(
                    [0, int(round(x)), int(round(y)), bw, bh, bw * bh])
            else:
                dropped += 1
        for i in d["images"]:
            recs.append({"file": f"extracted/DUO/DUO/images/{split}/{i['file_name']}",
                         "w": i["width"], "h": i["height"],
                         "classes": ["urchin"], "boxes": by_img.get(i["id"], [])})
    write_jsonl(f"{DS}/duo/labels.jsonl", recs)
    audit("duo", recs, {"non-echinus": dropped})


def _yolo_txt(root_rel, root_abs, keep, classes, out_path, name):
    """Shared yolov8-export converter. keep = {src_id: dst_id}."""
    from PIL import Image
    recs, dropped = [], 0
    for split in ("train", "valid", "test"):
        for lf in sorted(glob.glob(f"{root_abs}/{split}/labels/*.txt")):
            img_rel = f"{split}/images/" + os.path.basename(lf).replace(".txt", ".jpg")
            with Image.open(f"{root_abs}/{img_rel}") as im:
                W, H = im.size
            boxes = []
            for line in open(lf):
                p = line.split()
                ci = int(p[0])
                if ci not in keep:
                    dropped += 1
                    continue
                cx, cy, w, h = (float(v) for v in p[1:5])
                bw, bh = int(round(w * W)), int(round(h * H))
                boxes.append([keep[ci], int(round(cx * W - bw / 2)),
                              int(round(cy * H - bh / 2)), bw, bh, bw * bh])
            recs.append({"file": img_rel, "w": W, "h": H,
                         "classes": classes, "boxes": boxes})
    write_jsonl(out_path, recs)
    audit(name, recs, {"other-classes": dropped})


def conv_rf100():
    # data.yaml order: 0=echinus 1=holothurian 2=scallop 3=starfish 4=waterweeds
    root = f"{DS}/roboflow/rf100_underwater"
    _yolo_txt("rf100", root, {0: 0}, ["urchin"], f"{root}/labels.jsonl", "rf100")


def conv_rb74():
    # data.yaml order: 0=Black 1=Purple 2=Red 3=White -> purple/red only
    root = f"{DS}/roboflow/urchin_detector_74"
    _yolo_txt("rb74", root, {1: 0, 2: 1}, ["purple_urchin", "red_urchin"],
              f"{root}/labels.jsonl", "rb74")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"urchinbot": conv_urchinbot, "duo": conv_duo,
           "rf100": conv_rf100, "rb74": conv_rb74}
    for k, fn in fns.items():
        if which in (k, "all"):
            fn()
