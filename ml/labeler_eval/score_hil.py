#!/usr/bin/env python3
"""Score both labelers' HIL dets with ONE protocol -> the decision tables.

  ~/nereus_ml/venvs/gate/bin/python ml/labeler_eval/score_hil.py \
      [--out ~/nereus_ml/runs/labeler_hil_eval]

Reads gt.json + dets_yolox.json + dets_rfdetr.json (infer_hil.py).
Prints, per model: COCOeval headline (mAP50 / mAP50-95, maxDets=300 —
frames run up to 91 GT boxes, the COCO default 100 would clip), a
per-size mAP50 breakdown (GT sqrt-area bins), a conf-swept P/R table
(greedy IOU-0.50 matching, micro over all frames), a per-clip mAP50
split, and a crowded-vs-sparse split (frames >= 30 GT). Writes
summary.json next to the inputs.

GT-anchoring caveat (prints with the tables): this GT was corrected
from YOLOX-S auto-boxes — geometry anchors toward YOLOX-S.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hil_gt import SIZE_BINS, clip_of  # noqa: E402

CONF_SWEEP = [0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
CROWDED_MIN = 30      # GT boxes per frame at/above this = "crowded"
IOU_THR = 0.50


def cocoeval_map(gt, dets, img_ids=None, area_rng=None):
    """-> (mAP50, mAP50_95) with maxDets=300; empty det list -> zeros."""
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    import tempfile
    if not dets:
        return 0.0, 0.0
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        json.dump(gt, f)
        f.flush()
        with contextlib.redirect_stdout(io.StringIO()):
            coco = COCO(f.name)
            cdt = coco.loadRes([d for d in dets])
    ev = COCOeval(coco, cdt, "bbox")
    ev.params.maxDets = [300]
    if img_ids is not None:
        ev.params.imgIds = list(img_ids)
    if area_rng is not None:
        ev.params.areaRng = [[area_rng[0] ** 2, area_rng[1] ** 2]]
        ev.params.areaRngLbl = ["custom"]
    with contextlib.redirect_stdout(io.StringIO()):
        ev.evaluate()
        ev.accumulate()
    # stats via accumulate()'s precision tensor: [T,R,K,A,M]
    p = ev.eval["precision"]
    def ap(t_slice):
        v = p[t_slice, :, :, 0, 0]
        v = v[v > -1]
        return float(v.mean()) if v.size else 0.0
    i50 = int(np.argwhere(np.isclose(ev.params.iouThrs, 0.5))[0][0])
    return ap(slice(i50, i50 + 1)), ap(slice(None))


def greedy_pr(gt_by_img, dets_by_img, conf):
    """COCO-style greedy match at IOU_THR over dets with score>=conf.
    -> (precision, recall, tp, fp, n_gt)."""
    from pycocotools import mask as maskUtils
    tp = fp = 0
    n_gt = sum(len(g) for g in gt_by_img.values())
    for img_id, gts in gt_by_img.items():
        ds = [d for d in dets_by_img.get(img_id, []) if d["score"] >= conf]
        ds.sort(key=lambda d: -d["score"])
        if not ds:
            continue
        if gts:
            ious = maskUtils.iou([d["bbox"] for d in ds],
                                 [g for g in gts], [0] * len(gts))
        else:
            ious = np.zeros((len(ds), 0))
        taken = set()
        for di in range(len(ds)):
            best, bj = IOU_THR, -1
            for gj in range(len(gts)):
                if gj in taken:
                    continue
                if ious[di][gj] >= best:
                    best, bj = ious[di][gj], gj
            if bj >= 0:
                taken.add(bj)
                tp += 1
            else:
                fp += 1
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, n_gt)
    return prec, rec, tp, fp, n_gt


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(Path.home() / "nereus_ml" / "runs"
                                         / "labeler_hil_eval"))
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    gt = json.load(open(out / "gt.json"))
    n_img = len(gt["images"])
    n_box = len(gt["annotations"])

    gt_by_img = {}
    for a in gt["annotations"]:
        gt_by_img.setdefault(a["image_id"], []).append(a["bbox"])
    name_by_id = {im["id"]: im["file_name"] for im in gt["images"]}
    crowded = {i for i, g in gt_by_img.items() if len(g) >= CROWDED_MIN}
    sparse = set(name_by_id) - crowded
    clips = {}
    for i, name in name_by_id.items():
        clips.setdefault(clip_of(name), []).append(i)

    print(f"GT: {n_img} reviewed stills / {n_box} boxes; "
          f"crowded(>= {CROWDED_MIN} GT) = {len(crowded)} frames, "
          f"sparse = {len(sparse)}")
    print("CAVEAT: GT corrected FROM YOLOX-S auto-boxes — box geometry "
          "anchors toward YOLOX-S.\n")

    summary = {"n_img": n_img, "n_gt": n_box, "models": {}}
    for model in ("yolox", "rfdetr"):
        path = out / f"dets_{model}.json"
        if not path.exists():
            print(f"[{model}] {path} missing — skipped")
            continue
        payload = json.load(open(path))
        dets = payload["dets"]
        dets_by_img = {}
        for d in dets:
            dets_by_img.setdefault(d["image_id"], []).append(d)

        m50, m5095 = cocoeval_map(gt, dets)
        row = {"ckpt": payload["ckpt"], "wall_s": payload["wall_s"],
               "n_dets": len(dets), "map50": round(m50, 4),
               "map50_95": round(m5095, 4), "size": {}, "clip": {},
               "pr": [], "density": {}}
        print(f"== {model}  ({Path(payload['ckpt']).name}, "
              f"{len(dets)} raw dets, infer wall {payload['wall_s']}s)")
        print(f"  headline: mAP50 {m50:.3f}  mAP50-95 {m5095:.3f}")

        print(f"  {'size bin':>8} {'mAP50':>7} {'n GT':>6}")
        for label, lo, hi in SIZE_BINS:
            s50, _ = cocoeval_map(gt, dets, area_rng=(lo, hi))
            n_in = sum(1 for a in gt["annotations"]
                       if lo ** 2 <= a["area"] < hi ** 2)
            row["size"][label] = round(s50, 4)
            print(f"  {label:>8} {s50:>7.3f} {n_in:>6}")

        for label, ids in [("crowded", crowded), ("sparse", sparse)]:
            d50, _ = cocoeval_map(gt, dets, img_ids=ids)
            row["density"][label] = round(d50, 4)
        print(f"  density: crowded {row['density']['crowded']:.3f} "
              f"/ sparse {row['density']['sparse']:.3f}")

        for clip, ids in sorted(clips.items()):
            c50, _ = cocoeval_map(gt, dets, img_ids=ids)
            row["clip"][clip] = round(c50, 4)
        print("  per-clip mAP50: " + "  ".join(
            f"{c} {v:.3f}" for c, v in sorted(row["clip"].items())))

        print(f"  {'conf':>5} {'prec':>6} {'rec':>6} {'TP':>6} {'FP':>6}")
        for conf in CONF_SWEEP:
            p, r, tp, fp_, ng = greedy_pr(gt_by_img, dets_by_img, conf)
            row["pr"].append({"conf": conf, "prec": round(p, 4),
                              "rec": round(r, 4), "tp": tp, "fp": fp_})
            print(f"  {conf:>5.2f} {p:>6.3f} {r:>6.3f} {tp:>6} {fp_:>6}")
        print()
        summary["models"][model] = row

    json.dump(summary, open(out / "summary.json", "w"), indent=1)
    print(f"WROTE {out / 'summary.json'}")


if __name__ == "__main__":
    main()
