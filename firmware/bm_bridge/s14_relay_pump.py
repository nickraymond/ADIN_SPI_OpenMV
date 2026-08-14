# s14_relay_pump.py -- S14 bench rung B/C/E: HP-side relay pump (BENCHSPEC V16).
#
# Runs ON the AE3's HP core (MicroPython, stock-firmware features only --
# nothing flashed). Measures whether one HP python loop can frame traffic
# with the real uart_l2 codec (uart_codec.py) and push it out the USB VCP
# at >=2 Mbps sustained -- with the HE rpmsg leg feeding it (rung C) or a
# local generator isolating the framing+USB cost (rung B).
#
# Ops model (mirrors firmware/ae3_usb -- the proven S3 pattern): deployed
# as /flash/main.py via main_s14.py, board reboots into the service, the
# Pi owns the VCP raw while a rung runs (bench/s14_relay_counter.py is the
# other end). REPL is unavailable DURING a rung; ctrl-C from mpremote
# always drops back to the REPL (KeyboardInterrupt is never caught).
#
# Protocol (text -> binary -> text, per rung):
#   pump prints:  S14-PUMP ready\r\n            (text banner, on boot + after each rung)
#   host sends:   one JSON line {"rung":"B","secs":10,"unit":480,"agg":1,"crc":"c"}
#   pump prints:  CFG <echo json>\r\n
#   pump sends:   0x00                          (leading delimiter -- isolates any text)
#                 N wire frames                 (uart_l2 framing)
#                 1 summary frame               (l2 = b"S14END" + JSON)
#   pump prints:  DONE\r\n, then loops back to the banner.
#   Any inbound byte DURING a rung aborts it (summary still sent, aborted=1).
#
# Wire-frame payload: b"S14F" + seq(u32 LE) + data. seq is the wire frame
# counter; the counter end checks continuity + the codec's CRC-32C.
#
# Config keys:
#   rung "B" (local generator) | "C" (HE relay via he_spike's BCMD_PUMP)
#   secs     rung duration (deadline; C drains the in-flight burst past it)
#   unit     rung C: rpmsg msg size 32..496 (12-B header -> unit-12 data bytes)
#            rung B: data bytes per unit
#   agg      units aggregated per wire frame (1 = pass-through, 3 ~= S16 chunk)
#   crc      "c" real CRC-32C (the gate) | "z" binascii.crc32 (rung E:
#            prices crc32c-in-python) | "n" constant 0 (upper bound)
#
# he_spike protocol facts (firmware/he_spike/src/bench.c): BCMD_PUMP=5
# `<B3xII` (count, size); data frames type 0x45 [.. seq u32, crc u32,
# payload]; done reply 0x85. ELF: /flash/he_spike.elf (staged since S10).

import json
import select
import struct
import sys
import time

import openamp

import uart_codec as uc

try:
    from binascii import crc32 as _zcrc32
except ImportError:
    _zcrc32 = None

BANNER = "S14-PUMP ready"
ELF_PATH = "/flash/he_spike.elf"
APP_BASE = 0x60080000   # he_spike load address (s10_pipe_bench.py:25)
STATUS_PAGE = 0x600BFF00
BURST = 2000            # rung C: msgs per BCMD_PUMP request
QUEUE_CAP = 64          # rung C: relay backlog cap (drops counted, not silent)

TRACE_PATH = "/flash/s14_trace.txt"

BPUMP_DATA = 0x45
BREP_PUMP = 0x85
BCMD_PUMP = 5


def trace(msg):
    # Forensics for silent states: the VCP may be owned by a host that
    # cannot show us text, so state transitions go to flash too.
    try:
        with open(TRACE_PATH, "a") as f:
            f.write("%d %s\n" % (time.ticks_ms(), msg))
    except Exception:
        pass


def he_tick():
    """HE liveness: status-page tick (s10_pipe_bench layout), -1 if no magic.

    A frozen tick across a rung = the HE core wedged (found live in the
    first bench session: a stale HE instance survives HP resets and its
    tick freezes once its rpmsg peer vanishes).
    """
    import machine
    if machine.mem32[STATUS_PAGE] & 0xFFFFFFFF != 0x48455350:  # 'HESP'
        return -1
    return machine.mem32[STATUS_PAGE + 8] & 0xFFFFFFFF


