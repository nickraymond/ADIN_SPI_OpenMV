# test_ae3_spi_bench_helpers.py -- host-side unit tests for the pure
# helpers in ae3_spi_bench.py (the hardware paths are covered by the
# manual bench run on the AE3).
#
# Run:  python3 bench/test_ae3_spi_bench_helpers.py

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ae3_spi_bench import (fill_pattern, first_diff, mbps, percentile,
                           summarize, pass_fail)


class TestFillPattern(unittest.TestCase):
    def test_deterministic(self):
        a, b = bytearray(64), bytearray(64)
        fill_pattern(a, 7)
        fill_pattern(b, 7)
        self.assertEqual(a, b)

    def test_seed_changes_pattern(self):
        a, b = bytearray(64), bytearray(64)
        fill_pattern(a, 1)
        fill_pattern(b, 2)
        self.assertNotEqual(a, b)

    def test_not_trivial(self):
        a = bytearray(256)
        fill_pattern(a, 0)
        self.assertGreater(len(set(a)), 32)      # not constant / tiny cycle


class TestFirstDiff(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(first_diff(b"abc", b"abc"), -1)

    def test_diff_at_start_and_end(self):
        self.assertEqual(first_diff(b"xbc", b"abc"), 0)
        self.assertEqual(first_diff(b"abx", b"abc"), 2)


class TestMbps(unittest.TestCase):
    def test_known_value(self):
        # 1 MB in 1 s = 8 Mbps  (1e6-bit megabits, matching the pass rule)
        self.assertAlmostEqual(mbps(1_000_000, 1_000_000), 8.0)

    def test_zero_duration_is_zero_not_crash(self):
        self.assertEqual(mbps(1000, 0), 0.0)


class TestStats(unittest.TestCase):
    def test_summarize_odd(self):
        n, lo, med, p99, hi = summarize([5, 1, 3])
        self.assertEqual((n, lo, med, hi), (3, 1, 3, 5))

    def test_summarize_even_median_interpolates(self):
        self.assertEqual(summarize([1, 2, 3, 4])[2], 2.5)

    def test_p99_near_max(self):
        vals = list(range(100))
        self.assertEqual(percentile(sorted(vals), 99), 99)
        self.assertEqual(summarize(vals)[3], 99)

    def test_percentile_single_element(self):
        self.assertEqual(percentile([42], 99), 42)


class TestPassFail(unittest.TestCase):
    def test_passes_on_clean_fast_point(self):
        pts = [(5, 64, 3.0, 0), (25, 4096, 18.2, 0)]
        passed, best = pass_fail(pts, 12.0)
        self.assertTrue(passed)
        self.assertEqual(best, (25, 4096, 18.2, 0))

    def test_fast_point_with_errors_cannot_pass(self):
        pts = [(25, 4096, 18.2, 3), (10, 1024, 7.5, 0)]
        passed, best = pass_fail(pts, 12.0)
        self.assertFalse(passed)
        self.assertEqual(best, (10, 1024, 7.5, 0))

    def test_all_errors_gives_no_best(self):
        passed, best = pass_fail([(5, 64, 3.0, 1)], 12.0)
        self.assertFalse(passed)
        self.assertIsNone(best)

    def test_empty(self):
        self.assertEqual(pass_fail([], 12.0), (False, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
