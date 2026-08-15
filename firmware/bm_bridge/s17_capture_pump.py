# s17_capture_pump.py -- S17 bite 0: V16 relay re-check WITH capture +
# encode live on the HP core (BENCHSPEC BUILD-4 "re-check with
# capture+encode live before rate commitments"; S0 measure-first).
#
# Extends the S14 relay pump (s14_relay_pump.py -- rungs B/C/E unchanged,
# delegated) with two new rungs that add the camera-service CPU load to
# the SAME single HP python loop:
#
#   rung "F"  rung-C relay (HE BCMD_PUMP -> uart_l2 framing -> VCP) plus,
#             paced at a target fps: sensor.snapshot() (if a sensor is
#             usable) and a JPEG encode of the REEF REFERENCE IMAGE.
#             The reef image (bench/assets/ref_scene, S0 pipeline) gives
#             representative encode cost + bytes/frame -- the dim bench
#             room compresses ~2x too well (S0 finding, Nick's call
#             2026-08-15 to reuse the trick here).
#   rung "G"  rung F plus the encoded JPEG pushed DOWN to the HE via
#             BCMD_SINK_DATA chunks -- the honest approximation of the
#             real S17 camera path, where every image byte crosses rpmsg
#             twice (HP->HE to be published, HE->HP as L2 frames) plus
#             the VCP once. HE-side integrity ledger via BCMD_SINK_QUERY.
#
# Deployed as /flash/main.py via main_s17.py (VCP-gated fixture swap);
# counter = bench/s14_relay_counter.py (grown F/G support). Ops rules,
# protocol, crash/trace persistence: identical to the S14 pump.
#
# Config keys (beyond the S14 set):
#   fps   target capture rate for F/G (default 15)
#   q     JPEG quality (default 50 -- the D20 standing setting)
#
# he_spike sink protocol (firmware/he_spike/src/bench.c): BCMD_SINK_DATA
# = 0x03, msg [1B cmd][3B pad][u32 seq LE][u32 crc LE][payload]; crc =
# zlib crc32 (poly 0xEDB88320 == binascii.crc32) over the payload only;
# no reply. BCMD_SINK_QUERY = 0x04 -> BREP 0x84 {u32 count, bytes,
# crc_errs, seq_gaps, cyc_first, cyc_last}.

import json
import struct
import time

import uart_codec as uc

try:
    from binascii import crc32 as _crc32
except ImportError:
    _crc32 = None

BANNER = "S17-PUMP ready"

REF_CANDIDATES = (
    "/flash/ref_color_320x200.bmp",
    "/remote/ref_color_320x200.bmp",   # mpremote mount fallback
)

TRACE_PATH = "/flash/s14_trace.txt"    # shared trace file (one bench era)

# Sink leg framing (he_spike bench.c facts above).
BCMD_SINK_RESET = 0x02
BCMD_SINK_DATA = 0x03
BCMD_SINK_QUERY = 0x04
BREP_SINK_QUERY = 0x84
SINK_MSG = 480                  # total rpmsg msg bytes ("480 used" S14 fact)
SINK_HDR = 12
SINK_PAYLOAD = SINK_MSG - SINK_HDR


# ---- pure helpers (host-tested; no hardware imports) ---------------------

def sink_chunk_lens(nbytes, payload_max=SINK_PAYLOAD):
    """Payload split for one JPEG -> BCMD_SINK_DATA msgs (last may be short)."""
    if nbytes <= 0:
        return []
    n = (nbytes + payload_max - 1) // payload_max
    lens = [payload_max] * n
    rem = nbytes - payload_max * (n - 1)
    lens[-1] = rem
    return lens


def sink_pack_into(buf, seq, payload_mv):
    """Build one BCMD_SINK_DATA msg into buf; returns total msg length."""
    plen = len(payload_mv)
    struct.pack_into("<B3xII", buf, 0, BCMD_SINK_DATA, seq,
                     _crc32(payload_mv) & 0xFFFFFFFF)
    buf[SINK_HDR:SINK_HDR + plen] = payload_mv
    return SINK_HDR + plen


class CapturePacer:
    """Fixed-rate capture slots: due() fires when the next slot arrives.

    Quota-style (same scheme as the HE stream task, D27): slots owed =
    elapsed * fps; never bursts more than one slot per due() call, so a
    stalled loop degrades to a lower achieved fps instead of a burst.
    """

    def __init__(self, fps, t0_ms):
        self.interval_ms = 1000.0 / fps if fps > 0 else 0
        self.t0 = t0_ms
        self.done = 0

    def due(self, now_ms, ticks_diff=None):
        if self.interval_ms <= 0:
            return False
        el = ticks_diff(now_ms, self.t0) if ticks_diff else (now_ms - self.t0)
        return el >= (self.done + 1) * self.interval_ms


# ---- hardware-facing pieces (lazy imports; MicroPython only) -------------

def trace(msg):
    try:
        with open(TRACE_PATH, "a") as f:
            f.write("%d %s\n" % (time.ticks_ms(), msg))
    except Exception:
        pass


