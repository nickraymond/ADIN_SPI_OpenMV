#!/usr/bin/env python3
# test_s19_probe.py -- host tests for bench/probes/s19_pub_probe.py
# (CPython, no hardware). Two things need proving before the probe is
# allowed near the board:
#
#  1. Its synthetic bursts are BYTE-IDENTICAL to what the production
#     chunker emits (BridgeCore.capture_pub_msgs). A probe that measures
#     traffic the product never sends measures nothing -- this is the S18
#     lesson restated: probe 4 exercised capture+encode and never
#     published, and HD was cleared on that basis.
#  2. Its sample-page decoder matches he_sample.c's layout, including the
#     wrap rule and the "records were lost" accounting. A silently
#     truncated drain curve reads exactly like a complete one.

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "probes"))
sys.path.insert(0, os.path.join(HERE, "..", "firmware", "bm_bridge"))

import s19_pub_probe as p            # noqa: E402
from bm_bridge import BridgeCore     # noqa: E402

checks = 0
fails = 0


def check(cond, what):
    global checks, fails
    checks += 1
    if not cond:
        fails += 1
        print("FAIL: %s" % what)


# ---- 1. the probe emits exactly what the bridge emits ---------------------

def test_framing_matches_bridge():
    core = BridgeCore()
    # A JPEG that chunks to 26 payloads at the production ceiling -- the
    # HD case from the S18 rehearsal (54,232 B ledger / ~36 KB frame).
    data_max = p.CAMERA_MAX_PAYLOAD - p.CHUNK_HDR_LEN
    for count, size in ((3, 1400), (8, 1400), (26, 1400), (26, 350)):
        jpeg = bytes([0xC3]) * (count * (size - p.CHUNK_HDR_LEN))
        want = core.capture_pub_msgs(jpeg, 7, size)
        got = p.burst_msgs(7, count, size)
        check(got == want,
              "burst_msgs == capture_pub_msgs for count=%d size=%d"
              % (count, size))
        check(core.stats["cap_chunks"] >= count, "bridge chunked %d" % count)
        core.stats["cap_chunks"] = 0
    check(data_max == 1390, "production chunk carries 1390 JPEG bytes")


def test_chunk_header():
    pay = p.chunk_payload(7, 3, 26, 100)
    seq, idx, count, ln = struct.unpack(p.CHUNK_HDR_FMT,
                                        pay[:p.CHUNK_HDR_LEN])
    check((seq, idx, count, ln) == (7, 3, 26, 100), "chunk header round trip")
    check(len(pay) == 110, "payload = header + data")


def test_fragmentation():
    # <= 492 B rides one message; larger payloads continue in WCMD_FRAG
    # with hdr.len = TOTAL on the first message only (wire_frag.h).
    msgs = p.pub_msgs(p.chunk_payload(1, 0, 1, 400))
    check(len(msgs) == 1, "410 B payload = 1 message")
    check(msgs[0][0] == p.WCMD_PUB, "first message is WCMD_PUB")
    check(struct.unpack_from("<H", msgs[0], 2)[0] == 410, "hdr.len = total")

    msgs = p.pub_msgs(p.chunk_payload(1, 0, 1, 1390))
    check(len(msgs) == 3, "1400 B payload = 3 messages (492+492+416)")
    check(struct.unpack_from("<H", msgs[0], 2)[0] == 1400, "hdr.len = total")
    check([m[0] for m in msgs] == [p.WCMD_PUB, p.WCMD_FRAG, p.WCMD_FRAG],
          "continuations are WCMD_FRAG")
    check(sum(len(m) - 4 for m in msgs) == 1400, "all payload bytes sent")
    check(all(len(m) - 4 <= p.MSG_PAYLOAD for m in msgs),
          "no message exceeds the 492 B rpmsg budget")


def test_plan_holds_one_variable():
    counts = p.plan("count")
    check(all(size == 1400 and pace == 0 and not drain
              for (_, size, pace, drain) in counts),
          "count phase varies only the chunk count")
    b = dict(((c, s), (c * s)) for (c, s, _, _) in p.plan("bytes"))
    check(b[(26, 1400)] == 36400, "row A is the HD-sized burst")
    check(b[(26, 350)] == 9100,
          "row E holds count at 26 and cuts bytes 4x -- the discriminator")
    check(b[(52, 700)] == 36400 and b[(104, 350)] == 36400,
          "rows B/D hold BYTES constant while raising the count")
    pace = p.plan("pace")
    check(all(c == 26 and s == 1400 for (c, s, _, _) in pace),
          "pace phase holds the burst shape fixed")
    check(p.plan("nope") == [], "unknown phase plans nothing")
    v = p.plan("verify")
    shapes = set((c, s) for (c, s, _, _) in v)
    check((26, 1400) in shapes and (16, 1400) in shapes,
          "verify phase re-runs both bursts that killed the HE")
    check(all(pace == 0 for (_, _, pace, _) in v),
          "verify runs UNPACED -- pacing is not the fix being tested")
    check(any(not drain for (_, _, _, drain) in v),
          "verify keeps a non-draining row: backpressure must be "
          "sender-visible, not a dead core")
    check(any(drain for (_, _, _, drain) in v),
          "verify also models the fixed bridge, which drains as it pushes")
    check(max(c * s for (c, s, _, _) in v) > 36400,
          "verify pushes past the HD burst to find the new limit")


