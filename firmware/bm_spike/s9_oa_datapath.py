# s9_oa_datapath.py -- S9 bite 3 runner: OA data-path smoke. Seq-numbered
# raw Ethernet frames through bm_core's UNMODIFIED OA driver -> nereus001.
#
# Requires: firmware built with `build_spike.sh --clean --no-prot --hal alif`,
# hat #2 strapped OA (bite-1 state), S4 harness unchanged, the pair
# connected to nereus001 (hat #1, eth1 up -- the S5 receive fixture).
#
# Receiver first, on nereus001 (either works; frame_counter gives a verdict):
#   sudo tcpdump -i eth1 ether proto 0x88B5 -XX -c 20
#   sudo python3 bench/frame_counter.py --iface eth1 --duration 30
#
# Then from nereus000 (ALWAYS the by-id path -- two OpenMV boards live here):
#   mpremote connect /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_* \
#            run ~/ae3_flash/s9_oa_datapath.py
#
# Order (bite-2 lessons all apply):
#   0. fresh() + sanitize: chip state persists across everything (P4 reset
#      line dead, always-on 3V3) and PROTE can be flipped by garbage
#      traffic -- both-framing soft reset, verify CONFIG0 == 0x06.
#   1. dp_init at 5 MHz (the proven-clean OA rung; no 20 MHz in this bite).
#   2. Link wait (S5 measured ~1 s autoneg against nereus001).
#   3. TX N seq-numbered frames; the receiver's count is the artifact.
#   4. Exit sanitize.

import struct
import sys
import time
from machine import Pin, SPI

try:
    import bm_spike
except ImportError:
    print("FAIL: no bm_spike module -- flash a build_spike.sh image first")
    raise SystemExit

if getattr(bm_spike, "HAL", None) != "alif":
    print("FAIL: this firmware's bm_spike HAL is %r, need 'alif'"
          % getattr(bm_spike, "HAL", None))
    print("      rebuild with: build_spike.sh --clean --no-prot --hal alif")
    raise SystemExit

N_FRAMES = 20
FRAME_LEN = 500          # 14 B header + 486 B payload, the S5 shape

# --- S5 test-frame format, mirrored from firmware/adin_drv/s5_frames.py
# (single-file runner; bench/frame_counter.py mirrors the same constants).
DST_MAC = b"\x02\xad\x11\x10\x00\x03"     # nereus001 eth1 (NM-cloned MAC)
SRC_MAC = b"\x02\xad\x11\x10\x00\x04"     # this AE3 node
ETHERTYPE = 0x88B5                        # IEEE local experimental #1
MAGIC = b"BMS5"


def build_eth_frame(seq, frame_len=FRAME_LEN):
    body = MAGIC + struct.pack(">I", seq)
    pad_n = frame_len - 14 - len(body)
    pad = bytes(i & 0xFF for i in range(pad_n))
    return DST_MAC + SRC_MAC + struct.pack(">H", ETHERTYPE) + body + pad


print("=" * 64)
print("S9 bite 3: OA data-path smoke (frame TX via bm_core's driver)")
print(sys.version)
print("=" * 64)

# C statics survive soft resets -- drop stale handles first (bite-2 lesson).
bm_spike.fresh()

rst = Pin("P4", Pin.OUT, value=1)         # convention only; line measured dead
irqpin = Pin("P5", Pin.IN, Pin.PULL_UP)   # D14: board lacks INT_N pull-up

RESET_REG, CONFIG0 = 0x003, 0x004
CONFIG0_RESET_DEFAULT = 0x06              # measured on this chip, PROTE=0

# --- 0. sanitize (raw SPI, released before the native HAL starts) ------
_cs = Pin("P3", Pin.OUT, value=1)
_spi = SPI(0, baudrate=5_000_000, polarity=0, phase=0)

def _xfer(tx):
    rx = bytearray(len(tx))
    _cs.value(0)
    _spi.write_readinto(bytes(tx), rx)
    _cs.value(1)
    return rx

def _hdr(addr, wnr):
    v = (wnr << 29) | (addr << 8)
    return v | (0 if bin(v).count("1") & 1 else 1)

def _rd(addr):
    rx = _xfer(_hdr(addr, 0).to_bytes(4, "big") + bytes(12))
    return int.from_bytes(rx[8:12], "big")

