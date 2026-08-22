"""corpus_v1 dataset for stage-1 training (S8 bite E).

Augmentation policy comes straight from docs/urchin_corpus_plan.md:
downscale-augment big-target sources into the 24-64 px min-side band
(Urchinbot median 173 px); DUO/RF100 already live near the band and get
mild jitter only. Canvas is a fixed square (default 256), random-crop
placement, hflip, mild HSV. Boxes leave the canvas or fall below 8 px
min-side -> dropped.

labels.jsonl convention: boxes [ci, x0, y0, w, h, pixels] absolute px.
Targets for YOLOX get_losses: (max_boxes, 5) [cls, cx, cy, w, h] absolute
canvas px, zero-padded.
"""
import json
import random

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

BAND = (24, 64)          # deployment px band, plan stage 1
MIN_BOX = 8              # px min-side below which a box is dropped
MAX_BOXES = 120


def load_jsonl(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def _read_scaled(path, scale):
    """JPEG-DCT-domain fast path for the big Urchinbot frames."""
    flag = cv2.IMREAD_COLOR
    pre = 1
    if scale <= 0.125:
        flag, pre = cv2.IMREAD_REDUCED_COLOR_8, 8
    elif scale <= 0.25:
        flag, pre = cv2.IMREAD_REDUCED_COLOR_4, 4
    elif scale <= 0.5:
        flag, pre = cv2.IMREAD_REDUCED_COLOR_2, 2
    img = cv2.imread(path, flag)
    if img is None:
        raise IOError(f"unreadable image: {path}")
    return img


class CorpusDataset(Dataset):
    def __init__(self, jsonl_path, canvas=256, train=True, seed=0):
        self.recs = load_jsonl(jsonl_path)
        self.canvas = canvas
        self.train = train
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.recs)

    def _pick_scale(self, rec):
        boxes = rec["boxes"]
        if not self.train or not boxes:
            return min(1.0, self.canvas / max(rec["w"], rec["h"]))
        min_sides = sorted(min(b[3], b[4]) for b in boxes)
        ref = min_sides[len(min_sides) // 2]  # median box min-side
        target = self.rng.uniform(*BAND)
        s = target / max(ref, 1)
        return max(0.03, min(s, 1.5))

    def __getitem__(self, idx):
        rec = self.recs[idx]
        s = self._pick_scale(rec)
        img = _read_scaled(rec["file"], s)
        h0, w0 = img.shape[:2]
        tw, th = max(1, round(rec["w"] * s)), max(1, round(rec["h"] * s))
        if (w0, h0) != (tw, th):
            img = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        C = self.canvas
        # canvas placement: box-aware crop in train (aim at a random box with
        # jitter; 15% stay fully random so backgrounds are still seen),
        # top-left letterbox eval
        if self.train:
            if rec["boxes"] and self.rng.random() > 0.15:
                b = self.rng.choice(rec["boxes"])
                bcx, bcy = (b[1] + b[3] / 2) * s, (b[2] + b[4] / 2) * s
                jx = self.rng.uniform(-C / 3, C / 3)
                jy = self.rng.uniform(-C / 3, C / 3)
                ox = int(round(bcx + jx - C / 2))
                oy = int(round(bcy + jy - C / 2))
            else:
                ox = self.rng.randint(min(0, C - tw), max(0, tw - C)) if tw != C else 0
                oy = self.rng.randint(min(0, C - th), max(0, th - C)) if th != C else 0
            # keep the crop window overlapping the image
            ox = max(-C // 2, min(ox, tw - C // 2))
            oy = max(-C // 2, min(oy, th - C // 2))
        else:
            ox = oy = 0
        canvas = np.full((C, C, 3), 114, np.uint8)
        sx0, sy0 = max(0, ox), max(0, oy)
        dx0, dy0 = max(0, -ox), max(0, -oy)
        cw, ch = min(tw - sx0, C - dx0), min(th - sy0, C - dy0)
        if cw > 0 and ch > 0:
            canvas[dy0:dy0 + ch, dx0:dx0 + cw] = img[sy0:sy0 + ch, sx0:sx0 + cw]

        boxes = []
        for ci, x0, y0, bw, bh, *_ in rec["boxes"]:
            x0, y0 = x0 * s - ox, y0 * s - oy
            bw, bh = bw * s, bh * s
            # clip to canvas
            x1, y1 = max(0.0, x0), max(0.0, y0)
            x2, y2 = min(float(C), x0 + bw), min(float(C), y0 + bh)
            if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
                continue
            boxes.append([0, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1])

        flip = self.train and self.rng.random() < 0.5
        if flip:
            canvas = canvas[:, ::-1]
            for b in boxes:
                b[1] = C - b[1]
        if self.train:
            canvas = self._hsv(canvas)

        img_t = torch.from_numpy(
            np.ascontiguousarray(canvas[:, :, ::-1])).permute(2, 0, 1).float()
        targets = torch.zeros((MAX_BOXES, 5), dtype=torch.float32)
        for i, b in enumerate(boxes[:MAX_BOXES]):
            targets[i] = torch.tensor(b, dtype=torch.float32)
        return img_t, targets

    def _hsv(self, img, hgain=0.010, sgain=0.4, vgain=0.3):
        r = np.array([self.rng.uniform(-1, 1) for _ in range(3)]) * \
            [hgain, sgain, vgain] + 1
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] * r[0]) % 180
        hsv[..., 1] = np.clip(hsv[..., 1] * r[1], 0, 255)
        hsv[..., 2] = np.clip(hsv[..., 2] * r[2], 0, 255)
        return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
