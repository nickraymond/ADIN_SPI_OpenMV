# s18_order_probe.py -- does ORDER fix the S18 VGA-under-HE hard fault?
#
# Probe 1 established: with the HE ELF loaded, sensor.set_framesize(VGA)
# never returns -- the board leaves the USB bus. Heap was 4.07 MB free
# against a 512 KB VGA framebuffer, and there was no VCP traffic, so it
# is neither exhaustion nor bridge activity. The HE ELF loads at
# 0x60080000 (SRAM9_B upper half, per bm_he MANIFEST), which makes an
# overlap between OpenMV's framebuffer region and SRAM9_B the leading
# hypothesis: QVGA (320x200x2 = 128 KB) fits under the collision, VGA
# (640x400x2 = 512 KB) does not.
#
# If that is right, claiming the LARGE framebuffer FIRST and only then
# loading the HE should behave differently -- either the load fails
# cleanly (memory already taken) or everything works (allocator placed
# the buffer somewhere compatible). Both outcomes are informative, and
# the second is a workaround that unblocks S18: bring the sensor up at
# the session's maximum resolution before starting the stack, then only
# ever shrink.
#
# Same breadcrumb discipline as probe 1: flush before each risky call.

import time

LOG = "/flash/s18_order.txt"


def log(msg):
    line = "%8d %s" % (time.ticks_ms(), msg)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass
    print(line)


def cap(tag, q=50):
    import sensor
    log("%s: snapshot ->" % tag)
    img = sensor.snapshot()
    log("%s: to_jpeg ->" % tag)
    n = len(img.to_jpeg(quality=q, copy=True).bytearray())
    log("%s: jpeg %d B" % (tag, n))
    return n


def main():
    import gc, sensor
    log("==== order probe start ====")
    log("free heap %d" % gc.mem_free())

    # ---- 1. VGA FIRST, before the HE stack exists --------------------
    log("sensor.reset ->")
    sensor.reset()
    log("set_pixformat(RGB565) ->")
    sensor.set_pixformat(sensor.RGB565)
    log("set_framesize(VGA) [no HE loaded] ->")
    sensor.set_framesize(sensor.VGA)
    log("VGA set OK %dx%d free %d"
        % (sensor.width(), sensor.height(), gc.mem_free()))
    log("set_framebuffers(1) ->")
    sensor.set_framebuffers(1)
    log("framebuffers OK free %d" % gc.mem_free())
    sensor.skip_frames(time=300)
    cap("VGA-preHE")

    # ---- 2. now load the HE stack, framebuffer already claimed -------
    import bm_bridge as bb
    log("loading HE elf (VGA framebuffer already allocated) ->")
    he = bb.HeWire()
    rp = he.start()
    log("HE loaded OK; free heap %d" % gc.mem_free())
    time.sleep(2)

    # ---- 3. VGA capture with HE up (probe 1 died before reaching this)
    cap("VGA-withHE")

    # ---- 4. shrink to QVGA, then grow back ---------------------------
    log("set_framesize(QVGA) with HE up ->")
    sensor.set_framesize(sensor.QVGA)
    log("QVGA set OK free %d" % gc.mem_free())
    sensor.skip_frames(time=300)
    cap("QVGA-withHE")

    log("set_framesize(VGA) again with HE up ->")   # the probe-1 killer
    sensor.set_framesize(sensor.VGA)
    log("VGA re-set OK free %d" % gc.mem_free())
    sensor.skip_frames(time=300)
    cap("VGA-regrown")

    log("==== ORDER PROBE SURVIVED -- workaround is real ====")
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
