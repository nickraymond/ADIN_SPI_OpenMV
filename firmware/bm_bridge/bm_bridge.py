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

MSG_PAYLOAD = 492           # 496 rpmsg budget - 4 B wire header
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
RES_GEOM = {CAMERA_RES_QVGA: (320, 200),
            CAMERA_RES_VGA: (640, 400),
            CAMERA_RES_HD: (1280, 800)}
PF_NAME = {CAMERA_PF_COLOR: "RGB565", CAMERA_PF_MONO: "GRAYSCALE"}
# bridge_cfg.json spells these as words; unknown spellings fall back to
# the defaults rather than failing the whole one-shot.
CFG_RES = {"qvga": CAMERA_RES_QVGA, "vga": CAMERA_RES_VGA, "hd": CAMERA_RES_HD}
CFG_PF = {"color": CAMERA_PF_COLOR, "mono": CAMERA_PF_MONO,
          "grayscale": CAMERA_PF_MONO, "greyscale": CAMERA_PF_MONO}
STATUS_FMT = "<Q16s16sIIIIIIIIIIII"   # wire_status_t (88 B)
STATUS_KEYS = ("node_id", "ip_ll", "ip_ucast", "stage", "err",
               "tx_frames", "rx_frames", "tx_oversize", "link_up",
               "heap_free", "heap_min", "tx_dropped", "frag_errors",
               "stream_sent", "stream_errs")

CFG_PATH = "/flash/bridge_cfg.json"
CRASH_PATH = "/flash/bridge_crash.txt"
TRACE_PATH = "/flash/bridge_trace.txt"
ELF_PATH = "/flash/bm_he.elf"
BM_STATUS_PAGE = 0x600BFE00
RPMSG_QUEUE_CAP = 256       # HE->bridge backlog cap (drops counted)
PHASE1_TIMEOUT_MS = 600000  # no Pi attach in 10 min -> clean exit
QUIET_EXIT_MS = 30000       # linked, then silent 30 s (3x the 10 s
                            #   heartbeat period) -> Pi gone, clean exit


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
            payload = struct.pack(CHUNK_HDR_FMT, frame_seq, idx, count,
                                  take) + mv[off:off + take]
            off += take
            total = len(payload)
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

    def __init__(self):
        import select
        self._in = sys.stdin.buffer
        self._out = sys.stdout.buffer
        self._poll = select.poll()
        self._poll.register(self._in, 1)  # select.POLLIN

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
        mv = memoryview(data)
        total = 0
        n = len(mv)
        while total < n:
            w = self._out.write(mv[total:])
            if w:
                total += w


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

    def send(self, msg):
        self.ept.send(msg, timeout=1000)


def sensor_steps(cur_res, cur_pf, want_res, want_pf):
    """Sensor calls needed to reach (want_res, want_pf), in apply order.

    Pure -- host-tested without a sensor. Returns () when nothing
    changed, and that emptiness is the point: every set_pixformat /
    set_framesize is a sensor RE-INIT, which is the D15 crash class
    (firmware/ae3_usb/README.md). S18 lets the operator flip geometry
    from a web page, so the cheap guard against hammering the sensor is
    to touch it only on an actual delta.

    Pixel format is applied before framesize (OpenMV's own ordering in
    every example: set_pixformat -> set_framesize -> skip_frames), and a
    settle always follows a change -- the first frames after a re-init
    are garbage.

    VGA and above get an explicit set_framebuffers(1): the S0 bench
    measured that this sensor "needs set_framebuffers(1) for VGA+", and
    HD fits only one buffer at all (DESIGN §S0). It is re-applied after
    ANY geometry change at those sizes, because a pixformat change
    reallocates the buffer too (RGB565 -> GRAYSCALE halves it). Whether
    the single-buffer requirement is strictly necessary at VGA in THIS
    firmware is a hardware question -- nibble 3 answers it; until then
    the measured fact wins.
    """
    steps = []
    if cur_pf != want_pf:
        steps.append("pixformat")
    if cur_res != want_res:
        steps.append("framesize")
    if steps and want_res in (CAMERA_RES_VGA, CAMERA_RES_HD):
        steps.append("framebuffers")
    if steps:
        steps.append("settle")
    return tuple(steps)


