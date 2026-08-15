# s18_hd_probe.py -- does the pinned-count recipe hold at HD?
#
# Probe 3 proved QVGA<->VGA switching works with the HE core loaded, if
# the session maximum is allocated BEFORE the HE and set_framebuffers(1)
# is pinned before every set_framesize(). The whole recipe rests on the
# maximum fitting below SRAM9_B (HE ELF at 0x60080000), so HD is the
# real test of it:
#
#   QVGA RGB565   320x200x2 =   128,000 B   (proven)
#   VGA  RGB565   640x400x2 =   512,000 B   (proven)
#   HD   RGB565  1280x800x2 = 2,048,000 B   <- 4x VGA, untested
#   HD   GRAY    1280x800x1 = 1,024,000 B
#
# Also exercises the PIXEL FORMAT change (RGB565 <-> GRAYSCALE), which
# reallocates the buffer too and which S18 needs for HD-greyscale video
# -- that path has never been run with the HE core up either.
#
# Same breadcrumb discipline: flush before each risky call.

import time

LOG = "/flash/s18_hd.txt"


def log(msg):
    line = "%8d %s" % (time.ticks_ms(), msg)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass
    print(line)


def setmode(sensor, tag, fs, pf=None):
    """Pinned-count mode change, in the only order the driver accepts:
    pixformat -> framebuffers -> framesize.

    set_framebuffers() raises "Pixel format is not supported or is not
    set" on a freshly reset sensor (found here), so the format has to go
    first. What matters for the SRAM9_B collision is that the count is
    pinned immediately before set_framesize, which this preserves.
    """
    import gc
    if pf is not None:
        log("%s: set_pixformat ->" % tag)
        sensor.set_pixformat(pf)
    log("%s: set_framebuffers(1) ->" % tag)
    sensor.set_framebuffers(1)
    log("%s: set_framesize ->" % tag)
    sensor.set_framesize(fs)
    log("%s: OK %dx%d free %d" % (tag, sensor.width(), sensor.height(),
                                  gc.mem_free()))
    sensor.skip_frames(time=300)


def cap(tag, q=50):
    import sensor
    log("%s: snapshot ->" % tag)
    img = sensor.snapshot()
    n = len(img.to_jpeg(quality=q, copy=True).bytearray())
    log("%s: jpeg %d B" % (tag, n))
    return n


def main():
    import gc, sensor
    log("==== HD probe start ====")
    log("free %d" % gc.mem_free())

    # 1. claim HD (the session maximum) BEFORE the HE exists.
    #    set_framebuffers() refuses to run until BOTH pixformat and
    #    framesize are set (both found the hard way, two runs ago), but
    #    setting HD while the count is still unpinned is exactly the
    #    over-allocation we are trying to avoid -- S0 recorded that VGA+
    #    "needs set_framebuffers(1)". So: come up at QVGA (128 KB, always
    #    safe), pin the count there, THEN grow to HD.
    sensor.reset()
    log("reset OK free %d" % gc.mem_free())
    log("bootstrap: set_pixformat(RGB565) ->")
    sensor.set_pixformat(sensor.RGB565)
    log("bootstrap: set_framesize(QVGA) ->")
    sensor.set_framesize(sensor.QVGA)
    log("bootstrap: set_framebuffers(1) ->")
    sensor.set_framebuffers(1)
    log("bootstrap OK, count pinned, free %d" % gc.mem_free())
    setmode(sensor, "HD-preHE", sensor.HD)
    cap("HD-preHE")

    # 2. load the HE stack on top of the HD-sized framebuffer
    import bm_bridge as bb
    log("loading HE elf (HD framebuffer allocated) ->")
    rp = bb.HeWire().start()
    log("HE loaded free %d" % gc.mem_free())
    time.sleep(2)
    cap("HD-withHE")

    # 3. shrink down the whole ladder
    setmode(sensor, "VGA-down", sensor.VGA)
    cap("VGA-down")
    setmode(sensor, "QVGA-down", sensor.QVGA)
    cap("QVGA-down")

    # 4. grow back up the ladder -- the calls that killed probes 1 and 2
    setmode(sensor, "VGA-up", sensor.VGA)
    cap("VGA-up")
    log(">>> the big one: grow QVGA-path back to HD <<<")
    setmode(sensor, "HD-up", sensor.HD)
    cap("HD-up")

    # 5. pixel format change at HD (S18 wants HD greyscale for video)
    setmode(sensor, "HDmono", sensor.HD, sensor.GRAYSCALE)
    cap("HDmono")
    setmode(sensor, "HDcolor-back", sensor.HD, sensor.RGB565)
    cap("HDcolor-back")

    log("==== HD PROBE SURVIVED -- full ladder switchable ====")
    try:
        rp.stop()
        log("HE stopped")
    except Exception as e:
        log("HE stop: %r" % e)


try:
    main()
except Exception as e:
    import sys
    log("PYTHON EXCEPTION: %r" % e)
    try:
        with open(LOG, "a") as f:
            sys.print_exception(e, f)
            f.flush()
    except Exception:
        pass
