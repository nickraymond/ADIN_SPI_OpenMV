#!/usr/bin/env python3
"""Score a stage-1 YOLOX checkpoint on eval rung A (S8 bite E).

Rung A = Urchinbot's official 983-image test split, GT from the same
rung_a_eval labels the yolo11 baselines were scored on. Protocol: single
pass at --imgsz 640 (the ultralytics val() default the 0.243/0.351
baselines used; the model is fully convolutional), letterboxed top-left,
scored with pycocotools COCOeval. Deployment-mode tiled eval is bite C's
job, not this script's.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/eval_rung_a.py \
      ~/nereus_ml/runs/stage1_yolox/<run>/ema.pt [--imgsz 640]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model, RawExport, decode_raw  # noqa: E402

DS = Path.home() / "nereus_ml" / "datasets" / "urchinbot"
EVAL = DS / "rung_a_eval"


def load_gt(imgsz_meta):
    images, anns = [], []
    aid = 0
    for i, lab in enumerate(sorted((EVAL / "labels").glob("*.txt"))):
        name = lab.stem + ".JPG"
        w, h = imgsz_meta[name]
        images.append({"id": i, "file_name": name, "width": w, "height": h})
        for line in open(lab):
            _, cx, cy, bw, bh = map(float, line.split())
            anns.append({"id": aid, "image_id": i, "category_id": 0,
                         "bbox": [(cx - bw / 2) * w, (cy - bh / 2) * h,
                                  bw * w, bh * h],
                         "area": bw * w * bh * h, "iscrowd": 0})
            aid += 1
    return {"images": images, "annotations": anns,
            "categories": [{"id": 0, "name": "urchin"}]}


@torch.no_grad()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt")
    ap.add_argument("--arch", default="yolox-nano")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.001)
    ap.add_argument("--nms", type=float, default=0.65)
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(num_classes=1, arch=args.arch)
    ck = torch.load(args.ckpt, map_location="cpu")
    state = ck.get("model", ck)
    model.load_state_dict(state)
    raw = RawExport(model).to(device).eval()

    meta = {Path(r["file"]).name: (r["w"], r["h"])
            for r in map(json.loads, open(DS / "labels.jsonl"))}
    gt = load_gt(meta)
    id_by_name = {im["file_name"]: im["id"] for im in gt["images"]}

    from torchvision.ops import nms as tv_nms
    S = args.imgsz
    dets = []
    names = sorted(id_by_name)
    for start in range(0, len(names), args.batch):
        batch_names = names[start:start + args.batch]
        imgs, scales = [], []
        for name in batch_names:
            img = cv2.imread(str(EVAL / "images" / name))
            h0, w0 = img.shape[:2]
            s = S / max(h0, w0)
            img = cv2.resize(img, (round(w0 * s), round(h0 * s)),
                             interpolation=cv2.INTER_AREA)
            canvas = np.full((S, S, 3), 114, np.uint8)
            canvas[:img.shape[0], :img.shape[1]] = img
            imgs.append(canvas[:, :, ::-1])
            scales.append(s)
        x = torch.from_numpy(np.ascontiguousarray(
            np.stack(imgs))).permute(0, 3, 1, 2).float().to(device)
        outs = raw(x)
        pred = decode_raw([o.float() for o in outs]).cpu()  # (B,anchors,6)
        for bi, name in enumerate(batch_names):
            p = pred[bi]
            score = p[:, 4] * p[:, 5]
            keep = score > args.conf
            p, score = p[keep], score[keep]
            if not len(p):
                continue
            xyxy = torch.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2,
                                p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
            ki = tv_nms(xyxy, score, args.nms)
            xyxy, score = xyxy[ki] / scales[bi], score[ki]
            for (x1, y1, x2, y2), sc in zip(xyxy.tolist(), score.tolist()):
                dets.append({"image_id": id_by_name[name], "category_id": 0,
                             "bbox": [x1, y1, x2 - x1, y2 - y1],
                             "score": sc})
        done = min(start + args.batch, len(names))
        if done % 200 < args.batch:
            print(f"  {done}/{len(names)} imgs, {len(dets)} raw dets")

    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    import io, contextlib, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(gt, f)
        gt_path = f.name
    with contextlib.redirect_stdout(io.StringIO()):
        coco = COCO(gt_path)
        cdt = coco.loadRes(dets) if dets else None
    if cdt is None:
        print("ZERO detections — nothing to score"); return
    ev = COCOeval(coco, cdt, "bbox")
    with contextlib.redirect_stdout(io.StringIO()):
        ev.evaluate(); ev.accumulate()
    ev.summarize()
    print(f"\nRUNG A [{len(names)} imgs, imgsz={args.imgsz}]: "
          f"mAP50={ev.stats[1]:.3f} mAP50-95={ev.stats[0]:.3f} "
          f"(baselines ultralytics-protocol: yolo11n 0.243 / yolo11x 0.351; "
          f"ceiling 0.908)")


if __name__ == "__main__":
    main()
