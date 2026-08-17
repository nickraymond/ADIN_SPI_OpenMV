# s18_reinit_probe_e.py -- rung E: is RX-QUIESCENCE the missing condition?
#
# Rung D falsified the gate's premise: barrier answered + heap recovered +
# stream counters stable, and the board still died at set_framebuffers(1).
# The surviving hypothesis (SPEC): the killer is an HE->HP rpmsg ARRIVAL
# (MHU doorbell + MicroPython endpoint callback) landing during the
# framebuffer calls -- the barrier reply is sent directly and can overtake
# the published frames still draining through wire_pump_tx, and rung D
# stopped pumping the moment the gate said GO.
#
# Rung E is rung D plus ONE new step: after GO, keep pumping until the
# HE->HP side has been SILENT for QUIESCE_MS, and only then re-init.
#
#   survives -> the hypothesis holds; RX-quiescence becomes the gate's
#               final condition in bm_bridge.py (still bridge-only)
#   dies     -> the fix is not bridge-side; the bite goes back to Nick
#
# The probe also logs THE SMOKING GUN per rung: how many messages arrived
# BETWEEN the gate's GO and actual silence. Rung D re-inited exactly
# inside that window; a nonzero count here is the direct evidence.
#
# Off-chain: no Pi, no chain. Needs the updated /flash/bm_bridge.py
# (sha a1615f21..., deployed + verified in the rung D window) and a
# neutral /flash/main.py (the S6 fixture -- in place).

import sensor, time, gc

LOG = "/flash/reinit_probe_e.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000
LADDER = (0, 250, 1000)
PUMP_SLICE_MS = 2
QUIESCE_MS = 250           # HE->HP silent this long before any re-init
QUIESCE_CAP_MS = 10000     # never quiet -> SKIP the re-init, keep the board

_f = None
_bb = None
_core = None
_gate = None
_wire = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def pump():
    """Drain HE->HP rpmsg through the REAL BridgeCore parser.

    Yields first -- the _rx callback is what recycles the vring buffer,
    and MicroPython only runs it when the VM yields (S19 bite 2).
    """
    time.sleep_ms(PUMP_SLICE_MS)
    n = 0
    while _wire.queue:
        _core.he_msg(_wire.queue.pop(0))
        n += 1
    return n


def wait_for_gate():
    """Rung D's gate wait, unchanged: barrier + heap + stream counters."""
    t0 = time.ticks_ms()
    polls = 0
    while True:
        now = time.ticks_ms()
        v = _gate.poll(now, _core.status_seq, _core.status)
        polls += 1
        if v == _bb.GATE_GO:
            return ("GO", time.ticks_diff(now, t0), polls)
        if v == _bb.GATE_REFUSE:
            return ("REFUSE", time.ticks_diff(now, t0), polls)
        if v == _bb.GATE_QUERY:
            _wire.send(_core.query_msg())
            _gate.armed(_core.status_seq, now)
        pump()
        if _core.status is not None:
            _gate.note_status(_core.status)


def wait_for_silence():
    """THE NEW STEP. Pump until no HE->HP arrival for QUIESCE_MS.

    Returns (ok, late_msgs, waited_ms): late_msgs is the smoking gun --
    traffic that arrived AFTER the gate opened, i.e. exactly what rung D
    re-inited into the middle of.
    """
    t0 = time.ticks_ms()
    last_arrival = t0
    late = 0
    while True:
        now = time.ticks_ms()
        n = pump()
        if n:
            late += n
            last_arrival = now
        elif time.ticks_diff(now, last_arrival) >= QUIESCE_MS:
            return (True, late, time.ticks_diff(now, t0))
        if time.ticks_diff(now, t0) >= QUIESCE_CAP_MS:
            return (False, late, time.ticks_diff(now, t0))


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
    img = sensor.snapshot()
    return len(img.to_jpeg(quality=q, copy=True).bytearray())


