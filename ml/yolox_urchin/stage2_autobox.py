#!/usr/bin/env python3
"""Stage-2 auto-box: run the stage-1 detector over the GBIF clean set
(S8 bite E / corpus plan stage 2).

High-confidence boxes inherit the image's folder species (purple/red).
An underwater heuristic (red-channel attenuation: water kills R first)
gates out the ~70-75% out-of-water GBIF frames the S26 QA measured --
hands, dry specimens, museum shots. Everything is recorded, nothing is
deleted: rejected images keep their score in the jsonl for review.

Outputs under ~/nereus_ml/datasets/gbif_inat/autobox_<tag>/:
  labels.jsonl   GUI-compatible ([ci,x0,y0,w,h,pixels], classes purple/red)
  crops/<species>/<id>_<k>.jpg      classifier training crops (25% margin)
  montage_accept.jpg / montage_reject.jpg   eyeball check of the filter
  rung_b_candidates.json            150+150 crop paths for Nick's sitting
  stats.json                        thresholds, rates, model provenance

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/stage2_autobox.py \
      ~/nereus_ml/runs/stage1_yolox/stage1_v1/last.pt [--tag v1]
"""
import argparse
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model, RawExport, decode_raw  # noqa: E402

GBIF = Path.home() / "nereus_ml" / "datasets" / "gbif_inat"
CONF = 0.50          # high precision: boxes inherit species labels
NMS_IOU = 0.45
UW_THRESH = 0.18     # bg blue-green fraction; calibrated by eyeball montage
                     # 2026-08-22 (submerged >=0.18, dry/hands <=0.13;
                     # known miss: dark tidepools -- precision over recall)
CROP_MARGIN = 0.25
IMGSZ = 640


