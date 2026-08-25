#!/usr/bin/env python3
"""YOLOX raw-head decode + NMS, numpy only (S8 bite E, HIL host-side).

The deployed models emit RAW per-level heads (decode was deliberately cut
from the export so the NPUs see only conv ops — ml/compile_gate_report.md).
This module is the ONE decode the HIL uses everywhere off-board: the Pi
harness, the scorer, and the Mac verification tests all import it, so a
count can never disagree with the training-side math by construction drift.

Head layout per level (matches model.py RawExport): channels are
[tx, ty, tw, th, obj, cls...] with obj/cls sigmoids BAKED INTO THE EXPORT.
Decode (identical to model.py decode_raw):
    cx = (tx + grid_x) * stride        w = exp(tw) * stride
    cy = (ty + grid_y) * stride        h = exp(th) * stride
    score = obj * cls
Stride is inferred per level as input_size // H, so any (input size,
level set) works — 256 whole-frame and native-px tiles alike.

No torch on purpose: the Pi never needs it. The torch parity proof lives
in test_decode_np.py (Mac, gate venv).
"""
import numpy as np


def dequantize(arr, scale, zero_point):
    """int8/uint8 tensor + tflite quantization params -> float32."""
    return (arr.astype(np.float32) - float(zero_point)) * float(scale)


def _to_nchw(head):
    """Accept (1,H,W,C) NHWC (tflite) or (1,C,H,W) NCHW (torch); -> (C,H,W).
    C is the channel axis: the one of size 4+1+n_cls, never a power-of-two
    spatial size — disambiguated by which axis is smallest, which holds for
    every real level (C=6 vs H,W in {8..64})."""
    a = np.asarray(head, dtype=np.float32)
    if a.ndim != 4 or a.shape[0] != 1:
        raise ValueError(f"head must be (1,...,...,...), got {a.shape}")
    a = a[0]
    c_axis = int(np.argmin(a.shape))
    if c_axis == 0:                       # (C,H,W)
        return a
    if c_axis == 2:                       # (H,W,C)
        return a.transpose(2, 0, 1)
    raise ValueError(f"cannot locate channel axis in {a.shape}")


def decode_all(heads, input_size):
    """[(1,H,W,C) or (1,C,H,W), ...] -> (n_anchors, 4+1+n_cls) float32,
    rows [cx, cy, w, h, obj, cls...] in input-px coords, anchor order
    identical to model.py decode_raw given the same level order."""
    rows = []
    for head in heads:
        a = _to_nchw(head)
        c, h, w = a.shape
        stride = input_size / w
        if stride != input_size // w or input_size % w:
            raise ValueError(f"level {a.shape} does not divide input size "
                             f"{input_size}")
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                             np.arange(h, dtype=np.float32))
        out = np.empty((c, h, w), np.float32)
        out[0] = (a[0] + gx) * stride
        out[1] = (a[1] + gy) * stride
        out[2:4] = np.exp(a[2:4]) * stride
        out[4:] = a[4:]
        rows.append(out.reshape(c, h * w).T)
    return np.concatenate(rows, 0)


def nms(boxes_xyxy, scores, iou_thresh):
    """Greedy NMS; drops boxes with IoU > iou_thresh against a kept box
    (strict >, torchvision semantics). -> kept indices, descending score."""
    boxes = np.asarray(boxes_xyxy, np.float32)
    scores = np.asarray(scores, np.float32)
    order = np.argsort(-scores, kind="stable")
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    area = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        iw = np.maximum(0, np.minimum(x2[i], x2[rest])
                        - np.maximum(x1[i], x1[rest]))
        ih = np.maximum(0, np.minimum(y2[i], y2[rest])
                        - np.maximum(y1[i], y1[rest]))
        inter = iw * ih
        iou = inter / np.maximum(area[i] + area[rest] - inter, 1e-9)
        order = rest[iou <= iou_thresh]
    return keep


def detect(heads, input_size, conf=0.25, nms_iou=0.45):
    """Full pipeline: raw heads -> (n, 6) float32 detections
    [x1, y1, x2, y2, score, class_idx], score-descending, input-px coords."""
    p = decode_all(heads, input_size)
    n_cls = p.shape[1] - 5
    cls_scores = p[:, 4:5] * p[:, 5:]            # obj * per-class
    cls_idx = np.argmax(cls_scores, 1)
    score = cls_scores[np.arange(len(p)), cls_idx]
    m = score > conf
    if not m.any():
        return np.zeros((0, 6), np.float32)
    p, score, cls_idx = p[m], score[m], cls_idx[m]
    xyxy = np.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2,
                     p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
    keep = nms(xyxy, score, nms_iou)
    return np.concatenate([xyxy[keep], score[keep, None],
                           cls_idx[keep, None].astype(np.float32)],
                          1).astype(np.float32)


def cells_to_dets(cells, input_size, conf=0.25, nms_iou=0.45):
    """Sparse board-side cells -> (n,6) detections, same output contract as
    detect(). Cell rows are [level_H, y, x, tx, ty, tw, th, obj, cls] —
    what pi/hil/hil_board.py emits (obj-thresholded on-board; the wire
    replacement for full tensors after the N6 tobytes hard-fault,
    2026-08-25). Equivalence with detect() is unit-tested."""
    if not len(cells):
        return np.zeros((0, 6), np.float32)
    a = np.asarray(cells, np.float32)
    stride = input_size / a[:, 0]
    cx = (a[:, 3] + a[:, 2]) * stride
    cy = (a[:, 4] + a[:, 1]) * stride
    w = np.exp(a[:, 5]) * stride
    h = np.exp(a[:, 6]) * stride
    score = a[:, 7] * a[:, 8]
    m = score > conf
    if not m.any():
        return np.zeros((0, 6), np.float32)
    cx, cy, w, h, score = cx[m], cy[m], w[m], h[m], score[m]
    xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)
    keep = nms(xyxy, score, nms_iou)
    out = np.concatenate([xyxy[keep], score[keep, None],
                          np.zeros((len(keep), 1), np.float32)], 1)
    return out.astype(np.float32)


def merge_tiles(dets_per_tile, origins, nms_iou=0.45):
    """Tile detections -> one frame-coordinate set. dets_per_tile: list of
    (n,6) arrays from detect(); origins: matching [(x_off, y_off), ...].
    Shifts each tile's boxes by its origin, then one global NMS so an
    urchin straddling a tile seam is counted once."""
    shifted = []
    for dets, (ox, oy) in zip(dets_per_tile, origins):
        if len(dets) == 0:
            continue
        d = dets.copy()
        d[:, [0, 2]] += ox
        d[:, [1, 3]] += oy
        shifted.append(d)
    if not shifted:
        return np.zeros((0, 6), np.float32)
    alld = np.concatenate(shifted, 0)
    keep = nms(alld[:, :4], alld[:, 4], nms_iou)
    return alld[keep]
