#!/usr/bin/env python3
"""Host tests for the N6 stream helpers (S24 bite 1, S8 bite A). No board.

    python3 bench/test_n6_stream_helpers.py

Covers the pure pieces: the LAB threshold suggester, the stats arithmetic, and
the frame reader's framing/rejection paths -- the parts that decide whether a
malformed stream is surfaced or silently swallowed. S8 bite A adds the
multi-colour threshold parsing, the board's per-class counting (loaded out of
the board script with its MicroPython imports stubbed), and the frame saver.
"""

import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import n6_stream_host as H  # noqa: E402

BOARD_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "n6_stream_board.py")


def load_board_helpers():
    """Exec the board script on the host with its board-only bits removed.

    The script imports ``csi`` and calls ``main()`` at import time, neither of
    which exists on a Mac. Stubbing the one missing module and dropping the
    trailing call gets the pure helpers under test -- and ``classify_blobs``
    owns the per-class counts bite C compares against ground truth, which is
    not logic to leave covered only by pointing a camera at a wall.
    """
    src = open(BOARD_SRC).read()
    assert src.rstrip().endswith("main()"), "board script no longer ends in main()"
    src = src.rstrip()[:-len("main()")]
    sys.modules.setdefault("csi", types.ModuleType("csi"))
    mod = types.ModuleType("n6_stream_board_helpers")
    exec(compile(src, BOARD_SRC, "exec"), mod.__dict__)
    return mod


B = load_board_helpers()


class FakeBlob:
    """The blob attributes this code touches -- fields, not methods (S24)."""

    def __init__(self, x=0, y=0, w=10, h=10, pixels=100, code=1):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.pixels, self.code = pixels, code
        self.rect = (x, y, w, h)
        self.cx, self.cy = x + w // 2, y + h // 2


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

    def test_a_bounded_run_ends_instead_of_being_restarted(self):
        # --max-frames finishes with #D. Reconnecting there would restart the
        # board forever and make a bounded comparison run impossible to script.
        stats, latest = H.Stats(), H.Latest()
        state = {"quit": False}
        opened = {"n": 0}
        shutdown = {"n": 0}

        class FakeBoard:
            def __init__(self, port):
                opened["n"] += 1

            def start(self, text):
                return self

            def readline(self):
                return b""

            def stop(self):
                pass

        def fake_reader(out, latest_, stats_, state_, saver=None):
            state_["done"] = True

        real_board, real_reader, real_find = (H.SerialBoard, H.reader_loop,
                                              H.find_port)
        H.SerialBoard, H.reader_loop = FakeBoard, fake_reader
        H.find_port = lambda hint: "/dev/fake"
        try:
            H.supervise(None, "src", latest, stats, state, retry_s=0,
                        on_done=lambda: shutdown.__setitem__("n", 1))
        finally:
            H.SerialBoard, H.reader_loop, H.find_port = (real_board, real_reader,
                                                         real_find)
        self.assertEqual(opened["n"], 1)          # opened once, never retried
        self.assertEqual(shutdown["n"], 1)        # the HTTP server was stopped
        self.assertTrue(state["quit"])
        self.assertEqual(stats.reconnects, 0)


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
        self.assertEqual(H.cfg_from_args(args)["blob_classes"],
                         [("blob", (1, 2, 3, 4, -5, -6))])

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


class TestParseBlobClass(unittest.TestCase):
    def test_named_box(self):
        self.assertEqual(H.parse_blob_class("pink:20,70,10,50,-20,25", "blob", 0),
                         ("pink", (20, 70, 10, 50, -20, 25)))

    def test_bare_box_takes_the_blob_label(self):
        self.assertEqual(H.parse_blob_class("1,2,3,4,-5,-6", "ball", 0),
                         ("ball", (1, 2, 3, 4, -5, -6)))

    def test_bare_boxes_stay_distinguishable(self):
        # Two unnamed boxes must not both be called "ball" -- the per-class
        # counts would be unreadable and the dup check would reject the run.
        self.assertEqual(H.parse_blob_class("1,2,3,4,5,6", "ball", 1)[0], "ball2")

    def test_negative_bounds_survive(self):
        self.assertEqual(H.parse_blob_class("purple:10,80,10,65,-75,-10",
                                            "blob", 0)[1][-2:], (-75, -10))

    def test_spaces_are_tolerated(self):
        self.assertEqual(H.parse_blob_class("p: 1, 2,3,4,5,6", "blob", 0)[1],
                         (1, 2, 3, 4, 5, 6))

    def test_wrong_arity_names_the_argument_and_the_count(self):
        with self.assertRaises(ValueError) as cm:
            H.parse_blob_class("pink:1,2,3", "blob", 0)
        self.assertIn("needs 6", str(cm.exception))
        self.assertIn("got 3", str(cm.exception))

    def test_non_integer_bound_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            H.parse_blob_class("pink:1,2,3,4,5,six", "blob", 0)
        self.assertIn("non-integer", str(cm.exception))


