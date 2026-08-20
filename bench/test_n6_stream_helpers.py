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

    def test_eot_before_a_later_newline_still_ends(self):
        b = _board_over([b"\x04junk\n"])
        self.assertEqual(b.readline(), b"")

    def test_binary_payload_bytes_survive(self):
        b = _board_over([b"\xff\xd8\xff\xe0 payload\n"])
        self.assertEqual(b.readline(), b"\xff\xd8\xff\xe0 payload\n")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
