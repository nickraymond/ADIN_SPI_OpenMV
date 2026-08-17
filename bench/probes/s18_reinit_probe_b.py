# s18_reinit_probe_b.py -- rung B: does a LOADED but IDLE HE core wedge
# the sensor re-init?
#
# Rung A (s18_reinit_probe.py, run 2026-08-16) answered the first half:
# with NO HE core loaded, a re-init at a 0 ms delay after a capture is
# fine at QVGA, VGA and HD -- 12/12 PASS. So the sensor's own pipeline is
# NOT the mechanism, and the wedge needs the HE.
#
# That leaves two sub-cases, and they need DIFFERENT fixes:
#   HE merely LOADED wedges it   -> the coupling is the loaded core /
#                                   the SRAM9_B allocator neighbourhood,
#                                   and gating on wire_status_t.stream_sent
#                                   would do nothing;
#   only PUBLISHING wedges it    -> gate the re-init on stream_sent, which
#                                   bm_bridge.py already parses.
#
# This probe is the discriminator: identical ladder to rung A, with
# bm_he.elf LOADED and nothing driving it. No Pi, no chain, no VCP relay
# pump, no capture commands -- the HE comes up, announces bm-wire, and is
# then left alone while the sensor is re-initialised underneath it.
#
# Load order is the proven-safe one (S18 probes / SPEC 'THE RECIPE'):
# claim the HD framebuffer ceiling BEFORE the ELF loads. Growing the
# framebuffer with the core already loaded takes the board off the USB
# bus with nothing to catch.
#
# The HE is ALWAYS stopped on the way out (finally:), because a stale HE
# survives a warm reset and the recovery is a uhubctl power cycle.

import sensor, time, gc

LOG = "/flash/reinit_probe_b.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000
RECOVER_MS = 2000
BISECT = 3
LADDER = (0, 250, 1000)     # shorter than rung A: 4000 ms passed everywhere

_f = None
_he = None      # HeWire instance, so the queue depth is visible
_bb = None      # bm_bridge module


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def he_state():
    """Evidence that 'idle' is idle: core alive, and what it has sent us."""
    try:
        ok, tick = _bb._he_page()
        return "he tick=%d q=%d qdrops=%d" % (tick, len(_he.queue),
                                              _he.q_drops)
    except Exception as e:
        return "he state unreadable: %r" % e


def _call(what, fn):
    log("  . %s" % what)
    t0 = time.ticks_us()
    fn()
    return time.ticks_diff(time.ticks_us(), t0)


def reinit(res, pf, tag=""):
    """The bridge's _ensure_sensor() step order, one call at a time."""
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
    """What un-wedges it? R1 retry / R2 pause+retry / R3 reset+bootstrap.

    NOTE the asymmetry with rung A: R3 calls sensor.reset() with the HE
    core LOADED. That is a re-init, not a framebuffer grow -- the ceiling
    is already claimed and set_framebuffers(1) is re-pinned before every
    set_framesize -- but it is the riskiest call in this probe, so it is
    breadcrumbed and only reached after R1 and R2 have both failed.
    """
    log("  recovery: R1 immediate retry")
    ok, step, us = reinit(res, pf, "R1 ")
    if ok:
        return "R1-immediate"
    log("  recovery: R2 retry after %d ms (%s)" % (RECOVER_MS, he_state()))
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


def rung(size, cap_pf, to_pf, delay_ms):
    log("=== %s %s -> %s, delay %d ms  [%s]"
        % (size, cap_pf, to_pf, delay_ms, he_state()))
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
    if delay_ms:
        time.sleep_ms(delay_ms)
    ok, step, us = reinit(size, to_pf, "meas ")
    row = {"size": size, "delay": delay_ms, "bytes": nbytes,
           "snap_us": snap_us, "enc_us": enc_us, "ok": ok, "step": step,
           "us": us, "recovery": ""}
    if ok:
        log("  PASS (re-init %d us) [%s]" % (us, he_state()))
    else:
        log("  FAIL at %s [%s]" % (step, he_state()))
        row["recovery"] = recover(size, to_pf)
        log("  recovery: %s" % row["recovery"])
    return row


def sweep(size, rows):
    log("#### sweep %s (colour -> mono), HE LOADED + IDLE" % size)
    lo_fail = None
    hi_pass = None
    for d in LADDER:
        r = rung(size, "color", "mono", d)
        if r is None:
            continue
        rows.append(r)
        if r["ok"]:
            if hi_pass is None:
                hi_pass = d
        else:
            lo_fail = d
        time.sleep_ms(QUIET_MS)
        reinit(size, "color", "reset-to-colour ")
    if lo_fail is None or hi_pass is None or hi_pass <= lo_fail:
        log("#### %s: no boundary in the coarse ladder (fail=%r pass=%r)"
            % (size, lo_fail, hi_pass))
        return
    for _ in range(BISECT):
        mid = (lo_fail + hi_pass) // 2
        if mid <= lo_fail or mid >= hi_pass:
            break
        r = rung(size, "color", "mono", mid)
        if r is None:
            break
        rows.append(r)
        if r["ok"]:
            hi_pass = mid
        else:
            lo_fail = mid
        time.sleep_ms(QUIET_MS)
        reinit(size, "color", "reset-to-colour ")
    log("#### %s boundary: fails at %d ms, passes at %d ms"
        % (size, lo_fail, hi_pass))


def main():
    global _f, _he, _bb
    _f = open(LOG, "w")
    log("---- rung B start (HE core LOADED, idle) ----")
    bootstrap("hd")                     # ceiling BEFORE the ELF -- required

    import bm_bridge as bb
    _bb = bb
    if bb._he_running():
        log("ABORT: a stale HE is already running -- recovery: "
            "sudo uhubctl -l 3 -p 1 -a cycle -d 3, then mpremote reset")
        return
    log("loading HE elf (HD framebuffer already claimed) ->")
    _he = bb.HeWire()
    rp = _he.start()
    log("HE loaded, heap %d, %s" % (gc.mem_free(), he_state()))
    time.sleep(2)
    log("HE settled, %s" % he_state())

    rows = []
    try:
        for size in ("qvga", "vga", "hd"):
            sweep(size, rows)
    finally:
        log("---- summary (HE loaded + idle) ----")
        log("%-5s %-6s %-8s %-6s %-14s %-8s %s"
            % ("size", "delay", "srcbytes", "res", "failed-call", "us",
               "recovery"))
        for r in rows:
            log("%-5s %-6d %-8d %-6s %-14s %-8d %s"
                % (r["size"], r["delay"], r["bytes"],
                   "PASS" if r["ok"] else "FAIL", r["step"] or "-",
                   r["us"], r["recovery"] or "-"))
        log("final %s, heap %d" % (he_state(), gc.mem_free()))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r -- recovery: sudo uhubctl -l 3 -p 1 "
                "-a cycle -d 3, then mpremote reset" % e)
        log("---- rung B end ----")
        try:
            _f.close()
        except Exception:
            pass


main()
