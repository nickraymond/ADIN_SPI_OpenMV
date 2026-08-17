# s18_reinit_probe_c.py -- rung C: does PUBLISHING wedge the sensor re-init?
#
# The ladder so far (S18 bite B2 nibble 1, all off-chain, no Pi, no chain):
#   rung A  no HE core at all          -> 12/12 PASS  (sensor is not it)
#   rung B  HE loaded but idle         ->  9/9  PASS  (a loaded core is not it)
#   rung C  HE loaded AND publishing   ->  this probe
#
# The real bridge does: capture -> emit the frame's chunks as WCMD_PUB over
# rpmsg -> (next command) re-init the sensor. Rungs A and B skipped the
# middle step. This one puts it back, sized to the frame actually captured,
# so the burst is the burst the bridge would really have sent.
#
# What each outcome means, decided before the run:
#   FAILS here -> the mechanism is the HE publish path, and the fix is to
#                 gate the re-init on wire_status_t.stream_sent, which
#                 bm_bridge.py already parses. Bridge-only, no ABI change.
#   PASSES     -> publishing is not it either, and the only thing left that
#                 the real bridge does and this probe does not is the VCP
#                 relay pump (uart_l2 framing + USB CDC). The fix would then
#                 be to quiesce that pump around a re-init, and stream_sent
#                 would have been the wrong fix.
#
# Framing is copied from bench/probes/s19_pub_probe.py, which asserted it
# byte-identical to the production chunker. Draining mirrors the FIXED
# bridge (S19 bite 2): the HP services the HE->HP direction every chunk.
# drain() sleeps first on purpose -- the _rx callback is what recycles the
# vring buffer, and MicroPython only runs it when the VM yields. Popping
# our own list recycles nothing (found live, S19 bite 2).

import sensor, time, gc, struct

LOG = "/flash/reinit_probe_c.txt"
ELF_PATH = "/flash/bm_he.elf"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

WCMD_FRAG = 0x16
WCMD_PUB = 0x18
MSG_PAYLOAD = 492                # 496 rpmsg budget - 4 B wire header
CHUNK_HDR_FMT = "<IHHH"          # frame_seq | idx | count | payload_len
CHUNK_HDR_LEN = 10
CAMERA_MAX_PAYLOAD = 1400        # REV-28

BM_STATUS_PAGE = 0x600BFE00
BM_MAGIC = 0x424D4845

QUIET_MS = 8000
RECOVER_MS = 2000
LADDER = (0, 250, 1000)

_f = None
_wire = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


# ---- wire framing (s19_pub_probe.py, unchanged) -------------------------

def chunk_payload(seq, idx, count, data_len, fill=0xC3):
    return (struct.pack(CHUNK_HDR_FMT, seq, idx, count, data_len) +
            bytes([fill]) * data_len)


def pub_msgs(payload):
    total = len(payload)
    first = min(total, MSG_PAYLOAD)
    msgs = [struct.pack("<BBH", WCMD_PUB, 0, total) + payload[:first]]
    p = first
    while p < total:
        part = min(total - p, MSG_PAYLOAD)
        msgs.append(struct.pack("<BBH", WCMD_FRAG, 0, part) +
                    payload[p:p + part])
        p += part
    return msgs


def he_tick():
    import machine
    if machine.mem32[BM_STATUS_PAGE] & 0xFFFFFFFF != BM_MAGIC:
        return None
    return machine.mem32[BM_STATUS_PAGE + 12] & 0xFFFFFFFF


def he_ticking(window_ms=300):
    t0 = he_tick()
    if t0 is None:
        return False
    time.sleep_ms(window_ms)
    return he_tick() != t0


class Wire:
    """rpmsg 'bm-wire' client -- the bridge's HeWire, minus the VCP."""

    def __init__(self):
        self.ept = None
        self.queue = []
        self.rx_msgs = 0
        self.send_timeouts = 0

    def _ns(self, src, name):
        if name == "bm-wire":
            import openamp
            self.ept = openamp.Endpoint("bm-wire", self._rx, dest=src)

    def _rx(self, src, data):
        self.queue.append(bytes(data))

    def start(self):
        import openamp
        if he_ticking(60):
            raise OSError("stale HE still running -- mpremote reset first")
        openamp.new_service_callback(self._ns)
        rp = openamp.RemoteProc(ELF_PATH)
        rp.start()
        t0 = time.ticks_ms()
        while self.ept is None:
            if time.ticks_diff(time.ticks_ms(), t0) > 8000:
                raise OSError("bm-wire never announced")
            time.sleep_ms(5)
        return rp

    def send(self, msg):
        try:
            self.ept.send(msg, timeout=1000)
            return True
        except Exception:
            self.send_timeouts += 1
            return False

    def drain(self, ms=1):
        time.sleep_ms(ms)          # YIELD FIRST -- see header
        n = 0
        while self.queue:
            self.queue.pop(0)
            n += 1
            self.rx_msgs += 1
        return n


def publish_frame(seq, nbytes):
    """Emit the chunks the bridge would emit for an `nbytes` JPEG.

    Same chunking arithmetic as CaptureEngine/BridgeCore: 1400 B on the
    wire per chunk, 10 B of that a header. Drains every chunk, like the
    fixed bridge does.
    """
    data_len = CAMERA_MAX_PAYLOAD - CHUNK_HDR_LEN
    count = (nbytes + data_len - 1) // data_len
    t0 = time.ticks_ms()
    sent = 0
    for idx in range(count):
        n = min(data_len, nbytes - idx * data_len)
        for m in pub_msgs(chunk_payload(seq, idx, count, n)):
            if _wire.send(m):
                sent += 1
        _wire.drain()
    ms = time.ticks_diff(time.ticks_ms(), t0)
    log("  published %d chunks (%d msgs, %d ms, timeouts %d, rx %d)"
        % (count, sent, ms, _wire.send_timeouts, _wire.rx_msgs))
    return count, ms


