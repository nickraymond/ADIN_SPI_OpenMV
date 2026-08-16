# s19_pub_probe.py -- S19 bite 1: where does the HE heap go during a
# pub/sub chunk burst, and is the wall CHUNK COUNT or BYTES IN FLIGHT?
#
# Runs on the AE3's HP core (mpremote run), with NO Pi chain and NO
# camera:
#   * no Pi, because bm_pub transmits unconditionally -- vendored
#     pubsub.c bm_pub_wl calls bm_middleware_net_tx with no
#     remote-subscriber gate -- so the whole udp -> l2 -> netwire ->
#     bm_malloc -> txq path runs against a forced link-up with nobody
#     listening. Cheap, repeatable, no bench booking.
#   * no camera, because S18's allocator fault is a FRAMEBUFFER GROWTH
#     fault (SPEC §Open questions). This probe never touches the sensor,
#     so that failure mode is structurally absent and any death here is
#     the heap, not SRAM9_B.
#
# It publishes SYNTHETIC chunks whose framing is byte-identical to the
# bridge's own chunker (asserted against BridgeCore.capture_pub_msgs in
# bench/test_s19_probe.py) -- otherwise the probe would be measuring
# traffic the product never sends.
#
# THE EXPERIMENT. S18 measured: QVGA 3 chunks fine, VGA 8 fine, HD 26
# dies with `freertos: malloc failed` after 8. Three explanations fit
# that one data point, and they differ in what they predict:
#   (a) chunk COUNT -- some queue/vring depth is exceeded
#   (b) BYTES in flight -- ~36 KB against a 64 KB FreeRTOS heap
#   (c) drain starvation -- the HP's send loop (bm_bridge.py:835) pushes
#       a whole frame without servicing he.queue, so the HE cannot empty
#       its TX side while the burst arrives
# The sweeps below hold one variable at a time. Row E (26 chunks x 350 B)
# is the discriminator between (a) and (b): same count as the known
# failure, a quarter of the bytes.
#
# BREADCRUMB DISCIPLINE (S18, earned the hard way): every step is written
# AND FLUSHED to /flash before the call it names, so a fault that takes
# the board off the USB bus still leaves the answer behind.
#
# Recovery if the board vanishes: the `ae3-usb-unstick` skill (Pi reboot
# -- uhubctl cannot help, the Pi 5 root hub never cuts VBUS).
#
# Usage on the board:
#     mpremote connect <by-id> run bench/probes/s19_pub_probe.py
# Phase selection (the HE loads ONCE per boot, so a halt ends the run --
# warm reset, then pick up at the next phase):
#     /flash/s19_probe_cfg.json  {"phases": ["count"], "note": "..."}

import json
import struct
import time

LOG = "/flash/s19_pub.txt"
CFG = "/flash/s19_probe_cfg.json"
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

BM_STATUS_PAGE = 0x600BFE00
BM_MAGIC = 0x424D4845                  # 'BMHE'

# he_sample.h -- fixed page, self-describing header, 24 B records.
SAMPLE_PAGE = 0x600BFA00
SAMPLE_MAGIC = 0x504D5348              # 'HSMP'
SAMPLE_HDR_FMT = "<IIII"               # magic | version | capacity | count
SAMPLE_HDR_LEN = 16
SAMPLE_REC_FMT = "<HHHBBIIHHI"         # 24 B, he_sample_rec_t
SAMPLE_REC_LEN = 24
SAMPLE_PAGE_LEN = 1024


# ---- pure helpers (CPython-testable; bench/test_s19_probe.py) ------------

def chunk_payload(seq, idx, count, data_len, fill=0xC3):
    """One camera/stream chunk: 10 B LE header + data_len payload bytes."""
    return (struct.pack(CHUNK_HDR_FMT, seq, idx, count, data_len) +
            bytes([fill]) * data_len)


def pub_msgs(payload):
    """One chunk payload -> rpmsg messages (WCMD_PUB + WCMD_FRAG).

    Mirrors BridgeCore.capture_pub_msgs' fragmentation exactly: the first
    message carries hdr.len = TOTAL payload length, continuations are
    WCMD_FRAG with their own length (wire_frag.h; the vring is in-order,
    so no sequence field).
    """
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


