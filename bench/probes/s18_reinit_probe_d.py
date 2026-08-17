# s18_reinit_probe_d.py -- rung D: the S18 bite B2 fix, exercised.
#
# ACCEPTANCE TEST for the PublishGate in firmware/bm_bridge/bm_bridge.py.
# Rung C (s18_reinit_probe_c.py) ran capture -> publish -> re-init with no
# gate and took the board off the USB bus on its FIRST measured re-init.
# This runs the identical ladder with the gate in place and must survive
# all of it.
#
# It is deliberately NOT a re-implementation of the fix. It imports
# bm_bridge and drives the real BridgeCore, the real PublishGate and the
# real send_chunk_msgs, in the same order main()'s loop does -- so what
# passes here is the shipped code path, not a paraphrase of it.
#
#   publish a frame's chunks   -> gate.note_chunks()
#   gate.poll() says QUERY     -> send core.query_msg(), gate.armed()
#   pump rpmsg through core.he_msg() until WREP_STATUS bumps status_seq
#   gate.poll() says GO        -> only now touch the sensor
#
# DO NOT re-run rung C to "compare". Its ungated result is already on the
# record and re-running it costs a Pi reboot.
#
# Off-chain: no Pi, no chain, no VCP relay. Needs a neutral /flash/main.py
# (the S6 fixture) -- see the ae3-board-access skill.

import sensor, time, gc

LOG = "/flash/reinit_probe_d.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 8000
LADDER = (0, 250, 1000)
PUMP_SLICE_MS = 2          # how long each pump pass yields for

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

    Yields first: the _rx callback is what recycles the vring buffer, and
    MicroPython only runs it when the VM yields (S19 bite 2 -- popping our
    own list recycles nothing). Discards the encoded wire chunks; on this
    probe there is no VCP to write them to. What matters is the side
    effect -- a WREP_STATUS bumps core.status_seq, which is the barrier
    the gate waits on.
    """
    time.sleep_ms(PUMP_SLICE_MS)
    n = 0
    while _wire.queue:
        _core.he_msg(_wire.queue.pop(0))
        n += 1
    return n


def wait_for_gate(tag):
    """Mirror main()'s held-command handling. Returns (verdict, ms, polls)."""
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
    return len(img.to_jpeg(quality=q, copy=True).bytearray()), img


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
    """Emit the frame's chunks exactly as the bridge does."""
    msgs = _core.capture_pub_msgs(bytes(jpeg_len), seq,
                                  _bb.CAMERA_MAX_PAYLOAD)
    t0 = time.ticks_ms()
    _bb.send_chunk_msgs(msgs, _wire.send, pump)
    _gate.note_chunks(len(msgs), time.ticks_ms())
    return len(msgs), time.ticks_diff(time.ticks_ms(), t0)


def rung(seq, size, cap_pf, to_pf, delay_ms):
    log("=== %s %s -> %s, delay %d ms after PUBLISH (GATED)"
        % (size, cap_pf, to_pf, delay_ms))
    log("  quiet %d ms before setup" % QUIET_MS)
    time.sleep_ms(QUIET_MS)
    ok, step, us = reinit(size, cap_pf, "setup ")
    if not ok:
        log("  INVALID: setup failed at %s -- rung discarded" % step)
        return None
    try:
        nbytes, _img = capture()
    except Exception as e:
        log("  INVALID: capture threw %r -- rung discarded" % e)
        return None
    nmsgs, pub_ms = publish(nbytes, seq)
    log("  captured %d B, published %d msgs in %d ms" % (nbytes, nmsgs, pub_ms))
    if delay_ms:
        time.sleep_ms(delay_ms)
    verdict, gate_ms, polls = wait_for_gate("meas")
    log("  gate: %s after %d ms (%d polls, status_seq=%d, heap_high=%d)"
        % (verdict, gate_ms, polls, _core.status_seq, _gate.heap_high))
    if verdict != "GO":
        # A refusal is a PASS for safety and a FAIL for usefulness: the
        # board lives, but the bench lost an image. Both go in the table.
        log("  RESULT REFUSED (board safe, command dropped)")
        return {"size": size, "delay": delay_ms, "bytes": nbytes,
                "msgs": nmsgs, "gate": verdict, "gate_ms": gate_ms,
                "ok": None, "step": "-"}
    ok, step, us = reinit(size, to_pf, "meas ")
    log("  RESULT %s" % ("PASS" if ok else ("FAIL at %s" % step)))
    return {"size": size, "delay": delay_ms, "bytes": nbytes, "msgs": nmsgs,
            "gate": verdict, "gate_ms": gate_ms, "ok": ok, "step": step}


def main():
    global _f, _bb, _core, _gate, _wire
    _f = open(LOG, "w")
    log("---- rung D start (HE PUBLISHING, PublishGate ACTIVE) ----")
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
        log("---- summary (gated) ----")
        log("%-5s %-6s %-8s %-5s %-7s %-8s %s"
            % ("size", "delay", "srcbytes", "msgs", "gate", "gate_ms", "res"))
        for r in rows:
            log("%-5s %-6d %-8d %-5d %-7s %-8d %s"
                % (r["size"], r["delay"], r["bytes"], r["msgs"], r["gate"],
                   r["gate_ms"],
                   "REFUSED" if r["ok"] is None
                   else ("PASS" if r["ok"] else "FAIL at " + r["step"])))
        log("gate ledger: opens=%d refusals=%d worst_wait=%d ms"
            % (_gate.opens, _gate.refusals, _gate.wait_ms_max))
        log("wire: qdrops=%d, status_seq=%d" % (_wire.q_drops,
                                                _core.status_seq))
        log("ACCEPTANCE: %d rows, %d PASS, %d REFUSED, %d FAIL"
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
        log("---- rung D end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
