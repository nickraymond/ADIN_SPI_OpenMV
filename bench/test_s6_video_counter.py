# test_s6_video_counter.py -- host-side unit tests for the pure logic in
# bench/s6_video_counter.py (frame accounting + verdict). The raw socket
# path is covered by the manual bite-1 run.
#
# Run:  python3 bench/test_s6_video_counter.py

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "firmware", "adin_drv"))

from s6_video import Reassembler, chunk_frame
from s6_video_counter import FrameTracker, looks_like_jpeg, verdict

# > PAYLOAD_MAX so every simulated frame spans 2 chunks -- dropping a
# chunk then leaves a *partial* frame (observed but incomplete), which is
# what the edge/interior loss accounting distinguishes
JPEG = b"\xff\xd8" + b"j" * 2000 + b"\xff\xd9"


def run(frame_seqs, drop_chunk_of=(), corrupt=()):
    """Simulate a stream: whole frames, optionally dropping one chunk of
    some frames or sending structurally-invalid 'JPEG' bytes."""
    rasm, trk = Reassembler(), FrameTracker()
    for seq in frame_seqs:
        data = (b"nojpeg" * 40) if seq in corrupt else JPEG
        chunks = chunk_frame(data, seq)
        if seq in drop_chunk_of:
            chunks = chunks[:-1]
        for c in chunks:
            done = rasm.feed(c)
            if done:
                trk.frame_complete(*done)
    return trk.summary(rasm, 60.0)


class TestLooksLikeJpeg(unittest.TestCase):
    def test_accepts_soi_eoi(self):
        self.assertTrue(looks_like_jpeg(JPEG))

    def test_rejects_truncated_and_garbage(self):
        self.assertFalse(looks_like_jpeg(JPEG[:-2]))
        self.assertFalse(looks_like_jpeg(b"\x00" * 100))


class TestVerdict(unittest.TestCase):
    def test_clean_run_passes(self):
        s = run(range(50))
        self.assertEqual((s["complete"], s["lost"]), (50, 0))
        passed, line = verdict(s)
        self.assertTrue(passed)
        self.assertIn("PASS", line)

    def test_window_relative_late_attach_passes(self):
        s = run(range(1000, 1050))
        self.assertTrue(verdict(s)[0])

    def test_interior_incomplete_frame_fails(self):
        s = run(range(10), drop_chunk_of={5})
        self.assertEqual(s["lost"], 1)
        passed, line = verdict(s)
        self.assertFalse(passed)
        self.assertIn("lost", line)

    def test_edge_partial_not_counted_lost(self):
        # the last frame cut off by the window end is not link loss
        s = run(range(10), drop_chunk_of={9})
        self.assertEqual((s["lost"], s["edge_partial"]), (0, 1))
        self.assertTrue(verdict(s)[0])

    def test_bad_jpeg_fails(self):
        s = run(range(10), corrupt={4})
        self.assertEqual(s["jpeg_bad"], 1)
        passed, line = verdict(s)
        self.assertFalse(passed)
        self.assertIn("JPEG", line)

    def test_silence_fails(self):
        s = FrameTracker().summary(Reassembler(), 0)
        passed, line = verdict(s)
        self.assertFalse(passed)
        self.assertIn("no complete", line)

    def test_rate_math(self):
        s = run(range(30))
        self.assertAlmostEqual(s["fps"], 0.5)                  # 30 f / 60 s
        self.assertAlmostEqual(s["mbps"], 30 * len(JPEG) * 8 / 60.0 / 1e6)


if __name__ == "__main__":
    unittest.main()