def burst_msgs(seq, count, size):
    """A synthetic `count`-chunk frame, each chunk `size` bytes on the
    wire (header included) -- i.e. what the bridge would emit for a JPEG
    of count * (size - 10) bytes."""
    data_len = size - CHUNK_HDR_LEN
    msgs = []
    for idx in range(count):
        msgs.extend(pub_msgs(chunk_payload(seq, idx, count, data_len)))
    return msgs


def plan(phase):
    """Sweep rows for a phase. Each row: (count, size, pace_ms, drain).

    count  chunks in the synthetic frame
    size   bytes per chunk on the wire (<= 1400, REV-28)
    pace   ms of sleep between chunks (0 = back-to-back, as the bridge
           does today)
    drain  service the HE->HP rpmsg queue DURING the burst (the bridge
           does NOT -- bm_bridge.py:835)
    """
    if phase == "count":
        # Ramp to the wall at the production chunk size. First failure
        # here is the crossover the (a) hypothesis predicts.
        return [(n, 1400, 0, False) for n in (3, 8, 12, 16, 20, 26, 32)]
    if phase == "bytes":
        # A: the known HD failure. E: same COUNT, quarter the BYTES.
        # B/D: same BYTES, more chunks. C: half of both.
        return [(26, 1400, 0, False),    # A -- reproduce
                (26, 350, 0, False),     # E -- THE discriminator
                (13, 1400, 0, False),    # C
                (52, 700, 0, False),     # B
                (104, 350, 0, False)]    # D
    if phase == "pace":
        # Does time-between-chunks buy drain, and does draining during
        # the burst do what pacing does? (bite 2's premise, as a knob)
        return [(26, 1400, 2, False),
                (26, 1400, 5, False),
                (26, 1400, 10, False),
                (26, 1400, 0, True),
                (26, 1400, 2, True)]
    return []


def decode_page(buf):
    """Raw sample page bytes -> dict. Returns records OLDEST FIRST.

    `count` is records ever written; the ring holds the last `capacity`.
    A caller that missed more than `capacity` records between reads has
    lost data -- `lost` says how many, because a silently truncated
    curve reads as a complete one.
    """
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
                     "tx_dropped": f[7], "rpmsg_drops": f[8],
                     "tick_ms": f[9]})
    return {"ok": True, "version": version, "capacity": capacity,
            "count": count, "recs": recs}


def recs_since(page, mark):
    """Records written since `mark` (a previous page['count']), plus how
    many were overwritten before we could read them."""
    if not page.get("ok"):
        return [], 0
    new = page["count"] - mark
    if new <= 0:
        return [], 0
    lost = max(0, new - page["capacity"])
    return page["recs"][-min(new, page["capacity"]):], lost


def fmt_rec(r):
    return ("    chunk %3d/%-3d len=%4d err=%d txq=%2d heap=%6d min=%6d "
            "txdrop=%d rpdrop=%d t=%d"
            % (r["idx"], r["count"], r["len"], r["err"], r["txq"],
               r["heap_free"], r["heap_min"], r["tx_dropped"],
               r["rpmsg_drops"], r["tick_ms"]))


def verdict(rows):
    """rows: list of dicts from run_row. One line naming what the sweep
    showed -- printed AND written, so the answer is in the artifact."""
    died = [r for r in rows if not r["alive"]]
    if not died:
        floors = [r["heap_min_end"] for r in rows if r["heap_min_end"]]
        return ("SURVIVED all %d rows; lowest heap watermark %s B"
                % (len(rows), min(floors) if floors else "?"))
    d = died[0]
    return ("WALL at row count=%d size=%d pace=%d drain=%s -- "
            "%d chunks published before the HE stopped"
            % (d["count"], d["size"], d["pace"], d["drain"], d["published"]))


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


def read_page():
    import machine
    import uctypes
    if machine.mem32[SAMPLE_PAGE] & 0xFFFFFFFF != SAMPLE_MAGIC:
        return {"ok": False, "why": "no HSMP magic"}
    return decode_page(bytes(uctypes.bytearray_at(SAMPLE_PAGE,
                                                  SAMPLE_PAGE_LEN)))


def he_tick():
    import machine
    if machine.mem32[BM_STATUS_PAGE] & 0xFFFFFFFF != BM_MAGIC:
        return None
    return machine.mem32[BM_STATUS_PAGE + 12] & 0xFFFFFFFF


