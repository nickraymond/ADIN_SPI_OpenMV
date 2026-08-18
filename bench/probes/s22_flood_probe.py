# s22_flood_probe.py -- S22 bite 1: reproduce the HE flood mute
# off-chain and CLASSIFY the death.
#
# The finding (SPEC §Open questions, S18 reef matrix + 3 more events):
# sustained camera publish >= ~513 rpmsg msg/s first breaks the receiver
# ledger, then the HE wire task goes PERMANENTLY mute -- he2pi frozen
# while pi2he advances. Every real event ended in a reboot, so the HE
# ring, the sticky error word and the he_sample page were lost all four
# times. This probe's whole point is to be standing next to the body:
# flood at swept sustained rates with NO Pi and NO camera (the
# s19_pub_probe pattern -- bm_pub transmits unconditionally against a
# forced link-up), and when the mute lands, read the postmortem the real
# events never got:
#
#   BP->err  (0x600BFE08, sticky)  0xA110C = malloc-fail hook spun
#                                  0xDEAD  = vAssertCalled
#                                  0x570F  = stack overflow hook
#   BP->tick (0x600BFE0C)          frozen vs advancing
#   he_sample page (0x600BFA00)    last 40 published chunks: heap curve,
#                                  txq depth, tx_dropped, tx_stalls
#   HE debug ring                  the narrative, dumped to the log
#
# Classification -> fix shape (the bite's decision table):
#   err != 0, tick frozen        -> HOOK-SPIN: name the hook; malloc-fail
#                                   means some allocation path is
#                                   unbounded under sustained load
#   err == 0, tick frozen        -> PARKED: the wire task is blocked
#                                   (bm_pub's L2 enqueue runs inline in
#                                   wire_rx -- D27 says it can block)
#   err == 0, tick advancing,
#   no query reply               -> TX-DEAD: rr_send permanently failing
#                                   = HE->HP vring desync
#
# Traffic shape mirrors the real events, not a uniform burst: frames of
# production-framed chunks (byte-identical to BridgeCore.capture_pub_msgs,
# asserted in bench/test_s22_probe.py) sent at frame cadence -- bursty,
# like the bridge -- with the HP draining as it pushes (the fixed
# bridge's send_chunk_msgs behaviour). Rung rates come from the measured
# boundary arithmetic:
#   315 msg/s = QVGA q50 9,198 B @ 15 fps   (clean 4/4 on-chain)
#   513 msg/s = VGA mono 23,831 B @ ~10 fps (fatal 3/3 on-chain)
#   560 msg/s = QVGA q50 9,198 B @ ~28 fps  (Nick's demo: 60 s clean
#               twice in the matrix, fatal at ~5 min -- so dwell TIME is
#               a variable, and the fatal rungs dwell LONG)
#
# BREADCRUMB DISCIPLINE (S18): every step is flushed to /flash before
# the call it names. Recovery if the board vanishes: ae3-usb-unstick
# (Pi reboot; uhubctl cannot help on the Pi 5).
#
# Usage on the board (neutral /flash/main.py staged first -- see
# ae3-board-access):
#     mpremote connect <by-id> run bench/probes/s22_flood_probe.py
# Rung selection for resuming after a death (load-once rule):
#     /flash/s22_probe_cfg.json  {"rungs": ["fatal-513"], "note": "..."}

import json
import struct
import time

LOG = "/flash/s22_flood.txt"
CFG = "/flash/s22_probe_cfg.json"
ELF_PATH = "/flash/bm_he.elf"

# wire protocol (firmware/bm_he/src/bm_he.h) -- the subset this probe uses
WCMD_LINK = 0x13
WCMD_LINK_UP = 0x01
WCMD_QUERY = 0x14
WCMD_FRAG = 0x16
WCMD_PUB = 0x18
WREP_STATUS = 0x94

MSG_PAYLOAD = 492            # 496 rpmsg budget - 4 B wire header
CHUNK_HDR_FMT = "<IHHH"      # frame_seq | idx | count | payload_len
CHUNK_HDR_LEN = 10
CAMERA_MAX_PAYLOAD = 1400    # REV-28

STATUS_FMT = "<Q16s16sIIIIIIIIIIII"    # wire_status_t, 88 B
STATUS_KEYS = ("node_id", "ip_ll", "ip_ucast", "stage", "err",
               "tx_frames", "rx_frames", "tx_oversize", "link_up",
               "heap_free", "heap_min", "tx_dropped", "frag_errors",
               "stream_sent", "stream_errs")

