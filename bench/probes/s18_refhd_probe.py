# s18_refhd_probe.py -- probe G6: do the ref-scene HD images fit the MP
# heap on the sticky-fb firmware, alongside the HE and the claimed fb?
#
# The HD-ref guard (D2: keep until measured) refuses HD ref commands
# because run 5 died at the HD transition and the 1-3 MB ref-image load
# was the stated suspect. Nibble 1 showed the death was the TRANSITION,
# now fixed (A2 sticky-fb, G3 40/40). What remains unmeasured is the
# load itself: HD color ref is a ~3 MB decoded image against ~3.9 MB
# free MP heap. This probe loads both HD refs (mono, then color, then
# both-in-sequence as _load_ref would: old freed first), encodes each
# at q50 the way poll() does, and reports heap at every step. PASS =
# both load and encode with the HE resident and the HD fb claimed.
#
# Needs: neutral /flash/main.py, /flash/bm_bridge.py, /flash/ref_scene
# staged, fresh boot.

import sensor, time, gc

LOG = "/flash/refhd_probe.txt"
REF_DIR = "/flash/ref_scene"

_f = None


def log(msg):
    print(msg)
    try:
        _f.write("%d %s\n" % (time.ticks_ms(), msg))
        _f.flush()
    except Exception:
        pass


def load_one(names):
    import image
    for n in names:
        try:
            t0 = time.ticks_ms()
            img = image.Image(REF_DIR + "/" + n)
            log("  loaded %s in %d ms (heap %d)"
                % (n, time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
            return img, n
        except Exception as e:
            log("  %s not usable: %r" % (n, e))
    return None, None


def encode(img, tag):
    t0 = time.ticks_us()
    jpg = img.to_jpeg(quality=50, copy=True)
    us = time.ticks_diff(time.ticks_us(), t0)
    n = len(jpg.bytearray())
    log("  %s enc %d us, %d B (heap %d)" % (tag, us, n, gc.mem_free()))
    return n


def main():
    global _f
    _f = open(LOG, "w")
    log("---- probe G6 start (ref-HD heap fit, sticky-fb build) ----")
    import os
    log("ref_scene: %r" % (os.listdir(REF_DIR),))

    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(sensor.HD)
    sensor.skip_frames(time=300)
    log("hd fb claimed, heap %d" % gc.mem_free())

    import bm_bridge as bb
    wire = bb.HeWire()
    rp = wire.start()
    log("HE loaded, heap %d" % gc.mem_free())
    time.sleep(2)

    ok_mono = ok_color = False
    try:
        # HD mono (~1 MB decoded), the smaller ask first.
        log("== hd mono ref")
        img, n = load_one(("ref_mono_1280x800.pgm", "ref_mono_1280x800.jpg"))
        if img:
            ok_mono = encode(img, "hd-mono") > 0
        img = None
        gc.collect()
        log("  freed, heap %d" % gc.mem_free())

        # HD color (~3 MB decoded RGB565 = 2 MB... measured, not guessed).
        log("== hd color ref")
        img, n = load_one(("ref_color_1280x800.bmp", "ref_color_1280x800.jpg"))
        if img:
            ok_color = encode(img, "hd-color") > 0
        img = None
        gc.collect()
        log("  freed, heap %d" % gc.mem_free())

        # The _load_ref sequence: mono loaded, then a mode change frees
        # it and loads color (old freed FIRST -- the production order).
        log("== sequence mono -> color (production _load_ref order)")
        img, _ = load_one(("ref_mono_1280x800.pgm", "ref_mono_1280x800.jpg"))
        img = None
        gc.collect()
        img, _ = load_one(("ref_color_1280x800.bmp", "ref_color_1280x800.jpg"))
        if img:
            encode(img, "hd-color-seq")
        img = None
        gc.collect()
    finally:
        log("---- VERDICT: mono=%s color=%s ----"
            % ("PASS" if ok_mono else "FAIL",
               "PASS" if ok_color else "FAIL"))
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r" % e)
        log("---- probe G6 end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