class TestBlobClassesFromArgs(unittest.TestCase):
    def test_no_thresholds_leaves_the_board_default(self):
        self.assertEqual(H.blob_classes_from_args(H.parse_args([])), [])
        self.assertNotIn("blob_classes", H.cfg_from_args(H.parse_args([])))

    def test_two_colours_keep_their_order(self):
        args = H.parse_args(["--blob-thresh", "pink:1,2,3,4,5,6",
                             "--blob-thresh", "purple:7,8,9,10,11,12"])
        self.assertEqual([c[0] for c in H.blob_classes_from_args(args)],
                         ["pink", "purple"])

    def test_duplicate_names_are_rejected(self):
        args = H.parse_args(["--blob-thresh", "pink:1,2,3,4,5,6",
                             "--blob-thresh", "pink:7,8,9,10,11,12"])
        with self.assertRaises(ValueError) as cm:
            H.blob_classes_from_args(args)
        self.assertIn("duplicate", str(cm.exception))

    def test_bad_threshold_exits_cleanly_from_cfg(self):
        # The user must see a message, not a traceback.
        with self.assertRaises(SystemExit):
            H.cfg_from_args(H.parse_args(["--blob-thresh", "pink:1,2"]))

    def test_labels_fall_back_to_the_blob_label(self):
        self.assertEqual(H.class_labels(H.parse_args(["--blob-label", "ball"])),
                         ["ball"])
        self.assertEqual(H.class_labels(H.parse_args(
            ["--blob-thresh", "pink:1,2,3,4,5,6"])), ["pink"])

    def test_scan_mode_reaches_the_board(self):
        cfg = H.cfg_from_args(H.parse_args(["--blob-scan", "per-class"]))
        self.assertEqual(cfg["blob_scan"], "per-class")
        self.assertEqual(H.cfg_from_args(H.parse_args([]))["blob_scan"], "codes")

    def test_saving_turns_the_overlay_off(self):
        # A training image with boxes drawn into it is not a training image.
        self.assertFalse(H.cfg_from_args(
            H.parse_args(["--save-frames", "/tmp/nowhere"]))["overlay"])
        self.assertTrue(H.cfg_from_args(H.parse_args([]))["overlay"])


class TestBoardClassify(unittest.TestCase):
    def test_single_class_counts(self):
        rows, counts, amb = B.classify_blobs(
            [(FakeBlob(code=1), 1), (FakeBlob(code=1), 1)], 1)
        self.assertEqual((counts, amb, len(rows)), ([2], 0, 2))

    def test_two_classes_are_counted_apart(self):
        recs = [(FakeBlob(), 1), (FakeBlob(), 2), (FakeBlob(), 2)]
        _, counts, amb = B.classify_blobs(recs, 2)
        self.assertEqual((counts, amb), ([1, 2], 0))

    def test_multi_class_blob_is_counted_once_and_flagged(self):
        # merge=True can join two colours into one blob. Counting it into both
        # would inflate exactly the number bite C checks against ground truth.
        _, counts, amb = B.classify_blobs([(FakeBlob(), 0b11)], 2)
        self.assertEqual((counts, amb), ([1, 0], 1))

    def test_blob_matching_nothing_is_dropped_not_mislabelled(self):
        rows, counts, amb = B.classify_blobs([(FakeBlob(), 0)], 2)
        self.assertEqual((rows, counts, amb), ([], [0, 0], 0))

    def test_rows_carry_the_index_within_the_class(self):
        rows, _, _ = B.classify_blobs(
            [(FakeBlob(), 1), (FakeBlob(), 2), (FakeBlob(), 1)], 2)
        self.assertEqual([(r[0], r[3]) for r in rows], [(0, 1), (1, 1), (0, 2)])

    def test_bits_above_the_class_count_are_ignored(self):
        _, counts, amb = B.classify_blobs([(FakeBlob(), 0b100)], 2)
        self.assertEqual((counts, amb), ([0, 0], 0))


