# s10_pipe_bench.py -- S10 bite 1 runner. Runs ON the AE3's HP core
# (stock firmware, no flash) via mpremote from nereus000:
#
#   mpremote connect <by-id> cp he_spike.elf :/flash/he_spike.elf   # once
#   mpremote connect <by-id> run s10_pipe_bench.py
#
# Loads the FreeRTOS spike onto the HE core at runtime (remoteproc ELF
# load into SRAM9_B), then prints the verdict table:
#   A -- HE runs our FreeRTOS app (NS announce + PING answered)
#   B -- pipe throughput, HP->HE gated >= 5 Mbps (S10 TRACKER gate)
#   C -- HE owns SPI0 + its IRQ (internal loopback, no ADIN, no wiring)

import openamp
import struct
import time
import machine

try:
    from binascii import crc32
except ImportError:
    crc32 = None

ELF_PATHS = ("/flash/he_spike.elf", "he_spike.elf")
BIN_PATHS = ("/flash/he_spike.bin", "he_spike.bin")
APP_BASE = 0x60080000
STATUS_PAGE = 0x600BFF00
GATE_MBPS = 5.0
WINDOW_S = 5.0
MSG_SIZE = 480  # payload bytes per rpmsg message (496 max)

STAGES = {0: "-", 1: "BOOT", 2: "RTOS", 3: "DRIVER_OK", 4: "NS_SENT",
          5: "RUNNING"}


def m32(addr):
    return machine.mem32[addr] & 0xFFFFFFFF


def status_page():
    if m32(STATUS_PAGE) != 0x48455350:  # 'HESP'
        return None
    f = [m32(STATUS_PAGE + 4 * i) for i in range(8)]
    return {"stage": STAGES.get(f[1], f[1]), "tick": f[2], "err": f[3],
            "rsc": f[4], "rx": f[5], "tx": f[6], "irqs": f[7]}


class Bench:
    def __init__(self):
        self.ept = None
        self.replies = []
        self.pump_count = 0
        self.pump_bad = 0
        self.pump_seq = -1
        self.pump_done = None

    def ns(self, src, name):
        if name == "he-bench":
            self.ept = openamp.Endpoint("he-bench", self.rx, dest=src)

    def rx(self, src, data):
        b = bytes(data)
        if b[0] == 0x45:  # BPUMP_DATA
            seq, crc = struct.unpack_from("<II", b, 4)
            if seq != self.pump_seq + 1:
                self.pump_bad += 1
            if crc32 and crc32(b[12:]) & 0xFFFFFFFF != crc:
                self.pump_bad += 1
            self.pump_seq = seq
            self.pump_count += 1
        elif b[0] == 0x85:  # BREP(PUMP) done
            self.pump_done = struct.unpack_from("<I", b, 4)[0]
        else:
            self.replies.append(b)

    def cmd(self, msg, reply=True, timeout_ms=2000):
        self.replies.clear()
        self.ept.send(msg, timeout=1000)
        if not reply:
            return None
        t0 = time.ticks_ms()
        while not self.replies:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise OSError("no reply to cmd 0x%02x" % msg[0])
            time.sleep_ms(1)
        return self.replies.pop(0)


def load_remote(bench):
    # Preferred: remoteproc ELF load (handles cache maintenance).
    for p in ELF_PATHS:
        try:
            rp = openamp.RemoteProc(p)
            print("loader : ELF %s" % p)
            return rp
        except OSError:
            pass
        except TypeError:
            break  # ELF loading compiled out -> bin fallback
    # Fallback: poke the raw bin into SRAM9_B. Documented caveat: HP-side
    # D-cache may hold the image back; if stage never leaves '-' use the
    # ELF path instead.
    import uctypes
    for p in BIN_PATHS:
        try:
            blob = open(p, "rb").read()
        except OSError:
            continue
        dst = uctypes.bytearray_at(APP_BASE, len(blob))
        dst[:] = blob
        print("loader : bin poke %s (%d B)" % (p, len(blob)))
        return openamp.RemoteProc(APP_BASE)
    raise OSError("he_spike.elf/.bin not found on the board VFS")


def fmt_mbps(nbytes, secs):
    return nbytes * 8 / secs / 1e6


