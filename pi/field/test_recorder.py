#!/usr/bin/env python3
"""Host tests for the S32 recorder. No boards, no ffmpeg, no network.

The parts under test are the ones that fail SILENTLY on hardware: a resync that
accepts a bogus frame, a ring that drops without counting, a ceiling check that
green-lights an impossible request, and a media route that serves a file
outside the recordings directory.

    python3 pi/field/test_recorder.py
"""

import json
import os
import struct
import sys
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recorder as R            # noqa: E402
import record_run as RR         # noqa: E402
import transcode as T           # noqa: E402


def frame(seq, payload):
    return R.MAGIC + struct.pack("<II", seq, len(payload)) + payload


JPG = b"\xff\xd8" + b"body-bytes" + b"\xff\xd9"


class TestFrameParser(unittest.TestCase):
    def test_single_frame(self):
        p = R.FrameParser()
        self.assertEqual(p.feed(frame(0, JPG)), [(0, JPG)])

    def test_many_frames_one_chunk(self):
        p = R.FrameParser()
        blob = b"".join(frame(i, JPG) for i in range(5))
        self.assertEqual([s for s, _ in p.feed(blob)], [0, 1, 2, 3, 4])

    def test_split_across_reads(self):
        """A frame arriving in three reads must still come out whole and once."""
        p = R.FrameParser()
        f = frame(7, JPG)
        self.assertEqual(p.feed(f[:5]), [])
        self.assertEqual(p.feed(f[5:9]), [])
        self.assertEqual(p.feed(f[9:]), [(7, JPG)])

    def test_leading_banner_noise_is_skipped(self):
        p = R.FrameParser()
        out = p.feed(b"paste mode echo garbage\r\n" + frame(1, JPG))
        self.assertEqual(out, [(1, JPG)])
        self.assertGreater(p.dropped_bytes, 0)

    def test_absurd_length_is_refused_and_resyncs(self):
        """A corrupt length must not swallow the stream."""
        p = R.FrameParser()
        bad = R.MAGIC + struct.pack("<II", 3, 0xFFFFFFF0)
        out = p.feed(bad + frame(4, JPG))
        self.assertEqual(out, [(4, JPG)])
        self.assertEqual(p.resyncs, 1)

    def test_payload_that_is_not_jpeg_is_refused(self):
        """The SOI check is what makes a plausible length insufficient."""
        p = R.FrameParser()
        notjpeg = b"\x00\x01\x02\x03\x04\x05"
        out = p.feed(frame(9, notjpeg) + frame(10, JPG))
        self.assertEqual(out, [(10, JPG)])
        self.assertEqual(p.resyncs, 1)

    def test_zero_length_refused(self):
        p = R.FrameParser()
        out = p.feed(R.MAGIC + struct.pack("<II", 1, 0) + frame(2, JPG))
        self.assertEqual(out, [(2, JPG)])

    def test_magic_appearing_inside_payload_is_harmless(self):
        """The length field consumes the payload, so embedded magic is data."""
        p = R.FrameParser()
        tricky = b"\xff\xd8" + R.MAGIC + b"more" + b"\xff\xd9"
        self.assertEqual(p.feed(frame(0, tricky)), [(0, tricky)])


class TestRingBuffer(unittest.TestCase):
    def test_put_get_roundtrip(self):
        r = R.RingBuffer(1000)
        self.assertTrue(r.put((1, b"abc")))
        self.assertEqual(r.get(timeout=0.01), (1, b"abc"))

    def test_overflow_drops_and_counts(self):
        """A full ring must never silently discard."""
        r = R.RingBuffer(10)
        self.assertTrue(r.put((1, b"12345")))
        self.assertFalse(r.put((2, b"123456")))
        self.assertEqual(r.dropped_frames, 1)
        self.assertEqual(r.dropped_bytes, 6)

    def test_high_water_tracked(self):
        r = R.RingBuffer(100)
        r.put((1, b"x" * 40))
        r.put((2, b"x" * 30))
        self.assertEqual(r.high_water, 70)
        r.get(timeout=0.01)
        self.assertEqual(r.high_water, 70)     # a peak, not a level

    def test_get_returns_none_when_empty(self):
        self.assertIsNone(R.RingBuffer(10).get(timeout=0.01))

    def test_writer_thread_writes_every_frame(self):
        r = R.RingBuffer(1 << 20)
        state = {"done": False}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.mjpeg")
            t = threading.Thread(target=R.writer_thread, args=(r, path, state))
            t.start()
            for i in range(50):
                r.put((i, JPG))
            state["done"] = True
            r.close()
            t.join(timeout=10)
            self.assertEqual(state["written_frames"], 50)
            self.assertEqual(os.path.getsize(path), 50 * len(JPG))


