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


def _clock_over(times):
    """A fake clock that walks `times` then holds the last value.

    Holding rather than raising StopIteration matters: snapshot() also reads
    the clock (for stale_s), and a test should not have to count internal
    clock reads to stay green.
    """
    seq = list(times)
    state = {"i": 0}

    def clock():
        i = min(state["i"], len(seq) - 1)
        state["i"] += 1
        return seq[i]
    return clock


class TestStats(unittest.TestCase):
    def _stats(self, times):
        return H.Stats(clock=_clock_over(times))

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


class TestBandwidth(unittest.TestCase):
    """Bandwidth must come from delivered bytes, never from the q setting."""

    def test_mbps_over_the_window(self):
        # 3 frames at t=0,1,2 -> 2 s span, 2 payloads counted after the first.
        s = H.Stats(clock=_clock_over([0.0, 1.0, 2.0, 2.0]))
        for _ in range(3):
            s.note({"b64": 1000}, 125_000)      # 125 kB = 1 Mbit
        self.assertAlmostEqual(s.mbps(), 1.0, places=6)

    def test_wire_rate_uses_the_base64_size_not_the_jpeg(self):
        s = H.Stats(clock=_clock_over([0.0, 1.0, 1.0]))
        s.note({"b64": 200_000}, 100_000)
        s.note({"b64": 250_000}, 125_000)
        self.assertAlmostEqual(s.mbps(), 1.0, places=6)        # jpeg bytes
        self.assertAlmostEqual(s.wire_mbps(), 2.0, places=6)   # base64 bytes

    def test_zero_before_two_frames(self):
        s = H.Stats(clock=_clock_over([0.0]))
        s.note({"b64": 10}, 10)
        self.assertEqual(s.mbps(), 0.0)
        self.assertEqual(s.wire_mbps(), 0.0)

    def test_kb_per_frame_is_a_mean_of_delivered_payloads(self):
        s = H.Stats(clock=_clock_over([0.0, 1.0, 1.0]))
        s.note({}, 1024)
        s.note({}, 3072)
        self.assertAlmostEqual(s.kb_per_frame(), 2.0)

    def test_window_is_bounded(self):
        s = H.Stats(clock=_clock_over([float(i) for i in range(200)]))
        for _ in range(100):
            s.note({"b64": 10}, 10)
        self.assertLessEqual(len(s._win_bytes), H.Stats.WINDOW)
        self.assertLessEqual(len(s._win_wire), H.Stats.WINDOW)


class TestBannerFields(unittest.TestCase):
    """The #I banner is the provenance record: geometry, q, and which model."""

    BANNER = (b'#I {"board":"OpenMV-AE3 with X","fw":"v5","framesize":"VGA",'
              b'"w":640,"h":400,"pixfmt":"RGB565",'
              b'"model":"/rom/yolov8n_192.tflite","model_bytes":1994976,'
              b'"model_in":"((1, 192, 192, 3),)","model_out":"((1, 5, 756),)",'
              b'"arena":791056,"labels":["person"],"quality":50,"heap":1}\n')

    def _stats_after_banner(self):
        latest, stats = H.Latest(), H.Stats()
        H.reader_loop(_stream(self.BANNER), latest, stats, {"alive": True})
        return stats.snapshot()

    def test_geometry_reaches_the_snapshot(self):
        snap = self._stats_after_banner()
        self.assertEqual((snap["framesize"], snap["w"], snap["h"]),
                         ("VGA", 640, 400))
        self.assertEqual(snap["pixfmt"], "RGB565")

    def test_quality_reaches_the_snapshot(self):
        self.assertEqual(self._stats_after_banner()["quality"], 50)

    def test_model_identity_reaches_the_snapshot(self):
        snap = self._stats_after_banner()
        self.assertEqual(snap["model_bytes"], 1994976)   # settles apples-to-apples
        self.assertIn("192", snap["model_in"])
        self.assertEqual(snap["labels"], ["person"])
        self.assertEqual(snap["arena"], 791056)

    def test_missing_banner_leaves_safe_defaults(self):
        # No banner yet: the page must render, not crash on undefined.
        snap = H.Stats().snapshot()
        self.assertEqual(snap["framesize"], "")
        self.assertIsNone(snap["quality"])
        self.assertEqual(snap["model_bytes"], -1)


