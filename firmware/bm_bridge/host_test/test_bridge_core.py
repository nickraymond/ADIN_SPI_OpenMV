#!/usr/bin/env python3
# test_bridge_core.py -- host tests for bm_bridge.BridgeCore (CPython, no
# hardware). The core is the S16 bridge's entire data plane: rpmsg frag
# reassembly (HE->Pi), uart_l2 encode/decode, and Pi->HE fragmentation.
# Rules under test mirror firmware/bm_he/src/wire_frag.c exactly -- the
# C side has the same cases in firmware/bm_he/host_test/test_bm_he.c.

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uart_codec as uc            # noqa: E402
from bm_bridge import (            # noqa: E402
    BridgeCore, MSG_PAYLOAD, MAX_L2, STATUS_FMT,
    WCMD_FRAME_TX, WCMD_FRAME_RX, WCMD_FRAG, WCMD_LINK, WCMD_LINK_UP,
    WCMD_PING, WCMD_QUERY, WCMD_STREAM, WREP_STATUS,
)

checks = 0
fails = 0


def check(cond, what):
    global checks, fails
    checks += 1
    if not cond:
        fails += 1
        print("FAIL: %s" % what)


def he_frame_msgs(frame, port=1):
    """Fragment a frame the way the HE's wire_frag.c does (TX side)."""
    n = len(frame)
    first = min(n, MSG_PAYLOAD)
    msgs = [struct.pack("<BBH", WCMD_FRAME_TX, port, n) + frame[:first]]
    off = first
    while off < n:
        c = min(n - off, MSG_PAYLOAD)
        msgs.append(struct.pack("<BBH", WCMD_FRAG, port, c) +
                    frame[off:off + c])
        off += c
    return msgs


# ---- HE -> Pi: small frame, one message, encodes to a valid uart_l2 frame
core = BridgeCore()
frame = bytes(range(80))
msgs = he_frame_msgs(frame)
check(len(msgs) == 1, "small frame is one rpmsg message")
wires = core.he_msg(msgs[0])
check(len(wires) == 1, "one wire chunk out")
check(wires[0][-1] == 0, "wire chunk 0x00-terminated")
check(uc.frame_decode(wires[0][:-1]) == frame, "uart_l2 round-trip")
check(core.stats["he2pi_frames"] == 1 and core.stats["he2pi_bytes"] == 80,
      "he2pi counters")

# ---- HE -> Pi: max frame spans 4 messages, reassembles byte-exact
frame = bytes((i * 13) & 0xFF for i in range(MAX_L2))
msgs = he_frame_msgs(frame)
check(len(msgs) == 4, "1514 B frame -> 4 rpmsg messages")
outs = []
for m in msgs:
    outs += core.he_msg(m)
check(len(outs) == 1, "one wire chunk after reassembly")
check(uc.frame_decode(outs[0][:-1]) == frame, "1514 B round-trip")
check(core.stats["frag_errors"] == 0, "no frag errors on clean reassembly")

# ---- HE -> Pi: FRAG with no frame open is counted, not fatal
core = BridgeCore()
core.he_msg(struct.pack("<BBH", WCMD_FRAG, 1, 4) + b"abcd")
check(core.stats["frag_errors"] == 1, "orphan FRAG counted")

# ---- HE -> Pi: new first-msg mid-assembly drops the old one, resyncs
core = BridgeCore()
big = bytes(600)
core.he_msg(he_frame_msgs(big)[0])          # open assembly (600 > 492)
wires = core.he_msg(he_frame_msgs(bytes(range(50)))[0])   # resync
check(core.stats["frag_errors"] == 1, "abandoned assembly counted")
check(len(wires) == 1 and uc.frame_decode(wires[0][:-1]) == bytes(range(50)),
      "resync frame delivered")

# ---- HE -> Pi: oversize announced total is rejected
core = BridgeCore()
bad = struct.pack("<BBH", WCMD_FRAME_TX, 1, MAX_L2 + 1) + bytes(400)
check(core.he_msg(bad) == [] and core.stats["frag_errors"] == 1,
      "total > 1514 rejected")

# ---- HE -> Pi: WREP_STATUS parsed into the status dict
core = BridgeCore()
status = struct.pack(STATUS_FMT, 0xBE9C000000000003,
                     b"\xfe\x80" + bytes(14), b"\xfd\x00" + bytes(14),
                     7, 0, 10, 20, 1, 1, 30000, 25000, 2, 3, 400, 5)
core.he_msg(struct.pack("<BBH", WREP_STATUS, 0, len(status)) + status)
check(core.status is not None and
      core.status["node_id"] == 0xBE9C000000000003 and
      core.status["stage"] == 7 and core.status["tx_dropped"] == 2 and
      core.status["frag_errors"] == 3 and core.status["stream_sent"] == 400,
      "WREP_STATUS unpack (88 B wire_status_t)")