def load_ref():
    """Load the reef reference image. Returns (img, src) or (None, "none").

    Prefers a GC-heap copy (copy_to_fb=False) so the sensor keeps the
    frame buffer; falls back to the frame buffer (sensor then disabled --
    snapshot would clobber the reference).
    """
    import image
    for path in REF_CANDIDATES:
        try:
            return image.Image(path, copy_to_fb=False), "heap"
        except MemoryError:
            try:
                return image.Image(path, copy_to_fb=True), "fb"
            except Exception as e:
                trace("ref fb load failed %s: %r" % (path, e))
        except OSError:
            continue
        except Exception as e:
            trace("ref load failed %s: %r" % (path, e))
    return None, "none"


def init_sensor():
    """Bring the camera up (QVGA RGB565, the T1 shape). False if unusable."""
    try:
        import sensor
        sensor.reset()
        sensor.set_pixformat(sensor.RGB565)
        sensor.set_framesize(sensor.QVGA)
        sensor.skip_frames(time=300)
        return True
    except Exception as e:
        trace("sensor init failed: %r" % e)
        return False


def make_he():
    """HePump subclass adding the sink reply. Lazy: imports openamp chain."""
    import s14_relay_pump as s14

    class HeCapturePump(s14.HePump):
        def __init__(self):
            s14.HePump.__init__(self)
            self.sink_reply = None

        def rx(self, src, data):
            b = bytes(data)
            if b and b[0] == BREP_SINK_QUERY and len(b) >= 20:
                self.sink_reply = struct.unpack_from("<IIII", b, 4)
                return
            s14.HePump.rx(self, src, b)

        def sink_reset(self):
            self.ept.send(struct.pack("<B3x", BCMD_SINK_RESET), timeout=500)

        def sink_query(self, timeout_ms=1000):
            self.sink_reply = None
            self.ept.send(struct.pack("<B3x", BCMD_SINK_QUERY), timeout=500)
            t0 = time.ticks_ms()
            while self.sink_reply is None:
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    return None
                time.sleep_ms(2)
            return self.sink_reply

    return HeCapturePump()


