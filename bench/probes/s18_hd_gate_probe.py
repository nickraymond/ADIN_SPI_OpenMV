# s18_hd_gate_probe.py -- probe G: does the PublishGate's own open
# sequence kill the HD re-init?
#
# THE GAP THIS CLOSES. HD has never completed on any PublishGate build
# (S18 matrix findings 2+3), and both on-chain deaths happened with no
# trace line after the gate would have opened -- i.e. inside the HD
# re-init. But no off-chain probe has ever run the on-chain death
# sequence:
#
#   publish -> ~20 s quiet -> WCMD_QUERY/WREP_STATUS barrier x2 ->
#   grow to HD
#
# Rung F's HD rows only flipped pixformat AT HD (never the QVGA->HD
# grow as the measured step), its setup-grows all ran ~8 s after a
# publish WITHOUT the barrier, and its delay ladder stopped at 6 s.
# Rung D ran the barrier but at ~10 ms delays and QVGA only. This probe
# holds everything at the production constant (20 s) and varies one
# ingredient per row:
#
#   row 0  QVGA pub -> barrier x2 -> VGA color   calibration: the exact
#                                                sequence matrix run 4
#                                                survived repeatedly
#   row 1  QVGA pub -> no barrier  -> HD mono    the grow alone at 20 s
#   row 2  QVGA pub -> barrier x2 -> HD mono     THE on-chain death
#                                                sequence (discriminator)
#   row 3  QVGA pub -> barrier x2 -> HD color    drops the pixformat step
#   row 4  VGA  pub -> barrier x2 -> HD mono     bigger published burst
#   row 5  QVGA pub -> queries every 2 s during the wait -> barrier x2
#                   -> HD mono                   mimics the matrix
#                                                driver's cam-status
#                                                keep-alive traffic
#
# Published sizes are the measured on-chain ones (discriminator QVGA
# frame 5,842 B; reef VGA q50 29,148 B), synthetic bytes through the
# real chunker (capture_pub_msgs), so the burst shape is byte-exact.
#
# Breadcrumbs flush BEFORE each risky call (log() flushes): a board
# death mid-row still names its killing step in /flash/hd_gate_probe.txt.
# Rows that die after row 2 are lost -- acceptable: a kill at row 2 IS
# the reproduction, and the flash log survives it.
#
# Off-chain: no Pi chain, bm-light/bm-telemetry stopped. Needs a neutral
# /flash/main.py (ae3-board-access) and /flash/bm_bridge.py (the
# PublishGate build demo_up sha-synced). One HE load per boot (S14 rule)
# -- a re-run needs mpremote reset first.

import sensor, time, gc

LOG = "/flash/hd_gate_probe.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000            # pre-row settle (probe F's proven value)
WAIT_MS = 20000            # REINIT_MIN_QUIET_MS, the production constant
PUMP_SLICE_MS = 2
QVGA_PUB = 5842            # discriminator's exact published frame
VGA_PUB = 29148            # reef VGA q50 (matrix table)
R2_MS = 2000

# (name, prev_size, pub_bytes, barrier, to_res, to_pf, chatty_wait)
ROWS = (
    ("row0-cal-vga", "qvga", QVGA_PUB, True, "vga", "color", False),
    ("row1-nobarrier", "qvga", QVGA_PUB, False, "hd", "mono", False),
    ("row2-DEATHSEQ", "qvga", QVGA_PUB, True, "hd", "mono", False),
    ("row3-hdcolor", "qvga", QVGA_PUB, True, "hd", "color", False),
    ("row4-vgapub", "vga", VGA_PUB, True, "hd", "mono", False),
    ("row5-chatty", "qvga", QVGA_PUB, True, "hd", "mono", True),
)

_f = None
_bb = None
_core = None
_wire = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def pump():
    time.sleep_ms(PUMP_SLICE_MS)
    n = 0
    while _wire.queue:
        _core.he_msg(_wire.queue.pop(0))
        n += 1
    return n