class CaptureEngine:
    """Camera capture/encode for the S17 camera service (HP side).

    Commands arrive parsed from WREP_CAPTURE (BridgeCore.take_capture);
    poll() returns a JPEG to chunk-and-send when a capture is due.
    Frames are REAL camera captures (whatever the bench scene is) --
    the rate target was committed against reef-scene encode numbers
    (bite 0), a dim scene runs lighter than target, and that is stated
    in the demo, not hidden.

    Sensor bring-up is lazy (first command) and its failure is not
    fatal to the bridge: the relay keeps running, the refusal is
    traced, and the HE-side service ledger shows zero publishes.
    """

    def __init__(self):
        self.mode = CAMERA_MODE_STOP
        self.sensor_ok = None       # None = not tried yet
        self.cur_res = None         # geometry the sensor is actually holding
        self.cur_pf = None          # (None = unknown -> next cmd re-applies)
        self.q = CAP_DEFAULT_Q
        self.interval_ms = 100
        self.rate_bps = 0
        self.payload_max = CAMERA_MAX_PAYLOAD
        self.until = 0
        self.t_start = 0
        self.slots = 0              # pacing slots consumed this stream
        self.caps = 0               # total frames captured (lifetime)
        self.sent_bytes = 0         # JPEG bytes this stream (rate cap)

    def _framesize(self, sensor, res):
        if res == CAMERA_RES_VGA:
            return sensor.VGA
        if res == CAMERA_RES_HD:
            return sensor.HD
        return sensor.QVGA

    def _ensure_sensor(self, res, pf):
        """Bring the sensor to (res, pf); no-op when already there.

        Failure is never fatal to the bridge -- the relay keeps running,
        the refusal is traced, and the HE service ledger shows zero
        publishes. A failure MID-SWITCH leaves the real geometry unknown,
        so we forget it and let the next command re-apply from scratch
        rather than trusting a half-applied state. Only a sensor that
        never came up at all latches off.
        """
        if self.sensor_ok is False:
            return False
        try:
            import sensor
            if self.sensor_ok is None:
                sensor.reset()
                self.cur_res = self.cur_pf = None
            steps = sensor_steps(self.cur_res, self.cur_pf, res, pf)
            for step in steps:
                if step == "pixformat":
                    sensor.set_pixformat(sensor.GRAYSCALE
                                         if pf == CAMERA_PF_MONO
                                         else sensor.RGB565)
                elif step == "framesize":
                    sensor.set_framesize(self._framesize(sensor, res))
                elif step == "framebuffers":
                    sensor.set_framebuffers(1)   # S0: required at VGA+
                elif step == "settle":
                    sensor.skip_frames(time=300)
            self.cur_res, self.cur_pf = res, pf
            self.sensor_ok = True
            if steps:
                w, h = RES_GEOM.get(res, (0, 0))
                _trace("camera: sensor -> %dx%d %s (%s)"
                       % (w, h, PF_NAME.get(pf, "?"), ",".join(steps)))
            return True
        except Exception as e:
            self.cur_res = self.cur_pf = None    # geometry now unknown
            if self.sensor_ok is None:
                self.sensor_ok = False           # never came up: latch off
            _trace("camera: sensor setup FAILED res=%d pf=%d: %r"
                   % (res, pf, e))
            return False

    def command(self, cmd):
        if cmd["mode"] == CAMERA_MODE_STOP:
            if self.mode != CAMERA_MODE_STOP:
                _trace("camera: stop (%d frames, %d B this stream)"
                       % (self.slots, self.sent_bytes))
            self.mode = CAMERA_MODE_STOP
            return
        res = cmd.get("res", CAP_DEFAULT_RES)
        pf = cmd.get("pf", CAP_DEFAULT_PF)
        if not self._ensure_sensor(res, pf):
            _trace("camera: cmd mode %d REFUSED -- no sensor" % cmd["mode"])
            return
        self.q = cmd["q"]
        self.interval_ms = 10000 // cmd["fps_x10"]   # fps_x10 -> ms/frame
        self.rate_bps = cmd["rate_bps"]
        self.payload_max = cmd["payload_max"]
        self.mode = cmd["mode"]
        self.t_start = time.ticks_ms()
        self.until = time.ticks_add(self.t_start, cmd["secs"] * 1000)
        self.slots = 0
        self.sent_bytes = 0
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
                _trace("camera: stream done (%d frames, %d B)"
                       % (self.slots, self.sent_bytes))
                self.mode = CAMERA_MODE_STOP
                return None
            el = time.ticks_diff(now, self.t_start)
            if el < (self.slots + 1) * self.interval_ms:
                return None          # next fps slot not due
            if self.rate_bps and self.sent_bytes * 8000 > self.rate_bps * el:
                return None          # over the byte budget, skip the slot
        import sensor
        img = sensor.snapshot()
        jpg = img.to_jpeg(quality=self.q, copy=True)
        b = jpg.bytearray()          # proven idiom (s6_video_tx.py)
        self.slots += 1
        self.caps += 1
        self.sent_bytes += len(b)
        if self.mode == CAMERA_MODE_SINGLE:
            self.mode = CAMERA_MODE_STOP
        return b


def main():
    core = BridgeCore()
    usb = UsbVcp()
    he = HeWire()
    engine = CaptureEngine()
    cfg = _load_cfg()

    try:
        import os
        os.remove(TRACE_PATH)
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

    rp = he.start()
    _trace("he loaded, bm-wire announced")

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

        while True:
            idle = True

            # HE -> Pi
            if he.queue:
                idle = False
                for wire in core.he_msg(he.queue.pop(0)):
                    usb.write(wire)

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
                # as a service-triggered command, no HE round trip.
                engine.command({
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
                })
                camera_sent = True
                _trace("camera cfg armed: %s" % json.dumps(camera_cfg))

            # S17 camera service: commands from the HE (WREP_CAPTURE ->
            # take_capture), captures go back down as WCMD_PUB chunks
            # which the HE publishes on camera/stream.
            cap_cmd = core.take_capture()
            if cap_cmd is not None:
                engine.command(cap_cmd)
            jpeg = engine.poll(now)
            if jpeg is not None:
                idle = False
                for msg in core.capture_pub_msgs(jpeg, engine.caps - 1,
                                                 engine.payload_max):
                    he.send(msg)

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