def bootstrap(ceiling="hd"):
    log("bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("bootstrap: ceiling %s claimed, heap %d" % (ceiling, gc.mem_free()))


def publish(jpeg_len, seq):
    msgs = _core.capture_pub_msgs(bytes(jpeg_len), seq,
                                  _bb.CAMERA_MAX_PAYLOAD)
    _bb.send_chunk_msgs(msgs, _wire.send, pump)
    _gate.note_chunks(len(msgs), time.ticks_ms())
    return len(msgs)


def rung(seq, size, cap_pf, to_pf, delay_ms):
    log("=== %s %s -> %s, delay %d ms (GATE + QUIESCENCE)"
        % (size, cap_pf, to_pf, delay_ms))
    log("  quiet %d ms before setup" % QUIET_MS)
    time.sleep_ms(QUIET_MS)
    ok, step, us = reinit(size, cap_pf, "setup ")
    if not ok:
        log("  INVALID: setup failed at %s -- rung discarded" % step)
        return None
    try:
        nbytes = capture()
    except Exception as e:
        log("  INVALID: capture threw %r -- rung discarded" % e)
        return None
    nmsgs = publish(nbytes, seq)
    log("  captured %d B, published %d msgs" % (nbytes, nmsgs))
    if delay_ms:
        time.sleep_ms(delay_ms)
    verdict, gate_ms, polls = wait_for_gate()
    log("  gate: %s after %d ms (%d polls)" % (verdict, gate_ms, polls))
    if verdict != "GO":
        log("  RESULT REFUSED at the gate (board safe, command dropped)")
        return {"size": size, "delay": delay_ms, "bytes": nbytes,
                "late": -1, "quiesce_ms": 0, "ok": None, "step": "-"}
    quiet_ok, late, q_ms = wait_for_silence()
    log("  quiesce: %d msgs AFTER GO (rung D re-inited into these), "
        "silent after %d ms%s"
        % (late, q_ms, "" if quiet_ok else " -- CAP HIT, skipping re-init"))
    if not quiet_ok:
        return {"size": size, "delay": delay_ms, "bytes": nbytes,
                "late": late, "quiesce_ms": q_ms, "ok": None, "step": "-"}
    ok, step, us = reinit(size, to_pf, "meas ")
    stray = len(_wire.queue)
    if stray:
        log("  ! %d messages arrived DURING the re-init" % stray)
    log("  RESULT %s" % ("PASS" if ok else ("FAIL at %s" % step)))
    return {"size": size, "delay": delay_ms, "bytes": nbytes, "late": late,
            "quiesce_ms": q_ms, "ok": ok, "step": step}


def main():
    global _f, _bb, _core, _gate, _wire
    _f = open(LOG, "w")
    log("---- rung E start (gate + %d ms RX-quiescence) ----" % QUIESCE_MS)
    bootstrap("hd")                     # ceiling BEFORE the ELF -- required

    import bm_bridge as bb
    _bb = bb
    _core = bb.BridgeCore()
    _gate = bb.PublishGate()
    _wire = bb.HeWire()
    log("loading HE elf (HD framebuffer already claimed) ->")
    rp = _wire.start()
    log("HE loaded, heap %d" % gc.mem_free())
    time.sleep(2)
    pump()

    rows = []
    seq = 1
    try:
        for size in ("qvga", "vga", "hd"):
            for d in LADDER:
                r = rung(seq, size, "color", "mono", d)
                seq += 1
                if r is not None:
                    rows.append(r)
                time.sleep_ms(QUIET_MS)
                reinit(size, "color", "reset-to-colour ")
    finally:
        log("---- summary (gate + quiescence) ----")
        log("%-5s %-6s %-8s %-9s %-10s %s"
            % ("size", "delay", "srcbytes", "late-msgs", "quiesce", "res"))
        for r in rows:
            log("%-5s %-6d %-8d %-9d %-10d %s"
                % (r["size"], r["delay"], r["bytes"], r["late"],
                   r["quiesce_ms"],
                   "SKIPPED" if r["ok"] is None
                   else ("PASS" if r["ok"] else "FAIL at " + r["step"])))
        log("gate ledger: opens=%d refusals=%d worst_wait=%d ms"
            % (_gate.opens, _gate.refusals, _gate.wait_ms_max))
        log("ACCEPTANCE: %d rows, %d PASS, %d SKIPPED, %d FAIL"
            % (len(rows),
               len([r for r in rows if r["ok"] is True]),
               len([r for r in rows if r["ok"] is None]),
               len([r for r in rows if r["ok"] is False])))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r -- recovery: sudo uhubctl -l 3 -p 1 "
                "-a cycle -d 3, then mpremote reset" % e)
        log("---- rung E end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