class UsbVcp:
    """USB CDC via the console streams (pattern: firmware/ae3_usb/main.py)."""

    def __init__(self):
        self._in = sys.stdin.buffer
        self._out = sys.stdout.buffer
        self._poll = select.poll()
        self._poll.register(self._in, select.POLLIN)

    def any(self):
        return 1 if self._poll.poll(0) else 0

    def read_available(self):
        out = bytearray()
        while self._poll.poll(0):
            b = self._in.read(1)
            if not b:
                break
            out += b
        return bytes(out)

    def readline(self, timeout_ms=None):
        """Line read (config phase only); None on timeout with no input.

        The timeout lets the idle loop re-print its banner: the boot-time
        banner is emitted before the host has the port open and is lost
        (found live, first smoke run).
        """
        line = bytearray()
        t0 = time.ticks_ms()
        while True:
            if self._poll.poll(20):
                b = self._in.read(1)
                if not b:
                    continue
                if b == b"\n":
                    return bytes(line)
                if b != b"\r":
                    line += b
            elif not line and timeout_ms is not None \
                    and time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return None

    def write(self, data):
        mv = memoryview(data)
        total = 0
        n = len(mv)
        while total < n:
            w = self._out.write(mv[total:])
            if w:
                total += w


class HePump:
    """he_spike he-bench endpoint client (subset of s10_pipe_bench.Bench)."""

    def __init__(self):
        self.ept = None
        self.pong = False
        self.queue = []
        self.rx_msgs = 0
        self.rx_gaps = 0
        self.q_drops = 0
        self.last_seq = -1
        self.done = None

    def ns(self, src, name):
        if name == "he-bench":
            self.ept = openamp.Endpoint("he-bench", self.rx, dest=src)

    def rx(self, src, data):
        b = bytes(data)
        t = b[0]
        if t == 0x81:                    # BREP(PING)
            self.pong = True
        elif t == BPUMP_DATA:
            seq = struct.unpack_from("<I", b, 4)[0]
            if self.last_seq >= 0 and seq != self.last_seq + 1:
                self.rx_gaps += 1
            self.last_seq = seq
            self.rx_msgs += 1
            if len(self.queue) >= QUEUE_CAP:
                self.q_drops += 1
            else:
                self.queue.append(b)
        elif t == BREP_PUMP:
            self.done = struct.unpack_from("<I", b, 4)[0]

    def start(self):
        # LIFECYCLE RULES (all bench-earned, matrix runs 2026-08-14):
        # 1. Load ONCE per service boot and never stop mid-life: a second
        #    stop->load->start cycle in one boot times out waiting for the
        #    ns announcement (the prior cycle's host endpoint shadows the
        #    re-bind; he_tick showed HE alive and announcing).
        # 2. Rung ends always drain_burst(), so HE is IDLE whenever the
        #    service can die (KeyboardInterrupt lives in readline, between
        #    rungs). Starting over a stale-but-IDLE HE works; starting
        #    over a stale MID-BURST HE blocks in C before any timeout.
        # 3. Worst-case recovery (service killed mid-rung some other way):
        #    cold cycle + warm reset:
        #      sudo uhubctl -l 3 -p 1 -a cycle -d 3   (nereus000 topology)
        #      mpremote connect <by-id> reset
        #    (cold boot alone does NOT start main.py on this build).
        self.ept = None
        openamp.new_service_callback(self.ns)
        rp = openamp.RemoteProc(ELF_PATH)
        rp.start()
        t0 = time.ticks_ms()
        while self.ept is None:
            if time.ticks_diff(time.ticks_ms(), t0) > 5000:
                raise OSError("he-bench never announced (he_tick=%d)"
                              % he_tick())
            time.sleep_ms(5)
        return rp

    def request_burst(self, count, size):
        self.done = None
        self.last_seq = -1
        self.ept.send(struct.pack("<B3xII", BCMD_PUMP, count, size),
                      timeout=1000)

    def drain_burst(self, timeout_ms=3000):
        """Consume (and discard) the in-flight burst until HE reports done,
        so bursts never straddle rung boundaries. Returns True if drained."""
        t0 = time.ticks_ms()
        while self.done is None:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return False
            if self.queue:
                self.queue.clear()
            time.sleep_ms(2)
        self.queue.clear()
        return True


def _crc_fn_for(mode):
    if mode == "z" and _zcrc32:
        return lambda mv: _zcrc32(mv) & 0xFFFFFFFF
    if mode == "n":
        return lambda mv: 0
    return None                     # real crc32c (uart_codec default)


