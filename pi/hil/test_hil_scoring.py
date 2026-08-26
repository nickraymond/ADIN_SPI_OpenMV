"""Host tests for match_frame (S8 E6) — the ONE scoring function.

The live monitor counters and the rows.jsonl post-pass both call
match_frame, so its semantics are pinned here: visibility filter, edge
filter, greedy IOU match, and the pixel-floor IGNORE rules.

Needs numpy (the Pi has it; a bare Mac python may not — skipped there,
run on the bench before acceptance).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import numpy as np
    from hil_harness import match_frame, STILL_W, STILL_H
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

# H = diag(STILL_W, STILL_H, 1) with cam == still size makes the
# still->camera mapping the identity on pixel coordinates, so GT boxes
# land exactly where they were drawn and the geometry is checkable by
# hand.
CAM_W, CAM_H = 1920, 1080


def H_identity():
    return np.diag([float(STILL_W), float(STILL_H), 1.0])


def gt(x, y, w, h):
    """A labels.jsonl-shaped GT box: [class, x, y, w, h, px]."""
    return [0, x, y, w, h, 0]


def dets(*rows):
    """Detections [x1,y1,x2,y2,conf] in camera px."""
    return np.array(rows, np.float64) if rows else np.zeros((0, 5))


@unittest.skipUnless(HAVE_DEPS, "numpy not installed on this host")
class TestMatchFrame(unittest.TestCase):
    def test_perfect_match(self):
        mf = match_frame(dets([100, 100, 200, 200, 0.9]),
                         [gt(100, 100, 100, 100)],
                         H_identity(), CAM_W, CAM_H, min_gt_px=30)
        self.assertEqual(mf["n_match"], 1)
        self.assertEqual(mf["n_gt_floor"], 1)
        self.assertEqual(mf["n_match_floor"], 1)
        self.assertEqual(mf["n_false_floor"], 0)

    def test_miss_and_false(self):
        mf = match_frame(dets([800, 800, 900, 900, 0.8]),
                         [gt(100, 100, 100, 100)],
                         H_identity(), CAM_W, CAM_H, min_gt_px=30)
        self.assertEqual(mf["n_match"], 0)
        self.assertEqual(mf["n_gt_floor"], 1)      # the miss
        self.assertEqual(mf["n_match_floor"], 0)
        self.assertEqual(mf["n_false_floor"], 1)   # the stray det

    def test_floor_ignore_semantics(self):
        # A 20 px GT under the 30 px floor: not a miss, and the det
        # matched to it is NOT a false (COCO IGNORE, not delete).
        mf = match_frame(dets([100, 100, 120, 120, 0.9]),
                         [gt(100, 100, 20, 20)],
                         H_identity(), CAM_W, CAM_H, min_gt_px=30)
        self.assertEqual(mf["n_match"], 1)         # raw match exists
        self.assertEqual(mf["n_gt_floor"], 0)      # ignored, not a miss
        self.assertEqual(mf["n_match_floor"], 0)
        self.assertEqual(mf["n_false_floor"], 0)   # matched det not false

    def test_visibility_filter_drops_offscreen_gt(self):
        # GT hanging past the camera frame edge is not scoreable.
        mf = match_frame(dets(),
                         [gt(STILL_W - 50, 100, 100, 100),   # off right
                          gt(100, 100, 100, 100)],           # visible
                         H_identity(), CAM_W, CAM_H, min_gt_px=0)
        self.assertEqual(len(mf["boxes"]), 1)

    def test_edge_touching_det_dropped(self):
        mf = match_frame(dets([0, 0, 50, 50, 0.9]),          # touches edge
                         [gt(100, 100, 100, 100)],
                         H_identity(), CAM_W, CAM_H, min_gt_px=0)
        self.assertEqual(len(mf["dets"]), 0)

    def test_iou_thr_parameter(self):
        # det at IOU 0.25 vs the GT: matches at thr 0.20, not at 0.30
        # (E8 — the homography discriminator's knob)
        d = dets([160, 100, 260, 200, 0.9])
        g = [gt(100, 100, 100, 100)]
        strict = match_frame(d, g, H_identity(), CAM_W, CAM_H,
                             min_gt_px=30, iou_thr=0.30)
        loose = match_frame(d, g, H_identity(), CAM_W, CAM_H,
                            min_gt_px=30, iou_thr=0.20)
        self.assertEqual(strict["n_match"], 0)
        self.assertEqual(loose["n_match"], 1)

    def test_greedy_match_prefers_higher_conf(self):
        # Two dets over one GT: the higher-conf det takes the match,
        # the other becomes the false.
        mf = match_frame(dets([100, 100, 200, 200, 0.5],
                              [105, 105, 205, 205, 0.9]),
                         [gt(100, 100, 100, 100)],
                         H_identity(), CAM_W, CAM_H, min_gt_px=30)
        self.assertEqual(mf["n_match"], 1)
        self.assertEqual(mf["n_false_floor"], 1)
        self.assertIn(1, mf["pairs"])              # det idx 1 = conf 0.9


@unittest.skipUnless(HAVE_DEPS, "numpy not installed on this host")
class TestRescore(unittest.TestCase):
    """hil_rescore against a synthetic run dir with known counts."""

    def setUp(self):
        import shutil
        import tempfile
        from PIL import Image as PILImage
        self.dir = tempfile.mkdtemp(prefix="rescore_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        np.save(os.path.join(self.dir, "H_A.npy"), H_identity())
        PILImage.new("RGB", (CAM_W, CAM_H)).save(
            os.path.join(self.dir, "calib_A.jpg"))
        # labels: one still, one 100px GT box
        self.stills = os.path.join(self.dir, "stills")
        os.makedirs(self.stills)
        with open(os.path.join(self.stills, "labels.jsonl"), "w") as fh:
            fh.write(json.dumps(
                {"file": "s1.jpg",
                 "boxes": [[0, 100, 100, 100, 100, 0]]}) + "\n")
        # two rows: an exact hit and a 0.25-IOU near-miss
        with open(os.path.join(self.dir, "rows.jsonl"), "w") as fh:
            fh.write(json.dumps(
                {"board": "A", "phase": "t", "still": "s1.jpg",
                 "dets_cam": [[100, 100, 200, 200, 0.9]]}) + "\n")
            fh.write(json.dumps(
                {"board": "A", "phase": "t", "still": "s1.jpg",
                 "dets_cam": [[160, 100, 260, 200, 0.9]]}) + "\n")

    def test_counts_at_two_ious(self):
        import hil_rescore
        out, boards = hil_rescore.rescore(self.dir, self.stills,
                                          [0.30, 0.20], 30.0)
        self.assertEqual(boards, ["A"])
        # strict: only the exact hit matches; the near-miss is a false
        self.assertEqual(out[("A", 0.30)],
                         {"gt": 2, "match": 1, "false": 1, "frames": 2})
        # loose: both match — the "recall jump" the discriminator reads
        self.assertEqual(out[("A", 0.20)],
                         {"gt": 2, "match": 2, "false": 0, "frames": 2})


if __name__ == "__main__":
    unittest.main()