class TestRingSizing(unittest.TestCase):
    def test_covers_the_measured_stall(self):
        """nereus000 measured a 3.86 s write stall; the ring must cover it."""
        rate = 14.5e6
        self.assertGreaterEqual(R.ring_size_bytes(rate, 8e9), rate * 3.86)

    def test_clamped_by_a_small_host(self):
        """A Pi Zero 2 W has 512 MB and cannot lend 256 MB."""
        small = R.ring_size_bytes(14.5e6, 300 * 1024 * 1024)
        self.assertLessEqual(small, 300 * 1024 * 1024 * 0.25 + 1)

    def test_never_below_the_floor(self):
        self.assertGreaterEqual(R.ring_size_bytes(1000, 8e9), R.RING_MIN_BYTES)

    def test_never_above_the_cap(self):
        self.assertLessEqual(R.ring_size_bytes(1e12, 64e9), R.RING_MAX_BYTES)

    def test_missing_meminfo_does_not_crash(self):
        self.assertGreater(R.ring_size_bytes(14.5e6, None), 0)
        self.assertIsNone(R.mem_available_bytes("/nonexistent/meminfo"))


class TestPacing(unittest.TestCase):
    def test_free_run(self):
        self.assertEqual(R.pace_ms_for(0), 0)
        self.assertEqual(R.pace_ms_for(None), 0)

    def test_thirty_fps(self):
        self.assertEqual(R.pace_ms_for(30), 33)


class TestCeilingCheck(unittest.TestCase):
    CEIL = {"cameras": {"N6": {"cells": {"HD_q90": 29.3, "HD_q85": 34.9,
                                         "VGA_q90": 63.1}}}}

    def test_impossible_is_named(self):
        v, msg = RR.check_request(self.CEIL, "N6", "HD", 90, 30)
        self.assertEqual(v, "impossible")
        self.assertIn("29.3", msg)

    def test_tight_when_inside_ten_percent(self):
        v, _ = RR.check_request(self.CEIL, "N6", "HD", 85, 33)
        self.assertEqual(v, "tight")

    def test_ok_with_margin(self):
        v, _ = RR.check_request(self.CEIL, "N6", "HD", 85, 30)
        self.assertEqual(v, "ok")

    def test_unmeasured_is_not_silently_ok(self):
        """A cell nobody measured must never be reported as fine."""
        v, _ = RR.check_request(self.CEIL, "AE3", "HD", 90, 30)
        self.assertEqual(v, "unmeasured")

    # The encoder number excludes the board's own USB write and is always the
    # optimistic one. Measured on nereus000: HD q85 encodes at 34.9 fps and
    # DELIVERS 23.8. Guarding on 34.9 promises 30 and hands back 24.
    BOTH = {"cameras": {"N6": {"cells": {"HD_q85": 34.9},
                               "delivered": {"HD_q85": 23.81}}}}

    def test_delivered_is_preferred_over_encoder(self):
        val, kind = RR.ceiling_for(self.BOTH, "N6", "HD", 85)
        self.assertEqual(kind, "delivered")
        self.assertAlmostEqual(val, 23.81)

    def test_delivered_turns_a_false_ok_into_impossible(self):
        """The exact regression: 30 fps looks fine on 34.9, but is not."""
        enc_only = {"cameras": {"N6": {"cells": {"HD_q85": 34.9}}}}
        self.assertEqual(RR.check_request(enc_only, "N6", "HD", 85, 30)[0], "ok")
        self.assertEqual(RR.check_request(self.BOTH, "N6", "HD", 85, 30)[0],
                         "impossible")

    def test_encoder_verdict_says_the_real_rate_is_lower(self):
        _, msg = RR.check_request({"cameras": {"N6": {"cells": {"HD_q85": 34.9}}}},
                                  "N6", "HD", 85, 20)
        self.assertIn("lower", msg)


