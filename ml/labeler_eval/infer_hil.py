#!/usr/bin/env python3
"""Run ONE labeler over the reviewed HIL stills -> COCO det json.

Each model runs its own labeling-protocol inference (the protocol it
would actually auto-box with); the SCORING protocol (score_hil.py,
COCOeval) is identical for both. Venvs differ — run each mode in its
own venv:

  ~/nereus_ml/venvs/gate/bin/python ml/labeler_eval/infer_hil.py \
      --model yolox --ckpt ~/nereus_ml/runs/stage1_yolox/stage1_s_labeler/last.pt
  ~/nereus_ml/venvs/rfdetr/bin/python ml/labeler_eval/infer_hil.py \
      --model rfdetr --ckpt ~/nereus_ml/runs/rfdetr_gate/checkpoint_best_ema.pth

YOLOX protocol = hil_stills.py prelabel exactly (640 top-left 114-gray
letterbox, decode_raw, NMS 0.65 at conf 0.001 — the eval-grade floor;
the P/R sweep re-thresholds downstream). RF-DETR protocol =
model.predict(PIL, threshold=0.001) as in run_gate.py rung-A scoring.
"""
import argparse
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "yolox_urchin"))
from hil_gt import load_reviewed, coco_gt  # noqa: E402

OUT = Path.home() / "nereus_ml" / "runs" / "labeler_hil_eval"


def infer_yolox(rows, ckpt, conf=0.001, nms_iou=0.65, batch=8, imgsz=640):
    import cv2
    import numpy as np
    import torch
    from torchvision.ops import nms as tv_nms
    from model import build_model, RawExport, decode_raw

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(num_classes=1, arch="yolox-s", stem="focus")
    ck = torch.load(ckpt, map_location="cpu")
    model.load_state_dict(ck.get("model", ck))
    raw = RawExport(model).to(device).eval()

    dets = []
    with torch.no_grad():
        for start in range(0, len(rows), batch):
            chunk = rows[start:start + batch]
            imgs, metas = [], []
            for i, (name, path, w0, h0, _gt) in enumerate(chunk):
                img = cv2.imread(path)
                if img is None:
                    raise SystemExit(f"FAIL: cannot read {path}")
                s = imgsz / max(img.shape[:2])
                rs = cv2.resize(img, (round(img.shape[1] * s),
                                      round(img.shape[0] * s)),
                                interpolation=cv2.INTER_AREA)
                canvas = np.full((imgsz, imgsz, 3), 114, np.uint8)
                canvas[:rs.shape[0], :rs.shape[1]] = rs
                imgs.append(canvas[:, :, ::-1])
                metas.append((start + i, s))
            x = torch.from_numpy(np.ascontiguousarray(
                np.stack(imgs))).permute(0, 3, 1, 2).float().to(device)
            pred = decode_raw([o.float() for o in raw(x)]).cpu()
            for bi, (img_id, s) in enumerate(metas):
                p = pred[bi]
                score = p[:, 4] * p[:, 5]
                keep = score > conf
                p, score = p[keep], score[keep]
                if not len(p):
                    continue
                xyxy = torch.stack(
                    [p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2,
                     p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
                ki = tv_nms(xyxy, score, nms_iou)
                for (x1, y1, x2, y2), sc in zip((xyxy[ki] / s).tolist(),
                                                score[ki].tolist()):
                    dets.append({"image_id": img_id, "category_id": 0,
                                 "bbox": [x1, y1, x2 - x1, y2 - y1],
                                 "score": sc})
            print(f"  {min(start + batch, len(rows))}/{len(rows)} stills, "
                  f"{len(dets)} raw dets", flush=True)
    return dets


def infer_rfdetr(rows, ckpt, thr=0.001):
    from PIL import Image
    from rfdetr import RFDETRBase

    model = RFDETRBase(pretrain_weights=str(ckpt))
    dets = []
    for i, (name, path, w0, h0, _gt) in enumerate(rows):
        im = Image.open(path).convert("RGB")
        pred = model.predict(im, threshold=thr)
        for (x1, y1, x2, y2), score in zip(pred.xyxy, pred.confidence):
            dets.append({"image_id": i, "category_id": 0,
                         "bbox": [float(x1), float(y1),
                                  float(x2 - x1), float(y2 - y1)],
                         "score": float(score)})
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(rows)} stills, {len(dets)} raw dets",
                  flush=True)
    return dets


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True, choices=("yolox", "rfdetr"))
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    rows = load_reviewed()
    print(f"GT: {len(rows)} reviewed stills, "
          f"{sum(len(r[4]) for r in rows)} boxes")
    gt_path = out / "gt.json"
    json.dump(coco_gt(rows), open(gt_path, "w"))

    ckpt = Path(args.ckpt).expanduser()
    if not ckpt.exists():
        raise SystemExit(f"FAIL: checkpoint {ckpt} missing")
    t0 = time.time()
    dets = (infer_yolox if args.model == "yolox" else infer_rfdetr)(
        rows, ckpt)
    wall = time.time() - t0
    dt_path = out / f"dets_{args.model}.json"
    json.dump({"model": args.model, "ckpt": str(ckpt),
               "wall_s": round(wall, 1), "dets": dets},
              open(dt_path, "w"))
    if not dets:
        raise SystemExit("FAIL: zero detections — nothing written is "
                         "scoreable")
    print(f"WROTE {dt_path} — {len(dets)} dets, wall {wall:.0f}s")


if __name__ == "__main__":
    main()
