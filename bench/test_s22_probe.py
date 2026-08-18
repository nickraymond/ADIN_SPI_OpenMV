#!/usr/bin/env python3
# test_s22_probe.py -- host tests for bench/probes/s22_flood_probe.py
# (CPython, no hardware). What needs proving before the probe touches
# the board:
#
#  1. Its synthetic frames are BYTE-IDENTICAL to the production chunker
#     (BridgeCore.capture_pub_msgs) for REAL frame sizes -- including the
#     partial last chunk the s19 probe's uniform bursts never exercised.
#     A probe that measures traffic the product never sends measures
#     nothing (the S18 lesson).
#  2. Its rung arithmetic reproduces the measured boundary rates, so a
#     clean pass at "fatal-513" means the boundary moved, not that the
#     probe under-shot it.
#  3. Its classifier maps the three death shapes to the three verdicts
#     the fix decision hangs on.

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "probes"))
sys.path.insert(0, os.path.join(HERE, "..", "firmware", "bm_bridge"))

import s22_flood_probe as p          # noqa: E402
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
    # The rung frame sizes themselves, plus edge shapes: one-chunk, an
    # exact multiple of 1390 (no partial tail), and tiny.
    for n in (9198, 23831, 1390, 2780, 137, 1):
        jpeg = bytes([0xC3]) * n
        want = core.capture_pub_msgs(jpeg, 7, p.CAMERA_MAX_PAYLOAD)
        got = p.frame_msgs(7, n)
        check(got == want,
              "frame_msgs == capture_pub_msgs for %d B" % n)
    check(p.frame_msgs(0, 0) == [], "zero-byte frame -> no messages")


def test_rung_rates():
    # msg/s = msgs-per-frame * 1000 / period_ms must land in the
    # measured boundary zones (SPEC flood entry arithmetic).
    rates = {}
    for (name, frame_bytes, period_ms, dwell_s) in p.rung_plan():
        per_frame = len(p.frame_msgs(0, frame_bytes))
        rates[name] = per_frame * 1000.0 / period_ms
        if name != "burst-83":          # the burst rung is 3 shots, not
            check(dwell_s >= 60, "%s dwells >= 60 s" % name)  # a dwell
    check(290 <= rates["control-315"] <= 320,
          "control rung ~315 msg/s (got %.0f)" % rates["control-315"])
    check(500 <= rates["fatal-513"] <= 530,
          "fatal rung ~513 msg/s (got %.0f)" % rates["fatal-513"])
    check(540 <= rates["demo-560"] <= 580,
          "demo rung ~560 msg/s (got %.0f)" % rates["demo-560"])
    # The demo event died at ~5 min; its rung must dwell at least that.
    dwell = {r[0]: r[3] for r in p.rung_plan()}
    check(dwell["demo-560"] >= 300, "demo rung dwells past the ~5 min mark")
    check(dwell["fatal-513"] >= 120, "fatal rung dwells well past 60 s")


def test_frame_shape():
    # 9,198 B (QVGA q50 reef) -> 7 chunks, 20 msgs: 6 full chunks x 3
    # msgs + one 858 B tail chunk x 2 msgs. Matches the trace-measured
    # ~560 msg/s at ~28 fps.
    msgs = p.frame_msgs(0, 9198)
    check(len(msgs) == 20, "9198 B frame = 20 rpmsg msgs")
    pubs = [m for m in msgs if m[0] == p.WCMD_PUB]
    check(len(pubs) == 7, "9198 B frame = 7 chunks")
    # 23,831 B (VGA mono q50 reef) -> 18 chunks, 52 msgs.
    msgs = p.frame_msgs(0, 23831)
    check(len(msgs) == 52, "23831 B frame = 52 rpmsg msgs")
    pubs = [m for m in msgs if m[0] == p.WCMD_PUB]
    check(len(pubs) == 18, "23831 B frame = 18 chunks")


# ---- 3. the classifier ----------------------------------------------------

def test_classify():
    check("HOOK-SPIN" in p.classify(0xA110C, False, False) and
          "malloc" in p.classify(0xA110C, False, False),
          "malloc hook code -> HOOK-SPIN named")
    check("HOOK-SPIN" in p.classify(0x570F, False, False),
          "stack overflow code -> HOOK-SPIN")
    check("PARKED" in p.classify(0, False, False),
          "err 0 + frozen tick + no reply -> PARKED")
    check("TX-DEAD" in p.classify(0, True, False),
          "err 0 + moving tick + no reply -> TX-DEAD")
    check("ALIVE" in p.classify(0, True, True),
          "answered query -> ALIVE regardless of tick")


# ---- 2b. sample page decode (same layout as s19; keep the contract) -------

def test_decode_page():
    import struct
    hdr = struct.pack(p.SAMPLE_HDR_FMT, p.SAMPLE_MAGIC, 1, 3, 2)
    recs = b"".join(
        struct.pack(p.SAMPLE_REC_FMT, i, 26, 1400, 0, i, 20000 - i * 1488,
                    19000 - i * 1488, 0, 0, 1000 + i)
        for i in range(2))
    buf = hdr + recs + b"\x00" * (p.SAMPLE_PAGE_LEN - len(hdr) - len(recs))
    page = p.decode_page(buf)
    check(page["ok"] and page["count"] == 2 and len(page["recs"]) == 2,
          "sample page decodes")
    check(page["recs"][0]["heap_free"] == 20000 and
          page["recs"][1]["heap_free"] == 18512,
          "records decode oldest first with the right fields")
    bad = p.decode_page(b"\x00" * p.SAMPLE_PAGE_LEN)
    check(not bad["ok"], "bad magic refused")


for fn in (test_framing_matches_bridge, test_rung_rates, test_frame_shape,
           test_classify, test_decode_page):
    fn()

print("s22 probe host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
