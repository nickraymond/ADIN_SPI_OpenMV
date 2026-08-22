# relabel.py -- offline LAB blob labeler for the S8 B2 two-ball captures.
#
# WHY: the on-board labels are per-board-threshold blob boxes, and the capture
# session measured the N6's pink labels collapsing in run 2 (1.0/frame vs the
# AE3's 4.0 on the same scene -- the known blue-cast problem). Frames were
# saved CLEAN (no overlay), so labels can be recomputed on the Mac with one
# consistent labeler and relaxed per-board thresholds, instead of costing
# another bench window.
#
# Output: labels.jsonl next to each index.jsonl, same box format
# ([class_idx, x, y, w, h, pixels], classes ["pink","purple"]), so downstream
# consumers can read either file interchangeably.
#
#   ~/nereus_ml/venvs/fomo/bin/python ml/fomo/relabel.py ~/nereus_ml/datasets/two_ball
#
# The LAB space matches OpenMV's convention (CIELAB, L 0..100, a/b signed),
# so thresholds are comparable with the recipe's tuned ones. Conversion here
# is sRGB D65 -> Lab via the standard matrix; OpenMV quantizes from RGB565,
# which costs a couple of LAB units of agreement -- the widened boxes absorb
# that.
import json
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

# Per-board boxes, seeded from the recipe's tuned thresholds and widened
# where the capture measured misses. (L_lo, L_hi, A_lo, A_hi, B_lo, B_hi).
# The N6 pink box is the collapsed one: its b range (-19..-2) missed pink at
# range; widened toward the AE3's (+6) and eased on saturation.
THRESHOLDS = {
    "AE3": {"pink": (25, 80, 12, 45, -14, 10), "purple": (12, 68, 1, 22, -45, -12)},
    # N6 purple a_lo stays at the recipe's tuned 9: Nick's blue-gray shirt
    # sits at a=3..5 with b=-23..-31 (measured, frame_000225) -- only the
    # a channel separates it from the balls. Relaxing a_lo to 5 labelled
    # his torso "purple".
    "N6": {"pink": (25, 85, 18, 55, -22, 6), "purple": (17, 68, 9, 42, -75, -20)},
}
CLASSES = ["pink", "purple"]
MIN_PIXELS = {"AE3": 50, "N6": 60}   # below the boards' floors: no sensor noise
                                     # here, only JPEG grain, and small = far


def srgb_to_lab(rgb):
    """(H,W,3) uint8 sRGB -> (H,W,3) float Lab, L 0..100, a/b signed."""
    x = rgb.astype(np.float64) / 255.0
    x = np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = x @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    lab = np.empty_like(xyz)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


# Balls at VGA/1-2 m are compact blobs ~15-45 px across. Nick mid-scatter is
# in many frames, and his blue-gray shirt/jeans threshold as "purple" -- seen
# directly on the diagnostic overlay. Torso regions are big or elongated;
# these bounds keep balls and drop bodies.
MAX_PIXELS = 1400
MIN_SIDE, MAX_SIDE = 9, 52
ASPECT = (0.45, 2.2)


def label_frame(lab, thresholds, min_pixels):
    """-> [[class_idx, x, y, w, h, pixels], ...] via connected components."""
    boxes = []
    for ci, cname in enumerate(CLASSES):
        lo_hi = thresholds[cname]
        m = ((lab[..., 0] >= lo_hi[0]) & (lab[..., 0] <= lo_hi[1]) &
             (lab[..., 1] >= lo_hi[2]) & (lab[..., 1] <= lo_hi[3]) &
             (lab[..., 2] >= lo_hi[4]) & (lab[..., 2] <= lo_hi[5]))
        # Close 2 px gaps: specular highlights split one ball into fragments
        # (measured: N6 purple median 209 px vs pink 317 on the same scene).
        m = ndimage.binary_closing(m, structure=np.ones((5, 5), bool))
        lbl, n = ndimage.label(m)
        if not n:
            continue
        for sl_i, sl in enumerate(ndimage.find_objects(lbl)):
            px = int((lbl[sl] == sl_i + 1).sum())
            if px < min_pixels or px > MAX_PIXELS:
                continue
            y0, y1 = sl[0].start, sl[0].stop
            x0, x1 = sl[1].start, sl[1].stop
            w, h = int(x1 - x0), int(y1 - y0)
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
                continue
            if not (ASPECT[0] <= w / h <= ASPECT[1]):
                continue
            boxes.append([ci, int(x0), int(y0), w, h, px])
    return resolve_overlaps(boxes)


def resolve_overlaps(boxes):
    """One class per ball: when boxes of DIFFERENT classes overlap (IoU>0.4),
    keep the one with more matched pixels. Two same-class centroids on one
    ball would be fine; two different-class ones are conflicting supervision.
    """
    keep = [True] * len(boxes)
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a[0] == b[0] or not (keep[i] and keep[j]):
                continue
            ix = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            iy = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
            if ix <= 0 or iy <= 0:
                continue
            inter = ix * iy
            union = a[3] * a[4] + b[3] * b[4] - inter
            if inter / union > 0.4:
                keep[i if a[5] < b[5] else j] = False
    return [b for k, b in zip(keep, boxes) if k]


def has_reviewed(path):
    """True if any record in an existing labels.jsonl is hand-reviewed."""
    try:
        with open(path) as fh:
            return any(json.loads(ln).get("reviewed") for ln in fh
                       if ln.strip())
    except OSError:
        return False


def main(root, force=False):
    for run in sorted(os.listdir(root)):
        rdir = os.path.join(root, run)
        if not os.path.isdir(rdir):
            continue
        for board in sorted(os.listdir(rdir)):
            bdir = os.path.join(rdir, board)
            idx = os.path.join(bdir, "index.jsonl")
            if not os.path.isfile(idx):
                continue
            # B3 guard: a re-run must NEVER silently flatten hand
            # corrections back to auto-labels. The label GUI stamps
            # "reviewed": true on every frame it saves.
            if not force and has_reviewed(os.path.join(bdir, "labels.jsonl")):
                print("%s/%s: SKIPPED -- labels.jsonl contains hand-reviewed "
                      "frames (label_gui). Re-run with --force to discard "
                      "the corrections." % (run, board))
                continue
            thr = THRESHOLDS[board]
            minpx = MIN_PIXELS[board]
            out_path = os.path.join(bdir, "labels.jsonl")
            n_frames = 0
            per_class = np.zeros(len(CLASSES), dtype=np.int64)
            with open(out_path, "w") as out:
                for line in open(idx):
                    rec = json.loads(line)
                    img = Image.open(os.path.join(bdir, rec["file"])).convert("RGB")
                    lab = srgb_to_lab(np.asarray(img))
                    boxes = label_frame(lab, thr, minpx)
                    for b in boxes:
                        per_class[b[0]] += 1
                    out.write(json.dumps({
                        "file": rec["file"], "w": rec["w"], "h": rec["h"],
                        "classes": CLASSES, "boxes": boxes}) + "\n")
                    n_frames += 1
            avg = per_class / max(n_frames, 1)
            print("%s/%s: frames=%d avg pink=%.1f purple=%.1f -> %s"
                  % (run, board, n_frames, avg[0], avg[1], out_path))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    main(args[0] if args else
         os.path.expanduser("~/nereus_ml/datasets/two_ball"),
         force="--force" in sys.argv[1:])
