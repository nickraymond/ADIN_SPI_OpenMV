#!/usr/bin/env python3
"""E3 gate: fine-tune RF-DETR ONE epoch on corpus_v2, score rung A.

Nick's explicit shape (TRACKER bite E3): the cheap gate — one epoch,
rung-A mAP50 printed next to YOLOX-S's e1 0.658 — and only then does he
decide whether a full 60–120-epoch run is worth it. RF-DETR is Mac-side
ONLY (teacher/labeler): attention/LayerNorm/transpose all CPU-fallback
on the boards (compile gate, 2026-08-22).

  ~/nereus_ml/venvs/rfdetr/bin/python ml/rfdetr_gate/run_gate.py \
      [--dataset ~/nereus_ml/datasets/rfdetr_corpus_v2] \
      [--out ~/nereus_ml/runs/rfdetr_gate] [--epochs 1] [--skip-train]

Rung-A protocol mirrored from ml/yolox_urchin/eval_rung_a.py: the
983-image Urchinbot official test split, COCOeval mAP50. Wall-clock per
epoch is printed — it is half of what Nick's go/no-go weighs.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "yolox_urchin"))
from eval_rung_a import DS, EVAL, load_gt          # noqa: E402


def rung_a_score(model, out_dir, thr=0.02):
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    from PIL import Image

    img_dir = DS / "images"
    metas = {}
    names = sorted(p.stem for p in (EVAL / "labels").glob("*.txt"))
    dets = []
    t0 = time.time()
    for i, stem in enumerate(names):
        path = img_dir / (stem + ".JPG")
        im = Image.open(path).convert("RGB")
        metas[stem + ".JPG"] = im.size
        pred = model.predict(im, threshold=thr)
        for (x1, y1, x2, y2), score in zip(pred.xyxy, pred.confidence):
            dets.append({"image_id": i, "category_id": 0,
                         "bbox": [float(x1), float(y1),
                                  float(x2 - x1), float(y2 - y1)],
                         "score": float(score)})
        if (i + 1) % 100 == 0:
            print(f"  [eval] {i + 1}/{len(names)} imgs, "
                  f"{len(dets)} dets", flush=True)
    print(f"  [eval] wall {time.time() - t0:.0f}s")
    gt = load_gt(metas)
    gt_path = out_dir / "rung_a_gt.json"
    dt_path = out_dir / "rung_a_dets.json"
    json.dump(gt, open(gt_path, "w"))
    json.dump(dets, open(dt_path, "w"))
    coco = COCO(str(gt_path))
    ev = COCOeval(coco, coco.loadRes(str(dt_path)), "bbox")
    ev.evaluate()
    ev.accumulate()
    ev.summarize()
    return float(ev.stats[1])          # AP@0.50


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", default=str(Path.home() /
                    "nereus_ml/datasets/rfdetr_corpus_v2"))
    ap.add_argument("--out", default=str(Path.home() /
                    "nereus_ml/runs/rfdetr_gate"))
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--skip-train", action="store_true",
                    help="score the checkpoint already in --out")
    ap.add_argument("--ckpt", default=None,
                    help="checkpoint to score with --skip-train "
                         "(default: <out>/checkpoint_best_ema.pth)")
    ap.add_argument("--no-resume", action="store_true",
                    help="staged-training default is AUTO-RESUME from "
                         "<out>/checkpoint.pth when it exists (Stop "
                         "costs only the partial epoch; Start "
                         "continues); this forces a fresh start")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    from rfdetr import RFDETRBase
    if args.skip_train:
        # score the EMA weights (the YOLOX ruler's convention). NOTE
        # measured 2026-08-26: staged runs update checkpoint_best_ema/
        # _regular each epoch but leave checkpoint_best_total STALE —
        # scoring that would silently re-score old weights.
        ck = args.ckpt or (out / "checkpoint_best_ema.pth")
        print(f"scoring {ck}", flush=True)
        model = RFDETRBase(pretrain_weights=str(ck))
    else:
        model = RFDETRBase()           # 300 queries (>= densest 130 ✓)
        kw = {}
        # resume from last.ckpt FIRST: it carries optimizer + LR-sched
        # state. checkpoint_best_regular is a lightweight fallback that
        # restarts the optimizer COLD (rfdetr warns; we missed it on
        # 2026-08-25 — the e6→e13 curve segment carries that confound)
        for name in ("last.ckpt", "checkpoint.pth",
                     "checkpoint_best_regular.pth"):
            ck = out / name
            if ck.exists() and not args.no_resume:
                kw["resume"] = str(ck)
                print(f"RESUMING from {ck}", flush=True)
                break
        t0 = time.time()
        model.train(dataset_dir=str(Path(args.dataset).expanduser()),
                    epochs=args.epochs, batch_size=args.batch,
                    grad_accum_steps=args.grad_accum,
                    output_dir=str(out), **kw)
        wall = time.time() - t0
        print(f"TRAIN WALL: {wall:.0f}s "
              f"({wall / max(1, args.epochs):.0f}s/epoch)", flush=True)

    ap50 = rung_a_score(model, out)
    # durable score history — the cockpit plot reads THIS, not stdout
    # (a manual scorecard's stdout never reaches the console log)
    ep_hint = 0
    mc = out / "metrics.csv"
    if mc.exists():
        try:
            ep_hint = int(float(
                [ln for ln in mc.read_text().splitlines()
                 if ln.strip()][-1].split(",")[0]))
        except (ValueError, IndexError):
            pass
    with open(out / "rung_a_scores.jsonl", "a") as fh:
        fh.write(json.dumps({"ts": time.time(), "epoch": ep_hint,
                             "map50": round(ap50, 4)}) + "\n")
    print(f"\nRF-DETR GATE: rung-A mAP50 = {ap50:.3f} (epoch ~{ep_hint}; "
          f"YOLOX-S labeler was 0.658 @ e1, 0.800 final)")


if __name__ == "__main__":
    main()