class TestBoardBoxes(unittest.TestCase):
    def test_box_carries_class_then_geometry(self):
        rows, _, _ = B.classify_blobs([(FakeBlob(4, 5, 6, 7, 88), 2)], 2)
        self.assertEqual(B.box_list(rows, 32), [(1, 4, 5, 6, 7, 88)])

    def test_boxes_are_capped(self):
        rows, _, _ = B.classify_blobs([(FakeBlob(), 1)] * 10, 1)
        self.assertEqual(len(B.box_list(rows, 4)), 4)

    def test_palette_cycles_and_differs_per_class(self):
        self.assertNotEqual(B.class_colour(0), B.class_colour(1))
        self.assertEqual(B.class_colour(0), B.class_colour(len(B.PALETTE)))

    def test_blob_code_accepts_attribute_or_method(self):
        self.assertEqual(B.blob_code(FakeBlob(code=3)), 3)

        class Callable:
            code = staticmethod(lambda: 5)
        self.assertEqual(B.blob_code(Callable()), 5)

    def test_json_serialisers_emit_valid_json(self):
        self.assertEqual(json.loads(B._json_ints([1, -2, 3])), [1, -2, 3])
        self.assertEqual(json.loads(B._json_boxes([(0, 1, 2, 3, 4, 5)])),
                         [[0, 1, 2, 3, 4, 5]])


class TestBoardDetections(unittest.TestCase):
    class FakeImg:
        def __init__(self):
            self.calls = 0

        def draw_rectangle(self, *a, **k):
            self.calls += 1

        def draw_string(self, *a, **k):
            self.calls += 1

    OUT = [[((1, 2, 3, 4), 0.9), ((5, 6, 7, 8), 0.8)]]

    def test_detections_are_counted_with_the_overlay_off(self):
        # A capture run turns the overlay off to keep training frames clean.
        # Fusing the count into the drawing made `det` read 0 during capture.
        img = self.FakeImg()
        self.assertEqual(B.draw_detections(img, self.OUT, ["person"],
                                           draw=False), 2)
        self.assertEqual(img.calls, 0)

    def test_detections_are_drawn_when_the_overlay_is_on(self):
        img = self.FakeImg()
        self.assertEqual(B.draw_detections(img, self.OUT, ["person"],
                                           draw=True), 2)
        self.assertGreater(img.calls, 0)


class TestPerClassStats(unittest.TestCase):
    def test_counts_and_labels_reach_the_snapshot(self):
        st = H.Stats(labels=["pink", "purple"])
        st.note({"blobs": 5, "bc": [3, 2], "amb": 1}, 100)
        snap = st.snapshot()
        self.assertEqual(snap["blob_counts"], [3, 2])
        self.assertEqual(snap["blob_labels"], ["pink", "purple"])
        self.assertEqual(snap["amb"], 1)

    def test_older_board_without_counts_does_not_crash(self):
        st = H.Stats(labels=["pink"])
        st.note({"blobs": 2}, 100)
        self.assertEqual(st.snapshot()["blob_counts"], [])


class TestFrameSaver(unittest.TestCase):
    def test_saves_one_in_every_n(self):
        with tempfile.TemporaryDirectory() as d:
            saver = H.FrameSaver(d, every=3, labels=["pink"])
            written = [saver.put({"seq": i, "bb": []}, b"\xff\xd8jpg")
                       for i in range(7)]
            self.assertEqual([w is not None for w in written],
                             [True, False, False, True, False, False, True])
            self.assertEqual(saver.saved, 3)
            self.assertEqual(sorted(os.listdir(d)),
                             ["frame_000000.jpg", "frame_000001.jpg",
                              "frame_000002.jpg", "index.jsonl"])

    def test_filenames_are_monotonic_across_a_reconnect(self):
        # The board's seq restarts at 0 on reconnect; naming by seq would
        # overwrite the frames captured before the replug.
        with tempfile.TemporaryDirectory() as d:
            saver = H.FrameSaver(d, every=1)
            saver.put({"seq": 41}, b"a")
            saver.put({"seq": 0}, b"b")          # board came back
            self.assertEqual(sorted(os.listdir(d))[:2],
                             ["frame_000000.jpg", "frame_000001.jpg"])
            self.assertEqual(open(os.path.join(d, "frame_000001.jpg"), "rb")
                             .read(), b"b")

    def test_index_carries_the_boxes_that_made_the_frame(self):
        with tempfile.TemporaryDirectory() as d:
            saver = H.FrameSaver(d, every=1, labels=["pink", "purple"])
            saver.put({"seq": 9, "w": 640, "h": 400,
                       "bb": [[0, 1, 2, 3, 4, 55]], "bc": [1, 0], "amb": 0},
                      b"\xff\xd8")
            rec = json.loads(open(os.path.join(d, "index.jsonl")).read())
        self.assertEqual(rec["file"], "frame_000000.jpg")
        self.assertEqual(rec["boxes"], [[0, 1, 2, 3, 4, 55]])
        self.assertEqual(rec["classes"], ["pink", "purple"])
        self.assertEqual((rec["w"], rec["h"], rec["seq"]), (640, 400, 9))

    def test_the_jpeg_written_is_the_jpeg_received(self):
        # Rule 4: the artifact, not the return code.
        with tempfile.TemporaryDirectory() as d:
            saver = H.FrameSaver(d, every=1)
            saver.put({"seq": 0}, JPEG)
            self.assertEqual(open(os.path.join(d, "frame_000000.jpg"), "rb")
                             .read(), JPEG)


