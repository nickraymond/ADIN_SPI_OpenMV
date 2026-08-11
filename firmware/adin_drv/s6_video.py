# s6_video.py -- S6 video chunk protocol + reassembly (portable; no
# machine imports).
#
# One MJPEG video frame rarely fits one Ethernet frame (reef-scene QVGA
# q50 ~ 9.2 KB; SPI burst cap 2048 B, wire max 1514 B without FCS), so
# each JPEG is chunked. Shared by the AE3 sender (s6_video_tx.py), the
# Pi bench counter (bench/s6_video_counter.py) and, in bite 2, the shim.
#
# Wire layout (byte offsets in the Ethernet frame):
#     0  dst MAC / src MAC / EtherType 0x88B5   (reused from s5_frames)
#    14  magic b"BMV6"
#    18  frame_seq   BE32   video frame number
#    22  chunk_idx   BE16
#    24  chunk_count BE16
#    26  payload_len BE16   actual JPEG bytes in THIS chunk -- required
#                           because runt frames arrive zero-padded to the
#                           Ethernet minimum, so wire length lies
#    28  payload

import struct

import s5_frames

MAGIC = b"BMV6"
ETH_HDR_LEN = 14
SEQ_OFF = 18
HDR_FMT = ">IHHH"                    # frame_seq, chunk_idx, count, payload_len
PAYLOAD_OFF = 28
PAYLOAD_MAX = 1400                   # frame = 1428 B: < 1514 wire, < FIFO cap
MAX_CHUNKS = 64                      # sanity cap: 64 x 1400 = 87 KB >> HD q90

_ETH_HDR = (s5_frames.DST_MAC + s5_frames.SRC_MAC
            + struct.pack(">H", s5_frames.ETHERTYPE))


def n_chunks(nbytes, payload_max=PAYLOAD_MAX):
    if nbytes <= 0:
        raise ValueError("empty frame -- nothing to chunk")
    return (nbytes + payload_max - 1) // payload_max


def fill_chunk(buf, frame_seq, chunk_idx, chunk_count, payload):
    """Pack one chunk into a preallocated bytearray; returns total length.

    The TX loop reuses one buffer instead of allocating per chunk
    (MicroPython GC kindness); `payload` may be a memoryview slice.
    """
    plen = len(payload)
    buf[0:ETH_HDR_LEN] = _ETH_HDR
    buf[ETH_HDR_LEN:SEQ_OFF] = MAGIC
    struct.pack_into(HDR_FMT, buf, SEQ_OFF,
                     frame_seq, chunk_idx, chunk_count, plen)
    buf[PAYLOAD_OFF:PAYLOAD_OFF + plen] = payload
    return PAYLOAD_OFF + plen


def build_chunk(frame_seq, chunk_idx, chunk_count, payload):
    """Allocating convenience wrapper around fill_chunk (host/test use)."""
    buf = bytearray(PAYLOAD_OFF + len(payload))
    fill_chunk(buf, frame_seq, chunk_idx, chunk_count, payload)
    return bytes(buf)


def chunk_frame(data, frame_seq, payload_max=PAYLOAD_MAX):
    """Split one JPEG into a list of ready-to-send Ethernet frames."""
    mv = memoryview(data)
    count = n_chunks(len(data), payload_max)
    return [build_chunk(frame_seq, i, count,
                        mv[i * payload_max:(i + 1) * payload_max])
            for i in range(count)]


def parse_chunk(pkt):
    """(frame_seq, chunk_idx, chunk_count, payload bytes) or None.

    Tolerates trailing zero-pad (Ethernet minimum-frame padding) by
    trusting payload_len; rejects anything structurally inconsistent.
    """
    if len(pkt) < PAYLOAD_OFF:
        return None
    if struct.unpack_from(">H", pkt, 12)[0] != s5_frames.ETHERTYPE:
        return None
    if bytes(pkt[ETH_HDR_LEN:SEQ_OFF]) != MAGIC:
        return None
    frame_seq, idx, count, plen = struct.unpack_from(HDR_FMT, pkt, SEQ_OFF)
    if count < 1 or count > MAX_CHUNKS or idx >= count:
        return None
    if PAYLOAD_OFF + plen > len(pkt):
        return None
    return frame_seq, idx, count, bytes(pkt[PAYLOAD_OFF:PAYLOAD_OFF + plen])


class Reassembler:
    """Chunks in, complete JPEGs out. Pure logic, host-unit-tested.

    Bounded: at most MAX_PARTIAL frames under assembly; when a new frame
    seq needs a slot, the oldest partial is dropped (counted in
    frames_dropped -- on an ordered link that means its chunks were lost).
    """

    MAX_PARTIAL = 4
    DONE_REMEMBER = 8    # recently completed seqs, to classify late dupes

    def __init__(self):
        self.partial = {}          # frame_seq -> [count, [None]*count]
        self._order = []           # frame_seq arrival order for eviction
        self._done = []            # last few completed frame seqs
        self.chunks = 0
        self.bad_chunks = 0
        self.chunk_dupes = 0
        self.frames_complete = 0
        self.frames_dropped = 0
        self.min_seq = None
        self.max_seq = None

    def _evict(self, seq):
        del self.partial[seq]
        self._order.remove(seq)

    def feed(self, pkt):
        """Feed one raw Ethernet frame; returns (frame_seq, jpeg_bytes)
        when this chunk completes a frame, else None."""
        parsed = parse_chunk(pkt)
        if parsed is None:
            self.bad_chunks += 1
            return None
        seq, idx, count, payload = parsed
        self.chunks += 1
        self.min_seq = seq if self.min_seq is None else min(self.min_seq, seq)
        self.max_seq = seq if self.max_seq is None else max(self.max_seq, seq)
        if seq in self._done:
            self.chunk_dupes += 1
            return None
        entry = self.partial.get(seq)
        if entry is None:
            if len(self.partial) >= self.MAX_PARTIAL:
                oldest = self._order[0]
                self._evict(oldest)
                self.frames_dropped += 1
            entry = [count, [None] * count]
            self.partial[seq] = entry
            self._order.append(seq)
        if entry[0] != count:
            # sender/receiver disagree about this frame -- unrecoverable
            self._evict(seq)
            self.frames_dropped += 1
            self.bad_chunks += 1
            return None
        if entry[1][idx] is not None:
            self.chunk_dupes += 1
            return None
        entry[1][idx] = payload
        if any(c is None for c in entry[1]):
            return None
        data = b"".join(entry[1])
        self._evict(seq)
        self._done.append(seq)
        if len(self._done) > self.DONE_REMEMBER:
            self._done.pop(0)
        self.frames_complete += 1
        return seq, data