# ---- Pi -> HE: small frame -> single WCMD_FRAME_RX message
core = BridgeCore()
frame = bytes(range(90))
msgs = core.vcp_bytes(uc.frame_encode(frame))
check(len(msgs) == 1, "small Pi frame -> 1 rpmsg message")
cmd, port, ln = struct.unpack_from("<BBH", msgs[0], 0)
check(cmd == WCMD_FRAME_RX and port == 1 and ln == 90 and
      msgs[0][4:] == frame, "WCMD_FRAME_RX shape")
check(core.stats["pi2he_frames"] == 1, "pi2he counter")

# ---- Pi -> HE: 1514 B frame -> 4 messages with total-length first header
frame = bytes((i * 7) & 0xFF for i in range(MAX_L2))
msgs = core.vcp_bytes(uc.frame_encode(frame))
check(len(msgs) == 4, "1514 B Pi frame -> 4 rpmsg messages")
cmd, port, ln = struct.unpack_from("<BBH", msgs[0], 0)
check(cmd == WCMD_FRAME_RX and ln == MAX_L2 and
      len(msgs[0]) == 4 + MSG_PAYLOAD, "first msg: total len + 492 B")
cmd2, _, ln2 = struct.unpack_from("<BBH", msgs[1], 0)
check(cmd2 == WCMD_FRAG and ln2 == MSG_PAYLOAD, "continuation shape")
rebuilt = b"".join(bytes(m[4:]) for m in msgs)
check(rebuilt == frame, "fragments carry the frame byte-exact")
tail = struct.unpack_from("<BBH", msgs[3], 0)
check(tail[0] == WCMD_FRAG and tail[2] == MAX_L2 - 3 * MSG_PAYLOAD,
      "last fragment length (38 B)")

# ---- Pi -> HE: split delivery (partial reads) reassembles at delimiters
core = BridgeCore()
wire = uc.frame_encode(bytes(range(100))) + uc.frame_encode(bytes(range(60)))
msgs = []
for i in range(0, len(wire), 7):        # feed in 7-byte slivers
    msgs += core.vcp_bytes(wire[i:i + 7])
check(len(msgs) == 2 and core.stats["pi2he_frames"] == 2,
      "two frames out of slivered stream")

# ---- Pi -> HE: stray text resyncs, counted by the splitter, link survives
core = BridgeCore()
noise = b"MicroPython v5.0 on AE3\r\n>>> \x00"
msgs = core.vcp_bytes(noise + uc.frame_encode(b"hello-bm"))
check(len(msgs) == 1 and msgs[0][4:] == b"hello-bm",
      "frame survives boot-banner noise")
check(core.splitter.errors == 1, "noise counted as decode error")

# ---- Pi -> HE: corrupted CRC is dropped and counted
core = BridgeCore()
wire = bytearray(uc.frame_encode(bytes(range(50))))
wire[5] ^= 0xFF
msgs = core.vcp_bytes(bytes(wire))
check(msgs == [] and core.splitter.errors == 1, "CRC corruption dropped")

# ---- control messages: byte-exact shapes the HE unpacks blind
check(BridgeCore.link_msg(True) ==
      struct.pack("<BBHB", WCMD_LINK, 1, 1, WCMD_LINK_UP), "link up msg")
check(BridgeCore.link_msg(False) ==
      struct.pack("<BBHB", WCMD_LINK, 1, 1, 0), "link down msg")
check(BridgeCore.query_msg() == struct.pack("<BBH", WCMD_QUERY, 0, 0),
      "query msg")
sm = BridgeCore.stream_msg(2000000, 1400, 600)
check(sm == struct.pack("<BBH", WCMD_STREAM, 0, 8) +
      struct.pack("<IHH", 2000000, 1400, 600), "stream msg (wire_stream_t)")
pm = BridgeCore.ping_msg(0xBE9C000000000001, b"hi")
check(pm == struct.pack("<BBH", WCMD_PING, 0, 10) +
      struct.pack("<Q", 0xBE9C000000000001) + b"hi", "ping msg")

# ---- duplex session: a realistic conversation both ways at once
core = BridgeCore()
pi_frames = [bytes([i]) * (100 + i * 137) for i in range(1, 9)]   # to 1059 B
he_frames = [bytes([0x40 + i]) * (80 + i * 199) for i in range(1, 8)]
pi_wire = b"".join(uc.frame_encode(f) for f in pi_frames)
he_out = []
pi_out = []
hi = 0
for i in range(0, len(pi_wire), 63):
    pi_out += core.vcp_bytes(pi_wire[i:i + 63])
    if hi < len(he_frames):
        for m in he_frame_msgs(he_frames[hi]):
            he_out += core.he_msg(m)
        hi += 1
while hi < len(he_frames):
    for m in he_frame_msgs(he_frames[hi]):
        he_out += core.he_msg(m)
    hi += 1
check(core.stats["pi2he_frames"] == len(pi_frames), "duplex: all Pi frames")
check(core.stats["he2pi_frames"] == len(he_frames), "duplex: all HE frames")
decoded = [uc.frame_decode(w[:-1]) for w in he_out]
check(decoded == he_frames, "duplex: HE frames byte-exact on the wire")
check(core.stats["frag_errors"] == 0 and core.splitter.errors == 0,
      "duplex: zero errors")

print("bm_bridge host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
