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
    from hil_harness import (CamMap, find_markers, map_still_box,
                             match_frame, PowerTail, solve_cam_map,
                             solve_homography, STILL_W, STILL_H)
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


# The 9-marker grid playback_server serves (row-major), and a plausible
# camera geometry: box D fills most of a 640x400 frame with mild
# perspective — the E11 synthetic fixture.
SRC9 = [(x, y) for y in (0.15, 0.5, 0.85) for x in (0.185, 0.5, 0.815)]
DST4 = [(41.0, 28.0), (601.0, 44.0), (588.0, 371.0), (52.0, 355.0)]


def synthetic_cam(k1):
    """A ground-truth CamMap with known H (from 4 plausible corner
    correspondences) and known k1, plus its 9 observed marker px."""
    import math
    src4 = [SRC9[0], SRC9[2], SRC9[8], SRC9[6]]     # TL TR BR BL
    H = solve_homography(np.array(src4), np.array(DST4))
    M = CamMap(H, k1, 320.0, 200.0, math.hypot(640, 400) / 2)
    return M, M.frac_to_cam(np.array(SRC9))


@unittest.skipUnless(HAVE_DEPS, "numpy not installed on this host")
class TestCamMapSolver(unittest.TestCase):
    """E11: the H + radial-k1 fit and its distortion model."""

    def test_distort_undistort_round_trip(self):
        M = CamMap(np.eye(3), -0.25, 320.0, 200.0, 377.4)
        pts = np.array([[10.0, 20.0], [320.0, 200.0], [600.0, 380.0]])
        self.assertLess(np.abs(M.undistort(M.distort(pts)) - pts).max(),
                        1e-6)

    def test_solver_recovers_known_k1(self):
        # barrel distortion of the N6 class: solver must find k1 and a
        # map that reproduces the observed markers to sub-pixel
        M_true, dst9 = synthetic_cam(-0.18)
        M, diag = solve_cam_map(SRC9, dst9, 640, 400)
        self.assertLess(abs(M.k1 - (-0.18)), 0.02)
        self.assertLess(diag["rms_px"], 0.2)
        err = np.abs(M.frac_to_cam(np.array(SRC9)) - dst9).max()
        self.assertLess(err, 0.5)

    def test_zero_distortion_stays_zero(self):
        # a straight lens (the AE3 class) must not grow a fake k1
        _, dst9 = synthetic_cam(0.0)
        M, diag = solve_cam_map(SRC9, dst9, 640, 400)
        self.assertLess(abs(M.k1), 0.01)
        self.assertLess(diag["rms_px"], 0.2)

    def test_dlt4_midfield_error_is_the_before_number(self):
        # on a distorted camera the old 4-corner exact DLT must show a
        # clearly worse mid-field error than the k1-aware fit
        _, dst9 = synthetic_cam(-0.18)
        _, diag = solve_cam_map(SRC9, dst9, 640, 400)
        self.assertGreater(diag["dlt4_mid_rms_px"], 2.0)
        self.assertGreater(diag["dlt4_mid_rms_px"],
                           10 * max(diag["rms_px"], 0.01))

    def test_legacy_H_wrap_matches_plain_projective_math(self):
        # a bare 3x3 H through map_still_box == the pre-E11 math
        box = map_still_box(H_identity(), 100, 100, 50, 80)
        self.assertAlmostEqual(box[0], 100.0)
        self.assertAlmostEqual(box[1], 100.0)
        self.assertAlmostEqual(box[2], 150.0)
        self.assertAlmostEqual(box[3], 180.0)

    def test_cam_map_json_round_trip(self):
        M_true, _ = synthetic_cam(-0.18)
        M2 = CamMap.from_dict(json.loads(json.dumps(M_true.to_dict())))
        pts = np.array(SRC9)
        self.assertLess(np.abs(M2.frac_to_cam(pts)
                               - M_true.frac_to_cam(pts)).max(), 1e-9)


