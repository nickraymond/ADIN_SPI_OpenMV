# test_usb_frame_source.py -- host-side unit tests for the pure pieces of the
# USB frame source (StreamParser, JPEG checks, port discovery) and the bench
# helpers. No hardware, no pyserial needed.
#
# Run:  python3 pi/stream/test_usb_frame_source.py

import glob as _glob
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "bench"))

from usb_frame_source import (StreamParser, cp, find_openmv_port,  # noqa: E402
                              has_jpeg_eoi, looks_like_jpeg)
from usb_stream_bench import parse_modes, summarize  # noqa: E402

JPEG = b"\xff\xd8" + b"payload-bytes" + b"\xff\xd9"


def frame_bytes(seq, data, width=640, height=400):
    header = cp.frame_response("cid", seq, len(data), len(data), width, height)
    return cp.encode_message(header) + data


def completed_bytes(frames):
    return cp.encode_message(cp.completed_response("cid", {"frames": frames}))


class TestStreamParser(unittest.TestCase):
    def test_single_frame_then_completed(self):
        parser = StreamParser()
        events = parser.feed(frame_bytes(0, JPEG) + completed_bytes(1))
        self.assertEqual([k for k, _, _ in events], ["frame", "control"])
        kind, msg, payload = events[0]
        self.assertEqual(payload, JPEG)
        self.assertEqual(msg["frame"]["seq"], 0)
        self.assertEqual(events[1][1]["status"], "completed")

    def test_byte_by_byte_feed(self):
        wire = frame_bytes(3, JPEG) + frame_bytes(4, JPEG) + completed_bytes(2)
        parser = StreamParser()
        events = []
        for i in range(len(wire)):
            events.extend(parser.feed(wire[i:i + 1]))
        self.assertEqual([k for k, _, _ in events], ["frame", "frame", "control"])
        self.assertEqual([e[1]["frame"]["seq"] for e in events[:2]], [3, 4])
        self.assertEqual([e[2] for e in events[:2]], [JPEG, JPEG])

    def test_payload_may_contain_newlines(self):
        data = b"\xff\xd8" + b"\n" * 50 + b"\xff\xd9"
        parser = StreamParser()
        events = parser.feed(frame_bytes(0, data) + completed_bytes(1))
        self.assertEqual(events[0][2], data)
        self.assertEqual(events[1][1]["status"], "completed")

    def test_junk_line_surfaced_not_swallowed(self):
        parser = StreamParser()
        wire = b"MicroPython v1.28.0 on OPENMV_AE3\n" + frame_bytes(0, JPEG)
        events = parser.feed(wire)
        self.assertEqual(events[0][0], "junk")
        self.assertIn(b"MicroPython", events[0][1])
        self.assertEqual(events[1][0], "frame")

    def test_failed_control_passes_through(self):
        parser = StreamParser()
        wire = cp.encode_message(cp.failed_response("cid", cp.ERR_CAPTURE_FAILED, "boom"))
        events = parser.feed(wire)
        self.assertEqual(events[0][0], "control")
        self.assertEqual(events[0][1]["error"]["code"], "capture_failed")

    def test_blank_lines_skipped(self):
        parser = StreamParser()
        self.assertEqual(parser.feed(b"\n\n  \n"), [])


class TestRebootAction(unittest.TestCase):
    def test_reboot_is_allowlisted(self):
        # Guards the local patch: if firmware/ae3_usb is ever re-vendored from
        # upstream, this fails until the reboot action is re-applied (README.md
        # §Known firmware crash — hosts depend on it between stream sessions).
        self.assertIn("reboot", cp.ALLOWED_ACTIONS)


class TestJpegChecks(unittest.TestCase):
    def test_good_jpeg(self):
        self.assertTrue(looks_like_jpeg(JPEG))
        self.assertTrue(has_jpeg_eoi(JPEG))

    def test_bad(self):
        self.assertFalse(looks_like_jpeg(b"\x00\x01\x02\x03"))
        self.assertFalse(has_jpeg_eoi(JPEG[:-2] + b"\x00\x00"))
        self.assertFalse(looks_like_jpeg(b""))
        self.assertFalse(has_jpeg_eoi(b"\xff\xd9"))  # too short to be a frame


class TestFindPort(unittest.TestCase):
    def test_finds_first_sorted_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("usb-OpenMV_OpenMV_Camera_bbb-if00",
                         "usb-OpenMV_OpenMV_Camera_aaa-if00"):
                open(os.path.join(tmp, name), "w").close()
            found = find_openmv_port(os.path.join(tmp, "usb-OpenMV_OpenMV_Camera_*-if00"))
            self.assertTrue(found.endswith("aaa-if00"))

    def test_no_match_raises_with_hint(self):
        with self.assertRaises(FileNotFoundError):
            find_openmv_port("/nonexistent/usb-OpenMV_*-if00")


class TestBenchHelpers(unittest.TestCase):
    def test_parse_modes(self):
        self.assertEqual(parse_modes("VGA:50, hd:70"), [("VGA", 50), ("HD", 70)])
        with self.assertRaises(ValueError):
            parse_modes("VGA")
        with self.assertRaises(ValueError):
            parse_modes("")

    def test_summarize_rates(self):
        # 11 frames of 10 KB over exactly 1 s -> 10 fps; Mbps counts bytes after
        # the first frame (rate is per-interval, matching the fps denominator).
        seqs = list(range(11))
        sizes = [10240] * 11
        s = summarize(seqs, sizes, t_first=100.0, t_last=101.0)
        self.assertEqual(s["frames"], 11)
        self.assertEqual(s["dropped"], 0)
        self.assertAlmostEqual(s["fps"], 10.0)
        self.assertAlmostEqual(s["mbps"], 10 * 10240 * 8 / 1e6, places=3)
        self.assertAlmostEqual(s["kb_avg"], 10.0)

    def test_summarize_gaps_and_empty(self):
        s = summarize([0, 1, 5], [100, 100, 100], 0.0, 1.0)
        self.assertEqual(s["dropped"], 3)  # 2,3,4 missing
        self.assertEqual(summarize([], [], 0, 0)["frames"], 0)

    def test_summarize_single_frame_no_div_zero(self):
        s = summarize([7], [2048], 5.0, 5.0)
        self.assertEqual(s["frames"], 1)
        self.assertEqual(s["fps"], 0.0)
        self.assertEqual(s["mbps"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