sanitized = False
for attempt in range(2):
    # unprotected then protected soft reset -- one lands in either mode
    _xfer(_hdr(RESET_REG, 1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes(8))
    _xfer(_hdr(RESET_REG, 1).to_bytes(4, "big") + (1).to_bytes(4, "big")
          + (0xFFFFFFFE).to_bytes(4, "big") + bytes(4))
    time.sleep_ms(30)
    cfg0 = _rd(CONFIG0)
    if cfg0 == CONFIG0_RESET_DEFAULT:
        sanitized = True
        break
print("sanitize: CONFIG0=0x%08X (%s)" %
      (cfg0, "OK, PROTE=0" if sanitized else "UNEXPECTED -- continuing, "
       "but chip state is suspect"))
_spi.deinit()

# Trampoline armed as scaffolding: the bridge never registers the driver's
# INT callback (static symbol), so edges are counted and ignored. SyncConfig
# enables the NVIC line; falling edges (e.g. LOFE until link-up) are benign.
irqpin.irq(handler=bm_spike.irq_trampoline, trigger=Pin.IRQ_FALLING, hard=True)

# --- 1. init bridge at 5 MHz ------------------------------------------
actual = bm_spike.setup(5_000_000)
print("native SPI0 up: requested 5 MHz, controller reports %d Hz" % actual)
print("-" * 64)
fail_rung, mac_init, phyid, phy_init, devid, sync = bm_spike.dp_init()
print("-" * 64)

ok = fail_rung == 0
if ok:
    print("VERDICT A: init bridge UP -- driver PHY init + SYNC through the")
    print("           unmodified OA driver (DEVID 0x%04X/0x%04X)"
          % (devid >> 16, devid & 0xFFFF))
else:
    print("VERDICT A: FAIL at rung %d (see rung lines above)" % fail_rung)
    if fail_rung == 2:
        print("  -> PHYID never matched: straps/harness/PROTE -- run the")
        print("     bite-2 checklist (s9_hal_native.py) to split HAL-vs-fixture")
    elif fail_rung == 5:
        print("  -> the NEW surface: MDIO-over-OA or the PHY identity check;")
        print("     DEVID words above say which (want 0x0283/0xBC91)")

# --- 2. link wait ------------------------------------------------------
link = False
if ok:
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 5000:
        if bm_spike.dp_link():
            link = True
            break
        time.sleep_ms(100)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    if link:
        print("link: UP after %d ms (autoneg vs nereus001)" % dt)
    else:
        print("link: still DOWN after %d ms -- pair connected? nereus001 eth1 up?" % dt)
        ok = False

# --- 3. TX smoke -------------------------------------------------------
if ok:
    print("-" * 64)
    print("TX: %d seq-numbered %d-byte frames (S5 format, EtherType 0x88B5)"
          % (N_FRAMES, FRAME_LEN))
    sent = 0
    done_total = 0
    t0 = time.ticks_ms()
    for seq in range(N_FRAMES):
        done = bm_spike.dp_send(build_eth_frame(seq))
        sent += 1
        done_total += done
        time.sleep_ms(10)     # smoke pacing, not a throughput bench
    dt = time.ticks_diff(time.ticks_ms(), t0)

    txd, txc, state, hdrp, ftrp, syncerr, fd, spierr = bm_spike.dp_stats()
    print("   %d submitted in %d ms; tx-done callbacks %d; TXC credits %d"
          % (sent, dt, txd, txc))
    print("   errors: hdr-parity %d, ftr-parity %d, footer-SYNC %d, "
          "frame-drop %d, spiErr %d" % (hdrp, ftrp, syncerr, fd, spierr))

    if done_total == N_FRAMES and txd == N_FRAMES and \
            hdrp == 0 and ftrp == 0 and syncerr == 0 and spierr == 0:
        print("VERDICT B: %d/%d frames into the MAC TX FIFO, zero OA errors"
              % (txd, N_FRAMES))
        print("           -> now check the RECEIVER (tcpdump/frame_counter on")
        print("              nereus001) -- that count is the demo artifact")
    else:
        ok = False
        print("VERDICT B: FAIL -- submit/done mismatch or OA errors (above)")

# --- 4. exit sanitize (best effort, native framing) --------------------
print("-" * 64)
try:
    bm_spike.write_reg(RESET_REG, 1)
    time.sleep_ms(20)
    print("exit sanitize: soft reset sent, CONFIG0=0x%08X"
          % bm_spike.read_reg(CONFIG0))
except Exception as e:
    print("exit sanitize FAILED (%s) -- next run's pre-flight will recover" % e)

print("=" * 64)
print("BITE 3 SENDER RESULT: %s" % ("PASS (pending receiver count)" if ok else "FAIL"))
