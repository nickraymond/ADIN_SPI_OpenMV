# test_frame_counter.py -- host-side unit tests for the pure logic in
# bench/frame_counter.py (parse + loss accounting + verdict). The raw
# AF_PACKET socket path is covered by the manual S5 demo run.
#
# Run:  python3 bench/test_frame_counter.py

import struct
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "firmware", "adin_drv"))

from frame_counter import parse_frame, SeqTracker, verdict, ETHERTYPE, MAGIC
from s5_frames import build_eth_frame


def frame(seq):
    return build_eth_frame(seq)


class TestParseFrame(unittest.TestCase):
    def test_parses_real_sender_frames(self):
        # frame_counter must accept exactly what s5_frames builds
        self.assertEqual(parse_frame(frame(0)), 0)
        self.assertEqual(parse_frame(frame(123456)), 123456)

    def test_rejects_wrong_ethertype(self):
        f = bytearray(frame(1))
        struct.pack_into(">H", f, 12, 0x0800)
        self.assertIsNone(parse_frame(bytes(f)))

    def test_rejects_wrong_magic(self):
        f = bytearray(frame(1))
        f[14:18] = b"XXXX"
        self.assertIsNone(parse_frame(bytes(f)))

    def test_rejects_short_frame(self):
        self.assertIsNone(parse_frame(frame(1)[:20]))


class TestSeqTracker(unittest.TestCase):
    def _run(self, seqs):
        trk = SeqTracker()
        for s in seqs:
            trk.feed(s, 500)
        return trk

    def test_clean_run_no_loss(self):
        trk = self._run(range(100))
        self.assertEqual(trk.lost, 0)
        self.assertEqual(trk.expected, 100)
        self.assertEqual(trk.dupes, 0)
        self.assertEqual(trk.out_of_order, 0)

    def test_gap_counts_as_lost(self):
        trk = self._run([0, 1, 2, 5, 6])   # 3, 4 missing
        self.assertEqual(trk.lost, 2)

    def test_window_relative_start(self):
        # counter attached late: sender already at seq 1000
        trk = self._run(range(1000, 1100))
        self.assertEqual(trk.lost, 0)
        self.assertEqual(trk.expected, 100)

    def test_dupe_not_counted_as_progress(self):
        trk = self._run([0, 1, 1, 2])
        self.assertEqual(trk.dupes, 1)
        self.assertEqual(trk.lost, 0)
        self.assertEqual(len(trk.seen), 3)

    def test_out_of_order_detected_but_not_lost(self):
        trk = self._run([0, 2, 1, 3])
        self.assertEqual(trk.out_of_order, 1)
        self.assertEqual(trk.lost, 0)

    def test_rate_math(self):
        trk = self._run(range(10))
        s = trk.summary(2.0)
        self.assertAlmostEqual(s["fps"], 5.0)
        self.assertAlmostEqual(s["mbps"], 10 * 500 * 8 / 2.0 / 1e6)


class TestVerdict(unittest.TestCase):
    def test_pass_on_zero_loss(self):
        trk = SeqTracker()
        for s in range(50):
            trk.feed(s, 500)
        passed, line = verdict(trk.summary(60.0))
        self.assertTrue(passed)
        self.assertIn("PASS", line)

    def test_fail_on_loss(self):
        trk = SeqTracker()
        for s in [0, 1, 9]:
            trk.feed(s, 500)
        passed, line = verdict(trk.summary(60.0))
        self.assertFalse(passed)
        self.assertIn("FAIL", line)
        self.assertIn("7 of 10", line)

    def test_fail_on_silence(self):
        passed, line = verdict(SeqTracker().summary(60.0))
        self.assertFalse(passed)
        self.assertIn("no test frames", line)


if __name__ == "__main__":
    unittest.main()