def run_rung(usb, cfg, he):
    rung = cfg.get("rung", "B")
    secs = int(cfg.get("secs", 10))
    unit = int(cfg.get("unit", 480))
    agg = int(cfg.get("agg", 1))
    crc_mode = cfg.get("crc", "c")
    crc_fn = _crc_fn_for(crc_mode)

    data_per_unit = (unit - 12) if rung == "C" else unit
    l2_len = 8 + data_per_unit * agg
    if l2_len > uc.MAX_L2_SIZE:
        raise ValueError("l2 %d > %d" % (l2_len, uc.MAX_L2_SIZE))

    # Preallocated hot-path buffers (no per-frame allocation).
    l2 = bytearray(l2_len)
    l2[0:4] = b"S14F"
    payload_buf = bytearray(l2_len + uc.FRAME_OVERHEAD)
    wire = bytearray(uc.cobs_max_encoded(l2_len + uc.FRAME_OVERHEAD) + 1)
    if rung == "B":
        pat = bytes(range(256)) * ((data_per_unit * agg) // 256 + 1)
        l2[8:] = pat[: data_per_unit * agg]

    usb.write(b"\x00")              # leading delimiter (see test_uart_codec)
    seq = 0
    wire_bytes = 0
    aborted = 0
    fill = 0                        # rung C: data bytes staged into l2
    t0 = time.ticks_ms()
    deadline = time.ticks_add(t0, secs * 1000)

    if rung == "C":
        he.rx_msgs = he.rx_gaps = he.q_drops = 0
        he.queue.clear()
        he.request_burst(BURST, unit)
        burst_left = BURST

    while True:
        now = time.ticks_ms()
        expired = time.ticks_diff(deadline, now) <= 0
        if usb.any():
            usb.read_available()
            aborted = 1
            break

        if rung == "B":
            if expired:
                break
            struct.pack_into("<I", l2, 4, seq)
            w = uc.frame_encode_into(wire, payload_buf, l2, l2_len, crc_fn)
            usb.write(memoryview(wire)[:w])
            seq += 1
            wire_bytes += w
        else:
            # Relay: drain the rpmsg queue into aggregated wire frames.
            if he.queue:
                msg = he.queue.pop(0)
                mlen = len(msg) - 12
                l2[8 + fill : 8 + fill + mlen] = msg[12:]
                fill += mlen
                if fill >= data_per_unit * agg:
                    struct.pack_into("<I", l2, 4, seq)
                    w = uc.frame_encode_into(wire, payload_buf, l2, l2_len, crc_fn)
                    usb.write(memoryview(wire)[:w])
                    seq += 1
                    wire_bytes += w
                    fill = 0
            elif he.done is not None:
                burst_left = 0
                if expired:
                    break
                he.request_burst(BURST, unit)
                burst_left = BURST
            elif expired and not he.queue:
                break
            else:
                time.sleep_ms(0)

    el = time.ticks_diff(time.ticks_ms(), t0) / 1000
    summary = {
        "rung": rung, "crc": crc_mode, "unit": unit, "agg": agg,
        "frames": seq, "l2_len": l2_len, "wire_bytes": wire_bytes,
        "secs": el, "aborted": aborted,
        "mbps_l2": round(seq * l2_len * 8 / el / 1e6, 3) if el else 0,
    }
    if rung == "C":
        summary["src_msgs"] = he.rx_msgs
        summary["src_gaps"] = he.rx_gaps
        summary["q_drops"] = he.q_drops
        summary["drained"] = 1 if he.drain_burst() else 0
        # Liveness = a PING round trip (the status-page tick freezes while
        # HE idles healthily -- found live, first matrix run).
        he.pong = False
        he.ept.send(b"\x01", timeout=500)
        t1 = time.ticks_ms()
        while not he.pong and time.ticks_diff(time.ticks_ms(), t1) < 1000:
            time.sleep_ms(2)
        summary["he_alive"] = 1 if he.pong else 0
    # Summary frame MUST use the rung's crc mode -- a default-crc S14END
    # is invisible to a z/n-mode counter (found live: z/n rungs "hung"
    # on exactly this one rejected frame).
    end = b"S14END" + json.dumps(summary).encode()
    usb.write(uc.frame_encode(end, crc_fn))
    return summary


def main():
    usb = UsbVcp()
    he = HePump()
    rp = None
    try:
        import os
        os.remove(TRACE_PATH)
    except Exception:
        pass
    trace("service up")
    while True:
        usb.write(b"\r\n" + BANNER.encode() + b"\r\n")
        line = usb.readline(timeout_ms=2000)
        if not line:
            continue                # timeout, blank, or handshake newline:
                                    # re-print the banner (the counter's
                                    # liveness probe relies on this)
        try:
            cfg = json.loads(line)
        except ValueError:
            trace("bad json: %r" % line[:40])
            usb.write(b"ERR bad json\r\n")
            continue
        trace("cfg %r" % line[:80])
        if cfg.get("rung") == "Q":     # host asks the service to exit to REPL
            if rp is not None:
                rp.stop()              # leave HE STOPPED (S10 fixture custom)
            usb.write(b"BYE\r\n")
            return
        if cfg.get("rung") == "C" and rp is None:
            rp = he.start()            # once per boot -- see start()'s rules
        usb.write(("CFG " + json.dumps(cfg) + "\r\n").encode())
        summary = run_rung(usb, cfg, he)
        trace("rung end %s" % json.dumps(summary))
        usb.write(("DONE " + json.dumps(summary) + "\r\n").encode())
