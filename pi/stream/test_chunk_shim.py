# test_chunk_shim.py -- host-side unit tests for the pure pieces of the S6
# shim: ingest-wire encoding and the chunk->JPEG->frozen-parser path.
# No hardware, no sockets.
#
# Run:  python3 pi/stream/test_chunk_shim.py

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunk_shim import encode_frame, valid_jpeg  # noqa: E402
from usb_frame_source import StreamParser  # noqa: E402
from s6_video import Reassembler, chunk_frame  # noqa: E402

JPEG = b"\xff\xd8" + b"j" * 3000 + b"\xff\xd9"   # 2 chunks' worth


class TestValidJpeg(unittest.TestCase):
    def test_accepts_soi_eoi(self):
        self.assertTrue(valid_jpeg(JPEG))

    def test_rejects_truncated_and_garbage(self):
        self.assertFalse(valid_jpeg(JPEG[:-2]))     # no EOI
        self.assertFalse(valid_jpeg(JPEG[2:]))      # no SOI
        self.assertFalse(valid_jpeg(b"\x00" * 64))


class TestEncodeFrame(unittest.TestCase):
    def test_roundtrips_through_frozen_parser(self):
        # the shim's output must be exactly what stream_server ingests
        events = StreamParser().feed(encode_frame(7, JPEG))
        self.assertEqual(len(events), 1)
        kind, msg, payload = events[0]
        self.assertEqual(kind, "frame")
        self.assertEqual(msg["frame"]["seq"], 7)
        self.assertEqual(msg["frame"]["size_bytes"], len(JPEG))
        self.assertEqual(payload, JPEG)

    def test_split_delivery_reassembles(self):
        # ingest TCP reads arrive in arbitrary segments
        wire = encode_frame(0, JPEG) + encode_frame(1, JPEG)
        parser = StreamParser()
        events = []
        for i in range(0, len(wire), 100):
            events += parser.feed(wire[i:i + 100])
        self.assertEqual([m["frame"]["seq"] for _, m, _ in events], [0, 1])


class TestChunkToIngestPath(unittest.TestCase):
    def test_pair_wire_to_ingest_wire(self):
        # full pure path: AE3 chunker -> Reassembler -> shim encode ->
        # frozen parser; the JPEG must survive byte-identical
        rasm = Reassembler()
        done = [d for d in (rasm.feed(c) for c in chunk_frame(JPEG, 42)) if d]
        self.assertEqual(len(done), 1)
        seq, data = done[0]
        self.assertTrue(valid_jpeg(data))
        _, msg, payload = StreamParser().feed(encode_frame(0, data))[0]
        self.assertEqual(payload, JPEG)


if __name__ == "__main__":
    unittest.main()