class TestPerCameraSettings(unittest.TestCase):
    """The cameras are not equals, so one global setting cannot serve them.

    HD q70 gives the N6 30.24 fps and the AE3 2.29. Measured, this rig.
    """

    def test_each_camera_gets_its_own_measured_default(self):
        self.assertEqual(RR.settings_for("N6", "HD", 85), ("HD", 70))
        self.assertEqual(RR.settings_for("AE3", "HD", 85), ("VGA", 50))

    def test_explicit_override_beats_the_default(self):
        got = RR.settings_for("AE3", "HD", 85,
                              {"AE3": {"framesize": "QVGA", "quality": 50}})
        self.assertEqual(got, ("QVGA", 50))

    def test_partial_override_keeps_the_rest_of_the_default(self):
        self.assertEqual(RR.settings_for("AE3", "HD", 85,
                                         {"AE3": {"quality": 70}}),
                         ("VGA", 70))

    def test_none_values_do_not_clobber(self):
        """A form that sends nothing for a field must not erase the default."""
        self.assertEqual(RR.settings_for("N6", "HD", 85,
                                         {"N6": {"framesize": None,
                                                 "quality": None}}),
                         ("HD", 70))

    def test_unknown_camera_falls_back_to_the_global_ask(self):
        self.assertEqual(RR.settings_for("IMX", "VGA", 50), ("VGA", 50))

    def test_quality_is_coerced_to_int(self):
        """The form sends strings; a str would break the "%s_q%d" cell key."""
        fs, q = RR.settings_for("N6", "HD", 85, {"N6": {"quality": "90"}})
        self.assertIsInstance(q, int)
        self.assertEqual(q, 90)


class TestSessions(unittest.TestCase):
    def test_broken_manifest_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "rec_bad"))
            with open(os.path.join(d, "rec_bad", "manifest.json"), "w") as f:
                f.write("{not json")
            got = R.load_sessions(d)
            self.assertEqual(len(got), 1)
            self.assertIn("broken", got[0])

    def test_sizes_are_read_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "rec_x")
            os.makedirs(sd)
            with open(os.path.join(sd, "N6.mjpeg"), "wb") as f:
                f.write(b"x" * 1234)
            with open(os.path.join(sd, "manifest.json"), "w") as f:
                json.dump({"name": "rec_x", "cameras": [
                    {"label": "N6", "mjpeg": "N6.mjpeg", "mp4": "N6.mp4"}]}, f)
            got = R.load_sessions(d)[0]
            self.assertEqual(got["cameras"][0]["mjpeg_bytes"], 1234)
            self.assertEqual(got["cameras"][0]["mp4_bytes"], 0)

    def test_missing_root_is_empty_not_an_error(self):
        self.assertEqual(R.load_sessions("/nonexistent/dir"), [])


class TestBoardScript(unittest.TestCase):
    def test_cfg_is_prepended_and_parses(self):
        src = R.build_board_script({"framesize": "HD", "quality": 85},
                                   board_src="print(_CFG)\n")
        self.assertTrue(src.startswith("_CFG = "))
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)          # noqa: S102 - fixture
        self.assertEqual(ns["_CFG"]["quality"], 85)

    def test_real_board_script_compiles(self):
        """The file we push to the board must at least be valid Python."""
        with open(os.path.join(_HERE, "record_board.py")) as f:
            compile(f.read(), "record_board.py", "exec")


