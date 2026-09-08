#!/usr/bin/env python3
"""Pure-logic tests for the optics toolkit. No images, no hardware, no cv2."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestSceneMetrics(unittest.TestCase):
    """scene_metrics needs only numpy/PIL, so it is testable directly."""

    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("numpy/pillow not installed")
        import scene_metrics
        cls.m = scene_metrics

    def test_noise_floor_is_zero_on_a_flat_field(self):
        import numpy as np
        self.assertAlmostEqual(self.m.noise_floor(np.full((128, 128), 100.0)), 0.0)

    def test_noise_floor_tracks_added_noise(self):
        import numpy as np
        rng = np.random.default_rng(0)
        quiet = np.full((256, 256), 100.0) + rng.normal(0, 1, (256, 256))
        loud = np.full((256, 256), 100.0) + rng.normal(0, 4, (256, 256))
        self.assertLess(self.m.noise_floor(quiet), self.m.noise_floor(loud))

    def test_noise_floor_ignores_scene_edges(self):
        """The median-of-flattest-quartile is what makes this a CAMERA
        measurement: a hard edge is scene content and must not dominate."""
        import numpy as np
        a = np.full((256, 256), 50.0)
        b = a.copy()
        b[:, 128:] = 200.0                      # a big, noiseless edge
        self.assertAlmostEqual(self.m.noise_floor(a), self.m.noise_floor(b))

    def test_vignette_is_one_on_a_flat_field(self):
        import numpy as np
        self.assertAlmostEqual(self.m.vignette(np.full((300, 400), 128.0)), 1.0)

    def test_acutance_is_zero_on_a_flat_field(self):
        import numpy as np
        self.assertEqual(self.m.acutance(np.full((64, 64), 7.0)), 0.0)


class TestCardMetricsRois(unittest.TestCase):
    """The ROIs are hand-placed constants; guard their shape, not their values."""

    @classmethod
    def setUpClass(cls):
        try:
            import cv2  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("opencv not installed")
        import card_metrics
        cls.c = card_metrics

    def test_rois_lie_inside_the_canvas(self):
        for name in ("WHITE", "GREY", "COLOUR", "BAR"):
            x0, y0, x1, y1 = getattr(self.c, name)
            self.assertTrue(0 <= x0 < x1 <= 900, name)
            self.assertTrue(0 <= y0 < y1 <= 560, name)

    def test_rois_do_not_overlap_the_white_reference(self):
        """WHITE must sample UNPRINTED card, or white balance measures ink."""
        wx0, wy0, wx1, wy1 = self.c.WHITE
        for name in ("GREY", "COLOUR", "BAR"):
            x0, y0, x1, y1 = getattr(self.c, name)
            overlap = not (x1 <= wx0 or x0 >= wx1 or y1 <= wy0 or y0 >= wy1)
            self.assertFalse(overlap, "%s overlaps WHITE" % name)


class TestRectifyRois(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cv2  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("opencv not installed")
        import rectify_card
        cls.r = rectify_card

    def test_every_roi_is_a_sane_window(self):
        for name, (x0, y0, x1, y1) in self.r.ROI.items():
            self.assertLess(x0, x1, name)
            self.assertLess(y0, y1, name)
            self.assertGreater((x1 - x0) * (y1 - y0), 5000, name)

    def test_canvas_is_landscape_like_the_card(self):
        self.assertGreater(self.r.OUT_W, self.r.OUT_H)


if __name__ == "__main__":
    unittest.main(verbosity=1)
