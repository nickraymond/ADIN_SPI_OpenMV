# s18_reinit_probe.py -- is the sensor re-init failure the HE's fault?
#
# Runs ON the AE3 with NO Pi, NO chain and (rung A) NO HE core loaded.
# Reproduces the bridge's exact ladder: claim the HD ceiling, capture,
# then re-init the sensor after a controlled delay.
#
# The question this answers, and nothing else: does
# `set_pixformat(GRAYSCALE)` shortly after a snapshot fail on its own, or
# does it need the HE core running and publishing? That decides whether the
# fix belongs in the bridge's sensor handling or in its HE flow control.
#
# Breadcrumbs are flushed to flash BEFORE each risky call, because the S18
# record has calls that take the board off the USB bus with nothing to
# catch -- a crash you cannot read is a crash you debug twice.

import sensor, time, gc

LOG = "/flash/reinit_probe.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}


def log(msg):
    print(msg)
    try:
        with open(LOG, "a") as f:
            f.write("%d %s\n" % (time.ticks_ms(), msg))
    except Exception:
        pass


def bootstrap(ceiling="hd"):
    """The bridge's bootstrap(), verbatim in shape (SPEC 'THE RECIPE')."""
    log("bootstrap: reset")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)   # small: legalises the pin
    sensor.set_framebuffers(1)          # pin BEFORE the big alloc
    sensor.set_framesize(RES[ceiling])
    sensor.skip_frames(time=300)
    log("bootstrap: ceiling %s claimed" % ceiling)


def reinit(res, pf):
    """The bridge's _ensure_sensor() step order."""
    log("  reinit-> %s %s (pixformat)" % (res, pf))
    sensor.set_pixformat(PF[pf])
    log("  reinit-> framebuffers")
    sensor.set_framebuffers(1)
    log("  reinit-> framesize")
    sensor.set_framesize(RES[res])
    sensor.skip_frames(time=300)
    log("  reinit-> OK")


def capture(q=50):
    img = sensor.snapshot()
    b = img.to_jpeg(quality=q, copy=True).bytearray()
    return len(b)


def rung(name, cap_res, cap_pf, to_res, to_pf, delay_ms):
    """capture at (cap_res,cap_pf) -> wait delay_ms -> re-init to (to_*)."""
    log("=== %s: capture %s %s, wait %d ms, re-init -> %s %s"
        % (name, cap_res, cap_pf, delay_ms, to_res, to_pf))
    try:
        reinit(cap_res, cap_pf)          # get to the capture geometry
        n = capture()
        log("  captured %d B" % n)
        if delay_ms:
            time.sleep_ms(delay_ms)
        reinit(to_res, to_pf)
        n2 = capture()
        log("  RESULT %s PASS (post-reinit capture %d B)" % (name, n2))
        return True
    except Exception as e:
        log("  RESULT %s FAIL: %r" % (name, e))
        return False


def main():
    log("---- probe start (no HE core loaded) ----")
    bootstrap("hd")
    results = []
    # One variable at a time: same transition, decreasing quiet time.
    for d in (0, 100, 250, 500, 1000, 2000):
        ok = rung("qvga-color->mono d=%d" % d, "qvga", "color",
                  "qvga", "mono", d)
        results.append(("qvga->mono", d, ok))
        if not ok:
            # Wedged? Try the next rung anyway -- whether a later rung can
            # still pass is itself the answer about recovery.
            pass
    # Bigger source frame = longer sensor DMA, if that is the mechanism.
    for d in (0, 500, 2000):
        ok = rung("vga-color->mono d=%d" % d, "vga", "color", "vga", "mono", d)
        results.append(("vga->mono", d, ok))

    log("---- summary ----")
    for what, d, ok in results:
        log("%-12s delay=%-5d %s" % (what, d, "PASS" if ok else "FAIL"))
    log("---- probe end ----")


main()