def pump_wait(ms, query_every=0):
    """Wait ms while draining; optionally send a WCMD_QUERY every
    query_every ms (the matrix driver's cam-status keep-alive shape)."""
    t0 = time.ticks_ms()
    tq = t0
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        pump()
        if query_every and \
                time.ticks_diff(time.ticks_ms(), tq) >= query_every:
            tq = time.ticks_ms()
            try:
                _wire.send(_core.query_msg(), timeout=50)
            except Exception as e:
                log("  ! chatty query send failed: %r" % e)


def barrier_once(tag):
    """One WCMD_QUERY -> wait for the WREP_STATUS arrival, pumping.
    Returns True when a reply landed (status_seq advanced)."""
    seq0 = _core.status_seq
    log("  . %s barrier query (seq %d)" % (tag, seq0))
    _wire.send(_core.query_msg(), timeout=1000)
    t0 = time.ticks_ms()
    while _core.status_seq == seq0:
        pump()
        if time.ticks_diff(time.ticks_ms(), t0) > 2000:
            log("  ! %s barrier reply TIMED OUT" % tag)
            return False
    log("  . %s barrier reply in %d ms (heap_free %s)"
        % (tag, time.ticks_diff(time.ticks_ms(), t0),
           (_core.status or {}).get("heap_free", "?")))
    return True


def _call(what, fn):
    log("  . %s" % what)
    t0 = time.ticks_us()
    fn()
    return time.ticks_diff(time.ticks_us(), t0)


def reinit(res, pf, tag=""):
    """Bridge step order (sensor_steps); (ok, failing_step, fail_us).
    pf=None skips the pixformat step (same-format grow)."""
    steps = []
    if pf is not None:
        steps.append(("pixformat", lambda: sensor.set_pixformat(PF[pf])))
    steps.append(("framebuffers", lambda: sensor.set_framebuffers(1)))
    steps.append(("framesize", lambda: sensor.set_framesize(RES[res])))
    steps.append(("settle", lambda: sensor.skip_frames(time=300)))
    for name, fn in steps:
        t0 = time.ticks_us()
        try:
            _call("%s%s" % (tag, name), fn)
        except Exception as e:
            us = time.ticks_diff(time.ticks_us(), t0)
            log("  ! %s%s THREW after %d us: %r" % (tag, name, us, e))
            return (False, name, us)
    return (True, None, 0)


def capture(q=50):
    img = sensor.snapshot()
    return len(img.to_jpeg(quality=q, copy=True).bytearray())


def bootstrap(ceiling="hd"):
    log("  bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("  bootstrap: ceiling %s re-claimed, heap %d"
        % (ceiling, gc.mem_free()))


def publish(nbytes, seq):
    msgs = _core.capture_pub_msgs(bytes(nbytes), seq,
                                  _bb.CAMERA_MAX_PAYLOAD)
    _bb.send_chunk_msgs(msgs, _wire.send, pump)
    return len(msgs)


def recover(res, pf):
    """Probe F's ladder. Returns which rung healed it, or NONE."""
    log("  recovery R1: immediate retry")
    ok, step, us = reinit(res, pf, "R1 ")
    if ok:
        return "R1"
    log("  recovery R2: retry after %d ms" % R2_MS)
    pump_wait(R2_MS)
    ok, step, us = reinit(res, pf, "R2 ")
    if ok:
        return "R2"
    log("  recovery R3: sensor.reset() + full bootstrap, HE LOADED")
    try:
        bootstrap("hd")
    except Exception as e:
        log("  ! R3 bootstrap THREW: %r" % e)
        return "NONE"
    ok, step, us = reinit(res, pf, "R3 ")
    return "R3" if ok else "NONE"