class TestReaderLoopSaves(unittest.TestCase):
    def test_frames_reach_the_saver_and_the_count_is_visible(self):
        with tempfile.TemporaryDirectory() as d:
            saver = H.FrameSaver(d, every=1, labels=["pink"])
            latest, stats = H.Latest(), H.Stats(labels=["pink"])
            H.reader_loop(_stream(_frame_bytes(0, JPEG) + _frame_bytes(1, JPEG)),
                          latest, stats, {"alive": True}, saver)
            self.assertEqual(saver.saved, 2)
            self.assertEqual(stats.snapshot()["saved"], 2)

    def test_a_failing_save_is_surfaced_not_swallowed(self):
        class Full(H.FrameSaver):
            def put(self, hdr, jpg):
                raise OSError("No space left on device")
        with tempfile.TemporaryDirectory() as d:
            latest, stats = H.Latest(), H.Stats()
            H.reader_loop(_stream(_frame_bytes(0, JPEG)), latest, stats,
                          {"alive": True}, Full(d))
            self.assertEqual(stats.frames, 1)        # the stream survives
            self.assertTrue(any(b"save failed" in j for j in stats.junk))


class TestLabBoxOverlap(unittest.TestCase):
    """The guard for the one configuration that silently under-counts.

    Measured on the N6 (nibble 3): in a single find_blobs call over a list,
    each pixel goes to the FIRST matching threshold, so an earlier box that
    overlaps a later one takes the shared pixels and the later one can report
    zero -- with `amb` staying 0, because only one code bit is ever set.
    """

    WIDE = (0, 100, -128, 127, -128, 127)
    BRIGHT = (50, 100, -128, 127, -128, 127)      # strict subset of WIDE

    def test_a_nested_box_is_fully_shadowed(self):
        self.assertEqual(H.lab_overlap_fraction(self.WIDE, self.BRIGHT), 1.0)

    def test_disjoint_boxes_do_not_overlap(self):
        pink = (20, 70, 10, 50, 0, 25)
        purple = (10, 80, 10, 65, -75, -10)        # b ranges do not meet
        self.assertEqual(H.lab_overlap_fraction(pink, purple), 0.0)
        self.assertEqual(H.shadowed_pairs([("pink", pink),
                                           ("purple", purple)]), [])

    def test_partial_overlap_is_quantified(self):
        # L overlaps on 5..10 (6 of 11 values); A and B match exactly.
        earlier = (0, 10, 0, 10, 0, 10)
        later = (5, 15, 0, 10, 0, 10)
        self.assertAlmostEqual(H.lab_overlap_fraction(earlier, later),
                               (6 * 11 * 11) / (11 * 11 * 11))

    def test_boxes_touching_on_one_plane_still_share_pixels(self):
        # Ranges are inclusive, so L=10 satisfies both -- a real overlap.
        self.assertGreater(H.lab_overlap_fraction((0, 10, 0, 10, 0, 10),
                                                  (10, 20, 0, 10, 0, 10)), 0)

    def test_the_pair_is_reported_in_list_order(self):
        # Order is what decides the winner, so the guard must say which box
        # is doing the shadowing, not just that two of them overlap.
        pairs = H.shadowed_pairs([("wide", self.WIDE), ("bright", self.BRIGHT)])
        self.assertEqual([(a, b) for a, b, _ in pairs], [("wide", "bright")])

    def test_every_pair_is_checked(self):
        box = (0, 100, -128, 127, -128, 127)
        pairs = H.shadowed_pairs([("a", box), ("b", box), ("c", box)])
        self.assertEqual([(a, b) for a, b, _ in pairs],
                         [("a", "b"), ("a", "c"), ("b", "c")])

    def test_the_documented_example_thresholds_overlap(self):
        # The pink/purple pair used in the help text and docs overlaps in `b`
        # by ~9%. Pinned as a test because it is the exact configuration a
        # reader would copy, and it under-counts under the default scan.
        pink = (20, 70, 10, 50, -20, 25)
        purple = (10, 80, 10, 65, -75, -10)
        pairs = H.shadowed_pairs([("pink", pink), ("purple", purple)])
        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(pairs[0][2], 0.0877, places=3)


