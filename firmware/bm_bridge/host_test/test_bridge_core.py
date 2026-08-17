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
import bm_bridge                   # noqa: E402
from bm_bridge import (            # noqa: E402
    BridgeCore, MSG_PAYLOAD, MAX_L2, STATUS_FMT,
    WCMD_FRAME_TX, WCMD_FRAME_RX, WCMD_FRAG, WCMD_LINK, WCMD_LINK_UP,
    WCMD_PING, WCMD_QUERY, WCMD_STREAM, WCMD_PUB, WREP_STATUS,
    WREP_CAPTURE, CHUNK_HDR_FMT, CHUNK_HDR_LEN, CAMERA_MAX_PAYLOAD,
    CAP_DEFAULT_Q, CAP_DEFAULT_FPS_X10, CAP_DEFAULT_SECS,
    CAMERA_MODE_SINGLE, CAMERA_MODE_STREAM,
    CAP_DEFAULT_RES, CAP_DEFAULT_PF, CAMERA_RES_HD, CAMERA_PF_MONO,
    CAMERA_RES_QVGA, CAMERA_RES_VGA, CAMERA_PF_COLOR, sensor_steps,
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

# ---- S17: WREP_CAPTURE parse + camera chunker ----------------------------

core = BridgeCore()

# WREP_CAPTURE: explicit values pass through; the reply produces no VCP
# output; take_capture is fetch-and-clear.
cap_body = struct.pack("<BBHIHHBB", CAMERA_MODE_STREAM, 60, 150, 2000000,
                       600, 1000, CAMERA_RES_HD, CAMERA_PF_MONO)
out = core.he_msg(struct.pack("<BBH", WREP_CAPTURE, 0, len(cap_body)) +
                  cap_body)
check(out == [], "capture: no wire output")
cmd = core.take_capture()
check(cmd == {"mode": CAMERA_MODE_STREAM, "q": 60, "fps_x10": 150,
              "rate_bps": 2000000, "secs": 600, "payload_max": 1000,
              "res": CAMERA_RES_HD, "pf": CAMERA_PF_MONO},
      "capture: explicit fields pass through")
check(core.take_capture() is None, "capture: fetch-and-clear")

# Zeros -> bridge defaults; oversize payload_max clamped to REV-28.
cap_body = struct.pack("<BBHIHHBB", CAMERA_MODE_SINGLE, 0, 0, 0, 0, 1500, 0, 0)
core.he_msg(struct.pack("<BBH", WREP_CAPTURE, 0, len(cap_body)) + cap_body)
cmd = core.take_capture()
check(cmd["q"] == CAP_DEFAULT_Q and cmd["fps_x10"] == CAP_DEFAULT_FPS_X10
      and cmd["secs"] == CAP_DEFAULT_SECS, "capture: zeros -> defaults")
check(cmd["rate_bps"] == 0, "capture: rate 0 stays 0 (fps-paced)")
check(cmd["payload_max"] == CAMERA_MAX_PAYLOAD, "capture: pmax clamped")
check(cmd["res"] == CAP_DEFAULT_RES and cmd["pf"] == CAP_DEFAULT_PF,
      "capture: geometry zeros -> bridge defaults")

# An S17-length (12 B) body must NOT be parsed as if geometry were there:
# the ABI moved in lockstep, and a stale HE image is a real bench state.
cap_body = struct.pack("<BBHIHH", CAMERA_MODE_SINGLE, 60, 100, 0, 60, 1000)
core.he_msg(struct.pack("<BBH", WREP_CAPTURE, 0, len(cap_body)) + cap_body)
check(core.take_capture() is None, "capture: 12 B S17 body rejected")

# Truncated capture msg ignored, no crash, nothing pending.
core.he_msg(struct.pack("<BBH", WREP_CAPTURE, 0, 4) + b"\x01\x00\x00\x00")
check(core.take_capture() is None, "capture: truncated body ignored")

# ---- S18: sensor step planning -------------------------------------------
# These assertions encode a hardware fact measured the expensive way
# (bench/probes/, three board lock-ups): with the HE core loaded, the
# framebuffer COUNT must be pinned immediately before every set_framesize,
# or a grow expands the pool into SRAM9_B and the board leaves the USB bus
# with no catchable error. Order here is not style -- it is the fix.
check(sensor_steps(CAMERA_RES_QVGA, CAMERA_PF_COLOR,
                   CAMERA_RES_QVGA, CAMERA_PF_COLOR) == (),
      "steps: unchanged geometry touches nothing")
check(sensor_steps(CAMERA_RES_QVGA, CAMERA_PF_COLOR,
                   CAMERA_RES_HD, CAMERA_PF_COLOR)
      == ("framebuffers", "framesize", "settle"), "steps: resolution only")
check(sensor_steps(CAMERA_RES_HD, CAMERA_PF_COLOR,
                   CAMERA_RES_HD, CAMERA_PF_MONO)
      == ("pixformat", "framebuffers", "framesize", "settle"),
      "steps: pixformat change still re-applies framesize (realloc)")
check(sensor_steps(CAMERA_RES_QVGA, CAMERA_PF_COLOR,
                   CAMERA_RES_HD, CAMERA_PF_MONO)
      == ("pixformat", "framebuffers", "framesize", "settle"),
      "steps: both change, pixformat first")
# The invariant that keeps the board alive, asserted over the whole ladder.
for cr in (None, CAMERA_RES_QVGA, CAMERA_RES_VGA, CAMERA_RES_HD):
    for wr in (CAMERA_RES_QVGA, CAMERA_RES_VGA, CAMERA_RES_HD):
        for cp in (None, CAMERA_PF_COLOR, CAMERA_PF_MONO):
            for wp in (CAMERA_PF_COLOR, CAMERA_PF_MONO):
                st = sensor_steps(cr, cp, wr, wp)
                if not st:
                    continue
                check(st.index("framebuffers") == st.index("framesize") - 1,
                      "steps: count pinned immediately before every resize")
                check(st[-1] == "settle", "steps: a change always settles")
                if "pixformat" in st:
                    check(st[0] == "pixformat", "steps: pixformat leads")

# ---- S18: the ceiling guard ----------------------------------------------
# Growing past the ceiling claimed before the HE loaded is unrecoverable,
# so CaptureEngine must refuse it rather than ask the allocator.
eng = bm_bridge.CaptureEngine(ceiling=CAMERA_RES_VGA)
check(eng.ceiling == CAMERA_RES_VGA, "ceiling: honoured from constructor")
check(not eng.booted and eng._ensure_sensor(CAMERA_RES_QVGA, CAMERA_PF_COLOR)
      is False, "ceiling: no sensor work before bootstrap()")
eng.booted = True                     # simulate a successful bootstrap
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_VGA, CAMERA_PF_COLOR
check(eng._ensure_sensor(CAMERA_RES_HD, CAMERA_PF_COLOR) is False,
      "ceiling: HD refused under a VGA ceiling")
check(eng.cur_res == CAMERA_RES_VGA,
      "ceiling: a refusal leaves the live geometry untouched")
check(bm_bridge.CaptureEngine().ceiling == bm_bridge.CAP_DEFAULT_CEILING,
      "ceiling: defaults to HD so the full ladder is offerable")


def reassemble_pubs(msgs):
    """HE-side view: wire_frag reassembly of WCMD_PUB (+FRAG) messages ->
    the payloads camera_svc_publish would bm_pub verbatim."""
    payloads = []
    cur = None
    total = 0
    for m in msgs:
        cmd_b, _port, ln = struct.unpack_from("<BBH", m, 0)
        body = m[4:]
        if cmd_b == WCMD_PUB:
            assert cur is None, "frame while open"
            if len(body) >= ln:
                payloads.append(bytes(body[:ln]))
            else:
                cur = bytearray(body)
                total = ln
        else:
            assert cmd_b == WCMD_FRAG and cur is not None
            cur += body[:ln]
            if len(cur) == total:
                payloads.append(bytes(cur))
                cur = None
    assert cur is None, "dangling assembly"
    return payloads


# Small JPEG (fits one chunk, one rpmsg msg).
jpeg = bytes(range(256)) * 1               # 256 B
msgs = core.capture_pub_msgs(jpeg, 7, CAMERA_MAX_PAYLOAD)
check(len(msgs) == 1, "chunker: small jpeg -> one msg")
pubs = reassemble_pubs(msgs)
check(len(pubs) == 1, "chunker: one chunk")
seq, idx, count, plen = struct.unpack_from(CHUNK_HDR_FMT, pubs[0], 0)
check((seq, idx, count, plen) == (7, 0, 1, 256), "chunker: header fields")
check(pubs[0][CHUNK_HDR_LEN:] == jpeg, "chunker: payload byte-exact")

# Reef-sized JPEG: multi-chunk, every chunk <= 1400, frags correct,
# reassembled JPEG byte-exact.
core = BridgeCore()
jpeg = bytes((i * 37 + 11) & 0xFF for i in range(9200))
msgs = core.capture_pub_msgs(jpeg, 42, CAMERA_MAX_PAYLOAD)
pubs = reassemble_pubs(msgs)
data_max = CAMERA_MAX_PAYLOAD - CHUNK_HDR_LEN
want_chunks = (len(jpeg) + data_max - 1) // data_max
check(len(pubs) == want_chunks, "chunker: chunk count (9200 B -> %d)"
      % want_chunks)
check(all(len(p) <= CAMERA_MAX_PAYLOAD for p in pubs),
      "chunker: REV-28 ceiling on every payload")
got = bytearray()
for i, p in enumerate(pubs):
    seq, idx, count, plen = struct.unpack_from(CHUNK_HDR_FMT, p, 0)
    check(seq == 42 and idx == i and count == want_chunks
          and plen == len(p) - CHUNK_HDR_LEN,
          "chunker: header consistent (chunk %d)" % i)
    got += p[CHUNK_HDR_LEN:]
check(bytes(got) == jpeg, "chunker: multi-chunk jpeg byte-exact")
check(core.stats["cap_frames"] == 1 and core.stats["cap_chunks"] ==
      want_chunks and core.stats["cap_bytes"] == 9200,
      "chunker: stats ledger")
# Every rpmsg message respects the 496 B budget.
check(all(len(m) <= 4 + MSG_PAYLOAD for m in msgs),
      "chunker: rpmsg budget on every msg")

# Custom (small) payload_max still splits correctly.
core = BridgeCore()
msgs = core.capture_pub_msgs(bytes(100), 0, 64)
pubs = reassemble_pubs(msgs)
check(len(pubs) == 2 and all(len(p) <= 64 for p in pubs),
      "chunker: custom payload_max")
check(core.capture_pub_msgs(b"", 0, CAMERA_MAX_PAYLOAD) == [],
      "chunker: empty jpeg -> nothing")
check(core.capture_pub_msgs(bytes(10), 0, CHUNK_HDR_LEN) == [],
      "chunker: degenerate payload_max -> nothing")

# ---- S19 bite 2: drain while pushing a frame's chunks --------------------
# The HE-side fix makes the HE stop consuming inbound rpmsg once its own
# TX backs up, so the HP->HE vring fills and ept.send blocks. A send loop
# that is not draining he.queue meanwhile stalls both directions until the
# 1 s send timeout -- rpmsg drops and a broken frame. This is NOT pacing:
# pacing was measured and does not fix the heap wall (DESIGN §S19).

trace = []


def fake_send(m):
    trace.append("s%d" % m[0])


def fake_drain():
    trace.append("D")


del trace[:]
n = bm_bridge.send_chunk_msgs([bytes([i]) for i in range(9)],
                              fake_send, fake_drain, every=3)
check(trace == ["s0", "s1", "s2", "D", "s3", "s4", "s5", "D",
                "s6", "s7", "s8", "D"],
      "sender drains after every chunk (9 msgs, every=3)")
check(n == 3, "9 msgs / every 3 = 3 drains")

del trace[:]
n = bm_bridge.send_chunk_msgs([bytes([i]) for i in range(7)],
                              fake_send, fake_drain, every=3)
check(trace[-1] == "D" and trace.count("D") == 3,
      "a partial trailing group still ends on a drain")
check(len([t for t in trace if t != "D"]) == 7, "every message is sent")
check(n == 3, "7 msgs / every 3 = 2 + final drain")

del trace[:]
bm_bridge.send_chunk_msgs([], fake_send, fake_drain, every=3)
check(trace == ["D"], "empty frame still services the other direction")

del trace[:]
bm_bridge.send_chunk_msgs([bytes([i]) for i in range(4)],
                          fake_send, fake_drain, every=0)
check(trace == ["s0", "s1", "s2", "s3", "D"],
      "every=0 disables interleaving but keeps the final drain")

# A real HD frame is 26 chunks x 3 messages; the sender must service the
# other direction 26 times, not once at the end.
del trace[:]
core = BridgeCore()
msgs = core.capture_pub_msgs(bytes(26 * (CAMERA_MAX_PAYLOAD - CHUNK_HDR_LEN)),
                             0, CAMERA_MAX_PAYLOAD)
n = bm_bridge.send_chunk_msgs(msgs, fake_send, fake_drain)
check(len(msgs) == 78, "HD frame = 26 chunks x 3 rpmsg messages")
check(n == 26, "HD frame drains 26 times, once per chunk")
check(bm_bridge.CHUNK_DRAIN_EVERY == 3,
      "default every matches the messages-per-chunk at 1400 B")

# ---- S18 reef-matrix: ref-scene source -----------------------------------
# Asset naming is a contract with demo_up.sh's staging arm and the S0
# assets in bench/assets/ref_scene -- lock it with tests.
from bm_bridge import (  # noqa: E402
    ref_asset_names, CAMERA_RES_HD as _HD, CAMERA_MODE_STOP,
)

check(ref_asset_names(CAMERA_RES_QVGA, CAMERA_PF_COLOR) ==
      ("ref_color_320x200.bmp", "ref_color_320x200.jpg"),
      "ref assets: QVGA color = 320x200 BMP, JPEG fallback second")
check(ref_asset_names(CAMERA_RES_VGA, CAMERA_PF_MONO) ==
      ("ref_mono_640x400.pgm", "ref_mono_640x400.jpg"),
      "ref assets: VGA mono = 640x400 PGM, JPEG fallback second")
check(ref_asset_names(_HD, CAMERA_PF_COLOR)[0] == "ref_color_1280x800.bmp",
      "ref assets: HD color geometry")

# CaptureEngine in ref mode, against fake sensor/image/time modules.
class _FakeTime:
    _t = 1000
    @classmethod
    def ticks_ms(cls):
        return cls._t
    @classmethod
    def ticks_us(cls):
        return cls._t * 1000
    @staticmethod
    def ticks_diff(a, b):
        return a - b
    @staticmethod
    def ticks_add(a, b):
        return a + b
    @staticmethod
    def time():
        return _FakeTime._t / 1000.0


class _FakeJpeg:
    def __init__(self, tag):
        self._tag = tag
    def bytearray(self):
        return self._tag


class _FakeImg:
    def __init__(self, tag):
        self.tag = tag
    def to_jpeg(self, quality=50, copy=True):
        return _FakeJpeg(b"jpeg-of-" + self.tag)


class _FakeSensor:
    RGB565 = GRAYSCALE = QVGA = VGA = HD = 0
    @staticmethod
    def snapshot():
        return _FakeImg(b"live")
    @staticmethod
    def reset():
        pass
    @staticmethod
    def set_pixformat(x):
        pass
    @staticmethod
    def set_framesize(x):
        pass
    @staticmethod
    def set_framebuffers(x):
        pass
    @staticmethod
    def skip_frames(time=0):
        pass


class _FakeImageMod:
    """image module whose Image() succeeds only for `available` names."""
    loads = 0
    available = ()
    def Image(self, path):
        _FakeImageMod.loads += 1
        name = path.rsplit("/", 1)[-1]
        if name not in _FakeImageMod.available:
            raise OSError("no such file: %s" % path)
        return _FakeImg(name.encode())


_real_time = bm_bridge.time
bm_bridge.time = _FakeTime
sys.modules["sensor"] = _FakeSensor
sys.modules["image"] = _FakeImageMod()

_cmd = {"mode": CAMERA_MODE_SINGLE, "q": 50, "fps_x10": 100, "rate_bps": 0,
        "secs": 60, "payload_max": CAMERA_MAX_PAYLOAD,
        "res": CAMERA_RES_QVGA, "pf": CAMERA_PF_COLOR}

# 1. sensor mode is untouched by the feature: poll encodes the snapshot.
eng = bm_bridge.CaptureEngine()
eng.booted = True
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
eng.command(dict(_cmd))
check(eng.mode == CAMERA_MODE_SINGLE, "sensor mode: command accepted")
check(eng.poll(0) == b"jpeg-of-live", "sensor mode: encodes the live scene")
check(eng.enc_frames == 1, "encode accounting counts the frame")

# 2. ref mode with no asset staged: command REFUSED, mode stays STOP.
_FakeImageMod.available = ()
eng = bm_bridge.CaptureEngine(scene="ref")
eng.booted = True
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
eng.command(dict(_cmd))
check(eng.mode == CAMERA_MODE_STOP, "ref mode: missing asset REFUSES")
check(eng.poll(0) is None, "ref mode: refused command captures nothing")

# 3. ref mode with the raw asset staged: poll encodes the REF image, and
#    the discarded snapshot still happened (capture cost stays real).
_FakeImageMod.available = ("ref_color_320x200.bmp",)
eng = bm_bridge.CaptureEngine(scene="ref")
eng.booted = True
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
eng.command(dict(_cmd))
check(eng.mode == CAMERA_MODE_SINGLE, "ref mode: staged asset accepted")
check(eng.poll(0) == b"jpeg-of-ref_color_320x200.bmp",
      "ref mode: encodes the reference, not the live scene")

# 4. JPEG fallback: raw missing, .jpg staged -> loads the .jpg.
_FakeImageMod.available = ("ref_color_320x200.jpg",)
eng = bm_bridge.CaptureEngine(scene="ref")
eng.booted = True
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
eng.command(dict(_cmd))
check(eng.poll(0) == b"jpeg-of-ref_color_320x200.jpg",
      "ref mode: falls back to the staged JPEG")

# 5. Same-mode repeat does NOT reload the asset; a (res,pf) change does.
_FakeImageMod.available = ("ref_color_320x200.bmp", "ref_mono_320x200.pgm")
_FakeImageMod.loads = 0
eng = bm_bridge.CaptureEngine(scene="ref")
eng.booted = True
eng.sensor_ok = True
eng.cur_res, eng.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
eng.command(dict(_cmd))
n_first = _FakeImageMod.loads
eng.command(dict(_cmd))
check(_FakeImageMod.loads == n_first,
      "ref mode: repeat at the same (res,pf) reuses the loaded image")
mono = dict(_cmd)
mono["pf"] = CAMERA_PF_MONO
eng.command(mono)
check(_FakeImageMod.loads > n_first, "ref mode: pf change reloads")
check(eng.ref_key == (CAMERA_RES_QVGA, CAMERA_PF_MONO),
      "ref mode: loaded key tracks the commanded mode")

del sys.modules["sensor"]
del sys.modules["image"]
bm_bridge.time = _real_time

print("bm_bridge host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
