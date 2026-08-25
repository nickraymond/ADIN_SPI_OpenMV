"""corpus_v1 dataset for stage-1 training (S8 bite E).

Augmentation policy comes straight from docs/urchin_corpus_plan.md:
downscale-augment big-target sources into the 24-64 px min-side band
(Urchinbot median 173 px); DUO/RF100 already live near the band. Canvas
is a fixed square (default 256). Two placement modes:
  - single: box-aware crop (aim at a random box with jitter; 15% fully
    random so backgrounds are still seen)
  - mosaic (stage1_v2+): four band-scaled images quilted on a 2Cx2C
    board around a jittered center, then one C-crop biased toward a box.
    Box SIZES are preserved (no post-downscale), so the 24-64 band holds.
Then hflip + mild HSV, and optionally Gaussian blur (bite E2: the AE3
capture-softness fix — tiny collapses ~4x under blur while nano is
blur-immune, runs/e2_anomaly_2026-08-25). Blur is label-preserving and
drawn LAST so enabling it cannot change box placement for a given seed.
Haze is deliberately NOT augmented: measured harmless to tiny (the E2
haze sweep hurt only nano). Boxes leaving the canvas or under 8 px ->
dropped.

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
    if scale <= 0.125:
        flag = cv2.IMREAD_REDUCED_COLOR_8
    elif scale <= 0.25:
        flag = cv2.IMREAD_REDUCED_COLOR_4
    elif scale <= 0.5:
        flag = cv2.IMREAD_REDUCED_COLOR_2
    img = cv2.imread(path, flag)
    if img is None:
        raise IOError(f"unreadable image: {path}")
    return img


class CorpusDataset(Dataset):
    def __init__(self, jsonl_path, canvas=256, train=True, seed=0,
                 mosaic_prob=0.0, blur_prob=0.0, blur_sigma=(0.3, 2.5)):
        self.recs = load_jsonl(jsonl_path)
        self.canvas = canvas
        self.train = train
        self.rng = random.Random(seed)
        self.mosaic_prob = mosaic_prob  # trainer zeroes this for the
        # final no-aug epochs (YOLOX recipe)
        self.blur_prob = blur_prob      # stays ON through the no-aug
        # tail (label-preserving, mild — hflip/HSV-class, not mosaic-class)
        self.blur_sigma = blur_sigma    # E2 sweep: tiny collapses by
        # sigma 1.2-1.6 at this canvas scale; the range brackets it
        self.blur_rng = random.Random(seed ^ 0xB1A5)  # own stream: blur
        # must never consume main-rng draws, or enabling it would shift
        # box placement on every LATER sample (pinned by test)

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

    def _load_scaled(self, rec):
        """-> (resized BGR img, [[x0,y0,w,h],...] at that scale)."""
        s = self._pick_scale(rec)
        img = _read_scaled(rec["file"], s)
        tw, th = max(1, round(rec["w"] * s)), max(1, round(rec["h"] * s))
        if img.shape[1::-1] != (tw, th):
            img = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        boxes = [[b[1] * s, b[2] * s, b[3] * s, b[4] * s]
                 for b in rec["boxes"]]
        return img, boxes

    @staticmethod
    def _clip_boxes(boxes, C):
        out = []
        for x0, y0, bw, bh in boxes:
            x1, y1 = max(0.0, x0), max(0.0, y0)
            x2, y2 = min(float(C), x0 + bw), min(float(C), y0 + bh)
            if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
                continue
            out.append([0, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1])
        return out

    def _single(self, rec):
        img, raw = self._load_scaled(rec)
        th, tw = img.shape[:2]
        C = self.canvas
        if self.train:
            if raw and self.rng.random() > 0.15:
                bx = self.rng.choice(raw)
                bcx, bcy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
                ox = int(round(bcx + self.rng.uniform(-C / 3, C / 3) - C / 2))
                oy = int(round(bcy + self.rng.uniform(-C / 3, C / 3) - C / 2))
            else:
                ox = self.rng.randint(min(0, C - tw), max(0, tw - C)) if tw != C else 0
                oy = self.rng.randint(min(0, C - th), max(0, th - C)) if th != C else 0
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
        boxes = self._clip_boxes(
            [[x0 - ox, y0 - oy, bw, bh] for x0, y0, bw, bh in raw], C)
        return canvas, boxes

    def _mosaic(self, idx):
        C = self.canvas
        big = np.full((2 * C, 2 * C, 3), 114, np.uint8)
        xc = int(self.rng.uniform(0.6 * C, 1.4 * C))
        yc = int(self.rng.uniform(0.6 * C, 1.4 * C))
        all_boxes = []
        idxs = [idx] + [self.rng.randrange(len(self.recs)) for _ in range(3)]
        for k, i in enumerate(idxs):
            img, raw = self._load_scaled(self.recs[i])
            h, w = img.shape[:2]
            if k == 0:      # top-left: image's BR corner at (xc, yc)
                x1, y1 = max(xc - w, 0), max(yc - h, 0)
                x2, y2 = xc, yc
                sx, sy = w - (x2 - x1), h - (y2 - y1)
            elif k == 1:    # top-right: BL corner at center
                x1, y1 = xc, max(yc - h, 0)
                x2, y2 = min(xc + w, 2 * C), yc
                sx, sy = 0, h - (y2 - y1)
            elif k == 2:    # bottom-left: TR corner at center
                x1, y1 = max(xc - w, 0), yc
                x2, y2 = xc, min(yc + h, 2 * C)
                sx, sy = w - (x2 - x1), 0
            else:           # bottom-right: TL corner at center
                x1, y1 = xc, yc
                x2, y2 = min(xc + w, 2 * C), min(yc + h, 2 * C)
                sx, sy = 0, 0
            if x2 <= x1 or y2 <= y1:
                continue
            big[y1:y2, x1:x2] = img[sy:sy + (y2 - y1), sx:sx + (x2 - x1)]
            dx, dy = x1 - sx, y1 - sy
            all_boxes += [[bx + dx, by + dy, bw, bh]
                          for bx, by, bw, bh in raw]
        # final C-crop, biased toward a surviving box when there is one
        vis = [b for b in all_boxes
               if b[0] + b[2] > 0 and b[1] + b[3] > 0
               and b[0] < 2 * C and b[1] < 2 * C]
        if vis and self.rng.random() > 0.15:
            bx = self.rng.choice(vis)
            cx = bx[0] + bx[2] / 2 + self.rng.uniform(-C / 3, C / 3)
            cy = bx[1] + bx[3] / 2 + self.rng.uniform(-C / 3, C / 3)
        else:
            cx, cy = xc, yc
        ox = int(round(min(max(cx - C / 2, 0), C)))
        oy = int(round(min(max(cy - C / 2, 0), C)))
        canvas = big[oy:oy + C, ox:ox + C]
        boxes = self._clip_boxes(
            [[x0 - ox, y0 - oy, bw, bh] for x0, y0, bw, bh in all_boxes], C)
        return canvas, boxes

    def __getitem__(self, idx):
        if self.train and self.rng.random() < self.mosaic_prob:
            canvas, boxes = self._mosaic(idx)
        else:
            canvas, boxes = self._single(self.recs[idx])
        C = self.canvas
        if self.train and self.rng.random() < 0.5:
            canvas = canvas[:, ::-1]
            for b in boxes:
                b[1] = C - b[1]
        if self.train:
            canvas = self._hsv(np.ascontiguousarray(canvas))
        # blur rides its OWN rng stream (see __init__) — so blur on/off
        # cannot change the boxes a given seed produces (pinned by test)
        if self.train and self.blur_prob and \
                self.blur_rng.random() < self.blur_prob:
            sigma = self.blur_rng.uniform(*self.blur_sigma)
            if sigma > 0.05:
                canvas = cv2.GaussianBlur(
                    np.ascontiguousarray(canvas), (0, 0), sigma)
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
