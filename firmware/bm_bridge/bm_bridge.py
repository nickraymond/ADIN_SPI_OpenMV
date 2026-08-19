# bm_bridge.py -- S16 BUILD-2b: the HP bridge. Moves L2 frames between the
# HE rpmsg endpoint ("bm-wire", firmware/bm_he) and the USB VCP, framed
# with bm_sbc's uart_l2 codec (uart_codec.py, byte-exact vs Sofar's C).
# The Pi end is bm_sbc's stock --uart gateway pointed at this board's
# /dev/serial/by-id CDC path -- zero new Pi-side transport code (REV-20).
#
# Runs ON the AE3's HP core (MicroPython, stock firmware, nothing
# flashed), deployed as /flash/main.py via main_bridge.py. Ops model
# (firmware/bm_bridge/README.md rules are law): warm `mpremote reset`
# is the service entry. STOP MODEL (changed from the S14 pump, found
# live): kbd_intr is disabled for the service's life -- COBS bytes
# contain 0x03 and would kill the pump -- so ctrl-C/mpremote CANNOT
# stop a linked bridge; instead the bridge exits ITSELF 30 s after the
# Pi side goes quiet (stop bm_sbc, wait, then the board is at the
# REPL), or 10 min with no Pi attach at all; uhubctl stays the hammer.
# Every exit cause persists to /flash/bridge_crash.txt because a
# traceback printed into bm_sbc's decoder is lost as COBS garbage
# (BENCHSPEC BUILD-2b).
#
# Wire protocol facts (firmware/bm_he/src/bm_he.h + wire_frag.h):
#   rpmsg msg = 4 B header <BBH (cmd, port, len)> + payload, <=496 B total.
#   WCMD_FRAME_TX/RX carry hdr.len = TOTAL L2 frame length; frames beyond
#   one message continue in WCMD_FRAG msgs (in-order vring, no seq).
#   Max L2 frame network-wide = 1514 (REV-14; the HE sender enforces its
#   side, this bridge enforces the Pi->HE direction).
#
# Link discipline (REV-12): the bridge holds WCMD_LINK down until the Pi
# side actually speaks (first bytes on the VCP -- bm_sbc's gateway sends
# heartbeats on its UART port as soon as it opens the tty). Until link-up
# the HE stack transmits nothing, so the pipe stays quiet while unowned.
#
# Stream trigger (Nick-approved): /flash/bridge_cfg.json, e.g.
#   {"stream": {"mbps": 2.0, "payload": 1400, "secs": 600, "delay": 10},
#    "ping":   {"target": "0xbe9c000000000001", "delay": 5},
#    "camera": {"mode": "stream", "fps": 10, "mbps": 2.0, "secs": 60,
#               "delay": 10}}
# stream -> WCMD_STREAM to the HE publisher (delay counts from link-up);
# ping -> one WCMD_PING (a Camera-sourced 2-hop BCMP ping; the acceptance
# line lands on the HE debug ring -- dumped to the trace file at exit);
# camera -> a local CaptureEngine one-shot (S17 bring-up aid -- the real
# trigger is the camera/control service via WREP_CAPTURE).

import json
import struct
import sys
import time

import uart_codec as uc

# wire protocol (bm_he.h)
WCMD_FRAME_TX = 0x11
WCMD_FRAME_RX = 0x12
WCMD_LINK = 0x13
WCMD_LINK_UP = 0x01
WCMD_QUERY = 0x14
WCMD_PING = 0x15
WCMD_FRAG = 0x16
WCMD_STREAM = 0x17
WCMD_PUB = 0x18             # HP->HE: publish payload on camera/stream (S17)
WREP_STATUS = 0x94
WREP_CAPTURE = 0x95         # HE->HP: camera command, wire_capture_t <BBHIHH

# S23 bite 2: rpmsg buffers 512 -> 1544 (he_spike.h + openmv patch 0004),
# so the budget after the 16 B rpmsg header and our 4 B wire header is
# 1524 -- one 1400 B camera chunk or one full 1514 B L2 frame per
# message (the measured tax was ~0.57 ms PER MESSAGE). WCMD_FRAG remains
# for anything larger; on this stack nothing legal is.
MSG_PAYLOAD = 1524          # 1528 rpmsg budget - 4 B wire header
MAX_L2 = 1514               # REV-14 network-wide max frame

# S17 camera data plane: chunk header inside each camera/stream payload
# (camera_svc.h contract -- BMV6 adapted, little-endian like the rest of
# this wire, unlike BMV6's big-endian original):
#   frame_seq u32 | chunk_idx u16 | chunk_count u16 | payload_len u16
CHUNK_HDR_FMT = "<IHHH"
CHUNK_HDR_LEN = 10
CAMERA_MAX_PAYLOAD = 1400   # REV-28 ceiling (== HE's CAMERA_MAX_PAYLOAD)
# Bridge-side defaults (0 in wire_capture_t means "bridge default" --
# the defaults live HERE and nowhere else, camera_svc.c passes zeros).
CAP_DEFAULT_Q = 50          # D20 standing setting
CAP_DEFAULT_FPS_X10 = 100   # 10.0 fps
CAP_DEFAULT_SECS = 60
CAMERA_MODE_STOP = 0
CAMERA_MODE_SINGLE = 1
CAMERA_MODE_STREAM = 2

# S18 capture geometry (bm_he.h CAMERA_RES_* / CAMERA_PF_*). The sensor
# LETTERBOXES to 16:10 -- QVGA is 320x200, not 320x240 -- and
# QQVGA/SVGA/WXGA are unsupported on sensor 0x7936 (DESIGN §S0). The
# geometry table is documentation + trace text; the sensor derives the
# real thing from the framesize constant.
CAMERA_RES_QVGA = 1
CAMERA_RES_VGA = 2
CAMERA_RES_HD = 3
CAMERA_PF_COLOR = 1
CAMERA_PF_MONO = 2
CAP_DEFAULT_RES = CAMERA_RES_QVGA    # the T1 shape (D9)
CAP_DEFAULT_PF = CAMERA_PF_COLOR
# Framebuffer ceiling claimed at bridge start, BEFORE the HE ELF loads.
# Anything at or below it can be switched to freely for the rest of the
# run; anything above is refused. HD by default so the bench tool can
# offer the whole ladder -- the buffer is not on the MicroPython heap
# (measured: heap stayed ~3.8 MB with HD allocated), so claiming the
# ceiling costs nothing the rest of the bridge needs.
CAP_DEFAULT_CEILING = CAMERA_RES_HD
RES_GEOM = {CAMERA_RES_QVGA: (320, 200),
            CAMERA_RES_VGA: (640, 400),
            CAMERA_RES_HD: (1280, 800)}
PF_NAME = {CAMERA_PF_COLOR: "RGB565", CAMERA_PF_MONO: "GRAYSCALE"}
# bridge_cfg.json spells these as words; unknown spellings fall back to
# the defaults rather than failing the whole one-shot.
CFG_RES = {"qvga": CAMERA_RES_QVGA, "vga": CAMERA_RES_VGA, "hd": CAMERA_RES_HD}
CFG_PF = {"color": CAMERA_PF_COLOR, "mono": CAMERA_PF_MONO,
          "grayscale": CAMERA_PF_MONO, "greyscale": CAMERA_PF_MONO}
# S18 reef-matrix: scene source for the ENCODE path. "sensor" (default)
# encodes what the camera sees; "ref" encodes the S0 reef reference for
# the commanded (res, pf) instead -- the dark bench room compresses
# ~2.6x too well (S0/S18 B2 measured), so throughput measured on it is
# not representative of a deployment scene. In ref mode the sensor path
# runs UNCHANGED -- bring-up, re-inits, PublishGate, and a discarded
# sensor.snapshot() per frame -- so the B2 hazard, its gate, and the
# capture cost all stay in the measured pipeline. Only the pixels handed
# to the encoder are swapped.
REF_SCENE_DIR = "/flash/ref_scene"
STATUS_FMT = "<Q16s16sIIIIIIIIIIII"   # wire_status_t (88 B)
STATUS_KEYS = ("node_id", "ip_ll", "ip_ucast", "stage", "err",
               "tx_frames", "rx_frames", "tx_oversize", "link_up",
               "heap_free", "heap_min", "tx_dropped", "frag_errors",
               "stream_sent", "stream_errs")

CFG_PATH = "/flash/bridge_cfg.json"
CRASH_PATH = "/flash/bridge_crash.txt"
TRACE_PATH = "/flash/bridge_trace.txt"
TRACE_PREV_PATH = "/flash/bridge_trace.prev.txt"   # last run, kept for crashes
ELF_PATH = "/flash/bm_he.elf"
BM_STATUS_PAGE = 0x600BFE00
# HE->bridge backlog cap (drops counted). S22 bite 1b: 256 was the
# single-frame burst killer -- an 83-chunk q90 publish returns as ~332
# rpmsg msgs while the drain stage (the blocking VCP write, ~675 KB/s)
# is mid-burst, so the cap shed ~54 chunks and the receiver ledger
# blamed "the relay" (measured: same 54 on two different HE builds; the
# _rx callback recycles the vring buffer even when it drops, so no
# HE-side backpressure can reach this hop). Sized to the largest legal
# burst: HD color q90 ~= 190 chunks ~= 760 msgs, plus headroom. Worst
# case ~500 KB of transient MP heap against ~3.8 MB free -- still
# bounded, still counted, just no longer smaller than the product's own
# frames.
RPMSG_QUEUE_CAP = 1024
# Service the HE->HP direction every N messages while pushing a frame's
# chunks. S23 bite 2: one 1400 B chunk is now ONE rpmsg message, so 1 =
# "drain after every chunk" -- the same interleave cadence S19 bite 2
# established, in the new message geometry (see send_chunk_msgs).
CHUNK_DRAIN_EVERY = 1
PHASE1_TIMEOUT_MS = 600000  # no Pi attach in 10 min -> clean exit
QUIET_EXIT_MS = 30000       # linked, then silent 30 s (3x the 10 s
                            #   heartbeat period) -> Pi gone, clean exit