class TestPerBoardThresholds(unittest.TestCase):
    """Two sensors, two threshold sets -- measured, not speculative.

    The N6's blue cast puts its pink balls ~10 LAB units lower in b than the
    AE3's, so one shared box gave 5 blobs on one board and 18 on the other.
    """

    def test_parses_label_name_and_values(self):
        self.assertEqual(H.parse_board_thresh("AE3:pink:32,75,14,32,-16,6"),
                         ("AE3", ("pink", (32, 75, 14, 32, -16, 6))))

    def test_rejects_a_spec_without_a_board(self):
        with self.assertRaises(ValueError):
            H.parse_board_thresh("pink:1,2,3,4,5,6")

    def test_unknown_board_label_is_rejected_not_ignored(self):
        # A threshold that silently applies to nothing looks exactly like a
        # threshold that does not work.
        args = H.parse_args(["--board-thresh", "TYPO:pink:1,2,3,4,5,6"])
        with self.assertRaises(ValueError) as cm:
            H.board_thresh_map(args, ["AE3", "N6"])
        self.assertIn("TYPO", str(cm.exception))

    def test_duplicate_colour_for_one_board_is_rejected(self):
        args = H.parse_args(["--board-thresh", "AE3:pink:1,2,3,4,5,6",
                             "--board-thresh", "AE3:pink:7,8,9,10,11,12"])
        with self.assertRaises(ValueError):
            H.board_thresh_map(args, ["AE3"])

    def test_each_board_keeps_its_own_boxes(self):
        args = H.parse_args(["--board-thresh", "AE3:pink:32,75,14,32,-16,6",
                             "--board-thresh", "N6:pink:30,80,26,50,-30,-2"])
        m = H.board_thresh_map(args, ["AE3", "N6"])
        self.assertEqual(m["AE3"][0][1], (32, 75, 14, 32, -16, 6))
        self.assertEqual(m["N6"][0][1], (30, 80, 26, 50, -30, -2))

    def test_cfg_takes_an_explicit_class_list(self):
        # This is what makes per-board board scripts possible: each view
        # renders its own _CFG rather than sharing one.
        args = H.parse_args([])
        cfg = H.cfg_from_args(args, [("pink", (1, 2, 3, 4, 5, 6))])
        self.assertEqual(cfg["blob_classes"], [("pink", (1, 2, 3, 4, 5, 6))])

    def test_two_boards_produce_different_scripts(self):
        args = H.parse_args([])
        a = H.build_board_script_text(H.cfg_from_args(
            args, [("pink", (32, 75, 14, 32, -16, 6))]))
        b = H.build_board_script_text(H.cfg_from_args(
            args, [("pink", (30, 80, 26, 50, -30, -2))]))
        self.assertNotEqual(a, b)
        self.assertIn("-16", a.split("\n")[0])
        self.assertIn("-30", b.split("\n")[0])


class TestPerBoardPixels(unittest.TestCase):
    def test_parses_and_applies(self):
        args = H.parse_args(["--board-pixels", "AE3:60"])
        self.assertEqual(H.board_pixels_map(args, ["AE3"]), {"AE3": 60})
        cfg = H.cfg_from_args(args, None, pixels=60)
        self.assertEqual((cfg["blob_pixels"], cfg["blob_area"]), (60, 60))

    def test_unknown_board_is_rejected(self):
        args = H.parse_args(["--board-pixels", "NOPE:60"])
        with self.assertRaises(ValueError):
            H.board_pixels_map(args, ["AE3"])

    def test_non_numeric_is_rejected(self):
        args = H.parse_args(["--board-pixels", "AE3:lots"])
        with self.assertRaises(ValueError):
            H.board_pixels_map(args, ["AE3"])

    def test_default_falls_back_to_the_global_flag(self):
        args = H.parse_args(["--blob-pixels", "150"])
        self.assertEqual(H.cfg_from_args(args)["blob_pixels"], 150)