def he_ticking(window_ms=300):
    """Is the wire task going round its loop? BP->tick is written at the
    TOP of that loop (main.c), so this answers 'is the wire task free',
    NOT 'is the HE alive' -- a task parked in wire_pump_tx's 100 ms-per-
    message rr_send retry freezes the tick while the stack is perfectly
    healthy. Measured live on the first probe run, which is why the two
    questions are now asked separately."""
    t0 = he_tick()
    if t0 is None:
        return False
    time.sleep_ms(window_ms)
    return he_tick() != t0


def he_alive(wire=None):
    """Alive = the stack still ANSWERS. Ticking is checked first because
    it is instant; a frozen tick falls through to a real round trip with
    a long timeout, which is what separates 'wedged in a TX drain stall'
    from 'dead'."""
    if he_ticking():
        return True, "ticking"
    if wire is None:
        return False, "tick frozen, no wire to ask"
    if wire.query(timeout_ms=5000) is not None:
        return True, "TICK FROZEN but answered a query -- TX drain stall"
    return False, "tick frozen AND no reply"


def dump_ring():
    try:
        import machine
        import uctypes
        if machine.mem32[BM_STATUS_PAGE] & 0xFFFFFFFF != BM_MAGIC:
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
        with open(LOG, "a") as f:
            f.write("---- HE ring ----\n")
            f.write(text.decode())
            f.write("\n---- end ring ----\n")
            f.flush()
    except Exception as e:
        log("ring dump failed: %r" % (e,))


class Wire:
    """rpmsg 'bm-wire' client -- the bridge's HeWire, minus the VCP."""

    def __init__(self):
        self.ept = None
        self.queue = []
        self.status = None
        self.rx_msgs = 0
        self.rx_bytes = 0

    def _ns(self, src, name):
        if name == "bm-wire":
            import openamp
            self.ept = openamp.Endpoint("bm-wire", self._rx, dest=src)

    def _rx(self, src, data):
        self.queue.append(bytes(data))

    def start(self):
        import openamp
        # Load-once rule (README lifecycle): a stale HE from a previous
        # boot survives warm resets and cannot be safely re-attached.
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
        """Consume HE->HP messages, keeping any status reply. Returns the
        number of messages consumed -- the bridge's own drain, isolated
        so the burst can deliberately skip it.

        Counting matters: if the HE published but nothing comes back, the
        frames never reached the wire, and that is a different fault from
        a heap that simply ran out."""
        t0 = time.ticks_ms()
        n = 0
        while True:
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

    def drain_until_quiet(self, quiet_ms=300, max_ms=8000):
        """Drain until nothing arrives for quiet_ms (or max_ms elapses).
        A fixed sleep would truncate a slow drain and make a stall look
        like a loss."""
        t0 = time.ticks_ms()
        total = 0
        last = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < max_ms:
            got = self.drain(20)
            if got:
                total += got
                last = time.ticks_ms()
            elif time.ticks_diff(time.ticks_ms(), last) >= quiet_ms:
                break
        return total, time.ticks_diff(time.ticks_ms(), t0)

    def query(self, timeout_ms=2000):
        self.status = None
        self.send(struct.pack("<BBH", WCMD_QUERY, 0, 0))
        t0 = time.ticks_ms()
        while self.status is None:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                return None
            self.drain(5)
        return self.status