# bm_status_page_t (bm_he.h): magic +0, stage +4, err +8, tick +12
BM_STATUS_PAGE = 0x600BFE00
BM_MAGIC = 0x424D4845                  # 'BMHE'
ERR_NAMES = {0xA110C: "malloc-fail hook",
             0xDEAD: "assert",
             0x570F: "stack-overflow hook"}

# he_sample.h -- fixed page, self-describing header, 24 B records.
SAMPLE_PAGE = 0x600BFA00
SAMPLE_MAGIC = 0x504D5348              # 'HSMP'
SAMPLE_HDR_FMT = "<IIII"               # magic | version | capacity | count
SAMPLE_HDR_LEN = 16
SAMPLE_REC_FMT = "<HHHBBIIHHI"         # 24 B, he_sample_rec_t
SAMPLE_REC_LEN = 24
SAMPLE_PAGE_LEN = 1024

STATUS_EVERY_MS = 10000      # flood status sample cadence


# ---- pure helpers (CPython-testable; bench/test_s22_probe.py) ------------

def frame_msgs(seq, frame_bytes, payload_max=CAMERA_MAX_PAYLOAD, fill=0xC3):
    """A synthetic JPEG of frame_bytes -> rpmsg messages, byte-identical
    to BridgeCore.capture_pub_msgs (chunks of payload_max incl. the 10 B
    header, last chunk partial)."""
    data_max = payload_max - CHUNK_HDR_LEN
    if frame_bytes <= 0 or data_max <= 0:
        return []
    count = (frame_bytes + data_max - 1) // data_max
    msgs = []
    off = 0
    for idx in range(count):
        take = min(data_max, frame_bytes - off)
        payload = (struct.pack(CHUNK_HDR_FMT, seq, idx, count, take) +
                   bytes([fill]) * take)
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
    return msgs


def rung_plan():
    """The ladder. Each rung: (name, frame_bytes, period_ms, dwell_s).

    Rates are the measured on-chain boundary re-expressed as synthetic
    frames; msg/s below counts inbound WCMD_PUB+WCMD_FRAG only (the
    on-chain figures also carried query/keep-alive overhead):
      control-315   9,198 B / 66 ms  = 20 msgs x 15.2 fps ~ 303 msg/s
      fatal-513    23,831 B / 100 ms = 52 msgs x 10 fps   = 520 msg/s
      demo-560      9,198 B / 36 ms  = 20 msgs x 27.8 fps ~ 556 msg/s
    control dwells 60 s (a clean pass is its verdict); the fatal rungs
    dwell LONG because the demo event needed ~5 min at ~560 to die.
    """
    return [("control-315", 9198, 66, 60),
            ("fatal-513", 23831, 100, 300),
            ("demo-560", 9198, 36, 600)]


def classify(err, tick_moved, answered):
    """(BP->err, tick advancing?, query answered?) -> verdict string."""
    if err:
        return "HOOK-SPIN (%s, err=0x%X)" % (
            ERR_NAMES.get(err, "unknown code"), err)
    if answered:
        return "ALIVE (answered a query)"
    if tick_moved:
        return "TX-DEAD (tick advancing, err=0, no reply -- rr_send/vring)"
    return "PARKED (tick frozen, err=0, no reply -- task blocked)"


def decode_page(buf):
    """Raw sample page bytes -> dict, records OLDEST FIRST (s19 shape)."""
    if len(buf) < SAMPLE_HDR_LEN:
        return {"ok": False, "why": "short page"}
    magic, version, capacity, count = struct.unpack_from(SAMPLE_HDR_FMT,
                                                         buf, 0)
    if magic != SAMPLE_MAGIC:
        return {"ok": False, "why": "bad magic 0x%08x" % magic}
    if not capacity or SAMPLE_HDR_LEN + capacity * SAMPLE_REC_LEN > len(buf):
        return {"ok": False, "why": "bad capacity %d" % capacity}
    have = min(count, capacity)
    start = count - have
    recs = []
    for i in range(have):
        off = SAMPLE_HDR_LEN + ((start + i) % capacity) * SAMPLE_REC_LEN
        f = struct.unpack_from(SAMPLE_REC_FMT, buf, off)
        recs.append({"idx": f[0], "count": f[1], "len": f[2], "err": f[3],
                     "txq": f[4], "heap_free": f[5], "heap_min": f[6],
                     "tx_dropped": f[7], "tx_stalls": f[8],
                     "tick_ms": f[9]})
    return {"ok": True, "version": version, "capacity": capacity,
            "count": count, "recs": recs}