@unittest.skipUnless(HAVE_DEPS, "numpy not installed on this host")
class TestFindMarkers9(unittest.TestCase):
    def test_nine_centroids_row_major_order(self):
        # 9 bright squares, one per 3x3 cell, at known centers; the
        # black frame carries mild room glow that subtraction removes
        h, w = 400, 640
        black = np.full((h, w), 6.0, np.float32)
        calib = black.copy()
        centers = []
        for r in range(3):
            for c in range(3):
                cx = int(w * (c + 0.5) / 3) + (5 * (c - 1))
                cy = int(h * (r + 0.5) / 3) + (4 * (r - 1))
                calib[cy - 6:cy + 7, cx - 6:cx + 7] = 250.0
                centers.append((cx, cy))
        cents = find_markers(calib, black)
        self.assertEqual(len(cents), 9)
        for (mx, my), (ex, ey) in zip(cents, centers):
            self.assertLess(abs(mx - ex), 1.0)
            self.assertLess(abs(my - ey), 1.0)

    def test_dark_cell_fails_loud_with_cell_name(self):
        h, w = 400, 640
        black = np.zeros((h, w), np.float32)
        calib = black.copy()
        for r in range(3):
            for c in range(3):
                if (r, c) == (1, 1):
                    continue                        # center marker dark
                cx, cy = int(w * (c + 0.5) / 3), int(h * (r + 0.5) / 3)
                calib[cy - 6:cy + 7, cx - 6:cx + 7] = 250.0
        with self.assertRaises(SystemExit) as ctx:
            find_markers(calib, black)
        self.assertIn("CC", str(ctx.exception))


@unittest.skipUnless(HAVE_DEPS, "numpy not installed on this host")
class TestPowerTail(unittest.TestCase):
    """E9: the review page's per-frame power windows and the N/A rule."""

    def setUp(self):
        import shutil
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="ptail_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "power_test.jsonl")

    def write(self, rows, mode="a"):
        with open(self.path, mode) as fh:
            for ts, label, mw in rows:
                fh.write(json.dumps(
                    {"ts": ts, "ch": 1, "label": label, "V": 5.0,
                     "mA": mw / 5.0, "mW": mw}) + "\n")

    def test_window_peak_and_mj(self):
        # 10 Hz, 1 s window at 500 mW with one 900 mW sample:
        # peak 0.90 W, mJ = mean * 1 s
        rows = [(100.0 + i / 10.0, "AE3", 500.0) for i in range(11)]
        rows[5] = (100.5, "AE3", 900.0)
        self.write(rows)
        pt = PowerTail(self.dir)
        pw = pt.window("AE3", 100.0, 101.0)
        self.assertEqual(pw["peak_w"], 0.9)
        mean = (10 * 500.0 + 900.0) / 11
        self.assertAlmostEqual(pw["mj"], round(mean, 1), places=1)

    def test_dead_channel_is_none_never_a_number(self):
        # the N6's bypassed CH3 signature: rows present, ~0 mW
        self.write([(100.0 + i / 10.0, "N6", 0.0) for i in range(11)])
        pt = PowerTail(self.dir)
        self.assertIsNone(pt.window("N6", 100.0, 101.0))

    def test_absent_label_and_absent_file(self):
        self.write([(100.0, "AE3", 500.0)])
        pt = PowerTail(self.dir)
        self.assertIsNone(pt.window("N6", 99.0, 101.0))
        self.assertIsNone(PowerTail(self.dir + "_nope")
                          .window("AE3", 99.0, 101.0))

    def test_tail_sees_rows_written_after_first_query(self):
        self.write([(100.0, "AE3", 500.0)])
        pt = PowerTail(self.dir)
        self.assertIsNotNone(pt.window("AE3", 99.0, 101.0))
        self.write([(200.0 + i / 10.0, "AE3", 700.0) for i in range(11)])
        pw = pt.window("AE3", 200.0, 201.0)
        self.assertEqual(pw["peak_w"], 0.7)

    def test_partially_flushed_row_survives(self):
        self.write([(100.0, "AE3", 500.0)])
        half = json.dumps({"ts": 100.1, "ch": 1, "label": "AE3",
                           "V": 5.0, "mA": 100.0, "mW": 500.0})
        with open(self.path, "a") as fh:
            fh.write(half[:20])                    # no newline yet
        pt = PowerTail(self.dir)
        self.assertIsNotNone(pt.window("AE3", 99.0, 101.0))
        with open(self.path, "a") as fh:
            fh.write(half[20:] + "\n")
        pw = pt.window("AE3", 100.05, 100.2)
        self.assertEqual(pw["peak_w"], 0.5)


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

    def test_calib_json_preferred_over_legacy_npy(self):
        # E11: an identity calib json beside a WRONG npy — counts must
        # follow the identity mapping, proof the k1-aware json wins
        import hil_rescore
        with open(os.path.join(self.dir, "calib_A.json"), "w") as fh:
            json.dump(CamMap(H_identity()).to_dict(), fh)
        np.save(os.path.join(self.dir, "H_A.npy"),
                np.diag([2.0, 2.0, 1.0]))
        out, _ = hil_rescore.rescore(self.dir, self.stills, [0.30], 30.0)
        self.assertEqual(out[("A", 0.30)],
                         {"gt": 2, "match": 1, "false": 1, "frames": 2})


if __name__ == "__main__":
    unittest.main()