class TestStaleness(unittest.TestCase):
    """A frozen stream and a motionless scene look identical on screen.

    These pin the one signal that tells them apart, because this failure has
    now shown up three separate ways in this bite.
    """

    def test_stale_is_none_before_any_frame(self):
        s = H.Stats(clock=lambda: 100.0)
        self.assertIsNone(s.snapshot()["stale_s"])

    def test_stale_grows_after_the_last_frame(self):
        s = H.Stats(clock=_clock_over([100.0, 105.0]))
        s.note({}, 10)                       # frame lands at t=100
        self.assertEqual(s.snapshot()["stale_s"], 5.0)   # snapshot at t=105

    def test_status_is_reported(self):
        s = H.Stats(clock=lambda: 1.0)
        s.status = "board disconnected -- reconnecting"
        self.assertIn("reconnect", s.snapshot()["status"])


class TestFindPort(unittest.TestCase):
    def test_explicit_port_wins(self):
        self.assertEqual(H.find_port("/dev/cu.whatever"), "/dev/cu.whatever")

    def test_missing_board_raises_porterror_not_systemexit(self):
        # PortError is retryable; SystemExit would kill the supervisor.
        import glob
        real = glob.glob
        glob.glob = lambda pat: []
        try:
            with self.assertRaises(H.PortError):
                H.find_port(None)
        finally:
            glob.glob = real

    def test_ambiguous_board_raises_porterror(self):
        import glob
        real = glob.glob
        glob.glob = lambda pat: ["/dev/cu.usbmodem1", "/dev/cu.usbmodem2"]
        try:
            with self.assertRaises(H.PortError):
                H.find_port(None)
        finally:
            glob.glob = real


class TestSupervise(unittest.TestCase):
    def test_retries_while_the_board_is_absent_then_quits(self):
        stats, latest = H.Stats(), H.Latest()
        state = {"quit": False}
        calls = {"n": 0}

        def fake_find(hint):
            calls["n"] += 1
            if calls["n"] >= 3:
                state["quit"] = True
            raise H.PortError("no board")

        real = H.find_port
        H.find_port = fake_find
        try:
            H.supervise(None, "src", latest, stats, state, retry_s=0)
        finally:
            H.find_port = real
        self.assertGreaterEqual(calls["n"], 3)
        self.assertIn("waiting for board", stats.status)


class _FakeSerial:
    """Feeds canned chunks, then behaves like an idle-but-open port."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.is_open = True

    def read(self, _n):
        return self._chunks.pop(0) if self._chunks else b""


def _board_over(chunks):
    """A SerialBoard wired to a fake port, bypassing __init__'s real open."""
    b = H.SerialBoard.__new__(H.SerialBoard)
    b.ser = _FakeSerial(chunks)
    b._buf = bytearray()
    return b


class TestSerialBoardReadline(unittest.TestCase):
    def test_reads_plain_lines(self):
        b = _board_over([b"one\ntwo\n"])
        self.assertEqual(b.readline(), b"one\n")
        self.assertEqual(b.readline(), b"two\n")

    def test_eot_without_trailing_newline_ends_the_stream(self):
        # The raw REPL ends execution with 0x04 and NO newline. Missing this
        # blocked the reader forever and stopped the supervisor reconnecting.
        b = _board_over([b'#D {"frames":2}\n', b"\x04\x04>"])
        self.assertEqual(b.readline(), b'#D {"frames":2}\n')
        self.assertEqual(b.readline(), b"")

    def test_eot_at_buffer_start_ends(self):
        b = _board_over([b"\x04junk\n"])
        self.assertEqual(b.readline(), b"")

    def test_stray_eot_mid_line_does_NOT_end_the_stream(self):
        # base64 and the JSON headers contain no 0x04, so one deeper in a
        # line is corruption. Treating it as end-of-stream tore down a
        # healthy stream over a single bad byte.
        b = _board_over([b"abc\x04def\n", b"next\n"])
        self.assertEqual(b.readline(), b"abc\x04def\n")
        self.assertEqual(b.readline(), b"next\n")

    def test_binary_payload_bytes_survive(self):
        b = _board_over([b"\xff\xd8\xff\xe0 payload\n"])
        self.assertEqual(b.readline(), b"\xff\xd8\xff\xe0 payload\n")


