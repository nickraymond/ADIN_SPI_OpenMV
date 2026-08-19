#!/usr/bin/env python3
"""Host tests for the N6 stream helpers (S24 bite 1). No board, no serial port.

    python3 bench/test_n6_stream_helpers.py

Covers the pure pieces: the LAB threshold suggester, the stats arithmetic, and
the frame reader's framing/rejection paths -- the parts that decide whether a
malformed stream is surfaced or silently swallowed.
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import n6_stream_host as H  # noqa: E402


class TestSuggestThreshold(unittest.TestCase):
    def test_brackets_the_measured_mean(self):
        self.assertEqual(H.suggest_threshold((40, 30, -40)),
                         "15,65,10,50,-60,-20")

    def test_clamps_to_lab_limits(self):
        # L cannot go below 0 or above 100 whatever the margin says.
        self.assertEqual(H.suggest_threshold((5, 0, 0)).split(",")[0], "0")
        self.assertEqual(H.suggest_threshold((98, 0, 0)).split(",")[1], "100")

    def test_six_values_always(self):
        self.assertEqual(len(H.suggest_threshold((50, 0, 0)).split(",")), 6)


class TestStats(unittest.TestCase):
    def _stats(self, times):
        it = iter(times)
        return H.Stats(clock=lambda: next(it))

    def test_fps_from_span(self):
        s = self._stats([0.0, 1.0, 2.0])   # 3 frames spanning 2 s = 1.0 fps
        for _ in range(3):
            s.note({}, 100)
        self.assertAlmostEqual(s.fps(), 1.0)

    def test_fps_needs_two_frames(self):
        s = self._stats([0.0])
        s.note({}, 100)
        self.assertEqual(s.fps(), 0.0)

    def test_means_are_per_frame(self):
        s = self._stats([0.0, 1.0])
        s.note({"inf_us": 20000}, 10)
        s.note({"inf_us": 30000}, 10)
        self.assertEqual(s.snapshot()["inf_ms"], 25.0)   # not the sum

    def test_snapshot_has_no_lab_until_tuning(self):
        s = self._stats([0.0])
        s.note({}, 10)
        self.assertIsNone(s.snapshot()["lab"])
        self.assertEqual(s.snapshot()["suggest"], "")


def _stream(data):
    """Stands in for a SerialBoard: anything with a bytes readline()."""
    return io.BytesIO(data)


def _frame_bytes(seq, jpeg):
    import base64
    b64 = base64.b64encode(jpeg)
    hdr = {"seq": seq, "w": 4, "h": 4, "b64": len(b64), "jpeg": len(jpeg),
           "cap_us": 1, "inf_us": 2, "blob_us": 3, "enc_us": 4,
           "det": 0, "blobs": 0}
    return b"#F " + json.dumps(hdr).encode() + b"\n" + b64 + b"\n"


JPEG = b"\xff\xd8" + b"payload" + b"\xff\xd9"


class TestReaderLoop(unittest.TestCase):
    def _run(self, data):
        latest, stats = H.Latest(), H.Stats()
        state = {"alive": True}
        H.reader_loop(_stream(data), latest, stats, state)
        return latest, stats

    def test_decodes_a_frame(self):
        latest, stats = self._run(_frame_bytes(7, JPEG))
        self.assertEqual(stats.frames, 1)
        self.assertEqual(latest.get(), (JPEG, 7))   # (frame, seq)

    def test_banner_is_captured(self):
        _, stats = self._run(b'#I {"fw":"x"}\n')
        self.assertIn("fw", stats.info)

    def test_short_payload_is_rejected_not_shown(self):
        # A truncated payload must never reach the viewer as a frame.
        good = _frame_bytes(1, JPEG)
        broken = good[:-5] + b"\n"
        latest, stats = self._run(broken)
        self.assertEqual(stats.frames, 0)
        self.assertEqual(latest.get(), (b"", -1))   # nothing ever published
        self.assertTrue(stats.junk)

    def test_non_jpeg_payload_is_rejected(self):
        latest, stats = self._run(_frame_bytes(1, b"not a jpeg at all"))
        self.assertEqual(stats.frames, 0)
        self.assertTrue(stats.junk)

    def test_junk_lines_are_surfaced(self):
        # A board traceback must be visible, not swallowed.
        _, stats = self._run(b"Traceback (most recent call last):\n")
        self.assertTrue(stats.junk)

    def test_stream_survives_junk_between_frames(self):
        data = b"noise\n" + _frame_bytes(1, JPEG) + b"more noise\n" \
            + _frame_bytes(2, JPEG)
        latest, stats = self._run(data)
        self.assertEqual(stats.frames, 2)
        self.assertEqual(latest.get()[1], 2)        # newest seq wins

    def test_marks_process_dead_at_eof(self):
        latest, stats = H.Latest(), H.Stats()
        state = {"alive": True}
        H.reader_loop(_stream(b""), latest, stats, state)
        self.assertFalse(state["alive"])


class TestConfig(unittest.TestCase):
    def test_blob_thresh_needs_six_values(self):
        args = H.parse_args(["--blob-thresh", "1,2,3"])
        with self.assertRaises(SystemExit):
            H.cfg_from_args(args)

    def test_blob_thresh_parses(self):
        args = H.parse_args(["--blob-thresh", "1,2,3,4,-5,-6"])
        self.assertEqual(H.cfg_from_args(args)["blob_thresh"],
                         (1, 2, 3, 4, -5, -6))

    def test_no_detect_flag(self):
        self.assertFalse(H.cfg_from_args(H.parse_args(["--no-detect"]))["detect"])
        self.assertTrue(H.cfg_from_args(H.parse_args([]))["detect"])

    def test_tune_flag_reaches_the_board_config(self):
        self.assertTrue(H.cfg_from_args(H.parse_args(["--tune"]))["tune"])

    def test_build_script_injects_config_before_the_body(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = H.build_board_script({"framesize": "HD"}, d)
            text = open(path).read()
        self.assertTrue(text.startswith("_CFG = "))
        self.assertIn("'framesize': 'HD'", text)
        self.assertIn("def main():", text)          # the body came along
        self.assertLess(text.index("_CFG = "), text.index("import csi"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
