# s18_vga_probe.py -- isolate the S18 VGA-under-HE-stack hard fault.
#
# Throwaway diagnostic (S18 bite A nibble 3). Runs ON the AE3 HP core via
# `mpremote run`; nothing is flashed. Reproduces the failing condition in
# the SIMPLEST form that still contains it: HE stack LOADED over rpmsg,
# but NO VCP traffic and no Pi chain at all.
#
# Why this shape: on a clean REPL, VGA capture works, and so does the
# QVGA->VGA->QVGA runtime switch. Only VGA with the HE stack live has
# ever failed. This separates "HE merely loaded" from "bridge actively
# pumping the VCP" -- if it dies here, loading is sufficient.
#
# Every step is written to /flash AND fsync'd BEFORE the call it names is
# made, because the failure takes the whole USB stack down: the last line
# in the file is the call that killed the board. The first crash's
# evidence was lost to a trace wipe; this one cannot be.

import time

LOG = "/flash/s18_probe.txt"


def log(msg):
    line = "%8d %s" % (time.ticks_ms(), msg)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
            f.flush()          # breadcrumb must survive a hard fault
    except Exception:
        pass
    print(line)


def cap(tag, q=50):
    import sensor
    log("%s: snapshot() ->" % tag)
    img = sensor.snapshot()
    log("%s: snapshot OK, to_jpeg() ->" % tag)
    j = img.to_jpeg(quality=q, copy=True)
    n = len(j.bytearray())
    log("%s: jpeg %d B" % (tag, n))
    return n


def main():
    import gc
    log("==== probe start ====")
    log("free heap %d" % gc.mem_free())

    import bm_bridge as bb
    log("loading HE elf %s" % bb.ELF_PATH)
    he = bb.HeWire()
    rp = he.start()                     # raises if a stale HE is running
    log("HE loaded, bm-wire announced; free heap %d" % gc.mem_free())
    time.sleep(2)
    log("HE settled; free heap %d" % gc.mem_free())

    import sensor
    log("sensor.reset() ->")
    sensor.reset()
    log("set_pixformat(RGB565) ->")
    sensor.set_pixformat(sensor.RGB565)
    log("set_framesize(QVGA) ->")
    sensor.set_framesize(sensor.QVGA)
    log("skip_frames ->")
    sensor.skip_frames(time=300)
    log("QVGA up %dx%d free %d" % (sensor.width(), sensor.height(),
                                   gc.mem_free()))
    cap("QVGA")                          # control: known to work under HE

    # ---- the failing transition, one logged step at a time ----------
    log("--- switching to VGA (free %d) ---" % gc.mem_free())
    gc.collect()
    log("after gc.collect free %d" % gc.mem_free())
    log("set_framesize(VGA) ->")
    sensor.set_framesize(sensor.VGA)
    log("set_framesize(VGA) OK, %dx%d free %d"
        % (sensor.width(), sensor.height(), gc.mem_free()))
    log("set_framebuffers(1) ->")
    sensor.set_framebuffers(1)
    log("set_framebuffers(1) OK, free %d" % gc.mem_free())
    log("skip_frames ->")
    sensor.skip_frames(time=300)
    log("skip OK, free %d" % gc.mem_free())
    cap("VGA")

    log("==== PROBE SURVIVED ====")
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