# S18 bite B2 -- the sensor re-init hazard gate (PublishGate below).
# How often to re-post the WCMD_QUERY barrier while waiting. The HE
# answers a query in one wire-task pass; this only bounds the cost of a
# reply that was dropped, so it is a retry interval, not a settle.
REINIT_REQUERY_MS = 250
# THE fix (S18 bite B2, rungs C-F + the on-chain ladder): minimum
# wall-clock quiet after the last publish before ANY sensor re-init. The
# hazard is TIME since the publish, not anything the bridge can observe
# -- both observable proxies (publish drained, rpmsg silent) were
# measured insufficient. On-chain evidence, one number per row, all with
# DARK frames (~2.6x smaller than daylight):
#   QVGA source (~2-4 KB):  6.3 s PASS (the rehearsal's fast pair)
#   VGA source  (~11 KB):   2 s FAIL (bite B) . ~6.5 s FAIL (rehearsal)
#                           . 10 s FAIL . 15 s PASS (the ladder)
#   HD source:              UNMEASURED -- every ladder HD rung sat
#                           behind a latch from the 10 s failure
# The decay tracks published bytes (~1.5 s/KB fits every point), so 20 s
# is the safe side of the measured VGA boundary with margin -- NOT a
# certification for daylight HD (~93 KB), which the reef-scene matrix
# session must measure before anyone lowers or trusts this for HD.
# Off-chain numbers (>=500 ms passed 10/10) do NOT bind: the chain is
# measurably the harder environment.
REINIT_MIN_QUIET_MS = 20000
# A gate that never opens REFUSES the command rather than re-initialising
# into a live publish. Budget: the quiet window above, plus 5 s for the
# barrier -- reaching the deadline means something is genuinely wrong
# (HE wedged, rpmsg stalled), exactly when touching the sensor is least
# safe.
REINIT_DEADLINE_MS = REINIT_MIN_QUIET_MS + 5000
# heap_free must come back to within this of its learned high-water before
# the sensor is touched. One 1,400 B chunk costs the HE exactly 1,488 B
# (S19 bite 1, measured), so a slack below that detects a single
# outstanding transmit copy while tolerating ordinary allocator jitter.
REINIT_HEAP_SLACK = 1024

# PublishGate.poll() verdicts.
GATE_GO = "go"              # safe: apply the re-init now
GATE_WAIT = "wait"          # barrier posted, waiting on the HE's reply
GATE_QUERY = "query"        # caller should send core.query_msg()
GATE_REFUSE = "refuse"      # deadline passed: drop the command, do NOT re-init