class TestEncoderPick(unittest.TestCase):
    def test_software_fallback_when_no_device(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: None
        try:
            name, args = T.pick_encoder()
            self.assertEqual(name, "libx264")
            self.assertIn("libx264", args)
        finally:
            T._v4l2_encoder_present = real

    def test_hardware_used_when_device_exists(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: "/dev/video11"
        try:
            self.assertEqual(T.pick_encoder()[0], "h264_v4l2m2m")
        finally:
            T._v4l2_encoder_present = real

    def test_explicit_software_preference_wins(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: "/dev/video11"
        try:
            self.assertEqual(T.pick_encoder(prefer="software")[0], "libx264")
        finally:
            T._v4l2_encoder_present = real


class TestNoCameraPath(unittest.TestCase):
    """A missing camera must fail loudly and locally, not hang or crash.

    This is not hypothetical: on 2026-09-08 the AE3 went off the USB bus
    mid-session, and a recording asking for it had to still record the N6 and
    say plainly what was missing.
    """

    def test_no_cameras_found_reports_and_records_nothing(self):
        real = R.find_boards
        R.find_boards = lambda roles=(), **kw: ({}, ["AE3 did not answer"], {})
        try:
            with tempfile.TemporaryDirectory() as d:
                res = RR.run_recording(root=d, cameras=["AE3"], log=lambda m: None)
            self.assertFalse(res["ok"])
            self.assertIn("no cameras found", res["summary"])
            self.assertTrue(any("did not answer" in w for w in res["warnings"]))
        finally:
            R.find_boards = real

    def test_a_wedged_board_is_a_warning_not_an_exception(self):
        """The bounded probe reports; it must never raise into the caller."""
        real = R.find_boards
        R.find_boards = lambda roles=(), **kw: (
            {}, ["/dev/x did not answer within 25 s -- the board may be wedged"],
            {})
        try:
            with tempfile.TemporaryDirectory() as d:
                res = RR.run_recording(root=d, cameras=["N6"], log=lambda m: None)
            self.assertIn("wedged", " ".join(res["warnings"]))
        finally:
            R.find_boards = real


class TestPagesRender(unittest.TestCase):
    """The pages must actually build.

    They did not, once: a bare "%" inside the page's JavaScript ("% margin")
    was consumed by Python's %-formatting and the index page raised
    "not enough arguments for format string" -- while /healthz and the JSON API
    kept answering 200, so nothing looked wrong until a browser asked for the
    page. Rendering both pages in the suite is the cheap guard.
    """

    CEIL = {"cameras": {"N6": {"cells": {"HD_q85": 34.9},
                               "delivered": {"HD_q85": 23.81},
                               "note": "n"}}}
    SESSION = {
        "name": "rec_20260908T073433", "created_iso": "2026-09-08T07:34:33",
        "host": "nereus000",
        "settings": {"framesize": "HD", "quality": 85, "fps_requested": 30,
                     "duration_s": 5, "cameras": ["N6", "AE3"]},
        "cameras": [
            {"label": "N6", "written_frames": 120, "delivered_fps": 20.05,
             "capture_fps": 23.81, "mb_per_s": 3.94, "seq_gaps": 0,
             "ring_dropped_frames": 0, "mjpeg": "N6.mjpeg", "mp4": "N6.mp4",
             "mjpeg_bytes": 23402568, "mp4_bytes": 6857740,
             "start_offset_s": 0.0, "banner": {"w": 1280, "h": 800}},
            {"label": "AE3", "written_frames": 11, "delivered_fps": 2.1,
             "capture_fps": 2.1, "mb_per_s": 0.4, "seq_gaps": 3,
             "ring_dropped_frames": 0, "mjpeg": "AE3.mjpeg",
             "mjpeg_bytes": 900000, "mp4_bytes": 0,
             "start_offset_s": 0.42, "banner": {"w": 1280, "h": 800}},
        ],
    }

    def test_index_renders(self):
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            page = W.index_page(W.RecorderState(d), [self.SESSION], self.CEIL)
        self.assertIn("Video recorder", page)
        self.assertIn("rec_20260908T073433", page)
        self.assertNotIn("@@", page)          # every token substituted

    def test_index_renders_with_no_recordings_and_no_ceilings(self):
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            page = W.index_page(W.RecorderState(d), [], {})
        self.assertIn("No recordings yet", page)
        self.assertNotIn("@@", page)

    def test_viewer_renders_both_cameras_and_one_scrubber(self):
        import recorder_web as W
        page = W.viewer_page(self.SESSION)
        self.assertNotIn("@@", page)
        self.assertEqual(page.count("<video"), 2)          # both cameras
        self.assertEqual(page.count('id=scrub'), 1)        # ONE scrubber
        self.assertIn("22.3 MB", page)                     # file size shown
        self.assertIn("HD", page)                          # settings shown
        self.assertIn("data-offset='0.42'", page)          # alignment carried

    def test_viewer_flags_a_camera_with_no_mp4(self):
        """A clip the browser cannot play must say so, not fail silently."""
        import recorder_web as W
        page = W.viewer_page(self.SESSION)
        self.assertIn("no mp4", page)


class TestMediaPathSafety(unittest.TestCase):
    """The media route takes user-supplied names; traversal must be refused."""

    def test_safe_regex_rejects_traversal(self):
        import recorder_web as W
        for bad in ("..", ".", "../etc", "a/b", "", "x\x00", "/etc/passwd",
                    ".hidden", "..\\win"):
            self.assertIsNone(W.SAFE.match(bad), "accepted %r" % bad)

    def test_safe_path_serves_real_files_and_refuses_escapes(self):
        """Containment is the gate that must not be wrong, so exercise it."""
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "rec_a"))
            with open(os.path.join(d, "rec_a", "N6.mp4"), "wb") as f:
                f.write(b"x")
            with open(os.path.join(d, "secret.txt"), "w") as f:
                f.write("no")
            H = W.make_handler(W.RecorderState(d), d)
            # _safe_path never touches `self`, so an unbound call is a fair test
            # and avoids standing up a socket server to check a path rule.
            sp = H._safe_path
            self.assertIsNotNone(sp(None, "rec_a", "N6.mp4"))
            self.assertIsNone(sp(None, "..", "secret.txt"))
            self.assertIsNone(sp(None, "rec_a", "missing.mp4"))
            self.assertIsNone(sp(None, "rec_a", "../secret.txt"))

    def test_safe_regex_accepts_normal_names(self):
        import recorder_web as W
        for good in ("rec_20260908T014500", "N6.mp4", "AE3.mjpeg"):
            self.assertIsNotNone(W.SAFE.match(good))


if __name__ == "__main__":
    unittest.main(verbosity=2)
