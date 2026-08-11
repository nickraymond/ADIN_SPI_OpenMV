# test_s6_video.py -- host-side unit tests for the S6 chunk protocol +
# reassembler (pure logic; the SPI/camera path is covered by the manual
# bite-1 run).
#
# Run:  python3 firmware/adin_drv/test_s6_video.py

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import s5_frames
import s6_video
from s6_video import (PAYLOAD_MAX, PAYLOAD_OFF, Reassembler, build_chunk,
                      chunk_frame, fill_chunk, n_chunks, parse_chunk)


def reassemble(chunks):
    """Feed chunks through a Reassembler; return list of completions."""
    r = Reassembler()
    return [done for done in (r.feed(c) for c in chunks)
            if done is not None], r


class TestChunker(unittest.TestCase):
    def test_n_chunks(self):
        self.assertEqual(n_chunks(1), 1)
        self.assertEqual(n_chunks(PAYLOAD_MAX), 1)
        self.assertEqual(n_chunks(PAYLOAD_MAX + 1), 2)
        self.assertEqual(n_chunks(3 * PAYLOAD_MAX), 3)
        with self.assertRaises(ValueError):
            n_chunks(0)

    def test_fill_matches_build(self):
        buf = bytearray(PAYLOAD_OFF + PAYLOAD_MAX)
        n = fill_chunk(buf, 7, 2, 5, b"payload")
        self.assertEqual(bytes(buf[:n]), build_chunk(7, 2, 5, b"payload"))

    def test_header_fields(self):
        pkt = build_chunk(0x01020304, 3, 9, b"x" * 10)
        self.assertEqual(pkt[:6], s5_frames.DST_MAC)
        self.assertEqual(pkt[6:12], s5_frames.SRC_MAC)
        self.assertEqual(struct.unpack_from(">H", pkt, 12)[0],
                         s5_frames.ETHERTYPE)
        self.assertEqual(parse_chunk(pkt), (0x01020304, 3, 9, b"x" * 10))

    def test_wire_fits_ethernet_and_fifo(self):
        pkt = build_chunk(0, 0, 1, b"x" * PAYLOAD_MAX)
        self.assertLessEqual(len(pkt), 1514)   # wire max without FCS
        # and the driver's burst builder must accept it (FIFO cap check)
        from adin_spi import build_tx_burst
        build_tx_burst(pkt)                    # raises AdinError if too big

    def test_roundtrip_sizes(self):
        for size in (1, PAYLOAD_MAX - 1, PAYLOAD_MAX, PAYLOAD_MAX + 1,
                     2 * PAYLOAD_MAX, 9198):   # 9198 = reef QVGA q50
            data = bytes((i * 31 + size) & 0xFF for i in range(size))
            done, r = reassemble(chunk_frame(data, 42))
            self.assertEqual(done, [(42, data)], "size %d" % size)
            self.assertEqual(r.frames_complete, 1)


class TestParseChunk(unittest.TestCase):
    def _pkt(self):
        return build_chunk(5, 0, 2, b"hello")

    def test_rejects_short(self):
        self.assertIsNone(parse_chunk(self._pkt()[:PAYLOAD_OFF - 1]))

    def test_rejects_wrong_ethertype(self):
        f = bytearray(self._pkt())
        struct.pack_into(">H", f, 12, 0x0800)
        self.assertIsNone(parse_chunk(bytes(f)))

    def test_rejects_s5_frames(self):
        # same EtherType on the wire -- the S5 magic must not parse
        self.assertIsNone(parse_chunk(s5_frames.build_eth_frame(1)))

    def test_rejects_idx_past_count(self):
        self.assertIsNone(parse_chunk(build_chunk(5, 2, 2, b"x")))

    def test_rejects_payload_len_past_end(self):
        f = bytearray(self._pkt())
        struct.pack_into(">H", f, 26, 500)     # claims more than the pkt has
        self.assertIsNone(parse_chunk(bytes(f)))

    def test_tolerates_min_frame_padding(self):
        # runt chunks arrive zero-padded to the 60 B Ethernet minimum
        pkt = build_chunk(5, 1, 2, b"tail")
        padded = pkt + b"\x00" * (60 - len(pkt))
        self.assertEqual(parse_chunk(padded), (5, 1, 2, b"tail"))


class TestReassembler(unittest.TestCase):
    def test_missing_chunk_never_completes(self):
        chunks = chunk_frame(b"A" * (2 * PAYLOAD_MAX), 1)
        done, r = reassemble(chunks[:1])
        self.assertEqual(done, [])
        self.assertEqual(r.frames_complete, 0)

    def test_dupe_chunk_counted_not_double_completed(self):
        chunks = chunk_frame(b"B" * (PAYLOAD_MAX + 1), 1)
        done, r = reassemble([chunks[0], chunks[0], chunks[1], chunks[1]])
        self.assertEqual(len(done), 1)
        self.assertEqual(r.chunk_dupes, 2)     # one mid-assembly, one late

    def test_interleaved_frames_both_complete(self):
        a = chunk_frame(b"A" * (PAYLOAD_MAX + 1), 1)
        b = chunk_frame(b"B" * (PAYLOAD_MAX + 1), 2)
        done, r = reassemble([a[0], b[0], a[1], b[1]])
        self.assertEqual([seq for seq, _ in done], [1, 2])

    def test_partial_evicted_when_slots_exhausted(self):
        # 5 two-chunk frames, first halves only: the 5th arrival needs a
        # slot past MAX_PARTIAL=4 and evicts the oldest (seq 0); then the
        # second halves of seqs 1-4 complete them all
        frames = [chunk_frame(b"F" * (PAYLOAD_MAX + 1), seq)
                  for seq in range(Reassembler.MAX_PARTIAL + 1)]
        feed = [f[0] for f in frames] + [f[1] for f in frames[1:]]
        done, r = reassemble(feed)
        self.assertEqual(r.frames_dropped, 1)
        self.assertEqual([seq for seq, _ in done],
                         list(range(1, Reassembler.MAX_PARTIAL + 1)))
        self.assertEqual(r.partial, {})

    def test_chunk_count_mismatch_drops_frame(self):
        good = build_chunk(1, 0, 2, b"x" * 5)
        liar = build_chunk(1, 1, 3, b"y" * 5)  # same seq, different count
        done, r = reassemble([good, liar])
        self.assertEqual(done, [])
        self.assertEqual(r.frames_dropped, 1)
        self.assertEqual(r.bad_chunks, 1)

    def test_garbage_counted_bad(self):
        _, r = reassemble([b"\x00" * 64])
        self.assertEqual(r.bad_chunks, 1)
        self.assertEqual(r.chunks, 0)

    def test_min_max_track_all_observed_seqs(self):
        a = chunk_frame(b"A" * 10, 3)
        b = chunk_frame(b"B" * (2 * PAYLOAD_MAX), 9)[:1]   # partial only
        _, r = reassemble(a + b)
        self.assertEqual((r.min_seq, r.max_seq), (3, 9))


if __name__ == "__main__":
    unittest.main()