def run_row(wire, seq, count, size, pace, drain, mark):
    """One sweep row. Returns a result dict; never raises on HE death."""
    row = {"count": count, "size": size, "pace": pace, "drain": drain,
           "published": 0, "lost": 0, "alive": True, "heap_min_end": None,
           "sent_ms": 0, "rpmsg_send_errs": 0}

    st = wire.query()
    row["heap_before"] = st["heap_free"] if st else None
    log("ROW count=%d size=%d pace=%d drain=%s | heap_before=%s"
        % (count, size, pace, drain, row["heap_before"]))

    msgs = burst_msgs(seq, count, size)
    log("  burst: %d chunks -> %d rpmsg msgs, %d payload B"
        % (count, len(msgs), count * size))

    rx0 = wire.rx_msgs
    t0 = time.ticks_ms()
    per_chunk = len(msgs) // count if count else len(msgs)
    for i, m in enumerate(msgs):
        try:
            wire.send(m)
        except Exception as e:
            row["rpmsg_send_errs"] += 1
            log("  send failed at msg %d: %r" % (i, e))
            break
        if per_chunk and (i + 1) % per_chunk == 0:
            if drain:
                wire.drain(0)
            if pace:
                time.sleep_ms(pace)
    row["sent_ms"] = time.ticks_diff(time.ticks_ms(), t0)

    # Drain to quiet rather than for a fixed slice: the whole question is
    # how long the HE takes to give its frames back.
    got, drain_ms = wire.drain_until_quiet()
    row["rx_msgs"] = wire.rx_msgs - rx0
    row["drain_ms"] = drain_ms
    row["alive"], row["how"] = he_alive(wire)

    page = read_page()
    recs, lost = recs_since(page, mark)
    row["published"] = len(recs)
    row["lost"] = lost
    row["mark"] = page["count"] if page.get("ok") else mark
    if recs:
        row["heap_min_end"] = recs[-1]["heap_min"]
    log("  sent in %d ms | drained %d msgs in %d ms | %d records%s | %s"
        % (row["sent_ms"], row["rx_msgs"], drain_ms, len(recs),
           (" (+%d LOST to wrap)" % lost) if lost else "", row["how"]))
    for r in recs:
        log(fmt_rec(r))
    if row["alive"]:
        # The recovery number: did the heap the burst consumed come back?
        st = wire.query()
        if st:
            row["heap_after"] = st["heap_free"]
            log("  AFTER heap_free=%d (before %s, recovered %s) heap_min=%d "
                "tx_frames=%d tx_dropped=%d frag_errors=%d"
                % (st["heap_free"], row["heap_before"],
                   (st["heap_free"] - min(r["heap_free"] for r in recs))
                   if recs else "?",
                   st["heap_min"], st["tx_frames"], st["tx_dropped"],
                   st["frag_errors"]))
    return row


def main():
    log("=" * 60)
    log("S19 bite 1 pub probe -- synthetic chunk bursts, no Pi, no camera")
    try:
        with open(CFG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    # A row that kills the HE ends the run (load-once rule), so the cfg
    # can also name explicit rows -- that is how the sweep is resumed
    # after a wall instead of re-running the rows already answered.
    #   {"rows": [[26, 350, 0, false], [13, 1400, 0, false]]}
    explicit = [tuple(r) for r in cfg.get("rows", [])]
    phases = cfg.get("phases") or (["count"] if not explicit else [])
    log("phases=%s rows=%s note=%s" % (phases, explicit, cfg.get("note", "")))

    wire = Wire()
    log("loading HE elf %s" % ELF_PATH)
    rp = wire.start()
    log("bm-wire announced")

    rows = []
    try:
        # Force the link up: with no Pi there is nothing to announce it,
        # and l2 will not transmit on a down port. UP is collected by
        # l2's 100 ms renegotiation timer (REV-12), so wait for it.
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
        if not page.get("ok"):
            log("ABORT: sample page not readable (%s) -- is the staged "
                "ELF the S19 build?" % page.get("why"))
            return
        log("sample page ok: version=%d capacity=%d count=%d"
            % (page["version"], page["capacity"], page["count"]))
        mark = page["count"]

        seq = 0
        for phase in phases + (["explicit"] if explicit else []):
            for (count, size, pace, drain) in (explicit if phase == "explicit"
                                               else plan(phase)):
                seq += 1
                row = run_row(wire, seq, count, size, pace, drain, mark)
                row["phase"] = phase
                mark = row["mark"]
                rows.append(row)
                if not row["alive"]:
                    log("HE STOPPED -- ending the sweep here (load-once "
                        "rule: warm reset, then run the next phase)")
                    break
                wire.drain(200)
            if rows and not rows[-1]["alive"]:
                break
    finally:
        log("VERDICT: %s" % verdict(rows))
        dump_ring()
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


# Guarded so bench/test_s19_probe.py can import the pure helpers (the
# S18 probes called main() bare -- they had no host tests). Everything
# above this line is import-safe on CPython: hardware modules (machine,
# uctypes, openamp, sensor) are imported inside functions only.
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