class TestOverlayToggle(unittest.TestCase):
    """The overlay is drawn ON THE BOARD, so a live toggle rebuilds its script."""

    def test_cfg_overlay_can_be_overridden(self):
        args = H.parse_args([])
        self.assertTrue(H.cfg_from_args(args, None, overlay=True)["overlay"])
        self.assertFalse(H.cfg_from_args(args, None, overlay=False)["overlay"])

    def test_set_overlay_rebuilds_the_script(self):
        args = H.parse_args([])
        v = H.BoardView("AE3", "/dev/x")
        v.make_script = lambda on: H.build_board_script_text(
            H.cfg_from_args(args, None, overlay=on))
        v.set_overlay(False)
        self.assertFalse(v.overlay)
        self.assertIn("'overlay': False", v.script_text.split("\n")[0])
        v.set_overlay(True)
        self.assertIn("'overlay': True", v.script_text.split("\n")[0])

    def test_counts_survive_with_the_overlay_off(self):
        # Turning the picture clean must not turn the numbers off -- that is
        # the whole point of the toggle.
        cfg = H.cfg_from_args(H.parse_args([]), None, overlay=False)
        self.assertFalse(cfg["overlay"])
        self.assertTrue(cfg["blobs"])

    def test_supervise_accepts_a_script_callable(self):
        # The supervisor must re-read the script on each attach, or a toggle
        # would only take effect after a manual restart.
        seen = []

        class FakeBoard:
            def __init__(self, port):
                pass

            def start(self, text):
                seen.append(text)
                return self

            def readline(self):
                return b""

            def stop(self):
                pass

        stats, latest = H.Stats(), H.Latest()
        state = {"quit": False}
        texts = iter(["FIRST", "SECOND"])
        real_b, real_r, real_f = H.SerialBoard, H.reader_loop, H.find_port
        H.SerialBoard = FakeBoard
        H.find_port = lambda hint: "/dev/fake"

        def fake_reader(out, l, st, s_, saver=None):
            if len(seen) >= 2:
                s_["quit"] = True

        H.reader_loop = fake_reader
        try:
            H.supervise(None, lambda: next(texts), latest, stats, state,
                        retry_s=0, settle_s=0)
        finally:
            H.SerialBoard, H.reader_loop, H.find_port = real_b, real_r, real_f
        self.assertEqual(seen, ["FIRST", "SECOND"])


class TestMergedTwoBoardSeams(unittest.TestCase):
    """The seams where bite A meets the side-by-side viewer.

    Neither side's suite covered these, because neither side had both halves.
    """

    def test_blob_labels_reach_every_board(self):
        views = H.parse_board_specs(["AE3=/dev/a", "N6=/dev/b"],
                                    ["pink", "purple"])
        self.assertEqual([v.stats.labels for v in views],
                         [["pink", "purple"], ["pink", "purple"]])

    def test_each_board_reports_its_own_per_class_counts(self):
        views = H.parse_board_specs(["AE3=/dev/a", "N6=/dev/b"],
                                    ["pink", "purple"])
        views[0].stats.note({"blobs": 5, "bc": [3, 2], "amb": 0}, 10)
        views[1].stats.note({"blobs": 4, "bc": [2, 2], "amb": 1}, 10)
        snaps = [v.snapshot() for v in views]
        self.assertEqual([s["blob_counts"] for s in snaps], [[3, 2], [2, 2]])
        self.assertEqual([s["amb"] for s in snaps], [0, 1])
        self.assertEqual([s["label"] for s in snaps], ["AE3", "N6"])

    def test_the_page_shows_the_per_class_readout(self):
        views = H.parse_board_specs(["AE3=/dev/a"], ["pink"])
        page = H.multi_page(views)
        self.assertIn("blob_labels", page)
        self.assertIn("ambiguous", page)

    def test_two_boards_capture_into_separate_directories(self):
        # Both boards number frames from 0; one shared directory would mean
        # the second board silently overwrites the first board's dataset.
        with tempfile.TemporaryDirectory() as d:
            views = H.parse_board_specs(["AE3=/dev/a", "N6=/dev/b"], ["pink"])
            for v in views:
                v.saver = H.FrameSaver(os.path.join(d, v.label), 1, ["pink"])
                v.saver.put({"seq": 0, "bb": []}, b"\xff\xd8" + v.label.encode())
            self.assertEqual(sorted(os.listdir(d)), ["AE3", "N6"])
            for v in views:
                path = os.path.join(d, v.label, "frame_000000.jpg")
                self.assertEqual(open(path, "rb").read(),
                                 b"\xff\xd8" + v.label.encode())

    def test_supervise_takes_saver_and_on_done_as_keywords(self):
        # The merge changed this signature from both sides at once; positional
        # threading of `saver` into the backoff slot would be silent.
        import inspect
        params = inspect.signature(H.supervise).parameters
        for name in ("saver", "on_done", "backoff", "retry_s"):
            self.assertIn(name, params)
        self.assertIsNone(params["saver"].default)
        self.assertIsNone(params["on_done"].default)

    def test_a_bounded_run_waits_for_every_board(self):
        # The faster board must not tear the viewer down while the slower one
        # is still running -- that truncates the row being measured.
        views = H.parse_board_specs(["AE3=/dev/a", "N6=/dev/b"], ["pink"])
        shutdowns = []
        finished, lock = set(), __import__("threading").Lock()

        def _finished(label):
            with lock:
                finished.add(label)
                if len(finished) == len(views):
                    shutdowns.append(True)

        _finished("AE3")
        self.assertEqual(shutdowns, [])          # one board done: keep serving
        _finished("N6")
        self.assertEqual(shutdowns, [True])      # both done: shut down