def fmt_rec(r):
    return ("    chunk %3d/%-3d len=%4d err=%d txq=%2d heap=%6d min=%6d "
            "txdrop=%d stall=%d t=%d"
            % (r["idx"], r["count"], r["len"], r["err"], r["txq"],
               r["heap_free"], r["heap_min"], r["tx_dropped"],
               r["tx_stalls"], r["tick_ms"]))


def fmt_status(st):
    return ("heap=%d min=%d txdrop=%d txf=%d rxf=%d stall_page_n/a "
            "frag=%d stream=%d/%d"
            % (st["heap_free"], st["heap_min"], st["tx_dropped"],
               st["tx_frames"], st["rx_frames"], st["frag_errors"],
               st["stream_sent"], st["stream_errs"]))


# ---- hardware side --------------------------------------------------------

def log(msg):
    line = "%8d %s" % (time.ticks_ms(), msg)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass
    print(line)


def page_u32(off):
    import machine
    return machine.mem32[BM_STATUS_PAGE + off] & 0xFFFFFFFF


def he_page_ok():
    return page_u32(0) == BM_MAGIC


def he_tick():
    return page_u32(12) if he_page_ok() else None


def he_err():
    return page_u32(8) if he_page_ok() else None


def he_ticking(window_ms=300):
    """Is the wire task going round its loop? (BP->tick is written at the
    TOP of the loop -- frozen means blocked or dead, NOT necessarily the
    whole core: the S19 probe measured a healthy stack with a frozen
    tick during a TX drain stall.)"""
    t0 = he_tick()
    if t0 is None:
        return False
    time.sleep_ms(window_ms)
    return he_tick() != t0


def read_page():
    import machine
    import uctypes
    if machine.mem32[SAMPLE_PAGE] & 0xFFFFFFFF != SAMPLE_MAGIC:
        return {"ok": False, "why": "no HSMP magic"}
    return decode_page(bytes(uctypes.bytearray_at(SAMPLE_PAGE,
                                                  SAMPLE_PAGE_LEN)))


def dump_ring():
    try:
        import machine
        import uctypes
        if not he_page_ok():
            return
        addr = page_u32(28)
        size = page_u32(32)
        widx = page_u32(36)
        if not addr or not size:
            return
        ring = bytes(uctypes.bytearray_at(addr, size))
        n = min(widx, size)
        start = widx % size if widx > size else 0
        text = (ring[start:n] + ring[:start]) if widx > size else ring[:n]
        with open(LOG, "a") as f:
            f.write("---- HE ring ----\n")
            f.write(text.decode())
            f.write("\n---- end ring ----\n")
            f.flush()
        print("(HE ring dumped to log, %d B)" % len(text))
    except Exception as e:
        log("ring dump failed: %r" % (e,))


class Wire:
    """rpmsg 'bm-wire' client -- the bridge's HeWire, minus the VCP.
    (Verbatim from s19_pub_probe; probes are self-contained files.)"""

    def __init__(self):
        self.ept = None
        self.queue = []
        self.status = None
        self.rx_msgs = 0
        self.rx_bytes = 0
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
            raise OSError("stale HE still running -- reset the board "
                          "(mpremote reset) before running the probe")
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
        self.ept.send(msg, timeout=1000)

    def drain(self, ms=0):
        """Consume HE->HP messages (YIELD FIRST -- the _rx callback is
        what recycles the vring buffer; popping our own list does not)."""
        t0 = time.ticks_ms()
        n = 0
        while True:
            time.sleep_ms(1)
            while self.queue:
                m = self.queue.pop(0)
                n += 1
                self.rx_msgs += 1
                self.rx_bytes += len(m)
                if len(m) >= 4 and m[0] == WREP_STATUS and len(m) >= 4 + 88:
                    self.status = dict(zip(STATUS_KEYS,
                                           struct.unpack(STATUS_FMT,
                                                         m[4:4 + 88])))
            if time.ticks_diff(time.ticks_ms(), t0) >= ms:
                return n
            time.sleep_ms(1)

    def try_query(self, timeout_ms=2000):
        try:
            return self.query(timeout_ms)
        except Exception as e:
            self.send_timeouts += 1
            log("  (query send blocked: %r)" % (e,))
            return None

    def query(self, timeout_ms=2000):
        self.status = None
        self.send(struct.pack("<BBH", WCMD_QUERY, 0, 0))
        t0 = time.ticks_ms()
        while self.status is None:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return None
            self.drain(5)
        return self.status


