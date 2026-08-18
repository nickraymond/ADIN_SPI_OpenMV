# s18_hd_gate_probe2.py -- probe G2: is the killer "framebuffer GROW
# while pixformat=GRAYSCALE"?
#
# Probe G row 1 reproduced the matrix deaths off-chain WITHOUT the
# barrier: QVGA color -> [pixformat GRAYSCALE, fb(1), framesize(HD)]
# succeeded call-by-call, then the FIRST capture (settle) threw
# MemoryError after 120 ms; recovery R1 got 'Sensor control failed.',
# recovery R2's set_framebuffers took the board off the bus. So the
# PublishGate's open sequence is exonerated (row 1 had no barrier) and
# the suspect is the transition shape itself.
#
# THE PATTERN ACROSS ALL EVIDENCE: every HD-mono success on record
# (C1 demo, probe F x5) reached HD mono via HD COLOR -- a pixformat
# flip at an already-held size. Every death (matrix run 5, the
# sensor-mode discriminator, probe G row 1) was a DIRECT GROW into
# GRAYSCALE. No still on-chain ever grew while mono below HD either
# (QVGA/VGA mono rows were all flips at held sizes).
#
# G2 isolates it, no publish and no waits unless stated (if C fails
# with no publish at all, the hazard is a pure driver/allocator bug and
# the publish/quiet framing collapses):
#
#   A: QVGA color -> HD color        grow at RGB565      expect PASS
#   B: HD color -> HD mono           flip at held size   expect PASS
#   C: QVGA color -> QVGA mono (settle) -> HD mono
#                                    GROW AT GRAYSCALE   the test
#   D: QVGA mono -> VGA mono         grow at mono, small does the rule
#                                                        generalize?
#   E: publish 5842 B + 20 s + QVGA color -> HD color    the matrix's
#                                    hd-color still shape under gate
#                                    conditions          expect PASS
#   F: publish 5842 B + 20 s + QVGA color -> HD color -> HD mono
#                                    THE FIX CANDIDATE: grow in color,
#                                    flip after -- what a reordered
#                                    sensor_steps would do
#
# Rows ordered safe->risky; a death mid-ladder still leaves everything
# before it in /flash/hd_gate_probe2.txt (log flushes pre-call).
# Needs: neutral /flash/main.py, /flash/bm_bridge.py, one HE load per
# boot.

import sensor, time, gc

LOG = "/flash/hd_gate_probe2.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

QUIET_MS = 4000
WAIT_MS = 20000
PUMP_SLICE_MS = 2
QVGA_PUB = 5842

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
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        pump()


def step(name, fn):
    """One sensor call, breadcrumbed and timed; re-raises."""
    log("  . %s (heap %d)" % (name, gc.mem_free()))
    t0 = time.ticks_us()
    fn()
    us = time.ticks_diff(time.ticks_us(), t0)
    log("    ok %d us" % us)
    return us


def snap(tag, q=50):
    n = len(sensor.snapshot().to_jpeg(quality=q, copy=True).bytearray())
    log("  capture %s: %d B" % (tag, n))
    return n


def bootstrap(ceiling="hd"):
    log("  bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("  bootstrap: ceiling %s claimed, heap %d" % (ceiling, gc.mem_free()))


def publish(nbytes, seq):
    msgs = _core.capture_pub_msgs(bytes(nbytes), seq,
                                  _bb.CAMERA_MAX_PAYLOAD)
    _bb.send_chunk_msgs(msgs, _wire.send, pump)
    log("  published %d B as %d msgs" % (nbytes, len(msgs)))


def transition(tag, res, pf):
    """pixformat -> fb(1) -> framesize -> settle, sensor_steps order.
    Returns True on full success INCLUDING the settle capture."""
    try:
        step("%s pixformat %s" % (tag, pf),
             lambda: sensor.set_pixformat(PF[pf]))
        step("%s framebuffers(1)" % tag,
             lambda: sensor.set_framebuffers(1))
        step("%s framesize %s" % (tag, res),
             lambda: sensor.set_framesize(RES[res]))
        step("%s settle" % tag, lambda: sensor.skip_frames(time=300))
        return True
    except Exception as e:
        log("  ! %s FAILED: %r" % (tag, e))
        return False


def row(name, fn):
    log("=== %s" % name)
    pump_wait(QUIET_MS)
    try:
        ok = fn()
        log("  RESULT %s" % ("PASS" if ok else "FAIL"))
        return ok
    except Exception as e:
        log("  RESULT FAIL (row raised %r)" % e)
        return False


def main():
    global _f, _bb, _core, _wire
    _f = open(LOG, "w")
    log("---- probe G2 start (grow-at-GRAYSCALE isolation) ----")
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

    results = []
    try:
        # A: grow at RGB565 (probe-F analog).
        def a():
            if not transition("A1", "qvga", "color"):
                return False
            snap("A qvga")
            if not transition("A2", "hd", "color"):
                return False
            return snap("A hd-color") > 0
        results.append(("A grow-at-color", row("A: qvga color -> hd color "
                                               "(grow at RGB565)", a)))

        # B: flip at held size (probe-F meas analog).
        def b():
            if not transition("B1", "hd", "mono"):
                return False
            return snap("B hd-mono") > 0
        results.append(("B flip-at-hd", row("B: hd color -> hd mono "
                                            "(flip at held size)", b)))

        # C: THE TEST -- direct grow at GRAYSCALE, no publish anywhere
        # near it. Settle between the flip and the grow separates the
        # two reallocations.
        def c():
            if not transition("C1", "qvga", "color"):
                return False
            snap("C qvga-color")
            if not transition("C2", "qvga", "mono"):
                return False
            snap("C qvga-mono")
            if not transition("C3", "hd", "mono"):
                return False
            return snap("C hd-mono") > 0
        results.append(("C grow-at-mono", row("C: qvga mono -> hd mono "
                                              "(GROW AT GRAYSCALE)", c)))

        # D: does the rule generalize below HD?
        def d():
            if not transition("D1", "qvga", "mono"):
                return False
            snap("D qvga-mono")
            if not transition("D2", "vga", "mono"):
                return False
            return snap("D vga-mono") > 0
        results.append(("D vga-grow-at-mono", row("D: qvga mono -> vga "
                                                  "mono (small grow at "
                                                  "GRAYSCALE)", d)))

        # E: matrix hd-color still row shape, full gate conditions.
        def e():
            if not transition("E1", "qvga", "color"):
                return False
            snap("E qvga")
            publish(QVGA_PUB, 10)
            log("  waiting %d ms" % WAIT_MS)
            pump_wait(WAIT_MS)
            if not transition("E2", "hd", "color"):
                return False
            return snap("E hd-color") > 0
        results.append(("E hdcolor-under-gate", row("E: publish + 20 s + "
                                                    "qvga -> hd color", e)))

        # F: the fix candidate -- grow in color, flip after.
        def f():
            if not transition("F1", "qvga", "color"):
                return False
            snap("F qvga")
            publish(QVGA_PUB, 11)
            log("  waiting %d ms" % WAIT_MS)
            pump_wait(WAIT_MS)
            if not transition("F2", "hd", "color"):
                return False
            if not transition("F3", "hd", "mono"):
                return False
            return snap("F hd-mono") > 0
        results.append(("F fix-candidate", row("F: publish + 20 s + grow "
                                               "color THEN flip mono", f)))
    finally:
        log("---- summary ----")
        for name, ok in results:
            log("  %-22s %s" % (name, "PASS" if ok else "FAIL"))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r" % e)
        log("---- probe G2 end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
