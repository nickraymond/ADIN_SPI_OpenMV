# s18_hd_gate_probe5.py -- probe G5: the interim-path test. Is ONE
# transition per boot reliable with the HE loaded?
#
# G3/G4 established: sensor transitions degrade only when the HE core
# is resident (no-HE control 40/40; HE-loaded failed at #22 and #10;
# on-chain at #2 twice, under traffic). If a fresh boot straight into
# the commanded mode -- bootstrap, HE, ONE transition, capture, publish,
# capture again -- is reliable, then a boot-per-row bridge policy can
# deliver the matrix's HD rows on the CURRENT firmware while the real
# fix (static framebuffer) is built and flashed.
#
# This is G row 1's killer shape minus the accumulated transitions:
# publish + quiet + the HD mono state, but reached as the boot's first
# and only transition.
#
# Needs: neutral /flash/main.py, /flash/bm_bridge.py, fresh boot per run.

import sensor, time, gc

LOG = "/flash/hd_gate_probe5.txt"

RES = {"qvga": sensor.QVGA, "vga": sensor.VGA, "hd": sensor.HD}
PF = {"color": sensor.RGB565, "mono": sensor.GRAYSCALE}

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


def pump_wait(ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        time.sleep_ms(2)
        while _wire.queue:
            _core.he_msg(_wire.queue.pop(0))


def pump():
    while _wire.queue:
        _core.he_msg(_wire.queue.pop(0))


def snap(tag):
    n = len(sensor.snapshot().to_jpeg(quality=50, copy=True).bytearray())
    log("  capture %s: %d B" % (tag, n))
    return n


def main():
    global _f, _bb, _core, _wire
    _f = open(LOG, "w")
    log("---- probe G5 start (one transition per boot, HE loaded) ----")

    log("bootstrap (pre-HE):")
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)
    sensor.set_framebuffers(1)
    sensor.set_framesize(sensor.HD)
    sensor.skip_frames(time=300)
    log("  ceiling claimed, heap %d" % gc.mem_free())

    import bm_bridge as bb
    _bb = bb
    _core = bb.BridgeCore()
    _wire = bb.HeWire()
    log("loading HE elf ->")
    rp = _wire.start()
    log("HE loaded, heap %d" % gc.mem_free())
    time.sleep(2)
    pump()

    ok = False
    try:
        # The boot's ONE transition: hd color (bootstrap state) -> hd mono.
        log("THE transition: hd color -> hd mono (boot's first and only)")
        log("  . pixformat mono")
        sensor.set_pixformat(sensor.GRAYSCALE)
        log("  . framebuffers(1)")
        sensor.set_framebuffers(1)
        log("  . framesize hd")
        sensor.set_framesize(sensor.HD)
        log("  . settle")
        sensor.skip_frames(time=300)
        n1 = snap("hd-mono #1")

        # Publish a realistic HD mono frame's chunk burst, wait, capture
        # again -- the sustained-use shape of a matrix row.
        msgs = _core.capture_pub_msgs(bytes(n1), 0, _bb.CAMERA_MAX_PAYLOAD)
        _bb.send_chunk_msgs(msgs, _wire.send, pump)
        log("  published %d B as %d msgs" % (n1, len(msgs)))
        pump_wait(3000)
        n2 = snap("hd-mono #2")
        msgs = _core.capture_pub_msgs(bytes(n2), 1, _bb.CAMERA_MAX_PAYLOAD)
        _bb.send_chunk_msgs(msgs, _wire.send, pump)
        log("  published %d B as %d msgs" % (n2, len(msgs)))
        pump_wait(3000)
        n3 = snap("hd-mono #3")
        ok = n1 > 0 and n2 > 0 and n3 > 0
    except Exception as e:
        log("  ! FAILED: %r" % e)
    finally:
        log("---- VERDICT: %s ----" % ("PASS" if ok else "FAIL"))
        log("stopping HE ->")
        try:
            rp.stop()
            log("he stopped")
        except Exception as e:
            log("he stop FAILED: %r" % e)
        log("---- probe G5 end, heap %d ----" % gc.mem_free())
        try:
            _f.close()
        except Exception:
            pass


main()