def postmortem(wire, tag):
    """The read the four real events never got. Safe with a dead HE --
    everything here is mem32 or a timeout-bounded query."""
    log("POSTMORTEM (%s)" % tag)
    if not he_page_ok():
        log("  status page magic GONE -- core reset or page clobbered")
        return "NO-PAGE"
    err = he_err()
    stage = page_u32(4)
    t0 = he_tick()
    time.sleep_ms(300)
    t1 = he_tick()
    tick_moved = (t1 != t0)
    log("  BP: stage=%d err=0x%X tick=%d->%d (%s) tx=%d rx=%d"
        % (stage, err, t0, t1, "moving" if tick_moved else "FROZEN",
           page_u32(16), page_u32(20)))
    st = wire.try_query(timeout_ms=5000) if wire else None
    if st:
        log("  query ANSWERED: %s" % fmt_status(st))
    else:
        log("  query: no reply in 5 s")
    page = read_page()
    if page.get("ok"):
        log("  sample page: count=%d (last %d records follow)"
            % (page["count"], min(len(page["recs"]), 12)))
        for r in page["recs"][-12:]:
            log(fmt_rec(r))
    else:
        log("  sample page: %s" % page.get("why"))
    dump_ring()
    verdict = classify(err, tick_moved, st is not None)
    log("  CLASSIFICATION: %s" % verdict)
    return verdict


