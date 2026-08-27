#!/usr/bin/env python3
"""Side-by-side overlay gallery for the labeler bake-off — Nick's eyeball.

  ~/nereus_ml/venvs/gate/bin/python ml/labeler_eval/gallery_hil.py \
      [--conf 0.40] [--n 8]

Left panel = GT (green) + YOLOX-S (orange); right = GT (green) +
RF-DETR (cyan). One conf for both models (default 0.40 — measured
matched precision: YOLOX 0.919 / RF-DETR 0.907), so box-count
differences on screen are recall differences, not threshold artifacts.

Frame selection is sparse and decision-driven, not exhaustive:
the 2 densest frames, the 2 smallest-median-GT frames, the 2 frames
where the models' TP counts diverge most, and 2 from the clip with the
largest per-clip mAP50 gap. Composites land in <out>/gallery/.
"""
import argparse
import json
import statistics
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hil_gt import load_reviewed, clip_of, sqrt_area  # noqa: E402

GREEN, ORANGE, CYAN = (60, 220, 60), (0, 140, 255), (255, 220, 40)


def matches(gts, dets, conf, iou_thr=0.5):
    """greedy IOU matching (score desc) -> (tp_dets, fp_dets)."""
    from pycocotools import mask as maskUtils
    ds = sorted((d for d in dets if d["score"] >= conf),
                key=lambda d: -d["score"])
    if not ds:
        return [], []
    ious = (maskUtils.iou([d["bbox"] for d in ds], gts, [0] * len(gts))
            if gts else np.zeros((len(ds), 0)))
    taken, tp, fp = set(), [], []
    for di, d in enumerate(ds):
        best, bj = iou_thr, -1
        for gj in range(len(gts)):
            if gj not in taken and ious[di][gj] >= best:
                best, bj = ious[di][gj], gj
        (tp.append(d) if bj >= 0 else fp.append(d))
        if bj >= 0:
            taken.add(bj)
    return tp, fp


def draw(img, boxes, color, thick=2):
    for b in boxes:
        x, y, w, h = (int(round(v)) for v in b)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, thick)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(Path.home() / "nereus_ml" / "runs"
                                         / "labeler_hil_eval"))
    ap.add_argument("--conf", type=float, default=0.40)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--panel-w", type=int, default=1280)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    gal = out / "gallery"
    gal.mkdir(exist_ok=True)

    rows = load_reviewed()
    dets = {}
    for m in ("yolox", "rfdetr"):
        payload = json.load(open(out / f"dets_{m}.json"))
        by_img = {}
        for d in payload["dets"]:
            by_img.setdefault(d["image_id"], []).append(d)
        dets[m] = by_img

    # per-frame stats for selection
    stats = []
    for i, (name, path, w0, h0, gts) in enumerate(rows):
        my, _ = matches(gts, dets["yolox"].get(i, []), args.conf)
        mr, _ = matches(gts, dets["rfdetr"].get(i, []), args.conf)
        stats.append({
            "i": i, "name": name, "n_gt": len(gts),
            "med_px": statistics.median(sqrt_area(b) for b in gts)
            if gts else 0,
            "tp_gap": len(mr) - len(my)})

    picked, why = [], {}
    def take(cands, label, k=2):
        for s in cands:
            if len([p for p in picked]) >= args.n:
                return
            if s["i"] not in why:
                picked.append(s["i"])
                why[s["i"]] = label
                if sum(1 for i in picked if why[i] == label) >= k:
                    return
    take(sorted(stats, key=lambda s: -s["n_gt"]), "densest")
    take(sorted(stats, key=lambda s: s["med_px"]), "smallest-GT")
    take(sorted(stats, key=lambda s: -abs(s["tp_gap"])), "biggest-TP-gap")
    summary = json.load(open(out / "summary.json"))
    gaps = {c: summary["models"]["rfdetr"]["clip"][c]
            - summary["models"]["yolox"]["clip"][c]
            for c in summary["models"]["yolox"]["clip"]}
    hard_clip = max(gaps, key=lambda c: abs(gaps[c]))
    take(sorted((s for s in stats if clip_of(s["name"]) == hard_clip),
                key=lambda s: -s["n_gt"]), f"gap-clip {hard_clip}")

    print(f"gallery @ conf {args.conf}: {len(picked)} frames")
    for i in picked:
        name, path, w0, h0, gts = rows[i]
        img = cv2.imread(path)
        panels = []
        for m, color in (("yolox", ORANGE), ("rfdetr", CYAN)):
            p = img.copy()
            draw(p, gts, GREEN, 2)
            tp, fp = matches(gts, dets[m].get(i, []), args.conf)
            draw(p, [d["bbox"] for d in tp + fp], color, 2)
            label = (f"{m} conf>={args.conf:g}  TP {len(tp)}/{len(gts)}"
                     f"  FP {len(fp)}")
            cv2.putText(p, label, (12, 40), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (0, 0, 0), 5)
            cv2.putText(p, label, (12, 40), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (255, 255, 255), 2)
            s = args.panel_w / p.shape[1]
            panels.append(cv2.resize(p, (args.panel_w,
                                         round(p.shape[0] * s))))
        comp = np.hstack(panels)
        fn = gal / f"{Path(name).stem}.jpg"
        cv2.imwrite(str(fn), comp, [cv2.IMWRITE_JPEG_QUALITY, 82])
        print(f"  {fn.name}  [{why[i]}]  GT {rows[i][4] and len(rows[i][4])}"
              f"  gap {next(s['tp_gap'] for s in stats if s['i'] == i):+d}")


if __name__ == "__main__":
    main()
