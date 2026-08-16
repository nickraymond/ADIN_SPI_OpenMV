# s18_fb_probe.py -- can pinning set_framebuffers(1) make a GROW survive?
#
# Established (probes 1 + 2): with the HE core loaded, shrinking the
# framebuffer is safe and VGA capture is fine, but GROWING it kills the
# board inside sensor.set_framesize(). The HE ELF sits at 0x60080000
# (SRAM9_B upper half) and the allocator grows into it.
#
# Hypothesis under test: OpenMV picks the framebuffer COUNT to fit the
# pool, so shrinking to QVGA may quietly re-allocate several buffers,
# reflowing the pool; the later grow then has to expand it and runs into
# SRAM9_B. If the count is pinned to 1 at every step the pool never
# reflows, and the grow may survive.
#
# If this passes, S18 keeps free in-session resolution switching (pin the
# count, allocate the session maximum before loading the HE). If it dies
# at the same call, resolution is fixed per bridge session and the tool
# must re-stage the chain to change it.
#
# Same breadcrumb discipline: every step flushed to flash BEFORE the call
# it names, because the failure takes USB down with it.

import time

LOG = "/flash/s18_fb.txt"


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
    n = len(img.to_jpeg(quality=q, copy=True).bytearray())
    log("%s: jpeg %d B" % (tag, n))
    return n


def main():
    import gc, sensor
    log("==== framebuffers probe start ====")

    # 1. claim the session MAXIMUM before the HE exists (probe-2 recipe)
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    log("set_framesize(VGA) [pre-HE] ->")
    sensor.set_framesize(sensor.VGA)
    log("set_framebuffers(1) ->")
    sensor.set_framebuffers(1)
    sensor.skip_frames(time=300)
    log("VGA up pre-HE %dx%d free %d"
        % (sensor.width(), sensor.height(), gc.mem_free()))
    cap("VGA-preHE")

    # 2. load the HE stack
    import bm_bridge as bb
    log("loading HE elf ->")
    rp = bb.HeWire().start()
    log("HE loaded free %d" % gc.mem_free())
    time.sleep(2)
    cap("VGA-withHE")

    # 3. SHRINK with the count pinned first
    log("set_framebuffers(1) before shrink ->")
    sensor.set_framebuffers(1)
    log("set_framesize(QVGA) ->")
    sensor.set_framesize(sensor.QVGA)
    sensor.skip_frames(time=300)
    log("QVGA up free %d" % gc.mem_free())
    cap("QVGA-withHE")

    # 4. THE TEST: pin the count, then GROW back to VGA. This exact
    #    set_framesize(VGA) killed the board in probes 1 and 2.
    log("set_framebuffers(1) before grow ->")
    sensor.set_framebuffers(1)
    log("framebuffers pinned OK free %d" % gc.mem_free())
    gc.collect()
    log(">>> set_framesize(VGA) GROW with count pinned ->")
    sensor.set_framesize(sensor.VGA)
    log(">>> GROW SURVIVED %dx%d free %d"
        % (sensor.width(), sensor.height(), gc.mem_free()))
    sensor.skip_frames(time=300)
    cap("VGA-regrown")

    # 5. one more cycle, to prove it is repeatable and not luck
    log("cycle 2: shrink ->")
    sensor.set_framebuffers(1)
    sensor.set_framesize(sensor.QVGA)
    sensor.skip_frames(time=300)
    cap("QVGA-cycle2")
    log("cycle 2: grow ->")
    sensor.set_framebuffers(1)
    sensor.set_framesize(sensor.VGA)
    sensor.skip_frames(time=300)
    cap("VGA-cycle2")

    log("==== PINNED-COUNT SWITCHING WORKS ====")
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