def run_rung(wire, name, frame_bytes, period_ms, dwell_s):
    """One sustained-rate rung. Returns a result dict; never raises on
    HE death."""
    res = {"name": name, "frame_bytes": frame_bytes, "period_ms": period_ms,
           "dwell_s": dwell_s, "frames_sent": 0, "msgs_sent": 0,
           "send_errs": 0, "query_fails": 0, "alive": True, "verdict": ""}

    msgs0 = frame_msgs(0, frame_bytes)
    per_frame = len(msgs0)
    chunks = (frame_bytes + (CAMERA_MAX_PAYLOAD - CHUNK_HDR_LEN) - 1) \
        // (CAMERA_MAX_PAYLOAD - CHUNK_HDR_LEN)
    target = per_frame * 1000 // period_ms
    st = wire.try_query()
    log("RUNG %s: %d B/frame = %d chunks = %d msgs, period %d ms "
        "-> target ~%d msg/s inbound, dwell %d s | start heap=%s"
        % (name, frame_bytes, chunks, per_frame, period_ms, target,
           dwell_s, st["heap_free"] if st else "?"))

    t0 = time.ticks_ms()
    last_status = t0
    seq = 0
    while True:
        el = time.ticks_diff(time.ticks_ms(), t0)
        if el >= dwell_s * 1000:
            break

        # Status sample FIRST, on cadence, even when the frame quota is
        # behind -- at the demo rung's 36 ms period the send loop can
        # saturate, and a probe that only checks liveness between frames
        # would push into a corpse for the rest of the dwell.
        if time.ticks_diff(time.ticks_ms(), last_status) >= STATUS_EVERY_MS:
            last_status = time.ticks_ms()
            st = wire.try_query(timeout_ms=1500)
            achieved = res["msgs_sent"] * 1000 // max(el, 1)
            if st:
                log("  t=%3ds frames=%d msgs=%d (~%d msg/s) %s"
                    % (el // 1000, seq, res["msgs_sent"], achieved,
                       fmt_status(st)))
                res["query_fails"] = 0
            else:
                res["query_fails"] += 1
                log("  t=%3ds frames=%d msgs=%d (~%d msg/s) QUERY FAILED "
                    "(#%d) tick %s"
                    % (el // 1000, seq, res["msgs_sent"], achieved,
                       res["query_fails"],
                       "moving" if he_ticking(200) else "FROZEN"))
                # Two consecutive failed samples = the mute; stop pushing
                # and take the postmortem while the state is fresh.
                if res["query_fails"] >= 2:
                    res["alive"] = False
                    res["verdict"] = postmortem(wire, "%s t=%ds"
                                                % (name, el // 1000))
                    return res

        # Frame quota at the rung's cadence (integer arithmetic; the
        # bridge is exactly this bursty -- a frame's msgs back-to-back,
        # then the inter-frame gap).
        if seq < el // period_ms:
            msgs = frame_msgs(seq, frame_bytes)
            dead = False
            for i, m in enumerate(msgs):
                try:
                    wire.send(m)
                    res["msgs_sent"] += 1
                except Exception as e:
                    res["send_errs"] += 1
                    if res["send_errs"] <= 4:
                        log("  send blocked at frame %d msg %d: %r"
                            % (seq, i, e))
                    # A blocked send is backpressure, not death -- but
                    # three consecutive full-timeout blocks with a dead
                    # tick is death; check cheaply and move on.
                    if res["send_errs"] % 3 == 0 and not he_ticking(100):
                        dead = True
                        break
                if (i + 1) % 3 == 0:      # drain every ~chunk, as the
                    wire.drain(0)         # fixed bridge does
            wire.drain(0)
            seq += 1
            res["frames_sent"] = seq
            if dead and not he_ticking(300):
                res["alive"] = False
                res["verdict"] = postmortem(wire, "%s frame %d" % (name, seq))
                return res
            continue

        # Between frames: drain and breathe.
        wire.drain(0)
        time.sleep_ms(1)

    got, _ = drain_tail(wire)
    st = wire.try_query(timeout_ms=5000)
    res["alive"] = st is not None or he_ticking()
    el = max(time.ticks_diff(time.ticks_ms(), t0), 1)
    log("  RUNG %s DONE: %d frames, %d msgs (~%d msg/s achieved), "
        "%d send errs, %d query fails, drained %d tail msgs | end %s"
        % (name, res["frames_sent"], res["msgs_sent"],
           res["msgs_sent"] * 1000 // el, res["send_errs"],
           res["query_fails"], got,
           fmt_status(st) if st else "NO STATUS"))
    if not res["alive"]:
        res["verdict"] = postmortem(wire, "%s end-of-dwell" % name)
    return res


def drain_tail(wire, quiet_ms=300, max_ms=8000):
    t0 = time.ticks_ms()
    total = 0
    last = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < max_ms:
        got = wire.drain(20)
        if got:
            total += got
            last = time.ticks_ms()
        elif time.ticks_diff(time.ticks_ms(), last) >= quiet_ms:
            break
    return total, time.ticks_diff(time.ticks_ms(), t0)


def main():
    log("=" * 60)
    log("S22 bite 1 flood probe -- sustained synthetic publish, no Pi, "
        "no camera")
    try:
        with open(CFG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    want = cfg.get("rungs")
    ladder = [r for r in rung_plan() if not want or r[0] in want]
    log("rungs=%s note=%s" % ([r[0] for r in ladder], cfg.get("note", "")))

    wire = Wire()
    log("loading HE elf %s" % ELF_PATH)
    rp = wire.start()
    log("bm-wire announced")

    results = []
    try:
        # Force the link up (no Pi to announce it; l2 will not transmit
        # on a down port). UP is collected by l2's 100 ms renegotiation
        # timer (REV-12).
        wire.send(struct.pack("<BBHB", WCMD_LINK, 1, 1, WCMD_LINK_UP))
        t0 = time.ticks_ms()
        while True:
            st = wire.query()
            if st and st["link_up"]:
                log("link up, stage=%d heap_free=%d heap_min=%d"
                    % (st["stage"], st["heap_free"], st["heap_min"]))
                break
            if time.ticks_diff(time.ticks_ms(), t0) > 5000:
                log("ABORT: link never came up (status=%s)" % (st,))
                return
            time.sleep_ms(100)

        page = read_page()
        if page.get("ok"):
            log("sample page ok: version=%d capacity=%d count=%d"
                % (page["version"], page["capacity"], page["count"]))
        else:
            log("WARN: sample page not readable (%s) -- postmortems "
                "will miss the chunk curve" % page.get("why"))

        for (name, frame_bytes, period_ms, dwell_s) in ladder:
            res = run_rung(wire, name, frame_bytes, period_ms, dwell_s)
            results.append(res)
            if not res["alive"]:
                log("HE DEAD after rung %s -- ending the ladder "
                    "(load-once rule: warm reset to continue)" % name)
                break
            wire.drain(300)
    finally:
        dead = [r for r in results if not r["alive"]]
        if dead:
            log("VERDICT: MUTE REPRODUCED at rung %s -- %s"
                % (dead[0]["name"], dead[0]["verdict"]))
        elif results:
            log("VERDICT: SURVIVED all %d rungs (%s)"
                % (len(results), ", ".join(r["name"] for r in results)))
        else:
            log("VERDICT: no rungs ran")
        try:
            wire.send(struct.pack("<BBHB", WCMD_LINK, 1, 1, 0))
            time.sleep_ms(50)
        except Exception:
            pass
        try:
            rp.stop()
            log("he stopped")
        except Exception:
            log("he stop FAILED -- recovery: mpremote reset (or the "
                "ae3-usb-unstick ladder if the board is off the bus)")
        log("done")


# Guarded so bench/test_s22_probe.py can import the pure helpers.
# Hardware modules (machine, uctypes, openamp) import inside functions.
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import sys
        log("PYTHON EXCEPTION: %r" % (e,))
        try:
            with open(LOG, "a") as f:
                sys.print_exception(e, f)
                f.flush()
        except Exception:
            pass