def run_capture_rung(usb, cfg, he, ref, ref_src, sensor_ok):
    """Rung F/G: the s14 rung-C relay loop with capture slots woven in.

    Relay logic mirrors s14_relay_pump.run_rung's C branch (provenance:
    that loop measured 5.4 Mbps in S14); the capture path is the delta
    under test.
    """
    import s14_relay_pump as s14

    rung = cfg.get("rung", "F")
    secs = int(cfg.get("secs", 60))
    unit = int(cfg.get("unit", 480))
    agg = int(cfg.get("agg", 3))
    crc_mode = cfg.get("crc", "c")
    fps = float(cfg.get("fps", 15))
    q = int(cfg.get("q", 50))
    crc_fn = s14._crc_fn_for(crc_mode)
    sink = rung == "G"

    if sensor_ok:
        import sensor

    data_per_unit = unit - 12
    l2_len = 8 + data_per_unit * agg
    if l2_len > uc.MAX_L2_SIZE:
        raise ValueError("l2 %d > %d" % (l2_len, uc.MAX_L2_SIZE))

    l2 = bytearray(l2_len)
    l2[0:4] = b"S14F"               # counter compatibility: same frame magic
    payload_buf = bytearray(l2_len + uc.FRAME_OVERHEAD)
    wire = bytearray(uc.cobs_max_encoded(l2_len + uc.FRAME_OVERHEAD) + 1)
    sink_buf = bytearray(SINK_MSG)

    usb.write(b"\x00")
    seq = 0
    wire_bytes = 0
    aborted = 0
    fill = 0
    # Capture ledger
    enc_us = 0
    jpeg_bytes = 0
    jpeg_min = 0
    jpeg_max = 0
    sink_msgs = 0
    sink_bytes = 0
    sink_seq = 0
    sink_send_fails = 0
    gc_us = 0

    import gc
    he.rx_msgs = he.rx_gaps = he.q_drops = 0
    he.queue.clear()
    if sink:
        he.sink_reset()
        time.sleep_ms(20)
    t0 = time.ticks_ms()
    deadline = time.ticks_add(t0, secs * 1000)
    pacer = CapturePacer(fps, t0)
    he.request_burst(s14.BURST, unit)

    while True:
        now = time.ticks_ms()
        expired = time.ticks_diff(deadline, now) <= 0
        if usb.any():
            usb.read_available()
            aborted = 1
            break

        # Relay leg (s14 rung C, verbatim behavior).
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
            if expired:
                break
            he.request_burst(s14.BURST, unit)
        elif expired and not he.queue:
            break

        # Capture leg: at most one slot per loop pass (pacer degrades
        # gracefully when the loop is busy -- achieved fps is the result).
        if not expired and pacer.due(now, time.ticks_diff):
            if sensor_ok:
                sensor.snapshot()           # real capture timing, image unused
            te0 = time.ticks_us()
            jpg = ref.to_jpeg(quality=q, copy=True)
            enc_us += time.ticks_diff(time.ticks_us(), te0)
            jb = jpg.size()
            jpeg_bytes += jb
            jpeg_min = jb if not jpeg_min else min(jpeg_min, jb)
            jpeg_max = max(jpeg_max, jb)
            if sink:
                mv = memoryview(jpg.bytearray())
                off = 0
                for plen in sink_chunk_lens(jb):
                    n = sink_pack_into(sink_buf, sink_seq, mv[off:off + plen])
                    try:
                        he.ept.send(memoryview(sink_buf)[:n], timeout=1000)
                        sink_msgs += 1
                        sink_bytes += n
                    except Exception:
                        sink_send_fails += 1
                    sink_seq += 1
                    off += plen
            del jpg
            pacer.done += 1
            if pacer.done % 32 == 0:
                tg0 = time.ticks_us()
                gc.collect()
                gc_us += time.ticks_diff(time.ticks_us(), tg0)

    el = time.ticks_diff(time.ticks_ms(), t0) / 1000
    caps = pacer.done
    summary = {
        "rung": rung, "crc": crc_mode, "unit": unit, "agg": agg,
        "frames": seq, "l2_len": l2_len, "wire_bytes": wire_bytes,
        "secs": el, "aborted": aborted,
        "mbps_l2": round(seq * l2_len * 8 / el / 1e6, 3) if el else 0,
        "src_msgs": he.rx_msgs, "src_gaps": he.rx_gaps,
        "q_drops": he.q_drops,
        # Capture ledger
        "q": q, "fps_target": fps, "caps": caps,
        "cap_fps": round(caps / el, 2) if el else 0,
        "enc_ms": round(enc_us / caps / 1000.0, 2) if caps else 0,
        "jpeg_avg": jpeg_bytes // caps if caps else 0,
        "jpeg_min": jpeg_min, "jpeg_max": jpeg_max,
        "sensor": 1 if sensor_ok else 0, "ref_src": ref_src,
        "gc_ms": round(gc_us / 1000.0, 1),
    }
    if sink:
        summary["sink_msgs"] = sink_msgs
        summary["sink_bytes"] = sink_bytes
        summary["sink_send_fails"] = sink_send_fails
        summary["sink_mbps"] = (round(sink_bytes * 8 / el / 1e6, 3)
                                if el else 0)
    summary["drained"] = 1 if he.drain_burst() else 0
    if sink:
        rep = he.sink_query()
        if rep:
            summary["he_sink_count"] = rep[0]
            summary["he_sink_bytes"] = rep[1]
            summary["he_sink_crc_errs"] = rep[2]
            summary["he_sink_gaps"] = rep[3]
        else:
            summary["he_sink_count"] = -1
    he.pong = False
    he.ept.send(b"\x01", timeout=500)
    t1 = time.ticks_ms()
    while not he.pong and time.ticks_diff(time.ticks_ms(), t1) < 1000:
        time.sleep_ms(2)
    summary["he_alive"] = 1 if he.pong else 0

    end = b"S14END" + json.dumps(summary).encode()
    usb.write(uc.frame_encode(end, crc_fn))
    return summary


def main():
    import s14_relay_pump as s14

    usb = s14.UsbVcp()
    he = make_he()
    rp = None
    ref = None
    ref_src = "none"
    sensor_ok = False
    try:
        import os
        os.remove(TRACE_PATH)
    except Exception:
        pass
    trace("s17 service up")
    while True:
        usb.write(b"\r\n" + BANNER.encode() + b"\r\n")
        line = usb.readline(timeout_ms=2000)
        if not line:
            continue
        try:
            cfg = json.loads(line)
        except ValueError:
            trace("bad json: %r" % line[:40])
            usb.write(b"ERR bad json\r\n")
            continue
        trace("cfg %r" % line[:80])
        rung = cfg.get("rung")
        if rung == "Q":
            if rp is not None:
                rp.stop()              # leave HE STOPPED (S10 fixture custom)
            usb.write(b"BYE\r\n")
            return
        if rung in ("F", "G"):
            if ref is None:
                ref, ref_src = load_ref()
                if ref is None:
                    usb.write(b"ERR no ref image (stage "
                              b"/flash/ref_color_320x200.bmp)\r\n")
                    continue
                # Sensor only when the reef lives on the GC heap -- an
                # fb-resident reference would be clobbered by snapshot().
                sensor_ok = init_sensor() if ref_src == "heap" else False
                trace("ref=%s sensor=%d" % (ref_src, 1 if sensor_ok else 0))
        if rung in ("C", "F", "G") and rp is None:
            rp = he.start()            # once per boot (s14 lifecycle rules)
        usb.write(("CFG " + json.dumps(cfg) + "\r\n").encode())
        if rung in ("F", "G"):
            summary = run_capture_rung(usb, cfg, he, ref, ref_src, sensor_ok)
        else:
            summary = s14.run_rung(usb, cfg, he)
        trace("rung end %s" % json.dumps(summary))
        usb.write(("DONE " + json.dumps(summary) + "\r\n").encode())
