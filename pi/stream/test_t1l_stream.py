# test_t1l_stream.py -- host-side unit tests for the pure pieces of the S3
# T1L pipeline: sender pacing/encoding and receiver stats accounting.
# No hardware, no sockets, no pyserial.
#
# Run:  python3 pi/stream/test_t1l_stream.py

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stream_server import StreamStats  # noqa: E402
from t1l_sender import Pacer, encode_frame  # noqa: E402
from usb_frame_source import FrameRecord, StreamParser  # noqa: E402

JPEG = b"\xff\xd8" + b"x" * 40 + b"\xff\xd9"


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TestPacer(unittest.TestCase):
    def test_downsamples_36fps_to_15(self):
        clock = FakeClock()
        pacer = Pacer(15.0, clock=clock)
        sent = 0
        for _ in range(360):  # 10 s of frames at 36 fps
            if pacer.should_send():
                sent += 1
            clock.t += 1 / 36.0
        self.assertAlmostEqual(sent, 150, delta=2)

    def test_source_slower_than_target_sends_everything(self):
        # 5 fps source, 15 fps target: every frame arrives at/after its tick.
        clock = FakeClock()
        pacer = Pacer(15.0, clock=clock)
        sent = 0
        for _ in range(50):
            if pacer.should_send():
                sent += 1
            clock.t += 0.2
        self.assertEqual(sent, 50)

    def test_resyncs_after_stall(self):
        clock = FakeClock()
        pacer = Pacer(15.0, clock=clock)
        pacer.should_send()
        clock.t += 10.0  # source stalled 10 s
        self.assertTrue(pacer.should_send())
        # No burst of catch-up ticks: the immediately-next frame waits a period.
        clock.t += 0.01
        self.assertFalse(pacer.should_send())

    def test_first_frame_always_sends(self):
        self.assertTrue(Pacer(15.0, clock=FakeClock()).should_send())


class TestWireRoundTrip(unittest.TestCase):
    def test_encode_frame_parses_back(self):
        frame = FrameRecord(seq=7, width=320, height=200, data=JPEG)
        wire = encode_frame(42, frame) + encode_frame(43, frame)
        events = StreamParser().feed(wire)
        self.assertEqual([k for k, _, _ in events], ["frame", "frame"])
        self.assertEqual([e[1]["frame"]["seq"] for e in events], [42, 43])
        self.assertEqual(events[0][2], JPEG)
        self.assertEqual(events[0][1]["frame"]["size_bytes"], len(JPEG))


class TestStreamStats(unittest.TestCase):
    def test_counts_and_fps(self):
        clock = FakeClock()
        stats = StreamStats(clock=clock)
        for seq in range(16):  # 16 frames at exactly 15 fps
            stats.note_frame(seq, 1000)
            clock.t += 1 / 15.0
        snap = stats.snapshot(connected=True)
        self.assertEqual(snap["frames"], 16)
        self.assertEqual(snap["bytes"], 16000)
        self.assertEqual(snap["gaps"], 0)
        self.assertEqual(snap["resets"], 0)
        self.assertAlmostEqual(snap["fps"], 15.0, places=1)
        self.assertTrue(snap["ingest_connected"])

    def test_gap_counting(self):
        stats = StreamStats(clock=FakeClock())
        for seq in (0, 1, 2, 5, 6, 10):
            stats.note_frame(seq, 10)
        self.assertEqual(stats.gaps, 2 + 3)  # 3,4 then 7,8,9
        self.assertEqual(stats.resets, 0)

    def test_producer_restart_is_reset_not_gap(self):
        stats = StreamStats(clock=FakeClock())
        for seq in (0, 1, 2, 0, 1):
            stats.note_frame(seq, 10)
        self.assertEqual(stats.resets, 1)
        self.assertEqual(stats.gaps, 0)

    def test_fps_empty_and_single(self):
        stats = StreamStats(clock=FakeClock())
        self.assertEqual(stats.fps(), 0.0)
        stats.note_frame(0, 10)
        self.assertEqual(stats.fps(), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
