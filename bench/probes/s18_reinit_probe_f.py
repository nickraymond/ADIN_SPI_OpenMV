# s18_reinit_probe_f.py -- rung F: the two questions that decide the fix.
#
# Rung E established that the hazard is TIME SINCE THE PUBLISH -- both
# bridge-observable proxies (publish drained, rpmsg quiet) are falsified
# -- and reproduced the bite B wedge off-chain: one polite
# RuntimeError('Sensor control failed.') at ~270 ms, then every later
# set_framebuffers failing in 13 us for the rest of the session.
#
# Q1 -- CAN THE WEDGE BE CLEARED? Rung E never tried. Provoke the wedge
#      deliberately (250 ms, rung E's exact condition), then run the
#      recovery ladder: R1 immediate retry -> R2 retry after 2 s -> R3
#      sensor.reset() + full bootstrap. If R3 heals, the bridge can catch
#      and self-heal, and the wedge drops from "restart the bridge" to
#      "one failed command". If nothing heals, the probe ends early --
#      the boundary sweep is impossible without recovery.
#
# Q2 -- WHERE IS THE SAFE BOUNDARY? Delay ladder 500/1000/2000/4000/6000
#      ms at QVGA and HD (the size extremes; bite B says the boundary
#      scales with frame size). Known fixed points: ~270 ms fails
#      (rung E), >=6 s succeeds on-chain 3/3 (bite B). This replaces the
#      bench page's 8 s guess with a measured number. The fail
#      signature is recorded per row: a ~100 ms failing attempt is a
#      FRESH failure, a ~13 us one is the standing wedge -- they must
#      not be conflated.
#
# R3's sensor.reset() runs with the HE loaded. That is allowed by the
# recipe: the HD ceiling was claimed pre-HE, bootstrap re-pins before
# every resize, and regrow-to-the-same-ceiling under a live HE is the
# exact path s18_hd_probe proved. Breadcrumbs flush before each call
# anyway.
#
# Off-chain: no Pi, no chain. Needs /flash/bm_bridge.py a1615f21... and
# the S6 fixture as /flash/main.py (both in place, verified).

import sensor, time, gc

LOG = "/flash/reinit_probe_f.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000
PUMP_SLICE_MS = 2
LADDER_MS = (500, 1000, 2000, 4000, 6000)
SIZES = ("qvga", "hd")
R2_MS = 2000

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


def pump_wait(ms):
    """Wait ms while draining, as the production bridge loop would."""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        pump()


def _call(what, fn):
    log("  . %s" % what)
    t0 = time.ticks_us()
    fn()
    return time.ticks_diff(time.ticks_us(), t0)


def reinit(res, pf, tag=""):
    """Bridge step order; returns (ok, failing_step, fail_us)."""
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


def publish(jpeg_len, seq):
    msgs = _core.capture_pub_msgs(bytes(jpeg_len), seq,
                                  _bb.CAMERA_MAX_PAYLOAD)
    _bb.send_chunk_msgs(msgs, _wire.send, pump)
    return len(msgs)


def recover(res, pf):
    """Q1's answer. Returns which rung healed it, or NONE."""
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


def rung(seq, size, delay_ms, rows):
    """capture colour -> publish -> pump-wait delay -> re-init to mono."""
    log("=== %s colour -> mono, T=%d ms after publish" % (size, delay_ms))
    log("  quiet %d ms before setup" % QUIET_MS)
    pump_wait(QUIET_MS)
    ok, step, us = reinit(size, "color", "setup ")
    if not ok:
        log("  INVALID: setup failed at %s (%d us) -- recovering" % (step, us))
        r = recover(size, "color")
        log("  setup recovery: %s" % r)
        return r != "NONE"
    try:
        nbytes = capture()
    except Exception as e:
        log("  INVALID: capture threw %r" % e)
        return True
    nmsgs = publish(nbytes, seq)
    log("  captured %d B, published %d msgs, waiting %d ms"
        % (nbytes, nmsgs, delay_ms))
    pump_wait(delay_ms)
    ok, step, us = reinit(size, "mono", "meas ")
    row = {"size": size, "t": delay_ms, "bytes": nbytes, "ok": ok,
           "step": step, "us": us, "recovery": ""}
    if ok:
        log("  RESULT PASS")
    else:
        log("  RESULT FAIL at %s (%d us -- %s)"
            % (step, us, "fresh" if us > 10000 else "WEDGED-instant"))
        row["recovery"] = recover(size, "mono")
        log("  recovery: %s" % row["recovery"])
    rows.append(row)
    return row["ok"] or row["recovery"] != "NONE"


def main():
    global _f, _bb, _core, _wire
    _f = open(LOG, "w")
    log("---- rung F start (Q1 recovery, Q2 boundary) ----")
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
    q1 = "not-reached"
    try:
        # ---- Q1: provoke the wedge (rung E's exact condition), then
        # try to clear it. ------------------------------------------------
        log("#### Q1: provoke at 250 ms, then the recovery ladder")
        pump_wait(QUIET_MS)
        ok, step, us = reinit("qvga", "color", "setup ")
        if ok:
            nbytes = capture()
            publish(nbytes, 0)
            log("  published, waiting 250 ms (rung E's failing point)")
            pump_wait(250)
            ok, step, us = reinit("qvga", "mono", "meas ")
            if ok:
                # 250 ms passed this time -- rung E's point did not repro.
                # No wedge to study; the sweep still runs.
                q1 = "no-wedge-to-provoke"
                log("  250 ms PASSED this run -- no wedge provoked")
            else:
                log("  wedged as expected (%s, %d us) -- recovery ladder:"
                    % (step, us))
                q1 = recover("qvga", "mono")
                log("#### Q1 ANSWER: %s" % q1)
                if q1 == "NONE":
                    log("#### no recovery works -- sweep impossible, ending")
                    return
        else:
            q1 = "setup-failed"
            log("  Q1 setup failed at %s -- attempting recovery" % step)
            if recover("qvga", "color") == "NONE":
                return

        # ---- Q2: the boundary sweep. ------------------------------------
        seq = 1
        for size in SIZES:
            log("#### Q2 sweep: %s" % size)
            for t in LADDER_MS:
                alive = rung(seq, size, t, rows)
                seq += 1
                if not alive:
                    log("#### unrecoverable at %s T=%d -- ending sweep"
                        % (size, t))
                    return
    finally:
        log("---- summary ----")
        log("Q1 (does anything clear the wedge?): %s" % q1)
        log("%-5s %-7s %-8s %-6s %-14s %-10s %s"
            % ("size", "T(ms)", "srcbytes", "res", "failed-call",
               "fail-us", "recovery"))
        for r in rows:
            log("%-5s %-7d %-8d %-6s %-14s %-10d %s"
                % (r["size"], r["t"], r["bytes"],
                   "PASS" if r["ok"] else "FAIL",
                   r["step"] or "-", r["us"], r["recovery"] or "-"))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r -- recovery: sudo uhubctl -l 3 -p 1 "
                "-a cycle -d 3, then mpremote reset" % e)
        log("---- rung F end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
