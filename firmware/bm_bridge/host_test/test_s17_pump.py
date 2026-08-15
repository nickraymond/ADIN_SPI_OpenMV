#!/usr/bin/env python3
"""Host tests for s17_capture_pump.py pure parts (CPython).

Covers: sink chunk split, BCMD_SINK_DATA packing (incl. proving that
binascii.crc32 == he_spike's he_crc32 -- the claim the sink integrity
ledger rests on), and the capture pacer's quota behavior.

Run: python3 firmware/bm_bridge/host_test/test_s17_pump.py
"""

import os
import struct
import sys
from binascii import crc32

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import s17_capture_pump as sp  # noqa: E402  (must import clean on CPython)

CHECKS = [0, 0]


def check(desc, ok):
    CHECKS[0] += 1
    if not ok:
        CHECKS[1] += 1
        print("  FAIL: %s" % desc)
    else:
        print("  pass: %s" % desc)


def he_crc32_replica(data):
    """Bit-for-bit port of firmware/he_spike/src/bench.c he_crc32()."""
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
        tab.append(c)
    c = 0xFFFFFFFF
    for b in data:
        c = tab[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


print("== sink_chunk_lens ==")
check("empty -> no chunks", sp.sink_chunk_lens(0) == [])
check("negative -> no chunks", sp.sink_chunk_lens(-5) == [])
check("1 byte -> [1]", sp.sink_chunk_lens(1) == [1])
check("exactly one payload", sp.sink_chunk_lens(468) == [468])
check("one over", sp.sink_chunk_lens(469) == [468, 1])
check("exact multiple", sp.sink_chunk_lens(468 * 3) == [468] * 3)
check("reef-q50-ish 9200 B",
      sp.sink_chunk_lens(9200) == [468] * 19 + [308])
check("sum invariant", sum(sp.sink_chunk_lens(12345)) == 12345)
check("every chunk <= payload max",
      all(n <= sp.SINK_PAYLOAD for n in sp.sink_chunk_lens(99999)))
check("custom payload_max", sp.sink_chunk_lens(10, 4) == [4, 4, 2])

print("== crc algorithm claim ==")
for vec in (b"", b"123456789", bytes(range(256)) * 3):
    check("binascii.crc32 == he_crc32 (%d B)" % len(vec),
          (crc32(vec) & 0xFFFFFFFF) == he_crc32_replica(vec))

print("== sink_pack_into ==")
buf = bytearray(sp.SINK_MSG)
payload = bytes(range(200))
n = sp.sink_pack_into(buf, 7, memoryview(payload))
check("length = hdr + payload", n == sp.SINK_HDR + 200)
check("cmd byte", buf[0] == sp.BCMD_SINK_DATA)
check("pad zeroed", buf[1:4] == b"\x00\x00\x00")
check("seq LE", struct.unpack_from("<I", buf, 4)[0] == 7)
check("crc matches he_crc32 over payload only",
      struct.unpack_from("<I", buf, 8)[0] == he_crc32_replica(payload))
check("payload bytes verbatim", bytes(buf[sp.SINK_HDR:n]) == payload)
n2 = sp.sink_pack_into(buf, 0xFFFFFFFF, memoryview(b"x" * sp.SINK_PAYLOAD))
check("max payload fills the msg exactly", n2 == sp.SINK_MSG)
check("seq wraps at u32", struct.unpack_from("<I", buf, 4)[0] == 0xFFFFFFFF)

print("== CapturePacer ==")
p = sp.CapturePacer(10, 0)          # 10 fps -> 100 ms slots
check("not due at t=0", not p.due(0))
check("not due at t=99", not p.due(99))
check("due at t=100", p.due(100))
p.done = 1
check("slot 2 not due at t=150", not p.due(150))
check("slot 2 due at t=200", p.due(200))
# Stalled loop: owed slots do NOT burst -- one slot per due()+done cycle,
# achieved fps just degrades.
p2 = sp.CapturePacer(10, 0)
fired = 0
t = 1000                             # 10 slots owed instantly
for _ in range(3):
    if p2.due(t):
        fired += 1
        p2.done += 1
check("stalled loop fires once per pass (no burst suppression)", fired == 3)
p3 = sp.CapturePacer(0, 0)
check("fps=0 never due", not p3.due(10**9))

print("== module hygiene ==")
check("no hardware modules imported at top level",
      all(m not in sys.modules for m in ("openamp", "sensor", "image",
                                         "s14_relay_pump")))

print("\ns17 pump host tests: %d checks, %d failures" % (CHECKS[0], CHECKS[1]))
sys.exit(1 if CHECKS[1] else 0)