# ---- 2. the decoder matches he_sample.c -----------------------------------

CAP = 40


def build_page(records, count=None, magic=p.SAMPLE_MAGIC, capacity=CAP):
    """Assemble a page the way he_sample.c writes it: slot = n % capacity,
    header count = records EVER written."""
    total = len(records) if count is None else count
    buf = bytearray(p.SAMPLE_PAGE_LEN)
    struct.pack_into(p.SAMPLE_HDR_FMT, buf, 0, magic, 1, capacity, total)
    first = total - len(records)
    for i, r in enumerate(records):
        off = p.SAMPLE_HDR_LEN + ((first + i) % capacity) * p.SAMPLE_REC_LEN
        struct.pack_into(p.SAMPLE_REC_FMT, buf, off, *r)
    return bytes(buf)


def rec(idx, count=26, ln=1400, err=0, txq=0, heap=40000, hmin=30000,
        txdrop=0, rpdrop=0, tick=0):
    return (idx, count, ln, err, txq, heap, hmin, txdrop, rpdrop, tick)


def test_decode_page():
    page = p.decode_page(build_page([rec(0, heap=40000),
                                     rec(1, heap=38000),
                                     rec(2, heap=36000, err=12, txq=3)]))
    check(page["ok"], "page decodes")
    check(page["count"] == 3 and page["capacity"] == CAP, "header fields")
    check([r["idx"] for r in page["recs"]] == [0, 1, 2], "oldest first")
    check([r["heap_free"] for r in page["recs"]] == [40000, 38000, 36000],
          "the drain curve survives the round trip")
    check(page["recs"][2]["err"] == 12 and page["recs"][2]["txq"] == 3,
          "err + txq depth decoded")

    check(not p.decode_page(build_page([], magic=0))["ok"],
          "bad magic is rejected")
    check(not p.decode_page(b"\x00" * 8)["ok"], "short page is rejected")
    check(not p.decode_page(build_page([], capacity=9999))["ok"],
          "impossible capacity is rejected")


def test_decode_wrap():
    # 45 records written into a 40-slot ring: the oldest 5 are gone and
    # what remains must still be in order.
    recs = [rec(i % 26, heap=50000 - i * 100) for i in range(45)]
    page = p.decode_page(build_page(recs[-CAP:], count=45))
    check(page["count"] == 45 and len(page["recs"]) == CAP,
          "wrap keeps the last `capacity` records")
    check(page["recs"][0]["heap_free"] == 50000 - 5 * 100,
          "oldest surviving record is #5")
    check(page["recs"][-1]["heap_free"] == 50000 - 44 * 100,
          "newest record is last")


def test_recs_since():
    page = p.decode_page(build_page([rec(i) for i in range(10)]))
    got, lost = p.recs_since(page, 4)
    check([r["idx"] for r in got] == [4, 5, 6, 7, 8, 9], "slice since mark")
    check(lost == 0, "nothing lost inside capacity")
    got, lost = p.recs_since(page, 10)
    check(got == [] and lost == 0, "no new records")

    # 100 written since the mark, only 40 retained -> 60 lost, and the
    # probe must SAY so rather than print 40 and look complete.
    page = p.decode_page(build_page([rec(i % 26) for i in range(CAP)],
                                    count=100))
    got, lost = p.recs_since(page, 0)
    check(len(got) == CAP and lost == 60, "wrap loss is counted, not hidden")


def test_verdict():
    alive = [{"alive": True, "heap_min_end": 9000, "count": 3, "size": 1400,
              "pace": 0, "drain": False, "published": 3},
             {"alive": True, "heap_min_end": 4000, "count": 8, "size": 1400,
              "pace": 0, "drain": False, "published": 8}]
    check("SURVIVED" in p.verdict(alive) and "4000" in p.verdict(alive),
          "survival verdict reports the lowest watermark")
    dead = alive + [{"alive": False, "heap_min_end": 100, "count": 26,
                     "size": 1400, "pace": 0, "drain": False,
                     "published": 8}]
    v = p.verdict(dead)
    check("WALL" in v and "count=26" in v and "8 chunks" in v,
          "failure verdict names the row and how far it got")


for fn in (test_framing_matches_bridge, test_chunk_header,
           test_fragmentation, test_plan_holds_one_variable,
           test_decode_page, test_decode_wrap, test_recs_since,
           test_verdict):
    fn()

print("s19 probe host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
