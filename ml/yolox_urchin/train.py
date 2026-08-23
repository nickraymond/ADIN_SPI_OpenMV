#!/usr/bin/env python3
"""Stage-1 trainer: YOLOX-Nano (conv stem) on corpus_v1 (S8 bite E).

Reuses YOLOX's own loss (model(imgs, targets) in train mode = SimOTA);
loop/schedule kept boring: SGD+momentum, 3-epoch linear warmup, cosine
decay, EMA. Every run records config + git sha + corpus manifest sha
under ~/nereus_ml/runs/stage1_yolox/<run>/ with loss log + checkpoints.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train.py \
      --epochs 40 --batch 32 [--smoke N] [--resume ckpt.pt]
"""
import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model                       # noqa: E402
from data import CorpusDataset, load_jsonl          # noqa: E402

CORPUS = Path.home() / "nereus_ml" / "datasets" / "corpus_v1"
RUNS = Path.home() / "nereus_ml" / "runs" / "stage1_yolox"


class EMA:
    """Ramped decay (YOLOX/ultralytics style): early updates copy the model
    nearly directly, so the shadow never carries random-init pollution --
    the stage1_v1 lesson (fixed decay scored 0.478 vs last.pt's 0.573)."""

    def __init__(self, model, decay=0.9998, tau=2000):
        self.model = model
        self.decay = decay
        self.tau = tau
        self.updates = 0
        self.shadow = {k: v.detach().clone().float()
                       for k, v in model.state_dict().items()}

    @torch.no_grad()
    def update(self):
        self.updates += 1
        d = self.decay * (1 - math.exp(-self.updates / self.tau))
        for k, v in self.model.state_dict().items():
            s = self.shadow[k]
            if v.dtype.is_floating_point:
                s.mul_(d).add_(v.detach().float(), alpha=1 - d)
            else:
                s.copy_(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--canvas", type=int, default=256)
    ap.add_argument("--lr", type=float, default=None,
                    help="peak lr; default = 0.01/64*batch (YOLOX rule)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--smoke", type=int, default=0,
                    help="run N iterations then exit (loop proof + it/s)")
    ap.add_argument("--resume", default=None)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--arch", default="yolox-nano",
                    help="yolox exp name (yolox-nano | yolox-tiny)")
    ap.add_argument("--mosaic", type=float, default=0.0,
                    help="mosaic probability (0 disables)")
    ap.add_argument("--no-aug-epochs", type=int, default=10,
                    help="final epochs with mosaic off (YOLOX recipe)")
    ap.add_argument("--stop-after-hours", type=float, default=0,
                    help="checkpoint and exit cleanly at the first epoch "
                         "boundary past this wall time; resume with "
                         "--resume <run>/last.pt --run-name <same>")
    args = ap.parse_args()

    device = ("mps" if torch.backends.mps.is_available() else "cpu")
    lr = args.lr if args.lr else 0.01 / 64 * args.batch

    run = args.run_name or time.strftime("run_%Y%m%d_%H%M%S")
    rundir = RUNS / run
    rundir.mkdir(parents=True, exist_ok=True)

    manifest = (CORPUS / "manifest.json").read_bytes()
    try:
        git_sha = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent),
             "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        git_sha = "unknown"
    (rundir / "config.json").write_text(json.dumps({
        **vars(args), "lr_effective": lr, "device": device,
        "repo_git_sha": git_sha,
        "corpus_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "arch": "yolox-nano conv-stem 1-class (compile-gate 2026-08-22)",
    }, indent=2))

    ds = CorpusDataset(CORPUS / "train.jsonl", canvas=args.canvas, train=True,
                       mosaic_prob=args.mosaic)
    # persistent_workers=False on purpose: workers re-fork each epoch, so
    # the mosaic-off toggle for the no-aug tail actually reaches them.
    dl = DataLoader(ds, batch_size=args.batch, shuffle=True,
                    num_workers=args.workers, drop_last=True,
                    persistent_workers=False, pin_memory=False)

    model = build_model(num_classes=1, arch=args.arch).to(device).train()
    # BN defaults per YOLOX exp
    for m in model.modules():
        if isinstance(m, torch.nn.BatchNorm2d):
            m.eps, m.momentum = 1e-3, 0.03

    decay_p, other_p = [], []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        (decay_p if name.endswith("weight") and "bn" not in name
         else other_p).append(p)
    opt = torch.optim.SGD([{"params": decay_p, "weight_decay": 5e-4},
                           {"params": other_p, "weight_decay": 0.0}],
                          lr=lr, momentum=0.9, nesterov=True)

    start_epoch = 0
    ema = EMA(model)
    if args.resume:
        ck = torch.load(args.resume, map_location=device)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        ema.shadow = {k: v.to(device) for k, v in ck["ema"].items()}
        start_epoch = ck["epoch"] + 1
        print(f"resumed from {args.resume} at epoch {start_epoch}")

    iters_per_epoch = len(dl)
    warmup_iters = min(3 * iters_per_epoch, 1500)
    total_iters = args.epochs * iters_per_epoch

    def lr_at(it):
        if it < warmup_iters:
            return lr * it / warmup_iters
        t = (it - warmup_iters) / max(1, total_iters - warmup_iters)
        return lr * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * t)))

    log = open(rundir / "loss.log", "a")
    it_global = start_epoch * iters_per_epoch
    t_start = time.time()
    for epoch in range(start_epoch, args.epochs):
        if args.mosaic and epoch >= args.epochs - args.no_aug_epochs:
            if ds.mosaic_prob:
                print(f"epoch {epoch}: mosaic OFF (no-aug tail)")
            ds.mosaic_prob = 0.0
        for i, (imgs, targets) in enumerate(dl):
            for g in opt.param_groups:
                g["lr"] = lr_at(it_global)
            imgs, targets = imgs.to(device), targets.to(device)
            outputs = model(imgs, targets)
            loss = outputs["total_loss"]
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            ema.update()
            it_global += 1
            if it_global % 20 == 0 or args.smoke:
                dt = time.time() - t_start
                msg = (f"e{epoch} i{i}/{iters_per_epoch} it{it_global} "
                       f"lr={opt.param_groups[0]['lr']:.5f} "
                       f"loss={loss.item():.3f} "
                       f"iou={outputs['iou_loss'].item():.3f} "
                       f"obj={outputs['conf_loss'].item():.3f} "
                       f"cls={outputs['cls_loss'].item():.3f} "
                       f"{it_global - start_epoch * iters_per_epoch:d}it/"
                       f"{dt:.0f}s = {(it_global - start_epoch * iters_per_epoch) / dt:.2f} it/s")
                print(msg)
                log.write(msg + "\n")
                log.flush()
            if args.smoke and it_global - start_epoch * iters_per_epoch >= args.smoke:
                print(f"SMOKE DONE: {args.smoke} iters, "
                      f"{args.smoke / (time.time() - t_start):.2f} it/s, "
                      f"device={device}")
                return
        ck = {"model": model.state_dict(), "opt": opt.state_dict(),
              "ema": ema.shadow, "epoch": epoch}
        torch.save(ck, rundir / "last.pt")
        torch.save({"model": ema.shadow, "epoch": epoch},
                   rundir / "ema.pt")
        print(f"epoch {epoch} done, checkpointed -> {rundir}")
        if (args.stop_after_hours
                and time.time() - t_start > args.stop_after_hours * 3600):
            print(f"SESSION BOUNDARY: --stop-after-hours "
                  f"{args.stop_after_hours} reached at epoch {epoch}; "
                  f"resume with --resume {rundir}/last.pt "
                  f"--run-name {run}")
            return


if __name__ == "__main__":
    main()