class TestBoardSpecs(unittest.TestCase):
    def test_parses_in_order(self):
        vs = H.parse_board_specs(["AE3=/dev/ttyACM0", "N6=/dev/ttyACM1"])
        self.assertEqual([(v.label, v.port) for v in vs],
                         [("AE3", "/dev/ttyACM0"), ("N6", "/dev/ttyACM1")])

    def test_order_is_left_to_right(self):
        vs = H.parse_board_specs(["L=/dev/a", "R=/dev/b"])
        self.assertEqual(vs[0].label, "L")   # panel order is argument order

    def test_rejects_missing_equals(self):
        with self.assertRaises(SystemExit):
            H.parse_board_specs(["noequals"])

    def test_rejects_empty_label_or_port(self):
        for bad in (["=/dev/x"], ["A="]):
            with self.assertRaises(SystemExit):
                H.parse_board_specs(bad)

    def test_rejects_the_same_port_twice(self):
        # Two panels on one device would silently show the same board.
        with self.assertRaises(SystemExit):
            H.parse_board_specs(["A=/dev/x", "B=/dev/x"])

    def test_views_are_independent(self):
        a, b = H.parse_board_specs(["A=/dev/x", "B=/dev/y"])
        a.stats.note({"inf_us": 1000}, 10)
        self.assertEqual(a.stats.frames, 1)
        self.assertEqual(b.stats.frames, 0)     # one board cannot skew another
        a.latest.put(1, b"\xff\xd8x")
        self.assertEqual(b.latest.get(), (b"", -1))


class TestSupervisorSurvivesAttachErrors(unittest.TestCase):
    """A supervisor that can die is not a supervisor.

    mpremote raises TransportError, which is NOT an OSError. An
    OSError-only clause let it escape and kill the thread outright: on
    nereus000 the N6's panel then sat on its last frame forever while the
    AE3 beside it streamed happily. Any attach failure must back off and
    retry.
    """

    def _run_with_failing_attach(self, exc):
        state = {"quit": False}
        calls = {"n": 0}

        def boom(port, baudrate=115200):
            calls["n"] += 1
            if calls["n"] >= 3:
                state["quit"] = True
            raise exc

        real = H.SerialBoard
        H.SerialBoard = boom
        try:
            stats = H.Stats()
            H.supervise("/dev/x", "src", H.Latest(), stats, state, retry_s=0)
        finally:
            H.SerialBoard = real
        return calls["n"], stats

    def test_survives_a_non_oserror_transport_failure(self):
        class TransportError(Exception):
            pass
        n, stats = self._run_with_failing_attach(TransportError("failed to access"))
        self.assertGreaterEqual(n, 3)          # retried, did not die
        self.assertIn("not answering", stats.status)

    def test_survives_an_oserror_too(self):
        n, _ = self._run_with_failing_attach(OSError("device busy"))
        self.assertGreaterEqual(n, 3)


class TestMultiPage(unittest.TestCase):
    def test_one_panel_and_route_per_board(self):
        vs = H.parse_board_specs(["AE3=/dev/a", "N6=/dev/b"])
        page = H.multi_page(vs)
        for token in ('id="t0"', 'id="t1"', "/s/0/stream", "/s/1/stream",
                      "const N=2", "AE3", "N6"):
            self.assertIn(token, page)

    def test_single_board_still_renders(self):
        page = H.multi_page([H.BoardView("solo", "/dev/a")])
        self.assertIn("/s/0/stream", page)
        self.assertNotIn("/s/1/stream", page)

    def test_snapshot_carries_label_and_port(self):
        v = H.BoardView("AE3", "/dev/ttyACM0")
        snap = v.snapshot()
        self.assertEqual(snap["label"], "AE3")
        self.assertEqual(snap["port"], "/dev/ttyACM0")


class TestPortCandidates(unittest.TestCase):
    def test_linux_prefers_by_id(self):
        # by-id encodes the USB serial; ttyACM numbering is assignment-order
        # and swaps between boots. Getting this wrong is how a table gets
        # attributed to the wrong board.
        for pat in H.PORT_GLOBS["linux"]:
            self.assertIn("/dev/serial/by-id/", pat)

    def test_linux_matches_both_product_strings(self):
        # The AE3 enumerates as "OpenMV Camera", the N6 as a MicroPython
        # Pyboard VCP -- measured on nereus000. Both must be found.
        pats = H.PORT_GLOBS["linux"]
        self.assertTrue(any("OpenMV" in p for p in pats))
        self.assertTrue(any("MicroPython" in p for p in pats))


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