class TestFomoDecode(unittest.TestCase):
    """S8 B2: the board's FOMO grid decode, against hand-built logit grids.

    Mirrors ml/fomo/train.py's decode; a bug here miscounts every demo frame.
    """

    @staticmethod
    def grid(gh=6, gw=6):
        # background logit 5.0 everywhere -- nothing fires by default
        return [[[5.0, 0.0, 0.0] for _ in range(gw)] for _ in range(gh)]

    def test_empty_grid_counts_nothing(self):
        counts, boxes = B.fomo_decode(self.grid(), 3, 0.69)
        self.assertEqual(counts, [0, 0])
        self.assertEqual(boxes, [])

    @staticmethod
    def conf_pct(logits):
        """Winner softmax as int percent -- the reference for the box field."""
        import math
        best = max(logits)
        return int(100.0 / sum(math.exp(v - best) for v in logits) + 0.5)

    def test_single_cell_single_count(self):
        g = self.grid()
        g[2][3] = [0.0, 5.0, 0.0]              # pink wins by 5 > margin
        counts, boxes = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [1, 0])
        self.assertEqual(boxes, [(0, 3, 2, 1, 1, self.conf_pct([0.0, 5.0, 0.0]))])

    def test_adjacent_same_class_cells_group_to_one(self):
        g = self.grid()
        g[2][3] = [0.0, 5.0, 0.0]
        g[2][4] = [0.0, 5.0, 0.0]
        g[3][3] = [0.0, 5.0, 0.0]
        counts, boxes = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [1, 0])
        # extent 2x2 cells
        self.assertEqual(boxes, [(0, 3, 2, 2, 2, self.conf_pct([0.0, 5.0, 0.0]))])

    def test_diagonal_cells_stay_separate(self):
        g = self.grid()
        g[1][1] = [0.0, 0.0, 5.0]
        g[2][2] = [0.0, 0.0, 5.0]              # 4-connectivity: no diagonal
        counts, _ = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [0, 2])

    def test_adjacent_different_class_cells_stay_separate(self):
        g = self.grid()
        g[2][3] = [0.0, 5.0, 0.0]
        g[2][4] = [0.0, 0.0, 5.0]
        counts, _ = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [1, 1])

    def test_margin_is_respected(self):
        g = self.grid()
        g[2][3] = [0.0, 0.5, 0.0]              # beats bg by only 0.5 < 0.69
        counts, _ = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [0, 0])

    def test_background_never_counts_however_confident(self):
        g = self.grid()
        g[0][0] = [50.0, 0.0, 0.0]
        counts, _ = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [0, 0])


class TestFomoConfidence(unittest.TestCase):
    """D2: per-detection confidence on the decoded boxes.

    The bite's verifiable is physical (a half-out-of-threshold ball reads
    lower than a centred one); its decode-level analogue is that weaker
    winning logits produce a lower conf, which is what these pin.
    """

    grid = staticmethod(TestFomoDecode.grid)
    conf_pct = staticmethod(TestFomoDecode.conf_pct)

    def test_weak_margin_scores_below_strong(self):
        g = self.grid()
        g[1][1] = [0.0, 5.0, 0.0]              # decisive cell
        g[4][4] = [0.0, 1.0, 0.0]              # barely past the 0.69 margin
        counts, boxes = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [2, 0])
        by_pos = {(b[1], b[2]): b[5] for b in boxes}
        self.assertGreater(by_pos[(1, 1)], by_pos[(4, 4)])

    def test_group_conf_is_the_peak_cell(self):
        g = self.grid()
        g[2][3] = [0.0, 5.0, 0.0]
        g[2][4] = [0.0, 1.0, 0.0]              # same group, weaker cell
        counts, boxes = B.fomo_decode(g, 3, 0.69)
        self.assertEqual(counts, [1, 0])
        self.assertEqual(boxes[0][5], self.conf_pct([0.0, 5.0, 0.0]))

    def test_margin_pass_implies_conf_at_least_half(self):
        # ln(2) margin at 3 classes guarantees p(winner) >= 0.5 -- the
        # documented reason the margin constant is 0.69. Conf must agree.
        g = self.grid()
        g[3][3] = [0.0, 0.70, 0.0]             # just past ln(2)
        _, boxes = B.fomo_decode(g, 3, 0.69)
        self.assertGreaterEqual(boxes[0][5], 50)
        self.assertLessEqual(boxes[0][5], 100)

    def test_conf_survives_the_wire_encoding(self):
        # mb boxes must stay int-only lists: the board's _json_boxes/_json_ints
        # encoder rejects floats by design.
        g = self.grid()
        g[2][3] = [0.0, 5.0, 0.0]
        _, boxes = B.fomo_decode(g, 3, 0.69)
        for b in boxes:
            for v in b:
                self.assertIsInstance(v, int)
        B._json_boxes(boxes)                   # must not raise


