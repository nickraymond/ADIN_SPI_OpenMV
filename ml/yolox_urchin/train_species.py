#!/usr/bin/env python3
"""Stage-2 species head: purple-vs-red crop classifier (S8 bite E).

Trains from scratch (no pretrained weights -- keeps the open-release
model license-clean) on Nick's reviewed GBIF crops. Split is by SOURCE
IMAGE so near-duplicate crops from one photo never straddle train/eval.
Rung B (Nick-approved 2026-08-22): ~150 purple + ~46 red crops held out,
scored ONCE at the end -- never used for tuning; early-stop uses a 10%
val split carved from the train groups.

Augmentation: flip/rotate/crop/brightness/contrast only -- NO hue jitter
(color IS the class; the detector's hue-aug rule inverts here). Red
imbalance (~12:1) handled by weighted sampling, per the corpus plan.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train_species.py
"""
import json
import random
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler

CROPS = (Path.home() / "nereus_ml" / "datasets" / "gbif_inat"
         / "autobox_v1" / "crops_reviewed")
RUNDIR = Path.home() / "nereus_ml" / "runs" / "species_v1"
SPECIES = ["purple", "red"]
RUNG_B_QUOTA = {"purple": 150, "red": 46}
SIZE = 96
EPOCHS = 40
SEED = 42


def group_key(path):
    # crop files are <src-stem>_<k>.jpg; group = source image
    return path.stem.rsplit("_", 1)[0]


def split_sets():
    rng = random.Random(SEED)
    split = {"train": [], "rung_b": []}
    for ci, sp in enumerate(SPECIES):
        groups = defaultdict(list)
        for p in sorted((CROPS / sp).glob("*.jpg")):
            groups[group_key(p)].append(p)
        keys = sorted(groups)
        rng.shuffle(keys)
        held, n_held = [], 0
        for k in keys:
            if n_held >= RUNG_B_QUOTA[sp]:
                break
            held.append(k)
            n_held += len(groups[k])
        for k in keys:
            bucket = "rung_b" if k in set(held) else "train"
            split[bucket] += [(p, ci) for p in groups[k]]
    return split


class CropDS(Dataset):
    def __init__(self, items, train):
        self.items = items
        self.train = train
        self.rng = random.Random(SEED)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, ci = self.items[i]
        img = cv2.imread(str(path))
        if self.train:
            # pad-crop jitter, flips, small rotation, brightness/contrast.
            # NO hue jitter: color is the label.
            s = self.rng.uniform(1.0, 1.25)
            img = cv2.resize(img, (int(SIZE * s), int(SIZE * s)))
            ox = self.rng.randint(0, img.shape[1] - SIZE)
            oy = self.rng.randint(0, img.shape[0] - SIZE)
            img = img[oy:oy + SIZE, ox:ox + SIZE]
            if self.rng.random() < 0.5:
                img = img[:, ::-1]
            k = self.rng.randint(0, 3)
            img = np.rot90(img, k)
            a = self.rng.uniform(0.8, 1.2)
            b = self.rng.uniform(-20, 20)
            img = np.clip(img.astype(np.float32) * a + b, 0, 255)
        else:
            img = cv2.resize(img, (SIZE, SIZE)).astype(np.float32)
        x = torch.from_numpy(np.ascontiguousarray(img[:, :, ::-1])
                             ).permute(2, 0, 1) / 255.0
        return x, ci


def make_model():
    def block(cin, cout, stride):
        return [nn.Conv2d(cin, cout, 3, stride, 1, bias=False),
                nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]
    return nn.Sequential(
        *block(3, 16, 2), *block(16, 32, 2), *block(32, 64, 2),
        *block(64, 96, 2), *block(96, 128, 2),
        nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(128, 2))


def evaluate(model, items, name):
    dl = DataLoader(CropDS(items, train=False), batch_size=64)
    model.eval()
    cm = np.zeros((2, 2), int)
    with torch.no_grad():
        for x, y in dl:
            pred = model(x).argmax(1)
            for t, p in zip(y.tolist(), pred.tolist()):
                cm[t][p] += 1
    accs = {SPECIES[i]: cm[i][i] / max(1, cm[i].sum()) for i in range(2)}
    total = cm.trace() / max(1, cm.sum())
    print(f"{name}: overall {total:.3f} | "
          + " ".join(f"{s} {a:.3f} (n={cm[i].sum()})"
                     for i, (s, a) in enumerate(accs.items()))
          + f" | confusion {cm.tolist()}")
    return total, accs, cm


def main():
    torch.manual_seed(SEED)
    RUNDIR.mkdir(parents=True, exist_ok=True)
    split = split_sets()
    (RUNDIR / "split.json").write_text(json.dumps(
        {k: [[str(p), c] for p, c in v] for k, v in split.items()},
        indent=2))  # the auditable record of what rung B actually is
    rng = random.Random(SEED + 1)
    train_groups = sorted({group_key(p) for p, _ in split["train"]})
    rng.shuffle(train_groups)
    val_groups = set(train_groups[:max(1, len(train_groups) // 10)])
    tr = [(p, c) for p, c in split["train"] if group_key(p) not in val_groups]
    va = [(p, c) for p, c in split["train"] if group_key(p) in val_groups]
    n = {s: sum(1 for _, c in tr if c == i) for i, s in enumerate(SPECIES)}
    print(f"train {len(tr)} ({n}) | val {len(va)} | "
          f"rung B {len(split['rung_b'])}")

    weights = [1.0 / max(1, n[SPECIES[c]]) for _, c in tr]
    sampler = WeightedRandomSampler(weights, num_samples=len(tr),
                                    replacement=True)
    dl = DataLoader(CropDS(tr, train=True), batch_size=64, sampler=sampler,
                    num_workers=4)
    model = make_model()
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, EPOCHS)
    lossf = nn.CrossEntropyLoss()

    best_va, best_state = 0.0, None
    t0 = time.time()
    for ep in range(EPOCHS):
        model.train()
        tot = 0.0
        for x, y in dl:
            loss = lossf(model(x), y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        va_acc, _, _ = evaluate(model, va, f"e{ep} val")
        print(f"e{ep} loss {tot / len(dl):.3f} ({time.time() - t0:.0f}s)")
        if va_acc >= best_va:
            best_va = va_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    torch.save({"model": best_state, "species": SPECIES, "size": SIZE},
               RUNDIR / "best.pt")

    print("\n=== RUNG B (held out, scored once) ===")
    total, accs, cm = evaluate(model, split["rung_b"], "rung B")
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent),
             "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sha = "unknown"
    (RUNDIR / "result.json").write_text(json.dumps({
        "rung_b_overall": total, "rung_b_per_class": accs,
        "confusion": cm.tolist(), "best_val": best_va,
        "train_counts": n, "quota": RUNG_B_QUOTA, "epochs": EPOCHS,
        "size": SIZE, "seed": SEED, "git_sha": sha,
        "from_scratch": True, "no_hue_aug": True}, indent=2))
    print("saved ->", RUNDIR)


if __name__ == "__main__":
    main()
