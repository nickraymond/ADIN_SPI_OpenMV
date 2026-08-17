# s18_reinit_probe.py -- is the sensor re-init failure the HE's fault?
#
# Runs ON the AE3 with NO Pi, NO chain and NO HE core loaded. Reproduces
# the bridge's exact ladder: claim the HD ceiling, capture, then re-init
# the sensor after a controlled delay.
#
# THE question, and nothing else: does a re-init shortly after a snapshot
# fail on its own, or does it need the HE core running and publishing?
#   fails here  -> sensor-side  -> the fix is local waiting/retry/re-init
#                                  inside bm_bridge.py
#   passes here -> HE-coupled   -> gate the re-init on the
#                                  wire_status_t.stream_sent the bridge
#                                  already parses
#
# v2 (S18 bite B2 nibble 1). v1 was written in bite B and never executed;
# running it as-written would have produced ambiguous rows, because every
# rung's SETUP re-init fired zero-delay after the previous rung's capture
# and sat inside the same try -- so a setup failure was indistinguishable
# from the failure being measured. v2 changes four things:
#   1. rungs are isolated behind QUIET_MS of known-good idle (SPEC: >=6 s
#      succeeds 3/3), and a setup failure is reported as INVALID, never
#      as a result;
#   2. every sensor call is timed and tried separately, so the record
#      names the call that throws, not just the transition;
#   3. the delay ladder is coarse-then-bisect, per source-frame size, so
#      "the quiet time scales with the previous frame's size" becomes a
#      boundary in ms instead of an impression;
#   4. after every failure a RECOVERY ladder runs (immediate retry ->
#      retry after a pause -> full reset+bootstrap). The wedge, not the
#      throw, is what kills the bench for a whole session, so what clears
#      it is what picks the fix.
#
# Breadcrumbs are flushed to flash BEFORE each risky call, because the
# S18 record has calls that take the board off the USB bus with nothing
# to catch -- a crash you cannot read is a crash you debug twice.

import sensor, time, gc

LOG = "/flash/reinit_probe.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000      # known-good idle between rungs (SPEC: >=6 s, 3/3)
RECOVER_MS = 2000    # pause used by recovery rung R2
BISECT = 3           # bisection steps after the coarse ladder

_f = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def _call(what, fn):
    """Run one sensor call, timed, and report it by name."""
    log("  . %s" % what)
    t0 = time.ticks_us()
    fn()
    return time.ticks_diff(time.ticks_us(), t0)


def reinit(res, pf, tag=""):
    """The bridge's _ensure_sensor() step order, one call at a time.

    Returns (ok, failing_step, total_us). Mirrors sensor_steps():
    pixformat (only on a pf change) -> framebuffers -> framesize -> settle.
    """
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
    """Snapshot + encode, timed separately -- the encode is the long pole."""
    t0 = time.ticks_us()
    img = sensor.snapshot()
    t1 = time.ticks_us()
    b = img.to_jpeg(quality=q, copy=True).bytearray()
    t2 = time.ticks_us()
    return (len(b), time.ticks_diff(t1, t0), time.ticks_diff(t2, t1))