def main():
    verdicts = {}
    b = Bench()
    openamp.new_service_callback(b.ns)

    rp = load_remote(b)
    rp.start()

    t0 = time.ticks_ms()
    while b.ept is None:
        if time.ticks_diff(time.ticks_ms(), t0) > 5000:
            print("status page:", status_page())
            raise OSError("he-bench never announced (see status page)")
        time.sleep_ms(5)

    # ---- verdict A: FreeRTOS app alive --------------------------------
    rep = b.cmd(b"\x01")
    hz, tick, cyc = struct.unpack_from("<III", rep, 4)
    sp = status_page()
    verdicts["A"] = rep[0] == 0x81 and sp and sp["stage"] == "RUNNING" \
        and sp["err"] == 0
    print("A: FreeRTOS on HE  : %s  (core %d MHz, tick %d, stage %s)"
          % ("PASS" if verdicts["A"] else "FAIL", hz // 1000000, tick,
             sp["stage"] if sp else "?"))

    # ---- verdict B: throughput ----------------------------------------
    payload = bytes(range(256)) * 2
    payload = payload[:MSG_SIZE - 12]
    pcrc = (crc32(payload) & 0xFFFFFFFF) if crc32 else 0

    b.cmd(b"\x02")                          # SINK_RESET
    sent = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < WINDOW_S * 1000:
        b.ept.send(struct.pack("<B3xII", 3, sent, pcrc) + payload,
                   timeout=1000)
        sent += 1
    el = time.ticks_diff(time.ticks_ms(), t0) / 1000
    rep = b.cmd(b"\x04")                    # SINK_QUERY
    # NOTE: MicroPython's struct has no repeat counts -- spell formats out.
    cnt, byts, crce, gaps, c0, c1 = struct.unpack_from("<IIIIII", rep, 4)
    up = fmt_mbps(byts, el)
    ok_up = cnt == sent and crce == 0 and gaps == 0 and up >= GATE_MBPS
    print("B: HP->HE          : %s  %.1f Mbps (%d msgs %.1f s, "
          "crc_errs %d, gaps %d)%s"
          % ("PASS" if ok_up else "FAIL", up, cnt, el, crce, gaps,
             "" if crc32 else "  [no binascii.crc32: HE-side check only]"))

    b.pump_count = 0
    b.pump_bad = 0
    b.pump_seq = -1
    b.pump_done = None
    want = 20000
    b.cmd(struct.pack("<B3xII", 5, want, MSG_SIZE), reply=False)
    t0 = time.ticks_ms()
    while b.pump_done is None:
        if time.ticks_diff(time.ticks_ms(), t0) > 60000:
            break
        time.sleep_ms(2)
    el = time.ticks_diff(time.ticks_ms(), t0) / 1000
    down = fmt_mbps(b.pump_count * MSG_SIZE, el)
    ok_down = b.pump_done == want and b.pump_count == want \
        and b.pump_bad == 0
    print("   HE->HP          : %s  %.1f Mbps (%d/%d msgs %.1f s, bad %d)"
          % ("PASS" if ok_down else "FAIL", down, b.pump_count, want, el,
             b.pump_bad))
    verdicts["B"] = ok_up and ok_down

    # ---- verdict C: SPI0 ownership -------------------------------------
    rep = b.cmd(b"\x07", timeout_ms=5000)
    flags, txc, rxc, irqs, ctrlr0 = struct.unpack_from("<IIIII", rep, 4)
    verdicts["C"] = (flags & 0x0F) == 0x0F
    print("C: HE owns SPI0    : %s  (pinmux %d init %d loop %d irq %d "
          "[%d irqs] CTRLR0 0x%04x)"
          % ("PASS" if verdicts["C"] else "FAIL", flags & 1,
             (flags >> 1) & 1, (flags >> 2) & 1, (flags >> 3) & 1, irqs,
             ctrlr0))

    # ---- wrap up --------------------------------------------------------
    print("final status page  :", status_page())
    rp.stop()
    print()
    gate = "PASS" if all(verdicts.values()) else "FAIL"
    print("S10 bite 1 verdict : %s  (A:%s B:%s C:%s, gate >= %.0f Mbps)"
          % (gate, *["PASS" if verdicts[k] else "FAIL" for k in "ABC"],
             GATE_MBPS))
    return gate


main()