def rung(seq, name, prev, pub_bytes, barrier, to_res, to_pf, chatty, rows):
    log("=== %s: %s pub %d B -> wait %d ms%s -> %s%s -> %s %s"
        % (name, prev, pub_bytes, WAIT_MS,
           " (chatty)" if chatty else "",
           "barrier x2" if barrier else "no barrier",
           "", to_res, to_pf))
    log("  quiet %d ms before setup" % QUIET_MS)
    pump_wait(QUIET_MS)
    ok, step, us = reinit(prev, "color", "setup ")
    if not ok:
        log("  INVALID: setup failed at %s (%d us) -- recovering"
            % (step, us))
        r = recover(prev, "color")
        log("  setup recovery: %s" % r)
        return r != "NONE"
    try:
        nbytes = capture()
        log("  real capture %d B (aliveness); publishing synthetic %d B"
            % (nbytes, pub_bytes))
    except Exception as e:
        log("  INVALID: capture threw %r" % e)
        return True
    nmsgs = publish(pub_bytes, seq)
    log("  published %d msgs, waiting %d ms" % (nmsgs, WAIT_MS))
    pump_wait(WAIT_MS, query_every=2000 if chatty else 0)
    if barrier:
        # The gate's open sequence: two consecutive query/reply
        # exchanges, then the re-init immediately (sub-ms on-chain).
        b1 = barrier_once("b1")
        b2 = barrier_once("b2")
        if not (b1 and b2):
            log("  ! barrier incomplete (b1=%s b2=%s) -- row runs anyway,"
                " noted" % (b1, b2))
    # The pixformat step only exists on a format DELTA (sensor_steps);
    # prev rows leave the sensor at RGB565, so mono adds the step and
    # color skips it -- exactly the production planner's shape.
    meas_pf = to_pf if to_pf != "color" else None
    ok, step, us = reinit(to_res, meas_pf, "meas ")
    row = {"name": name, "prev": prev, "pub": pub_bytes,
           "barrier": barrier, "to": "%s %s" % (to_res, to_pf),
           "ok": ok, "step": step, "us": us, "recovery": ""}
    if ok:
        try:
            n2 = capture()
            log("  RESULT PASS (post capture %d B)" % n2)
        except Exception as e:
            log("  RESULT PASS-reinit but capture threw %r" % e)
            row["ok"] = False
            row["step"] = "post-capture"
    else:
        log("  RESULT FAIL at %s (%d us -- %s)"
            % (step, us, "fresh" if us > 10000 else "WEDGED-instant"))
        row["recovery"] = recover(to_res, to_pf)
        log("  recovery: %s" % row["recovery"])
    rows.append(row)
    return row["ok"] or row["recovery"] != "NONE"


def main():
    global _f, _bb, _core, _wire
    _f = open(LOG, "w")
    log("---- probe G start (HD under the PublishGate open sequence) ----")
    log("bootstrap (pre-HE):")
    bootstrap("hd")

    import bm_bridge as bb
    _bb = bb
    _core = bb.BridgeCore()
    _wire = bb.HeWire()
    log("loading HE elf ->")
    rp = _wire.start()
    log("HE loaded, heap %d" % gc.mem_free())
    time.sleep(2)
    pump()

    rows = []
    try:
        seq = 0
        for name, prev, pub, barrier, to_res, to_pf, chatty in ROWS:
            alive = rung(seq, name, prev, pub, barrier, to_res, to_pf,
                         chatty, rows)
            seq += 1
            if not alive:
                log("#### unrecoverable at %s -- ending ladder" % name)
                return
    finally:
        log("---- summary ----")
        log("%-15s %-5s %-7s %-8s %-9s %-6s %-14s %-9s %s"
            % ("row", "prev", "pub(B)", "barrier", "to", "res",
               "failed-call", "fail-us", "recovery"))
        for r in rows:
            log("%-15s %-5s %-7d %-8s %-9s %-6s %-14s %-9d %s"
                % (r["name"], r["prev"], r["pub"],
                   "yes" if r["barrier"] else "no", r["to"],
                   "PASS" if r["ok"] else "FAIL",
                   r["step"] or "-", r["us"], r["recovery"] or "-"))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r -- recovery: reboot nereus000 + "
                "demo_up" % e)
        log("---- probe G end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
