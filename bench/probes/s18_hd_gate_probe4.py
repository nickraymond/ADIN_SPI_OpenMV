# s18_hd_gate_probe4.py -- probe G4: CONTROL ARM -- the G3 soak with NO
# HE core loaded.
#
# G3 (HE loaded, zero traffic) failed at transition #22 -- a QVGA mono
# pixformat flip's set_framebuffers, 289 us, 'Sensor control failed.' --
# and the recovery bootstrap failed too. Together with G2 (#10, hard
# death) and G (#4ish, with publishes), the failure looks like a
# PROGRESSIVE leak/corruption in the framebuffer resize path (every
# resize = uma_free + uma_malign, framebuffer.c:158) that trips a
# threshold, politely (alloc fails) or fatally (corrupted free).
#
# B2 rung A's "no HE = safe" was 12/12 -- possibly just a soak that
# stopped below the threshold. This control runs the IDENTICAL soak
# with no HE. If it fails around the same transition count, the HE is
# exonerated entirely and this is a plain OpenMV firmware bug
# (upstream-reportable with this probe as the repro).
#
# Needs: neutral /flash/main.py, fresh boot.

import sensor, time, gc

LOG = "/flash/hd_gate_probe4.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

N_CYCLES = 8
GAP_MS = 500

CYCLE = (
    ("qvga", "color"),
    ("qvga", "mono"),
    ("vga", "mono"),
    ("hd", "mono"),
    ("hd", "color"),
)

_f = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def bootstrap(ceiling="hd"):
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)


def transition(tag, res, pf):
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
    global _f
    _f = open(LOG, "w")
    log("---- probe G4 start (CONTROL: same soak, NO HE) ----")
    log("bootstrap:")
    bootstrap("hd")
    log("  ceiling claimed, heap %d" % gc.mem_free())
    log("NO HE loaded (control arm)")

    total = 0
    fails = []
    healed = 0
    try:
        for cyc in range(N_CYCLES):
            for res, pf in CYCLE:
                tag = "c%d %s-%s" % (cyc, res, pf)
                time.sleep_ms(GAP_MS)
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
        log("---- probe G4 end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