# ---- the sensor ladder (identical to rungs A and B) ---------------------

def _call(what, fn):
    log("  . %s" % what)
    t0 = time.ticks_us()
    fn()
    return time.ticks_diff(time.ticks_us(), t0)


def reinit(res, pf, tag=""):
    steps = []
    if pf is not None:
        steps.append(("pixformat", lambda: sensor.set_pixformat(PF[pf])))
    steps.append(("framebuffers", lambda: sensor.set_framebuffers(1)))
    steps.append(("framesize", lambda: sensor.set_framesize(RES[res])))
    steps.append(("settle", lambda: sensor.skip_frames(time=300)))
    total = 0
    for name, fn in steps:
        try:
            total += _call("%s%s" % (tag, name), fn)
        except Exception as e:
            log("  ! %s%s THREW after %d us: %r" % (tag, name, total, e))
            return (False, name, total)
    return (True, None, total)


def capture(q=50):
    t0 = time.ticks_us()
    img = sensor.snapshot()
    t1 = time.ticks_us()
    b = img.to_jpeg(quality=q, copy=True).bytearray()
    t2 = time.ticks_us()
    return (len(b), time.ticks_diff(t1, t0), time.ticks_diff(t2, t1))


def bootstrap(ceiling="hd"):
    log("bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("bootstrap: ceiling %s claimed, heap %d" % (ceiling, gc.mem_free()))


def recover(res, pf):
    log("  recovery: R1 immediate retry")
    ok, step, us = reinit(res, pf, "R1 ")
    if ok:
        return "R1-immediate"
    log("  recovery: R2 retry after %d ms" % RECOVER_MS)
    time.sleep_ms(RECOVER_MS)
    ok, step, us = reinit(res, pf, "R2 ")
    if ok:
        return "R2-after-%dms" % RECOVER_MS
    log("  recovery: R3 full reset + bootstrap, HE LOADED")
    try:
        bootstrap("hd")
    except Exception as e:
        log("  ! R3 bootstrap THREW: %r" % e)
        return "NONE"
    ok, step, us = reinit(res, pf, "R3 ")
    return "R3-reset" if ok else "NONE"


def rung(seq, size, cap_pf, to_pf, delay_ms):
    log("=== %s %s -> %s, delay %d ms after PUBLISH (tick %s)"
        % (size, cap_pf, to_pf, delay_ms, he_tick()))
    log("  quiet %d ms before setup" % QUIET_MS)
    time.sleep_ms(QUIET_MS)
    ok, step, us = reinit(size, cap_pf, "setup ")
    if not ok:
        log("  INVALID: setup failed at %s -- rung discarded" % step)
        log("  setup recovery: %s" % recover(size, cap_pf))
        return None
    try:
        nbytes, snap_us, enc_us = capture()
    except Exception as e:
        log("  INVALID: capture threw %r -- rung discarded" % e)
        return None
    log("  captured %d B (snap %d us, encode %d us)"
        % (nbytes, snap_us, enc_us))
    chunks, pub_ms = publish_frame(seq, nbytes)
    if delay_ms:
        time.sleep_ms(delay_ms)
    ok, step, us = reinit(size, to_pf, "meas ")
    row = {"size": size, "delay": delay_ms, "bytes": nbytes,
           "chunks": chunks, "pub_ms": pub_ms, "ok": ok, "step": step,
           "us": us, "recovery": ""}
    if ok:
        log("  PASS (re-init %d us)" % us)
    else:
        log("  FAIL at %s" % step)
        row["recovery"] = recover(size, to_pf)
        log("  recovery: %s" % row["recovery"])
    return row


def sweep(size, rows, seq0):
    log("#### sweep %s (colour -> mono), HE LOADED + PUBLISHING" % size)
    seq = seq0
    for d in LADDER:
        r = rung(seq, size, "color", "mono", d)
        seq += 1
        if r is None:
            continue
        rows.append(r)
        time.sleep_ms(QUIET_MS)
        reinit(size, "color", "reset-to-colour ")
    return seq


def main():
    global _f, _wire
    _f = open(LOG, "w")
    log("---- rung C start (HE LOADED + PUBLISHING) ----")
    bootstrap("hd")                     # ceiling BEFORE the ELF -- required

    _wire = Wire()
    log("loading HE elf (HD framebuffer already claimed) ->")
    rp = _wire.start()
    log("HE loaded, heap %d, tick %s" % (gc.mem_free(), he_tick()))
    time.sleep(2)

    rows = []
    seq = 1
    try:
        for size in ("qvga", "vga", "hd"):
            seq = sweep(size, rows, seq)
    finally:
        log("---- summary (HE loaded + publishing) ----")
        log("%-5s %-6s %-8s %-7s %-7s %-6s %-14s %s"
            % ("size", "delay", "srcbytes", "chunks", "pub_ms", "res",
               "failed-call", "recovery"))
        for r in rows:
            log("%-5s %-6d %-8d %-7d %-7d %-6s %-14s %s"
                % (r["size"], r["delay"], r["bytes"], r["chunks"],
                   r["pub_ms"], "PASS" if r["ok"] else "FAIL",
                   r["step"] or "-", r["recovery"] or "-"))
        log("wire: rx %d msgs, send timeouts %d, tick %s"
            % (_wire.rx_msgs, _wire.send_timeouts, he_tick()))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r -- recovery: sudo uhubctl -l 3 -p 1 "
                "-a cycle -d 3, then mpremote reset" % e)
        log("---- rung C end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