class BridgeCore:
    """Pure duplex pump logic -- no hardware imports, host-testable.

    he_msg() takes one rpmsg message and returns uart_l2 wire chunks for
    the VCP; vcp_bytes() takes raw VCP bytes and returns rpmsg messages
    for the HE. Reassembly/fragmentation mirrors firmware/bm_he's
    wire_frag.c rules exactly.
    """

    def __init__(self):
        self.splitter = uc.StreamSplitter()
        self.reasm = None           # (port, total, bytearray) mid-assembly
        self.status = None          # last WREP_STATUS, dict
        self.status_seq = 0         # WREP_STATUS arrivals (PublishGate barrier)
        self.capture_cmd = None     # last WREP_CAPTURE, dict (take_capture)
        # Preallocated encode buffers (hot path, no per-frame allocation).
        self._payload_buf = bytearray(MAX_L2 + uc.FRAME_OVERHEAD)
        self._wire = bytearray(uc.cobs_max_encoded(MAX_L2 + uc.FRAME_OVERHEAD) + 1)
        self.stats = {
            "he2pi_frames": 0, "he2pi_bytes": 0,     # L2 frames HE -> VCP
            "pi2he_frames": 0, "pi2he_bytes": 0,     # L2 frames VCP -> HE
            "frag_errors": 0,                        # HE->HP reassembly drops
            "oversize": 0,                           # Pi frames > 1514 (REV-14)
            "unknown_cmds": 0,
            "cap_frames": 0, "cap_bytes": 0,         # JPEGs chunked (S17)
            "cap_chunks": 0,                         # WCMD_PUB payloads sent
            # S23 bite 2 profile: WHERE the ~2 ms/KB publish tax goes.
            # asm = capture_pub_msgs (python chunk/msg assembly),
            # send = send_chunk_msgs (ept.send + interleaved pump).
            "cap_asm_us": 0, "cap_send_us": 0, "cap_msgs": 0,
            # S23 relay-leg profile: cap_send_us split three ways.
            # ept = blocked in ept.send (rpmsg HP->HE); pump = the
            # interleaved drain inside the send window; the remainder
            # of cap_send_us is loop overhead. ept_slow counts sends
            # over 1 ms -- a vring-full wait quantized to a ms poll
            # shows up here as count x ~1000 us.
            "cap_ept_us": 0, "cap_ept_max_us": 0, "cap_ept_slow": 0,
            "cap_pump_us": 0,
            # VCP write meter, GLOBAL (send-window and main-loop drains
            # both land here): time inside usb.write vs bytes moved is
            # the drain path's real throughput; pump batch stats say
            # whether HE returns trickle (batch ~1) or burst.
            "vcp_us": 0, "vcp_max_us": 0, "vcp_writes": 0, "vcp_bytes": 0,
            "pump_calls": 0, "pump_msgs": 0, "pump_batch_max": 0,
            # Second-stage split (the ~1.25 ms/msg pump cost): time
            # inside core.he_msg (parse + frame_encode_into + copies)
            # vs the loop glue around it. A GC pause landing in he_msg
            # shows as a relay_enc_max_us spike.
            "relay_enc_us": 0, "relay_enc_max_us": 0,
        }

    # ---- HE -> Pi ---------------------------------------------------------

    def he_msg(self, b):
        """One rpmsg message from HE -> list of wire chunks for the VCP."""
        if len(b) < 4:
            self.stats["frag_errors"] += 1
            return []
        cmd, port, ln = struct.unpack_from("<BBH", b, 0)
        if cmd == WCMD_FRAME_TX:
            if self.reasm is not None:
                self.stats["frag_errors"] += 1      # resync on new frame
                self.reasm = None
            if ln > MAX_L2:
                self.stats["frag_errors"] += 1
                return []
            if len(b) - 4 >= ln:
                return [self._encode(memoryview(b)[4:4 + ln], ln)]
            self.reasm = (port, ln, bytearray(b[4:]))
            return []
        if cmd == WCMD_FRAG:
            if self.reasm is None:
                self.stats["frag_errors"] += 1
                return []
            port, total, buf = self.reasm
            buf += b[4:4 + ln]
            if len(buf) < total:
                return []
            self.reasm = None
            if len(buf) > total:
                self.stats["frag_errors"] += 1
                return []
            return [self._encode(memoryview(buf), total)]
        if cmd == WREP_STATUS:
            if ln >= struct.calcsize(STATUS_FMT) and len(b) - 4 >= ln:
                vals = struct.unpack_from(STATUS_FMT, b, 4)
                self.status = dict(zip(STATUS_KEYS, vals))
                # Counts ARRIVALS, not content. PublishGate needs to know
                # that a reply came back AFTER its barrier query, and two
                # identical status dicts are indistinguishable by value.
                self.status_seq += 1
            return []
        if cmd == WREP_CAPTURE:
            if ln >= 14 and len(b) - 4 >= 14:
                (mode, q, fps_x10, rate_bps, secs, payload_max,
                 res, pf) = struct.unpack_from("<BBHIHHBB", b, 4)
                # Zeros mean "bridge default" (camera_svc.c passes them
                # through untouched -- the defaults live here only).
                # Out-of-range res/pf never reach us: the HE service
                # refuses them outright (camera_svc.h).
                self.capture_cmd = {
                    "mode": mode,
                    "q": q or CAP_DEFAULT_Q,
                    "fps_x10": fps_x10 or CAP_DEFAULT_FPS_X10,
                    "rate_bps": rate_bps,           # 0 = fps-paced only
                    "secs": secs or CAP_DEFAULT_SECS,
                    "payload_max": min(payload_max or CAMERA_MAX_PAYLOAD,
                                       CAMERA_MAX_PAYLOAD),
                    "res": res or CAP_DEFAULT_RES,
                    "pf": pf or CAP_DEFAULT_PF,
                }
            return []
        self.stats["unknown_cmds"] += 1
        return []

    def take_capture(self):
        """Fetch-and-clear the last camera command (mirrors the HE-side
        single-slot last-wins mailbox)."""
        cmd = self.capture_cmd
        self.capture_cmd = None
        return cmd

    # ---- camera data plane: JPEG -> chunks -> WCMD_PUB msgs ---------------

    def capture_pub_msgs(self, jpeg, frame_seq, payload_max):
        """One JPEG -> list of rpmsg messages (WCMD_PUB + WCMD_FRAG).

        Each chunk = CHUNK_HDR_FMT header + a JPEG slice, total <=
        payload_max (<= 1400, REV-28). Each chunk is one WCMD_PUB
        payload; payloads beyond the 492 B rpmsg budget continue in
        WCMD_FRAG msgs -- the same fragmentation shape as vcp_bytes(),
        reassembled by the HE's wire_frag + published verbatim on
        camera/stream.
        """
        mv = memoryview(jpeg)
        n = len(mv)
        if n == 0:
            return []
        data_max = payload_max - CHUNK_HDR_LEN
        if data_max <= 0:
            return []
        count = (n + data_max - 1) // data_max
        msgs = []
        off = 0
        for idx in range(count):
            take = min(data_max, n - off)
            total = CHUNK_HDR_LEN + take
            if total <= MSG_PAYLOAD:
                # S23 bite 2 fast path (the only reachable shape at the
                # 1524 B budget): both headers packed in place, the JPEG
                # slice copied ONCE -- no intermediate payload object.
                buf = bytearray(4 + total)
                struct.pack_into("<BBH", buf, 0, WCMD_PUB, 0, total)
                struct.pack_into(CHUNK_HDR_FMT, buf, 4, frame_seq, idx,
                                 count, take)
                buf[4 + CHUNK_HDR_LEN:] = mv[off:off + take]
                msgs.append(buf)
                off += take
            else:
                # Legacy spill path: kept for smaller-budget stacks and
                # host tests; byte-identical wire shape to pre-S23.
                payload = struct.pack(CHUNK_HDR_FMT, frame_seq, idx, count,
                                      take) + mv[off:off + take]
                off += take
                first = min(total, MSG_PAYLOAD)
                msgs.append(struct.pack("<BBH", WCMD_PUB, 0, total) +
                            payload[:first])
                p = first
                while p < total:
                    part = min(total - p, MSG_PAYLOAD)
                    msgs.append(struct.pack("<BBH", WCMD_FRAG, 0, part) +
                                payload[p:p + part])
                    p += part
            self.stats["cap_chunks"] += 1
        self.stats["cap_frames"] += 1
        self.stats["cap_bytes"] += n
        return msgs

    def _encode(self, frame_mv, n):
        w = uc.frame_encode_into(self._wire, self._payload_buf, frame_mv, n)
        self.stats["he2pi_frames"] += 1
        self.stats["he2pi_bytes"] += n
        return bytes(self._wire[:w])

    def he_frame_wire(self, b):
        """S23 drain fast path: a COMPLETE `WCMD_FRAME_TX` message ->
        a memoryview of the encoded wire, or None = caller must fall
        back to he_msg (frags, replies, resync, oversize).

        Zero ~1.5 KB allocations: the returned memoryview aliases
        self._wire and is valid only until the NEXT encode on this
        core -- write it to the VCP before touching the core again.
        he_msg stays the allocating general path (host tests, spill
        shapes); test_bridge_core pins the two paths byte-identical.
        """
        if len(b) < 4:
            return None
        cmd, _port, ln = struct.unpack_from("<BBH", b, 0)
        if (cmd != WCMD_FRAME_TX or self.reasm is not None
                or ln > MAX_L2 or len(b) - 4 < ln):
            return None         # he_msg owns resync + error accounting
        w = uc.frame_encode_into(self._wire, self._payload_buf,
                                 memoryview(b)[4:4 + ln], ln)
        self.stats["he2pi_frames"] += 1
        self.stats["he2pi_bytes"] += ln
        return memoryview(self._wire)[:w]

    # ---- Pi -> HE ---------------------------------------------------------

    def vcp_bytes(self, chunk):
        """Raw VCP bytes -> list of rpmsg messages for the HE endpoint.

        The splitter counts CRC/decode errors itself (stray text resyncs
        at the next 0x00 -- bm_sbc behaves identically on its end).
        """
        msgs = []
        for frame in self.splitter.feed(chunk):
            n = len(frame)
            if n > MAX_L2:
                self.stats["oversize"] += 1
                continue
            first = min(n, MSG_PAYLOAD)
            msgs.append(struct.pack("<BBH", WCMD_FRAME_RX, 1, n) +
                        frame[:first])
            off = first
            while off < n:
                chunk_n = min(n - off, MSG_PAYLOAD)
                msgs.append(struct.pack("<BBH", WCMD_FRAG, 1, chunk_n) +
                            frame[off:off + chunk_n])
                off += chunk_n
            self.stats["pi2he_frames"] += 1
            self.stats["pi2he_bytes"] += n
        return msgs

    # ---- control messages -------------------------------------------------

    @staticmethod
    def link_msg(up):
        return struct.pack("<BBHB", WCMD_LINK, 1, 1,
                           WCMD_LINK_UP if up else 0)

    @staticmethod
    def query_msg():
        return struct.pack("<BBH", WCMD_QUERY, 0, 0)

    @staticmethod
    def stream_msg(rate_bps, payload_len, secs):
        # wire_stream_t <IHH; rate_bps == 0 stops a running stream
        body = struct.pack("<IHH", rate_bps, payload_len, secs)
        return struct.pack("<BBH", WCMD_STREAM, 0, len(body)) + body

    @staticmethod
    def ping_msg(target_node_id, payload=b"S16 camera 2-hop"):
        body = struct.pack("<Q", target_node_id) + payload
        return struct.pack("<BBH", WCMD_PING, 0, len(body)) + body


def send_chunk_msgs(msgs, send, drain, every=CHUNK_DRAIN_EVERY, stats=None):
    """Push one frame's WCMD_PUB messages, servicing the HE->HP direction
    every `every` messages. Returns the number of drain calls made.

    S19 bite 2 -- NOT pacing (pacing was measured and does not fix the
    heap wall, DESIGN §S19). This exists because the HE-side fix makes
    the HE stop consuming inbound rpmsg when its own TX backs up: the
    HP->HE vring then fills and `ept.send` blocks, and a send loop that
    is not draining `he.queue` meanwhile would stall both directions
    until the 1 s send timeout expires -- costing rpmsg drops and a
    broken frame. Draining as we push removes that.

    With `stats` (S23 relay profile), each send and each drain is timed
    into cap_ept_us / cap_pump_us so cap_send_us stops being a lump.
    """
    drains = 0
    for i, m in enumerate(msgs):
        if stats is None:
            send(m)
        else:
            t0 = _ticks_us()
            send(m)
            dt = _elapsed(t0, _ticks_us())
            stats["cap_ept_us"] += dt
            if dt > stats["cap_ept_max_us"]:
                stats["cap_ept_max_us"] = dt
            if dt > 1000:
                stats["cap_ept_slow"] += 1
        if every and (i + 1) % every == 0:
            if stats is None:
                drain()
            else:
                t0 = _ticks_us()
                drain()
                stats["cap_pump_us"] += _elapsed(t0, _ticks_us())
            drains += 1
    if not every or not msgs or len(msgs) % every:
        if stats is None:
            drain()      # always finish on a drain
        else:
            t0 = _ticks_us()
            drain()
            stats["cap_pump_us"] += _elapsed(t0, _ticks_us())
        drains += 1
    return drains


# --------------------------------------------------------------------------
# On-target service (MicroPython only below this line)
# --------------------------------------------------------------------------

def _trace(msg):
    try:
        with open(TRACE_PATH, "a") as f:
            f.write("%d %s\n" % (time.ticks_ms(), msg))
    except Exception:
        pass