def uw_score(img_bgr):
    """Underwater-ness = fraction of BORDER pixels with a saturated
    blue-green hue. Whole-frame color fails here: GBIF close-ups are
    dominated by the (red/purple) animal itself, so the scene cue lives
    in the image periphery. Dead tests and larvae are handled by the
    detector-confidence gate, not this score."""
    small = cv2.resize(img_bgr, (128, 128), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    m = np.ones((128, 128), bool)
    m[20:108, 20:108] = False
    h, s, v = hsv[..., 0][m], hsv[..., 1][m], hsv[..., 2][m]
    return float(((h >= 35) & (h <= 110) & (s > 40) & (v > 30)).mean())


@torch.no_grad()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt")
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(num_classes=1)
    ck = torch.load(args.ckpt, map_location="cpu")
    model.load_state_dict(ck.get("model", ck))
    raw = RawExport(model).to(device).eval()

    out = GBIF / f"autobox_{args.tag}"
    (out / "crops" / "purple").mkdir(parents=True, exist_ok=True)
    (out / "crops" / "red").mkdir(parents=True, exist_ok=True)

    from torchvision.ops import nms as tv_nms
    files = [(sp, p) for sp in ("purple", "red")
             for p in sorted((GBIF / "images" / sp).glob("*.jpg"))]
    print(f"{len(files)} GBIF images ({sum(1 for s, _ in files if s=='purple')} "
          f"purple / {sum(1 for s, _ in files if s=='red')} red)")

    records, crop_index = [], {"purple": [], "red": []}
    montage = {"accept": [], "reject": []}
    stats = {"underwater": 0, "dry_rejected": 0, "no_det": 0, "boxes": 0}
    ci_by_species = {"purple": 0, "red": 1}

    for start in range(0, len(files), args.batch):
        chunk = files[start:start + args.batch]
        imgs, metas = [], []
        for sp, path in chunk:
            img = cv2.imread(str(path))
            if img is None:
                continue
            h0, w0 = img.shape[:2]
            s = IMGSZ / max(h0, w0)
            rs = cv2.resize(img, (round(w0 * s), round(h0 * s)),
                            interpolation=cv2.INTER_AREA)
            canvas = np.full((IMGSZ, IMGSZ, 3), 114, np.uint8)
            canvas[:rs.shape[0], :rs.shape[1]] = rs
            imgs.append(canvas[:, :, ::-1])
            metas.append((sp, path, img, s, uw_score(img)))
        if not imgs:
            continue
        x = torch.from_numpy(np.ascontiguousarray(
            np.stack(imgs))).permute(0, 3, 1, 2).float().to(device)
        pred = decode_raw([o.float() for o in raw(x)]).cpu()
        for bi, (sp, path, img, s, uw) in enumerate(metas):
            h0, w0 = img.shape[:2]
            p = pred[bi]
            score = p[:, 4] * p[:, 5]
            keep = score > CONF
            p, score = p[keep], score[keep]
            boxes = []
            if len(p):
                xyxy = torch.stack(
                    [p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2,
                     p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
                ki = tv_nms(xyxy, score, NMS_IOU)
                xyxy, score = xyxy[ki] / s, score[ki]
                for (x1, y1, x2, y2), sc in zip(xyxy.tolist(), score.tolist()):
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w0, x2), min(h0, y2)
                    if x2 - x1 < 16 or y2 - y1 < 16:
                        continue
                    boxes.append((x1, y1, x2, y2, sc))
            underwater = uw >= UW_THRESH
            accepted = underwater and bool(boxes)
            stats["underwater"] += underwater
            if not underwater:
                stats["dry_rejected"] += 1
            elif not boxes:
                stats["no_det"] += 1
            rec_boxes = []
            if accepted:
                ci = ci_by_species[sp]
                for k, (x1, y1, x2, y2, sc) in enumerate(boxes):
                    bw, bh = x2 - x1, y2 - y1
                    rec_boxes.append([ci, round(x1), round(y1),
                                      round(bw), round(bh),
                                      round(bw * bh)])
                    mx, my = bw * CROP_MARGIN, bh * CROP_MARGIN
                    cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
                    cx2, cy2 = min(w0, int(x2 + mx)), min(h0, int(y2 + my))
                    cp = out / "crops" / sp / f"{path.stem}_{k}.jpg"
                    cv2.imwrite(str(cp), img[cy1:cy2, cx1:cx2],
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    crop_index[sp].append(
                        {"crop": str(cp), "src": str(path),
                         "conf": round(sc, 3), "px_min_side": round(min(bw, bh))})
                stats["boxes"] += len(rec_boxes)
            records.append({
                "file": str(path.relative_to(GBIF)), "w": w0, "h": h0,
                "classes": ["purple", "red"], "boxes": rec_boxes,
                "species_folder": sp, "uw_score": round(uw, 3),
                "n_det": len(boxes),
                "conf": [round(b[4], 3) for b in boxes],
                "accepted": accepted})
            bucket = "accept" if accepted else "reject"
            if len(montage[bucket]) < 25 and random.random() < 0.3:
                th = cv2.resize(img, (160, 160))
                montage[bucket].append(th)
        done = min(start + args.batch, len(files))
        if done % 400 < args.batch:
            print(f"  {done}/{len(files)} | accepted so far: "
                  f"{sum(1 for r in records if r['accepted'])}")

    with open(out / "labels.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    for name, tiles in montage.items():
        if tiles:
            rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles) - 4, 5)]
            if rows:
                cv2.imwrite(str(out / f"montage_{name}.jpg"), np.vstack(rows))

    rng = random.Random(42)
    rung_b = {sp: rng.sample(crop_index[sp], min(150, len(crop_index[sp])))
              for sp in ("purple", "red")}
    (out / "rung_b_candidates.json").write_text(json.dumps(rung_b, indent=2))

    n_acc = sum(1 for r in records if r["accepted"])
    stats.update({
        "model": args.ckpt, "conf_thresh": CONF, "uw_thresh": UW_THRESH,
        "images": len(records), "accepted_images": n_acc,
        "crops": {sp: len(v) for sp, v in crop_index.items()},
        "rung_b": {sp: len(v) for sp, v in rung_b.items()}})
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