def bootstrap(ceiling="hd"):
    """The bridge's bootstrap(), verbatim in shape (SPEC 'THE RECIPE')."""
    log("bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)   # small: legalises the pin
    sensor.set_framebuffers(1)          # pin BEFORE the big alloc
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("bootstrap: ceiling %s claimed, heap %d" % (ceiling, gc.mem_free()))


def recover(res, pf):
    """After a failure: what, if anything, un-wedges the sensor?

    This is the rung that chooses the fix. R1/R2 say the bridge can
    simply retry; only R3 says it must re-bootstrap; none says the
    session is lost and the wedge must be PREVENTED, not cured.
    """
    log("  recovery: R1 immediate retry")
    ok, step, us = reinit(res, pf, "R1 ")
    if ok:
        return "R1-immediate"
    log("  recovery: R2 retry after %d ms" % RECOVER_MS)
    time.sleep_ms(RECOVER_MS)
    ok, step, us = reinit(res, pf, "R2 ")
    if ok:
        return "R2-after-%dms" % RECOVER_MS
    log("  recovery: R3 full reset + bootstrap")
    try:
        bootstrap("hd")
    except Exception as e:
        log("  ! R3 bootstrap THREW: %r" % e)
        return "NONE"
    ok, step, us = reinit(res, pf, "R3 ")
    return "R3-reset" if ok else "NONE"


def rung(size, cap_pf, to_pf, delay_ms, to_size=None):
    """capture at (size,cap_pf) -> wait delay_ms -> re-init to (to_*).

    Returns a row dict, or None when SETUP failed (the rung is INVALID
    and must not be read as a measurement).
    """
    to_size = to_size or size
    log("=== %s %s -> %s %s, delay %d ms"
        % (size, cap_pf, to_size, to_pf, delay_ms))
    log("  quiet %d ms before setup" % QUIET_MS)
    time.sleep_ms(QUIET_MS)
    ok, step, us = reinit(size, cap_pf, "setup ")
    if not ok:
        log("  INVALID: setup failed at %s -- rung discarded" % step)
        r = recover(size, cap_pf)
        log("  setup recovery: %s" % r)
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
    ok, step, us = reinit(to_size, to_pf, "meas ")
    row = {"size": size, "to": to_size, "pf": cap_pf, "to_pf": to_pf,
           "delay": delay_ms, "bytes": nbytes, "snap_us": snap_us,
           "enc_us": enc_us, "ok": ok, "step": step, "us": us,
           "recovery": ""}
    if ok:
        log("  PASS (re-init %d us)" % us)
    else:
        log("  FAIL at %s" % step)
        row["recovery"] = recover(to_size, to_pf)
        log("  recovery: %s" % row["recovery"])
    return row


def sweep(size, rows):
    """Coarse ladder, then bisect the boundary. One variable: the delay.

    Same transition every time (colour -> mono, the one bite B tripped
    over); only the source-frame size changes between sweeps, which is
    the hypothesis under test.
    """
    log("#### sweep %s (colour -> mono)" % size)
    lo_fail = None    # largest delay that failed
    hi_pass = None    # smallest delay that passed
    for d in (0, 250, 1000, 4000):
        r = rung(size, "color", "mono", d)
        if r is None:
            continue
        rows.append(r)
        if r["ok"]:
            if hi_pass is None:
                hi_pass = d
        else:
            lo_fail = d
        # back to colour for the next rung, on a quiet sensor
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


def baseline():
    """Does a plain snapshot right after a capture work? (control rung)

    If back-to-back snapshots are fine, the pipeline is not simply busy
    and the re-init calls themselves are what the sensor objects to.
    """
    log("=== baseline: two back-to-back captures, no re-init")
    time.sleep_ms(QUIET_MS)
    ok, step, us = reinit("qvga", "color", "setup ")
    if not ok:
        log("  INVALID: baseline setup failed at %s" % step)
        return
    try:
        a = capture()
        b = capture()
        log("  baseline PASS %d B then %d B" % (a[0], b[0]))
    except Exception as e:
        log("  baseline FAIL: %r" % e)


def main():
    global _f
    _f = open(LOG, "w")
    log("---- probe start (no HE core loaded, v2) ----")
    bootstrap("hd")
    rows = []
    baseline()
    for size in ("qvga", "vga", "hd"):
        sweep(size, rows)
    log("---- summary ----")
    log("%-5s %-6s %-8s %-6s %-14s %-8s %s"
        % ("size", "delay", "srcbytes", "res", "failed-call", "us", "recovery"))
    for r in rows:
        log("%-5s %-6d %-8d %-6s %-14s %-8d %s"
            % (r["size"], r["delay"], r["bytes"], "PASS" if r["ok"] else "FAIL",
               r["step"] or "-", r["us"], r["recovery"] or "-"))
    log("---- encode table (from the rungs above) ----")
    for r in rows:
        log("%-5s %-6s q50 %7d B  snap %6d us  encode %7d us"
            % (r["size"], r["pf"], r["bytes"], r["snap_us"], r["enc_us"]))
    log("---- probe end, heap %d ----" % gc.mem_free())
    try:
        _f.close()
    except Exception:
        pass


main()