class TestModelBoxesOnHost(unittest.TestCase):
    """D2 host half: mb reaches the page snapshot verbatim, conf included."""

    def _stats(self):
        return H.Stats(clock=_clock_over([0.0, 1.0]))

    def test_mb_reaches_snapshot(self):
        s = self._stats()
        s.note({"mb": [[0, 10, 20, 30, 30, 87], [1, 50, 60, 30, 30, 62]]}, 10)
        self.assertEqual(s.snapshot()["model_boxes"],
                         [[0, 10, 20, 30, 30, 87], [1, 50, 60, 30, 30, 62]])

    def test_pre_d2_five_field_boxes_pass_through_unpadded(self):
        # An older board script sends no conf; the host must not invent one.
        s = self._stats()
        s.note({"mb": [[0, 10, 20, 30, 30]]}, 10)
        self.assertEqual(s.snapshot()["model_boxes"], [[0, 10, 20, 30, 30]])

    def test_absent_mb_is_an_empty_list(self):
        s = self._stats()
        s.note({}, 10)
        self.assertEqual(s.snapshot()["model_boxes"], [])


class TestModelKind(unittest.TestCase):
    def test_auto_sniffs_filenames(self):
        self.assertEqual(B.model_kind("/rom/yolov8n_192.tflite"), "yolo")
        self.assertEqual(B.model_kind("/flash/nereus_two_ball.tflite"), "fomo")
        self.assertEqual(B.model_kind("/rom/fomo_face_detection.tflite"), "fomo")
        self.assertEqual(B.model_kind("/rom/person_detect.tflite"), "raw")


class TestBoardModelMap(unittest.TestCase):
    def _args(self, specs):
        return types.SimpleNamespace(board_model=specs)

    def test_maps_label_to_path(self):
        m = H.board_model_map(self._args(["AE3:/flash/x.tflite",
                                          "N6:/rom/y.tflite"]), ["AE3", "N6"])
        self.assertEqual(m, {"AE3": "/flash/x.tflite", "N6": "/rom/y.tflite"})

    def test_unknown_label_is_an_error(self):
        with self.assertRaises(ValueError):
            H.board_model_map(self._args(["XX:/flash/x.tflite"]), ["AE3"])

    def test_missing_colon_is_an_error(self):
        with self.assertRaises(ValueError):
            H.board_model_map(self._args(["AE3=/flash/x.tflite"]), ["AE3"])

    def test_none_means_empty(self):
        self.assertEqual(H.board_model_map(self._args(None), ["AE3"]), {})


class TestModelCountsOnTheWire(unittest.TestCase):
    def test_stats_parses_mc_and_mdec(self):
        s = H.Stats(clock=_clock_over([0.0, 1.0]), labels=["pink", "purple"])
        s.note({"mc": [3, 2], "mdec_us": 8000}, 1000)
        snap = s.snapshot()
        self.assertEqual(snap["model_counts"], [3, 2])
        self.assertEqual(snap["mdec_ms"], 8.0)

    def test_missing_mc_is_empty_not_a_crash(self):
        s = H.Stats(clock=_clock_over([0.0, 1.0]))
        s.note({"det": 1}, 1000)
        self.assertEqual(s.snapshot()["model_counts"], [])


class TestPerBoardModelCfg(unittest.TestCase):
    def _args(self, **kw):
        base = dict(framesize="VGA", quality=50, max_seconds=3600,
                    max_frames=0, model="/rom/yolov8n_192.tflite",
                    model_kind="auto", model_labels=None, threshold=0.4,
                    no_detect=False, no_blobs=False, blob_pixels=150,
                    tune=False, blob_label="blob", blob_scan="codes",
                    save_frames=None, blob_thresh=None)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_model_override_wins(self):
        cfg = H.cfg_from_args(self._args(), classes=[], model="/flash/m.tflite")
        self.assertEqual(cfg["model"], "/flash/m.tflite")

    def test_no_override_keeps_global(self):
        cfg = H.cfg_from_args(self._args(), classes=[])
        self.assertEqual(cfg["model"], "/rom/yolov8n_192.tflite")

    def test_model_labels_ride_the_cfg(self):
        cfg = H.cfg_from_args(self._args(model_labels="pink, purple"),
                              classes=[])
        self.assertEqual(cfg["model_labels"], ["pink", "purple"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
