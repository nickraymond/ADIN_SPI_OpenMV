# s18_hd_gate_probe3.py -- probe G3: the transition soak. How often does
# a sensor reconfig die with the HE loaded and NOTHING else happening?
#
# Probes G and G2 dismantled every targeted hypothesis:
#   G  row 1: publish + 20 s + grow-to-HD-mono -> MemoryError, then the
#             recovery's set_framebuffers took the board off the bus.
#   G2 A-D:   the same grow (and every other suspect shape) PASSED with
#             no publish -- then row E died at a routine QVGA shrink's
#             set_framebuffers, before its publish ever ran.
# So: no publish required, no HD required, no grow required, no barrier
# required. What is left is a STOCHASTIC per-transition hazard that
# exists only with the HE core loaded (B2 rung A: 12/12 without it;
# rung B: 9/9 idle-HE at QVGA-scale counts). This probe measures its
# rate and whether it loads on any particular transition shape.
#
# Method: cycle all six bench modes with constant cadence, zero rpmsg
# traffic, breadcrumb every call. N_CYCLES x 6 transitions. Every
# transition is (pixformat, framebuffers, framesize, settle, capture),
# the production order. On a polite failure: record, one R3 recovery
# (reset + bootstrap), continue if healed. A hard death ends the run --
# the flash log names the call and the tally to that point.
#
# The cycle covers: shrinks, grows, flips, color and mono at all three
# sizes -- each visited N_CYCLES times:
#   hd color -> qvga color   (big shrink)
#   qvga color -> qvga mono  (flip at small)
#   qvga mono -> vga mono    (grow at mono)
#   vga mono -> hd mono      (grow at mono, big)
#   hd mono -> hd color      (flip at big)
#   hd color -> ...          (cycle closes at the ceiling)
#
# Needs: neutral /flash/main.py, /flash/bm_bridge.py, fresh boot.

import sensor, time, gc

LOG = "/flash/hd_gate_probe3.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

N_CYCLES = 8
GAP_MS = 500              # constant cadence between transitions

CYCLE = (
    ("qvga", "color"),    # from hd color: big shrink
    ("qvga", "mono"),     # flip at small
    ("vga", "mono"),      # grow at mono
    ("hd", "mono"),       # grow at mono, big
    ("hd", "color"),      # flip at big; closes at the ceiling
)

_f = None
_wire = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def pump_wait(ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        time.sleep_ms(2)
        while _wire.queue:
            _wire.queue.pop(0)


def bootstrap(ceiling="hd"):
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)


def transition(tag, res, pf):
    """(ok, failing_step, us). Breadcrumb flushed before each call."""
    steps = (
        ("pixformat", lambda: sensor.set_pixformat(PF[pf])),
        ("framebuffers", lambda: sensor.set_framebuffers(1)),
        ("framesize", lambda: sensor.set_framesize(RES[res])),
        ("settle", lambda: sensor.skip_frames(time=300)),
        ("capture", lambda: sensor.snapshot().to_jpeg(quality=50,
                                                      copy=True)),
    )
    for name, fn in steps:
        log("  . %s %s" % (tag, name))
        t0 = time.ticks_us()
        try:
            fn()
        except Exception as e:
            us = time.ticks_diff(time.ticks_us(), t0)
            log("  ! %s %s THREW after %d us: %r" % (tag, name, us, e))
            return (False, name, us)
    return (True, None, 0)


def main():
    global _f, _wire
    _f = open(LOG, "w")
    log("---- probe G3 start (transition soak, HE loaded, no traffic) ----")
    log("bootstrap (pre-HE):")
    bootstrap("hd")
    log("  ceiling claimed, heap %d" % gc.mem_free())

    import bm_bridge as bb
    _wire = bb.HeWire()
    log("loading HE elf ->")
    rp = _wire.start()
    log("HE loaded, heap %d" % gc.mem_free())
    time.sleep(2)

    total = 0
    fails = []
    healed = 0
    try:
        for cyc in range(N_CYCLES):
            for res, pf in CYCLE:
                tag = "c%d %s-%s" % (cyc, res, pf)
                pump_wait(GAP_MS)
                total += 1
                ok, stp, us = transition(tag, res, pf)
                if not ok:
                    fails.append((tag, stp, us))
                    log("  recovery: reset + bootstrap")
                    try:
                        bootstrap("hd")
                        healed += 1
                        log("  recovered, continuing at hd color")
                    except Exception as e:
                        log("  ! recovery bootstrap THREW: %r -- ending"
                            % e)
                        return
            log("cycle %d done, %d/%d clean, heap %d"
                % (cyc, total - len(fails), total, gc.mem_free()))
    finally:
        log("---- summary: %d transitions, %d polite failures, %d healed "
            "----" % (total, len(fails), healed))
        for tag, stp, us in fails:
            log("  FAIL %s at %s (%d us)" % (tag, stp, us))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r" % e)
        log("---- probe G3 end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