def _load_cfg():
    try:
        with open(CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _he_page():
    """(magic_ok, tick) from the BMHE status page, no rpmsg needed."""
    import machine
    if machine.mem32[BM_STATUS_PAGE] & 0xFFFFFFFF != 0x424D4845:  # 'BMHE'
        return (False, 0)
    return (True, machine.mem32[BM_STATUS_PAGE + 12] & 0xFFFFFFFF)


def _he_running():
    """A stale HE from a previous HP boot survives warm resets (S14 fact):
    magic present AND tick advancing across ~60 ms."""
    ok, t0 = _he_page()
    if not ok:
        return False
    time.sleep_ms(60)
    return _he_page()[1] != t0


def _dump_he_ring(tag):
    """HE debug ring -> trace file (the ring carries ping.c's acceptance
    line and the stream publisher's start/done narrative)."""
    try:
        import machine
        import uctypes
        if machine.mem32[BM_STATUS_PAGE] & 0xFFFFFFFF != 0x424D4845:
            return
        addr = machine.mem32[BM_STATUS_PAGE + 28] & 0xFFFFFFFF
        size = machine.mem32[BM_STATUS_PAGE + 32] & 0xFFFFFFFF
        widx = machine.mem32[BM_STATUS_PAGE + 36] & 0xFFFFFFFF
        if not addr or not size:
            return
        ring = bytes(uctypes.bytearray_at(addr, size))
        n = min(widx, size)
        start = widx % size if widx > size else 0
        text = (ring[start:n] + ring[:start]) if widx > size else ring[:n]
        with open(TRACE_PATH, "a") as f:
            f.write("---- HE ring (%s) ----\n" % tag)
            f.write(text.decode())
            f.write("\n---- end ring ----\n")
    except Exception:
        pass


class UsbVcp:
    """USB CDC via the console streams (pattern: s14_relay_pump.py)."""

    def __init__(self, stats=None):
        import select
        self._in = sys.stdin.buffer
        self._out = sys.stdout.buffer
        self._poll = select.poll()
        self._poll.register(self._in, 1)  # select.POLLIN
        self._stats = stats     # S23 relay profile: BridgeCore.stats

    def any(self):
        return 1 if self._poll.poll(0) else 0

    def read_available(self, max_bytes=2048):
        out = bytearray()
        while len(out) < max_bytes and self._poll.poll(0):
            b = self._in.read(1)
            if not b:
                break
            out += b
        return bytes(out)

    def write(self, data):
        stats = self._stats
        t0 = _ticks_us() if stats is not None else 0
        mv = memoryview(data)
        total = 0
        n = len(mv)
        while total < n:
            w = self._out.write(mv[total:])
            if w:
                total += w
        if stats is not None:
            dt = _elapsed(t0, _ticks_us())
            stats["vcp_us"] += dt
            if dt > stats["vcp_max_us"]:
                stats["vcp_max_us"] = dt
            stats["vcp_writes"] += 1
            stats["vcp_bytes"] += n


class HeWire:
    """rpmsg 'bm-wire' endpoint client (load-once rules per README)."""

    def __init__(self):
        self.ept = None
        self.queue = []
        self.q_drops = 0

    def _ns(self, src, name):
        if name == "bm-wire":
            import openamp
            self.ept = openamp.Endpoint("bm-wire", self._rx, dest=src)

    def _rx(self, src, data):
        if len(self.queue) >= RPMSG_QUEUE_CAP:
            self.q_drops += 1
            return
        self.queue.append(bytes(data))

    def start(self):
        """Load the HE ELF once per service boot (README lifecycle rules).
        A stale HE mid-traffic cannot be safely re-attached -- abort with
        the recovery pair instead."""
        import openamp
        if _he_running():
            raise OSError(
                "stale HE still running from a previous boot -- recovery: "
                "sudo uhubctl -l 3 -p 1 -a cycle -d 3, then mpremote reset")
        openamp.new_service_callback(self._ns)
        rp = openamp.RemoteProc(ELF_PATH)
        rp.start()
        t0 = time.ticks_ms()
        while self.ept is None:
            if time.ticks_diff(time.ticks_ms(), t0) > 8000:
                raise OSError("bm-wire never announced (he page: %r)"
                              % (_he_page(),))
            time.sleep_ms(5)
        return rp

    def send(self, msg, timeout=1000):
        self.ept.send(msg, timeout=timeout)


def sensor_steps(cur_res, cur_pf, want_res, want_pf):
    """Sensor calls needed to reach (want_res, want_pf), in apply order.

    Pure -- host-tested without a sensor. Returns () when nothing
    changed: every call here is a sensor re-init (the D15 crash class),
    and S18 hands that trigger to a web page.

    The order is not stylistic, it is the only one that survives.
    MEASURED on the bench 2026-08-15 (S18 probes 1-4, bench/probes/):

    * With the HE core loaded, GROWING the framebuffer kills the board
      outright -- USB off the bus, no Python exception, nothing to catch.
      The HE ELF lives at 0x60080000 (SRAM9_B upper half) and OpenMV's
      allocator grows into it. QVGA (128,000 B) stays clear; VGA
      (512,000 B) does not.
    * The trigger is the framebuffer COUNT reflowing: OpenMV sizes the
      count to fit the pool, so an unpinned shrink quietly re-allocates
      several buffers and the next grow has to expand the pool. Pinning
      set_framebuffers(1) IMMEDIATELY BEFORE every set_framesize stops
      the reflow -- proven repeatably across the full ladder incl. HD.
    * A pixel-format change reallocates too (RGB565 -> GRAYSCALE halves
      the buffer), so it takes the same pinned path and re-applies
      set_framesize even when the resolution is unchanged.

    Everything above the ceiling claimed before the HE loaded is
    off-limits; CaptureEngine enforces that, not this function.
    """
    if cur_res == want_res and cur_pf == want_pf:
        return ()
    steps = []
    if cur_pf != want_pf:
        steps.append("pixformat")
    # pin the count, THEN resize -- never the other way round
    steps.append("framebuffers")
    steps.append("framesize")
    steps.append("settle")      # first frames after a re-init are garbage
    return tuple(steps)


def ref_asset_names(res, pf):
    """Candidate ref-scene filenames for (res, pf), preference order.

    Pure -- host-tested. Raw first (the S0 originals: 24-bit BMP /
    binary PGM, byte-comparable to the S0 encode table), q95 JPEG
    second (staged instead when /flash is tight; the decode-once
    recompression bias is small but real, so the matrix output records
    which set was loaded -- the bridge traces the filename).
    """
    w, h = RES_GEOM.get(res, (0, 0))
    if pf == CAMERA_PF_MONO:
        return ("ref_mono_%dx%d.pgm" % (w, h),
                "ref_mono_%dx%d.jpg" % (w, h))
    return ("ref_color_%dx%d.bmp" % (w, h),
            "ref_color_%dx%d.jpg" % (w, h))


class PublishGate:
    """Keeps a sensor re-init from overlapping an HE publish.

    Pure -- host-tested without a board or an HE core.

    WHY THIS IS NOT A try/except. S18 bite B2 nibble 1 measured the
    hazard three ways off-chain, one ingredient at a time
    (bench/probes/s18_reinit_probe{,_b,_c}.py):

      * no HE core loaded          -- re-init at a 0 ms delay is safe,
                                      12/12 across QVGA, VGA and HD;
      * HE core loaded but IDLE    -- safe, 9/9;
      * HE core loaded + PUBLISHING-- the FIRST re-init took the board off
                                      the USB bus. No Python exception, no
                                      traceback, nothing to catch; recovery
                                      is a reboot of the host Pi.

    So the overlap has to not happen. Catching and recovering is not an
    option that the failure mode leaves open.

    THE BINDING CONDITION IS TIME (rungs C-F falsified everything else):
    REINIT_MIN_QUIET_MS of wall clock since the last publish, measured
    from note_chunks. The three conditions below it (barrier, heap,
    stream counters) are cheap, still true, and guard cases the clock
    cannot see (an HE backed up beyond the window; the synthetic stream
    publisher running) -- but none of them is sufficient without the
    clock, and that was measured, not assumed.

    THE BARRIER IS THE WIRE'S OWN ORDERING, NOT A TIMER. The HE wire task
    consumes the inbound vring in order and publishes each WCMD_PUB inline
    before it reads the next message (main.c rr_poll), so a WCMD_QUERY
    posted straight after a frame's last chunk is not processed until
    every one of those chunks has been published. The WREP_STATUS that
    comes back is therefore proof of drain. It costs no new wire commands:
    both ends have spoken WCMD_QUERY/WREP_STATUS since S14, so this is
    bridge-only -- no ABI change, no HE rebuild, no fork pin move.

    Note the TRACKER's original suggestion -- gate on
    wire_status_t.stream_sent -- does NOT work for the camera path: that
    counter belongs to the synthetic WCMD_STREAM publisher (main.c:361).
    The camera's pub_ok lives in camera_rep_t and goes to the Pi, never
    to the bridge. It IS the right signal for a different hazard -- the
    third condition below.

    SECOND CONDITION. bm_pub returns when the L2 frame is enqueued, not
    when it is on the wire, so the reply alone can still leave transmit
    copies on the HE heap -- exactly 1,488 B per 1,400 B chunk (S19 bite 1,
    measured). heap_free recovering to within REINIT_HEAP_SLACK of its
    learned high-water is the second condition. The high-water is LEARNED
    rather than hard-coded, so it survives an HE build whose idle heap
    differs, and a status that carries no heap_free simply does not gate
    on it.

    THIRD CONDITION (nibble 2 self-review amendment). The barrier only
    proves OUR chunks drained. The HE has a second publisher the bridge
    never feeds: the synthetic WCMD_STREAM relay stream (main.c s_stream
    task), which bm_pub's continuously for up to 600 s during the ledger
    regression -- and a re-init overlapping THAT is the same measured
    hazard. Its counters are stream_sent/stream_errs, so the gate opens
    only when TWO consecutive replies carry identical stream counters. A
    live stream advances them every sample and the command is refused at
    the deadline (correct: there is no safe moment); an idle one costs
    one extra query round trip, a few ms.

    NEVER OPENING IS NOT FATAL. Past `deadline_ms` the command is REFUSED
    -- which is exactly what the bridge already does for a sensor it
    cannot reach, and which the HE-side ledger already shows as zero
    publishes. A refused capture costs one image. A re-init into a live
    publish costs the bench.
    """

    def __init__(self, deadline_ms=REINIT_DEADLINE_MS,
                 requery_ms=REINIT_REQUERY_MS,
                 heap_slack=REINIT_HEAP_SLACK,
                 min_quiet_ms=REINIT_MIN_QUIET_MS):
        self.deadline_ms = deadline_ms
        self.requery_ms = requery_ms
        self.heap_slack = heap_slack
        self.min_quiet_ms = min_quiet_ms
        self.t_chunks = 0           # when the last chunks were handed over
        self.pending = False        # chunks handed over since the last clear
        self.barrier_seq = None     # status_seq that must advance
        self.t_wait = None          # when the held command started waiting
        self.t_query = 0            # when the barrier query went out
        self.wait_counters = None   # (stream_sent, stream_errs), 1st sample
        self.wait_seq = 0           # status_seq that sample was taken from
        self.heap_high = 0          # learned idle high-water of HE heap_free
        # Counters, traced at exit -- a gate that silently costs images is
        # worse than no gate.
        self.opens = 0
        self.refusals = 0
        self.wait_ms_max = 0

    def note_chunks(self, n, now):
        """A frame's chunks have just been handed to the HE."""
        if n > 0:
            self.pending = True
            self.t_chunks = now         # the quiet clock starts HERE
            self.barrier_seq = None     # any earlier barrier is stale now

    def note_status(self, status):
        """Learn the idle heap high-water from any status that arrives."""
        if status:
            hf = status.get("heap_free", 0)
            if hf > self.heap_high:
                self.heap_high = hf

    def _heap_ok(self, status):
        if not status or not self.heap_high:
            return True             # nothing to compare against yet
        return status.get("heap_free", 0) >= self.heap_high - self.heap_slack

    def poll(self, now, status_seq, status):
        """One decision per loop pass. Returns a GATE_* verdict.

        GATE_QUERY asks the caller to send core.query_msg(); the caller
        must then call armed() so the barrier knows what to wait for. The
        gate never sends anything and never sleeps -- blocking this loop
        would stop draining the HE, which is its own hazard (the S19 bite 2
        deadlock).
        """
        if not self.pending:
            return GATE_GO
        if self.t_wait is None:
            self.t_wait = now
        waited = _elapsed(self.t_wait, now)
        if waited >= self.deadline_ms:
            self.refusals += 1
            # Reset the WAIT, not the pending flag. Chunks we were never
            # told had drained are still, as far as we know, in flight --
            # so the NEXT command must post its own barrier rather than
            # inheriting a clean bill of health from a refusal.
            self._reset_wait()
            return GATE_REFUSE
        # THE binding condition (rungs C-F): wall-clock quiet since the
        # last publish. The clock runs from note_chunks, not from when
        # the command arrived -- a command at a human pace finds the
        # window already elapsed and pays only the barrier (~ms); only a
        # fast follow-up actually waits. No queries until it has passed:
        # the barrier cannot prove anything the clock has not.
        if _elapsed(self.t_chunks, now) < self.min_quiet_ms:
            return GATE_WAIT
        if self.barrier_seq is None:
            return GATE_QUERY
        if status_seq > self.barrier_seq:
            counters = self._stream_counters(status)
            if self.wait_counters is None:
                # First reply of this wait: proof OUR chunks drained, but
                # one reply says nothing about the synthetic stream
                # publisher. Sample its counters and ask once more.
                self.wait_counters = counters
                self.wait_seq = status_seq
                return GATE_QUERY
            if status_seq > self.wait_seq:
                # A LATER reply than the sample -- comparing a reply with
                # itself would wave a live stream through.
                if counters == self.wait_counters and self._heap_ok(status):
                    self.opens += 1
                    if waited > self.wait_ms_max:
                        self.wait_ms_max = waited
                    self._clear()
                    return GATE_GO
                # Counters moved (stream live) or heap still low: this
                # reply becomes the new sample and we keep watching.
                self.wait_counters = counters
                self.wait_seq = status_seq
        if _elapsed(self.t_query, now) >= self.requery_ms:
            return GATE_QUERY       # reply lost, stream live, or heap low
        return GATE_WAIT

    def armed(self, status_seq, now):
        """The caller has just sent the barrier query."""
        self.barrier_seq = status_seq
        self.t_query = now

    def _clear(self):
        self.pending = False
        self._reset_wait()

    def _reset_wait(self):
        self.barrier_seq = None
        self.t_wait = None
        self.wait_counters = None   # stability is proven per wait, never
        self.wait_seq = 0           #   remembered across one

    @staticmethod
    def _stream_counters(status):
        if not status:
            return (0, 0)
        return (status.get("stream_sent", 0), status.get("stream_errs", 0))


def _elapsed(t0, now):
    """ticks_diff that also works on plain ints in host tests."""
    try:
        return time.ticks_diff(now, t0)
    except AttributeError:          # CPython: time has no ticks_diff
        return now - t0


def _ticks_us():
    """ticks_us that also works in CPython host tests."""
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.time() * 1e6)


