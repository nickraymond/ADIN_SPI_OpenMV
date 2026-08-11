# test_ae3_npu_bench_helpers.py -- host-side unit tests for the pure
# helpers in ae3_npu_bench.py (model loading/timing is covered by the
# manual bench run on the AE3).
#
# Run:  python3 bench/test_ae3_npu_bench_helpers.py

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ae3_npu_bench import (parse_hw, tile_count, eff_fps, scaled_fish_px,
                           gate_line)


class TestParseHw(unittest.TestCase):
    def test_nhwc(self):
        self.assertEqual(parse_hw((1, 192, 192, 3)), (192, 192))

    def test_nhwc_nonsquare(self):
        self.assertEqual(parse_hw((1, 96, 128, 1)), (96, 128))

    def test_hwc(self):
        self.assertEqual(parse_hw((128, 128, 3)), (128, 128))

    def test_unsupported_raises(self):
        with self.assertRaises(ValueError):
            parse_hw((192, 192))


class TestTileCount(unittest.TestCase):
    def test_tile_covers_frame(self):
        self.assertEqual(tile_count(100, 100, 192, 192, 32), 1)

    def test_exact_division_no_overlap(self):
        # 4x2 grid of 320x400 tiles over 1280x800
        self.assertEqual(tile_count(1280, 800, 320, 400, 0), 4 * 2)

    def test_overlap_adds_tiles(self):
        no_ov = tile_count(1280, 800, 192, 192, 0)
        ov = tile_count(1280, 800, 192, 192, 32)
        self.assertGreater(ov, no_ov)

    def test_yolov8n_192_at_hd(self):
        # stride 160: x needs ceil((1280-192)/160)+1 = 8, y ceil(608/160)+1 = 5
        self.assertEqual(tile_count(1280, 800, 192, 192, 32), 8 * 5)

    def test_last_tile_always_reaches_edge(self):
        # stride 160: 1312 px = 192 + 7*160 exactly covered by 8 columns;
        # one more pixel forces a 9th
        self.assertEqual(tile_count(1312, 192, 192, 192, 32), 8)
        self.assertEqual(tile_count(1313, 192, 192, 192, 32), 9)

    def test_overlap_ge_tile_raises(self):
        with self.assertRaises(ValueError):
            tile_count(1280, 800, 32, 32, 32)


class TestEffFps(unittest.TestCase):
    def test_single_tile(self):
        self.assertAlmostEqual(eff_fps(50.0), 20.0)

    def test_tiled(self):
        self.assertAlmostEqual(eff_fps(25.0, tiles=8), 5.0)

    def test_zero_cost_is_zero_not_crash(self):
        self.assertEqual(eff_fps(0.0, tiles=10), 0.0)


class TestScaledFishPx(unittest.TestCase):
    def test_downscale(self):
        # 150 px fish, HD frame squeezed into a 192-wide input
        self.assertAlmostEqual(scaled_fish_px(150, 192, 1280), 22.5)

    def test_native_when_tiled(self):
        self.assertEqual(scaled_fish_px(100, 1280, 1280), 100)


class TestGateLine(unittest.TestCase):
    def test_meets(self):
        self.assertIn("MEETS", gate_line("m", 3.0, gate=3.0))

    def test_below(self):
        self.assertIn("BELOW", gate_line("m", 2.99, gate=3.0))

    def test_includes_name_and_number(self):
        line = gate_line("yolov8n tiled@HD", 4.2)
        self.assertIn("yolov8n tiled@HD", line)
        self.assertIn("4.2", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
