# bm_bridge.py -- S16 BUILD-2b: the HP bridge. Moves L2 frames between the
# HE rpmsg endpoint ("bm-wire", firmware/bm_he) and the USB VCP, framed
# with bm_sbc's uart_l2 codec (uart_codec.py, byte-exact vs Sofar's C).
# The Pi end is bm_sbc's stock --uart gateway pointed at this board's
# /dev/serial/by-id CDC path -- zero new Pi-side transport code (REV-20).
#
# Runs ON the AE3's HP core (MicroPython, stock firmware, nothing
# flashed), deployed as /flash/main.py via main_bridge.py. Ops model =
# the S14 pump (firmware/bm_bridge/README.md rules are law): warm
# `mpremote reset` is the service entry; mpremote attach kills it
# (KeyboardInterrupt); every exit cause persists to /flash/bridge_crash.txt
# because a traceback printed into bm_sbc's decoder is lost as COBS
# garbage (BENCHSPEC BUILD-2b).
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
#    "ping":   {"target": "0xbe9c000000000001", "delay": 5}}
# stream -> WCMD_STREAM to the HE publisher (delay counts from link-up);
# ping -> one WCMD_PING (a Camera-sourced 2-hop BCMP ping; the acceptance
# line lands on the HE debug ring -- dumped to the trace file at exit).

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
WREP_STATUS = 0x94

MSG_PAYLOAD = 492           # 496 rpmsg budget - 4 B wire header
MAX_L2 = 1514               # REV-14 network-wide max frame
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
        # Preallocated encode buffers (hot path, no per-frame allocation).
        self._payload_buf = bytearray(MAX_L2 + uc.FRAME_OVERHEAD)
        self._wire = bytearray(uc.cobs_max_encoded(MAX_L2 + uc.FRAME_OVERHEAD) + 1)
        self.stats = {
            "he2pi_frames": 0, "he2pi_bytes": 0,     # L2 frames HE -> VCP
            "pi2he_frames": 0, "pi2he_bytes": 0,     # L2 frames VCP -> HE
            "frag_errors": 0,                        # HE->HP reassembly drops
            "oversize": 0,                           # Pi frames > 1514 (REV-14)
            "unknown_cmds": 0,
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
        self.stats["unknown_cmds"] += 1
        return []

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


def main():
    core = BridgeCore()
    usb = UsbVcp()
    he = HeWire()
    cfg = _load_cfg()

    try:
        import os
        os.remove(TRACE_PATH)
    except Exception:
        pass
    _trace("bridge up, cfg %s" % json.dumps(cfg))

    rp = he.start()
    _trace("he loaded, bm-wire announced")

    try:
        # Phase 1: hold WCMD_LINK down until the Pi actually speaks
        # (bm_sbc's gateway heartbeats on its UART port as soon as it
        # opens the tty). Until link-up the HE stack transmits nothing,
        # so the pipe stays quiet while unowned. Drain rpmsg meanwhile.
        while not usb.any():
            if he.queue:
                he.queue.pop(0)
            time.sleep_ms(20)

        he.send(core.link_msg(True))
        usb.write(b"\x00")   # leading delimiter isolates any pre-link text
        t_link = time.ticks_ms()
        _trace("link up (first VCP bytes seen)")

        stream_cfg = cfg.get("stream") or None
        ping_cfg = cfg.get("ping") or None
        stream_sent = False
        ping_sent = False
        last_stat = time.ticks_ms()

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
                for msg in core.vcp_bytes(usb.read_available()):
                    he.send(msg)

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