class CaptureEngine:
    """Camera capture/encode for the S17 camera service (HP side).

    Commands arrive parsed from WREP_CAPTURE (BridgeCore.take_capture);
    poll() returns a JPEG to chunk-and-send when a capture is due.
    Frames are REAL camera captures (whatever the bench scene is) --
    the rate target was committed against reef-scene encode numbers
    (bite 0), a dim scene runs lighter than target, and that is stated
    in the demo, not hidden.

    Sensor bring-up is EAGER (bootstrap(), before the HE ELF loads) and
    its failure is not fatal to the bridge: the relay keeps running, the
    refusal is traced, and the HE-side service ledger shows zero
    publishes.

    Eager, not lazy (changed from S17 after the S18 probes): the
    framebuffer ceiling has to be claimed while the SRAM9_B region the
    HE core will occupy is still free. Once the HE is loaded, the
    allocator can only be asked for the same size or smaller -- asking
    for more takes the whole board off the USB bus with no catchable
    error. So the ceiling is claimed up front and enforced thereafter.
    """

    def __init__(self, ceiling=None, scene="sensor"):
        self.mode = CAMERA_MODE_STOP
        self.sensor_ok = None       # None = not tried yet
        self.booted = False         # bootstrap() ran and claimed a ceiling
        self.ceiling = ceiling or CAP_DEFAULT_CEILING
        self.scene = scene          # "sensor" | "ref" (S18 reef matrix)
        self.ref_img = None         # loaded reference image (ref mode)
        self.ref_key = None         # (res, pf) the loaded image matches
        self.cur_res = None         # geometry the sensor is actually holding
        self.cur_pf = None          # (None = unknown -> next cmd re-applies)
        self.q = CAP_DEFAULT_Q
        self.enc_420 = False        # force 4:2:0 chroma (color only, S23)
        self.interval_ms = 100
        self.rate_bps = 0
        self.payload_max = CAMERA_MAX_PAYLOAD
        self.until = 0
        self.t_start = 0
        self.slots = 0              # pacing slots consumed this stream
        self.caps = 0               # total frames captured (lifetime)
        self.sent_bytes = 0         # JPEG bytes this stream (rate cap)
        self.enc_us = 0             # encode time this command (matrix column)
        self.enc_frames = 0
        # S23 capture/encode overlap (re-opens D21 -- that verdict was
        # about the dead per-byte-polled SPI TX path, not this stack).
        # The csi module's snapshot(blocking=False) returns None on
        # WOULD_BLOCK and leaves the one-shot capture ARMED (verified in
        # ports/alif/omv_csi.c: CAM_CTRL_SNAPSHOT started before the
        # wait loop), so the sensor exposes/reads out while the main
        # loop chunk-and-sends the previous JPEG.
        self._csi = None            # csi handle behind the sensor shim
        self._nb = None             # None=unprobed, False=fallback, True=on
        self._img_ready = None      # frame collected early by a kick
        self.cap_pending = False    # a kicked capture is in flight
        # Double-buffer overlap (S23 GOLD): with 2 framebuffers the kick
        # moves BEFORE encode -- capture N+1 fills the free buffer while
        # buffer N is encoded, hiding the measured ~33 ms capture behind
        # the ~49 ms VGA encode. Enabled ONLY when 2 buffers fit inside
        # the bootstrap ceiling's high-water block WITH slack: the
        # sticky-fb allocator (patch 0001) reuses the block when the
        # request fits, so the S18 grow-off-bus hazard cannot fire; a
        # request at/over the block would gamble, so HD mono (2x
        # 1,024,000 = exactly the 2,048,000 claim) stays at 1.
        self.fb_count = 1           # what set_framebuffers currently holds

    def _framesize(self, sensor, res):
        if res == CAMERA_RES_VGA:
            return sensor.VGA
        if res == CAMERA_RES_HD:
            return sensor.HD
        return sensor.QVGA

    def _nb_handle(self):
        """The csi object behind the sensor shim, or None -> blocking
        fallback (old firmware / host fakes without _csi). Probed once;
        the verdict is traced so a fallback never hides silently."""
        if self._nb is None:
            try:
                import sensor
                self._csi = getattr(sensor, "_csi", None)
            except Exception:
                self._csi = None
            self._nb = self._csi is not None
            _trace("camera: non-block capture %s"
                   % ("ENABLED" if self._nb
                      else "unavailable -- blocking snapshot fallback"))
        return self._csi

    def _fb2_fits(self, res, pf):
        """True when TWO buffers of (res, pf) sit strictly inside the
        bootstrap ceiling's high-water block, with 64 KB slack for the
        allocator's queue/metadata overhead (which this side cannot
        see exactly -- strictly-inside is the whole safety argument)."""
        w, h = RES_GEOM.get(res, (0, 0))
        cw, ch = RES_GEOM.get(self.ceiling, (0, 0))
        frame = w * h * (1 if pf == CAMERA_PF_MONO else 2)
        return frame > 0 and 2 * frame + 65536 <= cw * ch * 2

    def _set_fb_count(self, n):
        """Change the buffer count, quiesced. No-op when already there."""
        if self.fb_count == n:
            return
        self._quiesce()              # never resize with a capture in flight
        import sensor
        sensor.set_framebuffers(n)
        self.fb_count = n
        _trace("camera: framebuffers -> %d" % n)

    def _quiesce(self):
        """Collect (and discard) any in-flight capture BEFORE the sensor
        is touched. A geometry/format change with the CSI DMA mid-frame
        is the S18 hazard class; every re-init must enter with the
        capture pipeline idle so the B2 model stays intact."""
        if not self.cap_pending and self._img_ready is None:
            return
        try:
            if self.cap_pending and self._csi is not None:
                self._csi.snapshot()        # blocking collect, discarded
        except Exception as e:
            _trace("camera: quiesce snapshot failed: %r" % e)
        self.cap_pending = False
        self._img_ready = None

    def bootstrap(self):
        """Claim the framebuffer CEILING. MUST run before the HE loads.

        The call order is forced by the driver and was learned live
        (S18 probes, bench/probes/): set_framebuffers() refuses until
        BOTH a pixel format and a frame size exist -- but calling
        set_framesize(HD) while the count is still unpinned is exactly
        the unpinned allocation that later kills the board. So: come up
        at QVGA (128,000 B, always safe), pin the count there, and only
        then grow to the ceiling.

        Failure is non-fatal and LATCHES the camera off: if the ceiling
        was never claimed, no later command may touch the allocator,
        because growing it post-HE-load is unrecoverable.
        """
        try:
            import sensor
            sensor.reset()
            sensor.set_pixformat(sensor.RGB565)
            sensor.set_framesize(sensor.QVGA)    # small: legalises the pin
            sensor.set_framebuffers(1)           # pin BEFORE the big alloc
            self.fb_count = 1
            sensor.set_framesize(self._framesize(sensor, self.ceiling))
            sensor.skip_frames(time=300)
            self.cur_res, self.cur_pf = self.ceiling, CAMERA_PF_COLOR
            self.sensor_ok = True
            self.booted = True
            w, h = RES_GEOM.get(self.ceiling, (0, 0))
            _trace("camera: ceiling claimed %dx%d RGB565 (pre-HE)" % (w, h))
            return True
        except Exception as e:
            self.sensor_ok = False
            self.booted = False
            self.cur_res = self.cur_pf = None
            _trace("camera: bootstrap FAILED: %r -- camera disabled for this "
                   "run, relay unaffected" % e)
            return False

    def needs_reinit(self, cmd):
        """Would this command actually touch the sensor?

        Only a real geometry/format delta re-initialises, and only a
        re-init is the S18 bite B2 hazard -- so a repeat capture at the
        same settings must NOT pay the gate's latency. A stop never
        touches the sensor at all. Same pure planner _ensure_sensor uses,
        so the two cannot disagree.
        """
        if cmd is None or cmd.get("mode") == CAMERA_MODE_STOP:
            return False
        res = cmd.get("res", CAP_DEFAULT_RES)
        pf = cmd.get("pf", CAP_DEFAULT_PF)
        return bool(sensor_steps(self.cur_res, self.cur_pf, res, pf))

    def _ensure_sensor(self, res, pf):
        """Bring the sensor to (res, pf); no-op when already there.

        Failure is never fatal to the bridge -- the relay keeps running,
        the refusal is traced, and the HE service ledger shows zero
        publishes. A failure MID-SWITCH leaves the real geometry unknown,
        so we forget it and let the next command re-apply from scratch
        rather than trusting a half-applied state.
        """
        if not self.booted or self.sensor_ok is not True:
            return False
        # THE guard. Above the claimed ceiling the allocator would grow
        # into the loaded HE core and take the board off the USB bus with
        # no catchable error, so this refusal is the only thing standing
        # between a web-page click and a bricked bench.
        if res > self.ceiling:
            _trace("camera: res %d REFUSED -- above the %d ceiling claimed "
                   "before the HE loaded" % (res, self.ceiling))
            return False
        try:
            self._apply(res, pf)
            return True
        except Exception as e:
            self.cur_res = self.cur_pf = None    # geometry now unknown
            _trace("camera: sensor setup FAILED res=%d pf=%d: %r"
                   % (res, pf, e))
            # S18 bite B2 self-heal (the backstop). The wedge this catches
            # was measured (rung E): after one 'Sensor control failed.'
            # every later set_framebuffers fails instantly, for the rest
            # of the session -- previously curable only by restarting the
            # bridge. Whether reset + re-bootstrap clears it is UNPROVEN
            # (rung F could not provoke the wedge to test it), but the
            # worst case is exactly today's behaviour, and the trace line
            # records the outcome so the bench accumulates the answer.
            # Safe to run here: the caller sits behind PublishGate's
            # quiet window, and re-claiming the SAME ceiling under a live
            # HE is the proven s18_hd_probe path.
            _trace("camera: self-heal -- reset + re-bootstrap + one retry")
            try:
                if not self.bootstrap():
                    return False     # bootstrap latched the camera off
                self._apply(res, pf)
                _trace("camera: self-heal SUCCEEDED")
                return True
            except Exception as e2:
                self.cur_res = self.cur_pf = None
                _trace("camera: self-heal FAILED: %r -- sensor wedged for "
                       "this run" % e2)
                return False

    def _apply(self, res, pf):
        """Apply the planned sensor steps; raises on failure."""
        import sensor
        steps = sensor_steps(self.cur_res, self.cur_pf, res, pf)
        if steps:
            self._quiesce()          # never re-init with a capture in flight
        for step in steps:
            if step == "pixformat":
                sensor.set_pixformat(sensor.GRAYSCALE
                                     if pf == CAMERA_PF_MONO
                                     else sensor.RGB565)
            elif step == "framebuffers":
                sensor.set_framebuffers(1)   # pin: stops the pool reflow
                self.fb_count = 1
            elif step == "framesize":
                sensor.set_framesize(self._framesize(sensor, res))
            elif step == "settle":
                sensor.skip_frames(time=300)
        self.cur_res, self.cur_pf = res, pf
        if steps:
            w, h = RES_GEOM.get(res, (0, 0))
            _trace("camera: sensor -> %dx%d %s (%s)"
                   % (w, h, PF_NAME.get(pf, "?"), ",".join(steps)))

    def _load_ref(self, res, pf):
        """Load the reef reference for (res, pf); True when ready.

        Loaded once per mode change, never per frame (HD color is a 2 MB
        heap object; per-frame loads would dominate the very number the
        matrix exists to measure). The previous image is freed FIRST:
        heap headroom is ~3.8 MB and the load needs contiguous space, so
        it only fits reliably with the old mode's image gone.

        Failure REFUSES the command upstream. Never a silent fall-through
        to the live scene -- dark-room bytes look plausible in a table
        and would poison the matrix invisibly.
        """
        key = (res, pf)
        if self.ref_key == key and self.ref_img is not None:
            return True
        import gc
        self.ref_img = None
        self.ref_key = None
        gc.collect()
        import image
        for name in ref_asset_names(res, pf):
            path = REF_SCENE_DIR + "/" + name
            try:
                t0 = time.ticks_ms()
                self.ref_img = image.Image(path)
                self.ref_key = key
                free = gc.mem_free() if hasattr(gc, "mem_free") else -1
                _trace("camera: ref scene %s loaded in %d ms (heap free %d)"
                       % (name, _elapsed(t0, time.ticks_ms()), free))
                return True
            except Exception as e:
                _trace("camera: ref scene %s not usable: %r" % (name, e))
        _trace("camera: ref scene MISSING for res=%d pf=%d -- stage "
               "/flash/ref_scene (demo_up.sh does it)" % (res, pf))
        return False

    def command(self, cmd):
        if cmd["mode"] == CAMERA_MODE_STOP:
            if self.mode != CAMERA_MODE_STOP:
                _trace("camera: stop (%d frames, %d B this stream)"
                       % (self.slots, self.sent_bytes))
            self.mode = CAMERA_MODE_STOP
            self._quiesce()          # don't leave a kicked capture behind
            return
        res = cmd.get("res", CAP_DEFAULT_RES)
        pf = cmd.get("pf", CAP_DEFAULT_PF)
        # Refusals come BEFORE the sensor re-init. The transition itself
        # is the measured hazard (S18 HD-stability nibble 1: transitions
        # degrade the board while the HE core is resident), so a command
        # this method is going to refuse must not pay one -- the shipped
        # order ran the full HD re-init and then refused, which is the
        # exact death the guard existed to prevent.
        if self.scene == "ref":
            # The HD-ref guard is LIFTED (S18 HD-stability, 2026-08-18).
            # Matrix run 5's "HD ref hard fault" was the sensor
            # TRANSITION dying under the stock firmware's per-resize
            # realloc -- no ref byte was ever loaded (no trace line).
            # On the sticky-fb build the transition soak runs 40/40 and
            # probe G6 measured both HD refs loading AND encoding with
            # the HE resident (color: 2 MB decoded, heap floor 1.92 MB,
            # enc 300.8 ms / 93,253 B @ q50).
            if not self._load_ref(res, pf):
                _trace("camera: cmd mode %d REFUSED -- no ref scene"
                       % cmd["mode"])
                return
        if not self._ensure_sensor(res, pf):
            _trace("camera: cmd mode %d REFUSED -- no sensor" % cmd["mode"])
            return
        # Double-buffer when it fits strictly inside the claimed ceiling
        # (sticky-fb reuses the block: no allocator touch, no S18 grow
        # hazard). Streams only; stills gain nothing from a second fb.
        if (cmd["mode"] == CAMERA_MODE_STREAM
                and self._nb_handle() is not None
                and self._fb2_fits(res, pf)):
            self._set_fb_count(2)
        else:
            self._set_fb_count(1)
        self.q = cmd["q"]
        # 4:2:0 chroma for every color encode, at every q (S23 bite 0,
        # Nick 2026-08-18): -14% encode / -7% bytes measured on the reef
        # refs (s22_enc_matrix). Mono NEVER gets the kwarg -- the encoder
        # has no subsampling knob for grayscale and forcing one there is
        # unmeasured territory.
        self.enc_420 = (pf == CAMERA_PF_COLOR)
        self.interval_ms = 10000 // cmd["fps_x10"]   # fps_x10 -> ms/frame
        self.rate_bps = cmd["rate_bps"]
        self.payload_max = cmd["payload_max"]
        self.mode = cmd["mode"]
        self.t_start = time.ticks_ms()
        self.until = time.ticks_add(self.t_start, cmd["secs"] * 1000)
        self.slots = 0
        self.sent_bytes = 0
        self.enc_us = 0
        self.enc_frames = 0
        w, h = RES_GEOM.get(res, (0, 0))
        _trace("camera: mode %d %dx%d %s q=%d %dms/frame rate=%d secs=%d pmax=%d"
               % (self.mode, w, h, PF_NAME.get(pf, "?"), self.q,
                  self.interval_ms, self.rate_bps, cmd["secs"],
                  self.payload_max))

    def poll(self, now):
        """Capture+encode one frame if due; returns JPEG bytes or None."""
        if self.mode == CAMERA_MODE_STOP:
            return None
        if self.mode == CAMERA_MODE_STREAM:
            if time.ticks_diff(self.until, now) <= 0:
                # enc avg is the matrix's encode column, measured in the
                # real pipeline -- one trace line per stream, not per
                # frame (the trace file lives on flash).
                _trace("camera: stream done (%d frames, %d B, enc avg "
                       "%d us/frame)"
                       % (self.slots, self.sent_bytes,
                          self.enc_us // max(1, self.enc_frames)))
                self.mode = CAMERA_MODE_STOP
                return None
            el = time.ticks_diff(now, self.t_start)
            if el < (self.slots + 1) * self.interval_ms:
                return None          # next fps slot not due
            if self.rate_bps and self.sent_bytes * 8000 > self.rate_bps * el:
                return None          # over the byte budget, skip the slot
        import sensor
        csi = self._nb_handle()
        if csi is None:
            img = sensor.snapshot()  # ref mode still pays the capture cost
        elif self._img_ready is not None:
            img = self._img_ready    # a kick completed before we returned
            self._img_ready = None
            self.cap_pending = False
        else:
            # Collect the kicked capture -- or, first frame of a stream,
            # arm one. None = WOULD_BLOCK: the capture runs in hardware
            # while the main loop keeps draining; poll again next pass.
            img = csi.snapshot(blocking=False)
            if img is None:
                self.cap_pending = True
                return None
            self.cap_pending = False
        if self.scene == "ref":
            if self.ref_img is None:
                return None          # command() refused; belt and braces
            img = self.ref_img
        t0 = _ticks_us()
        if self.enc_420:
            import image
            jpg = img.to_jpeg(quality=self.q, copy=True,
                              subsampling=image.JPEG_SUBSAMPLING_420)
        else:
            jpg = img.to_jpeg(quality=self.q, copy=True)
        self.enc_us += _elapsed(t0, _ticks_us())
        self.enc_frames += 1
        b = jpg.bytearray()          # proven idiom (s6_video_tx.py)
        self.slots += 1
        self.caps += 1
        self.sent_bytes += len(b)
        if self.mode == CAMERA_MODE_SINGLE:
            self.mode = CAMERA_MODE_STOP
            _trace("camera: single enc %d us, %d B, q=%d"
                   % (self.enc_us, len(b), self.q))
        elif csi is not None and self.fb_count < 2:
            # fb=1 overlap: kick the NEXT capture now (copy=True above
            # freed the fb) so exposure/readout runs under the send of
            # THIS jpeg. At fb>=2 there is NO kick: the collect call
            # itself releases the just-encoded buffer, arms the next
            # capture into it, and returns the frame captured during the
            # previous cycle -- capture rides fully under encode. A rare
            # instantly-complete frame is kept for the next poll, never
            # dropped. Frame latency grows by up to one cycle (image
            # taken earlier than encoded) -- fine for this product,
            # stated here rather than hidden.
            k = csi.snapshot(blocking=False)
            if k is not None:
                self._img_ready = k      # complete already: nothing in flight
                self.cap_pending = False
            else:
                self.cap_pending = True
        return b


def main():
    core = BridgeCore()
    usb = UsbVcp(stats=core.stats)
    he = HeWire()
    cfg = _load_cfg()
    engine = CaptureEngine(
        ceiling=CFG_RES.get(str(cfg.get("ceiling", "hd")).lower(),
                            CAP_DEFAULT_CEILING),
        scene=str(cfg.get("scene", "sensor")).lower())

    # Keep ONE generation of the previous trace instead of deleting it.
    # Bench-earned (S18 bite A): a capture hard-faulted the board below
    # MicroPython -- no traceback, no exit record -- and the board came
    # back up running this launcher, whose first act was to wipe the only
    # evidence of what it had been doing. A crash you cannot read twice
    # is a crash you debug twice.
    try:
        import os
        try:
            os.remove(TRACE_PREV_PATH)
        except Exception:
            pass
        os.rename(TRACE_PATH, TRACE_PREV_PATH)
    except Exception:
        pass
    _trace("bridge up, cfg %s" % json.dumps(cfg))

    # THE console is the wire (found live, first chain bring-up): MicroPython
    # scans inbound VCP bytes for the interrupt char 0x03 and raises
    # KeyboardInterrupt in the running service -- and COBS output freely
    # contains 0x03, so bm_sbc's first frames kill the bridge. Disable it
    # for the service's whole life; ctrl-C therefore CANNOT stop the
    # bridge -- it stops itself when the Pi side goes quiet (below), and
    # the uhubctl cold cycle stays the hammer (cold boot = REPL).
    kbd_off = False
    try:
        import micropython
        micropython.kbd_intr(-1)
        kbd_off = True
    except Exception:
        pass
    _trace("kbd_intr %s" % ("disabled" if kbd_off else "UNAVAILABLE"))

    # ORDER IS LOad-BEARING: claim the framebuffer ceiling while SRAM9_B
    # is still free. After he.start() the allocator can only be asked for
    # the ceiling or less -- asking for more takes the board off the USB
    # bus with nothing to catch (S18 probes 1-2). Non-fatal: a failed
    # bootstrap disables the camera and leaves the relay untouched.
    engine.bootstrap()

    rp = he.start()
    _trace("he loaded, bm-wire announced")

    # Above the try: the finally traces the gate's ledger, and a phase-1
    # timeout returns THROUGH that finally -- an unbound name there would
    # mask the real reason the bridge exited.
    gate = PublishGate()        # S18 bite B2 -- see the class docstring
    pending_cmd = None          # camera command held until the gate opens
    last_status_seq = 0

    try:
        # Phase 1: hold WCMD_LINK down until the Pi actually speaks
        # (bm_sbc's gateway heartbeats on its UART port as soon as it
        # opens the tty). Until link-up the HE stack transmits nothing,
        # so the pipe stays quiet while unowned. Drain rpmsg meanwhile.
        # Bounded: a bridge nobody ever attaches to exits on its own
        # (ctrl-C is disabled -- see kbd_intr above).
        t0 = time.ticks_ms()
        while not usb.any():
            if time.ticks_diff(time.ticks_ms(), t0) > PHASE1_TIMEOUT_MS:
                _trace("phase 1 timeout -- no Pi attach, exiting")
                return
            if he.queue:
                he.queue.pop(0)
            time.sleep_ms(20)

        he.send(core.link_msg(True))
        usb.write(b"\x00")   # leading delimiter isolates any pre-link text
        t_link = time.ticks_ms()
        _trace("link up (first VCP bytes seen)")

        stream_cfg = cfg.get("stream") or None
        ping_cfg = cfg.get("ping") or None
        camera_cfg = cfg.get("camera") or None
        stream_sent = False
        ping_sent = False
        camera_sent = False
        last_stat = time.ticks_ms()
        last_rx = time.ticks_ms()

        def pump_he_to_pi():
            """Drain the HE->HP queue into the VCP. Hoisted out of the
            loop so the chunk sender can call it too (S19 bite 2)."""
            n = 0
            stats = core.stats
            while he.queue:
                m = he.queue.pop(0)
                t0 = _ticks_us()
                wire = core.he_frame_wire(m)   # zero-alloc fast path
                dt = _elapsed(t0, _ticks_us())
                stats["relay_enc_us"] += dt
                if dt > stats["relay_enc_max_us"]:
                    stats["relay_enc_max_us"] = dt
                if wire is not None:
                    usb.write(wire)            # consume before next encode
                else:
                    # Non-frame or spill shapes: the allocating general
                    # path (untimed by relay_enc -- rare by design).
                    for w2 in core.he_msg(m):
                        usb.write(w2)
                n += 1
            stats["pump_calls"] += 1
            stats["pump_msgs"] += n
            if n > stats["pump_batch_max"]:
                stats["pump_batch_max"] = n
            return n

        while True:
            idle = True

            # HE -> Pi
            if he.queue:
                idle = False
                pump_he_to_pi()

            # Pi -> HE
            if usb.any():
                idle = False
                last_rx = time.ticks_ms()
                for msg in core.vcp_bytes(usb.read_available()):
                    he.send(msg)
            elif time.ticks_diff(time.ticks_ms(), last_rx) > QUIET_EXIT_MS:
                # The Pi side heartbeats every ~10 s while alive; a long
                # silence means bm_sbc is gone. Exit cleanly -- this IS
                # the bridge's stop mechanism (ctrl-C is disabled).
                _trace("vcp quiet %d ms -- pi side gone, exiting"
                       % QUIET_EXIT_MS)
                break

            # one-shot triggers, delays counted from link-up
            now = time.ticks_ms()
            if stream_cfg and not stream_sent and \
                    time.ticks_diff(now, t_link) >= \
                    int(stream_cfg.get("delay", 10)) * 1000:
                rate = int(float(stream_cfg.get("mbps", 2.0)) * 1e6)
                he.send(core.stream_msg(rate,
                                        int(stream_cfg.get("payload", 1400)),
                                        int(stream_cfg.get("secs", 600))))
                stream_sent = True
                _trace("stream cmd sent: %s" % json.dumps(stream_cfg))
            if ping_cfg and not ping_sent and \
                    time.ticks_diff(now, t_link) >= \
                    int(ping_cfg.get("delay", 5)) * 1000:
                he.send(core.ping_msg(int(ping_cfg["target"], 0)))
                ping_sent = True
                _trace("ping cmd sent: %s" % json.dumps(ping_cfg))
            if camera_cfg and not camera_sent and \
                    time.ticks_diff(now, t_link) >= \
                    int(camera_cfg.get("delay", 10)) * 1000:
                # Local camera one-shot (bring-up aid): same engine path
                # as a service-triggered command, no HE round trip. Goes
                # through the same held-command slot so the B2 gate has
                # exactly one path to cover, not two.
                pending_cmd = {
                    "mode": (CAMERA_MODE_STREAM
                             if camera_cfg.get("mode", "stream") == "stream"
                             else CAMERA_MODE_SINGLE),
                    "q": int(camera_cfg.get("q", CAP_DEFAULT_Q)),
                    "fps_x10": int(float(camera_cfg.get("fps", 10.0)) * 10),
                    "rate_bps": int(float(camera_cfg.get("mbps", 0)) * 1e6),
                    "secs": int(camera_cfg.get("secs", CAP_DEFAULT_SECS)),
                    "payload_max": min(int(camera_cfg.get("payload",
                                                          CAMERA_MAX_PAYLOAD)),
                                       CAMERA_MAX_PAYLOAD),
                    "res": CFG_RES.get(str(camera_cfg.get("res", "qvga")).lower(),
                                       CAP_DEFAULT_RES),
                    "pf": CFG_PF.get(str(camera_cfg.get("pf", "color")).lower(),
                                     CAP_DEFAULT_PF),
                }
                camera_sent = True
                _trace("camera cfg armed: %s" % json.dumps(camera_cfg))

            # S17 camera service: commands from the HE (WREP_CAPTURE ->
            # take_capture), captures go back down as WCMD_PUB chunks
            # which the HE publishes on camera/stream.
            #
            # S18 bite B2: a command that re-initialises the sensor is HELD
            # until the HE has finished publishing the previous frame
            # (PublishGate). Held, not blocked -- this loop must keep
            # draining the HE while it waits, or the wait becomes the S19
            # deadlock. Last-wins while held, mirroring the HE's own
            # single-slot mailbox, so a fast double-click collapses to the
            # newest command instead of queueing two re-inits.
            cap_cmd = core.take_capture()
            if cap_cmd is not None:
                if pending_cmd is not None:
                    _trace("camera: command superseded while gated")
                pending_cmd = cap_cmd
            if pending_cmd is not None:
                if not engine.needs_reinit(pending_cmd):
                    engine.command(pending_cmd)     # no delta: no hazard
                    pending_cmd = None
                else:
                    verdict = gate.poll(now, core.status_seq, core.status)
                    if verdict == GATE_GO:
                        engine.command(pending_cmd)
                        pending_cmd = None
                    elif verdict == GATE_QUERY:
                        # SHORT timeout, and arm only on success. The
                        # default 1 s would park this loop inside ept.send
                        # when the HP->HE vring is full -- and this loop is
                        # what drains the other direction, so a blocking
                        # send here recreates the S19 bite 2 deadlock. A
                        # dropped barrier just costs one re-query.
                        try:
                            he.send(core.query_msg(), timeout=50)
                            gate.armed(core.status_seq, now)
                        except Exception as e:
                            _trace("camera: barrier query not sent (%r) -- "
                                   "will retry" % e)
                        idle = False
                    elif verdict == GATE_REFUSE:
                        # Deliberately NOT a re-init attempt. See PublishGate.
                        _trace("camera: re-init REFUSED -- HE never confirmed "
                               "the previous publish drained within %d ms; "
                               "command dropped rather than risk the board"
                               % REINIT_DEADLINE_MS)
                        pending_cmd = None
                    else:
                        idle = False        # waiting on the barrier reply
            # While a re-init is held, STOP capturing. A running stream
            # publishes ~15 frames a second, and every frame re-arms the
            # barrier -- so without this the gate would never open and a
            # mid-stream resolution change would always hit the deadline
            # and be refused. The pending command supersedes the current
            # mode anyway; letting the in-flight frame drain is the point.
            jpeg = engine.poll(now) if pending_cmd is None else None
            if jpeg is not None:
                idle = False
                t0 = _ticks_us()
                msgs = core.capture_pub_msgs(jpeg, engine.caps - 1,
                                             engine.payload_max)
                t1 = _ticks_us()
                send_chunk_msgs(msgs, he.send, pump_he_to_pi,
                                stats=core.stats)
                t2 = _ticks_us()
                core.stats["cap_asm_us"] += _elapsed(t0, t1)
                core.stats["cap_send_us"] += _elapsed(t1, t2)
                core.stats["cap_msgs"] += len(msgs)
                gate.note_chunks(len(msgs), now)
            if core.status is not None and core.status_seq != last_status_seq:
                last_status_seq = core.status_seq
                gate.note_status(core.status)

            # periodic stats snapshot (flash only -- the VCP is a data pipe)
            if time.ticks_diff(now, last_stat) >= 30000:
                last_stat = now
                _trace("stats %s splitter f=%d e=%d qdrops=%d"
                       % (json.dumps(core.stats), core.splitter.frames,
                          core.splitter.errors, he.q_drops))

            if idle:
                time.sleep_ms(1)
    finally:
        # Teardown on ANY exit (KeyboardInterrupt included): final ledger
        # + HE ring to the trace file, link down, HE stopped. End-of-life
        # stop is allowed by the lifecycle rules; stop->reload within one
        # boot is not -- the next session warm-resets first.
        _trace("exit stats %s splitter f=%d e=%d qdrops=%d"
               % (json.dumps(core.stats), core.splitter.frames,
                  core.splitter.errors, he.q_drops))
        # The gate costs images when it refuses; say so out loud rather
        # than letting a quiet refusal look like a camera that did nothing.
        _trace("exit gate opens=%d refusals=%d worst_wait=%d ms"
               % (gate.opens, gate.refusals, gate.wait_ms_max))
        try:
            he.send(core.link_msg(False))
            time.sleep_ms(50)   # let the HE see the link drop
        except Exception:
            pass
        _dump_he_ring("exit")
        try:
            rp.stop()
            _trace("he stopped")
        except Exception:
            _trace("he stop FAILED -- recovery: sudo uhubctl -l 3 -p 1 "
                   "-a cycle -d 3, then mpremote reset")
        if kbd_off:
            try:
                import micropython
                micropython.kbd_intr(3)   # console is a console again
            except Exception:
                pass
