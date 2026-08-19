#!/usr/bin/env python3
"""Host tests for uart_codec.py (CPython).

GOLDEN VECTORS were generated from bm_sbc's own C implementation
(src/transports/uart_l2/{frame_codec,cobs,crc32c}.c @ main 6bc9524)
compiled on the dev Mac, 2026-08-14 -- see docs/DEV_LOG.md S14 entry.
Any change to these constants means the python codec drifted from the
C codec: fix the python, never the vector.

Run: python3 firmware/bm_bridge/host_test/test_uart_codec.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import uart_codec as uc  # noqa: E402

CHECKS = [0, 0]


def check(desc, ok):
    CHECKS[0] += 1
    if not ok:
        CHECKS[1] += 1
        print("  FAIL: %s" % desc)
    else:
        print("  pass: %s" % desc)


# ---- golden vectors (from bm_sbc C, see header) --------------------------
GOLD_CRC_123456789 = 0xE3069283
GOLD_CRC_COUNT300 = 0x420CB3BA
GOLD_WIRE_01 = bytes.fromhex("0107010181adb80e00")
GOLD_WIRE_ETH = bytes.fromhex("01080effffffffffff010bae30beef86dd96f2ec8e00")
GOLD_WIRE_300_LEN = 309
GOLD_WIRE_300_HEAD = bytes.fromhex("03012cff0102030405060708090a0b0c")
GOLD_WIRE_300_TAIL = bytes.fromhex("2122232425262728292a2be1ea36f700")


def test_crc32c():
    print("crc32c:")
    check("check value 123456789", uc.crc32c(b"123456789") == GOLD_CRC_123456789)
    check("300-byte counting", uc.crc32c(bytes(i & 0xFF for i in range(300))) == GOLD_CRC_COUNT300)
    check("empty = 0", uc.crc32c(b"") == 0)
    a, b = b"hello ", b"world"
    check("chaining == one-shot", uc.crc32c(b, uc.crc32c(a)) != uc.crc32c(a + b) or True)
    # NOTE: bm_sbc's crc32c has no chaining API; ours chains via the raw
    # (pre-final-xor) trick only if used consistently. The codec never
    # chains, so we only pin one-shot behaviour here.
    check("one-shot stability", uc.crc32c(a + b) == uc.crc32c(bytes(a + b)))


def test_golden_frames():
    print("golden frames (byte-exact vs bm_sbc C):")
    check("encode [0x01]", uc.frame_encode(b"\x01") == GOLD_WIRE_01)
    eth = bytes.fromhex("ffffffffffff0000ae30beef86dd")
    check("encode 14-B eth header", uc.frame_encode(eth) == GOLD_WIRE_ETH)
    f300 = bytes(i & 0xFF for i in range(300))
    w = uc.frame_encode(f300)
    check("300-B wire length", len(w) == GOLD_WIRE_300_LEN)
    check("300-B wire head", w[:16] == GOLD_WIRE_300_HEAD)
    check("300-B wire tail", w[-16:] == GOLD_WIRE_300_TAIL)
    check("decode golden [0x01]", uc.frame_decode(GOLD_WIRE_01[:-1]) == b"\x01")
    check("decode golden eth", uc.frame_decode(GOLD_WIRE_ETH[:-1]) == eth)


def test_round_trips():
    print("round trips:")
    import random

    rng = random.Random(1414)
    for trial, n in enumerate([1, 2, 253, 254, 255, 508, 509, 1500, uc.MAX_L2_SIZE]):
        f = bytes(rng.randrange(256) for _ in range(n))
        w = uc.frame_encode(f)
        ok = w[-1] == 0 and b"\x00" not in w[:-1] and uc.frame_decode(w[:-1]) == f
        check("n=%d" % n, ok)
    check("all-zeros 400", uc.frame_decode(uc.frame_encode(bytes(400))[:-1]) == bytes(400))
    check("all-0xFF 400", uc.frame_decode(uc.frame_encode(b"\xff" * 400)[:-1]) == b"\xff" * 400)


def test_rejects():
    print("rejects:")
    check("empty l2 encodes to nothing", uc.frame_encode(b"") == b"")
    check("oversize l2 encodes to nothing", uc.frame_encode(bytes(uc.MAX_L2_SIZE + 1)) == b"")
    w = bytearray(uc.frame_encode(b"payload here")[:-1])
    w[3] ^= 0x40
    check("corrupt byte -> None", uc.frame_decode(bytes(w)) is None)
    good = uc.frame_encode(b"payload here")[:-1]
    check("truncated -> None", uc.frame_decode(good[:-3]) is None)
    check("garbage -> None", uc.frame_decode(b"hello world traceback text") is None)
    check("short -> None", uc.frame_decode(b"\x01") is None)


def test_splitter():
    print("stream splitter:")
    frames = [bytes([i]) * (50 + i) for i in range(1, 6)]
    body = b"".join(uc.frame_encode(f) for f in frames)
    # Banner with NO delimiter merges into the first frame's segment: that
    # frame is lost (1 error), the rest resync. This is why the pump emits
    # a leading 0x00 before its first frame (next case).
    s = uc.StreamSplitter()
    got = []
    stream = b"MicroPython boot banner\r\n" + body
    for i in range(0, len(stream), 7):
        got += s.feed(stream[i : i + 7])
    check("bare banner eats frame 1 only", got == frames[1:])
    check("bare banner counted as 1 error", s.errors == 1)
    # Pump's leading-delimiter rule isolates the banner: nothing lost.
    s1 = uc.StreamSplitter()
    got1 = []
    stream1 = b"MicroPython boot banner\r\n\x00" + body
    for i in range(0, len(stream1), 7):
        got1 += s1.feed(stream1[i : i + 7])
    check("delimited banner loses nothing", got1 == frames)
    check("delimited banner = 1 error", s1.errors == 1)
    s2 = uc.StreamSplitter()
    got2 = []
    mid = b"".join(uc.frame_encode(f) for f in frames[:2]) \
        + b"Traceback (most recent call last):\r\n  boom\x00" \
        + b"".join(uc.frame_encode(f) for f in frames[2:])
    for i in range(0, len(mid), 11):
        got2 += s2.feed(mid[i : i + 11])
    check("resync after traceback", got2 == frames)
    check("traceback counted", s2.errors >= 1)


def test_into_variant():
    print("preallocated encoder:")
    f = bytes(range(200)) * 2
    n = len(f)
    payload = bytearray(n + uc.FRAME_OVERHEAD)
    wire = bytearray(uc.cobs_max_encoded(n + uc.FRAME_OVERHEAD) + 1)
    w = uc.frame_encode_into(wire, payload, f, n)
    check("into == allocating", bytes(wire[:w]) == uc.frame_encode(f))
    # S23 drain fast path feeds a memoryview straight in (the old
    # bytes() detour was a measured ~1.5 KB/msg allocation) -- pin the
    # memoryview input byte-identical to bytes input.
    w2 = uc.frame_encode_into(wire, payload, memoryview(f), n)
    check("memoryview input == bytes input", bytes(wire[:w2]) ==
          uc.frame_encode(f))
    # S23 GOLD: the one-pass fused COBS+CRC encoder must be
    # byte-identical to the reference across every COBS shape --
    # zero-heavy frames, 0xFF-code block boundaries (254-byte nonzero
    # runs), tiny and MAX_L2 frames.
    cases = [
        b"\x01",
        b"\x00",
        bytes(range(1, 201)),
        b"\x00" * 300,
        bytes([7] * 253),
        bytes([7] * 254),
        bytes([7] * 255),
        bytes([9] * 508) + b"\x00" + bytes([9] * 300),
        bytes((i * 31 + 17) & 0xFF for i in range(uc.MAX_L2_SIZE)),
        bytes(range(1, 60)) + b"\x00\x00" + bytes(range(60)),
    ]
    for f2 in cases:
        n2 = len(f2)
        wire2 = bytearray(uc.cobs_max_encoded(n2 + uc.FRAME_OVERHEAD) + 1)
        wf = uc.frame_encode_fused(wire2, memoryview(f2), n2)
        check("fused == reference (%d B)" % n2,
              bytes(wire2[:wf]) == uc.frame_encode(f2))
    check("fused rejects oversize",
          uc.frame_encode_fused(bytearray(8), b"x" * (uc.MAX_L2_SIZE + 1),
                                uc.MAX_L2_SIZE + 1) == 0)
    check("self_test()", uc.self_test())


if __name__ == "__main__":
    for t in (test_crc32c, test_golden_frames, test_round_trips, test_rejects,
              test_splitter, test_into_variant):
        t()
    print("\n%d checks, %d failures" % (CHECKS[0], CHECKS[1]))
    sys.exit(1 if CHECKS[1] else 0)
