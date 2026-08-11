# s9_hal_native.py -- S9 bite 2 runner: the Alif-native ADI-HAL.
#
# Requires: firmware built with `build_spike.sh --no-prot --hal alif`,
# hat #2 strapped OA (bite-1 state), S4 harness unchanged (P0-P5).
#
# Run from nereus000 (ALWAYS the by-id path -- two OpenMV boards live here):
#   mpremote connect /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_* \
#            run ~/ae3_flash/s9_hal_native.py
#
# Order matters (hard-won 2026-08-11, all measured on this rig):
#   0. Sanitize: the chip's state persists across EVERYTHING (the P4 reset
#      line is ineffective, the hat is powered from the Pi's always-on
#      3V3), and garbage traffic can flip CONFIG0.PROTE -- observed after
#      a 20 MHz bench rung: misclocked MOSI decoded as a valid CONFIG0
#      write. With PROTE=1 the chip silently drops our unprotected writes
#      (reads still work -- first data word aligns in both framings) and
#      latches CDPE. So: soft-reset in BOTH framings (one always lands),
#      then verify CONFIG0 == reset default.
#   1. Verify + IRQ proof at 5 MHz, the proven speed, BEFORE any
#      garbage-risk traffic.
#   2. Bench ladder last; the 20 MHz rung reads garbage on this rig
#      (finding: RX sample delay) and may leave chip state unspecified --
#      nothing gates on it and a final sanitize follows it.

import time
from machine import Pin, SPI

import sys

try:
    import bm_spike
except ImportError:
    print("FAIL: no bm_spike module -- flash a build_spike.sh image first")
    raise SystemExit

if getattr(bm_spike, "HAL", None) != "alif":
    print("FAIL: this firmware's bm_spike HAL is %r, need 'alif'"
          % getattr(bm_spike, "HAL", None))
    print("      rebuild with: build_spike.sh --no-prot --hal alif")
    raise SystemExit

print("=" * 64)
print("S9 bite 2: Alif-native ADI-HAL (SPI0 FIFO-burst + INT_N IRQ)")
print(sys.version)
print("=" * 64)

# C statics survive soft resets -- drop any bench handle a previous
# session left behind (a stale one benches all-fails; measured).
bm_spike.fresh()

# Scaffolding pins. P4 reset is kept for convention but measured
# ineffective on this rig (register scratch survives the pulse) --
# flagged fixture question; resets below are the chip's soft reset.
rst = Pin("P4", Pin.OUT, value=1)
irqpin = Pin("P5", Pin.IN, Pin.PULL_UP)   # D14: board lacks INT_N pull-up

ST0, ST1, IMASK0, RESET_REG, CONFIG0 = 0x008, 0x009, 0x00C, 0x003, 0x004
LOFE_M = 0x10
CONFIG0_RESET_DEFAULT = 0x06   # measured on this chip, PROTE (bit 5) = 0

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

# Arm the IRQ path (re-armed again later: the driver's failed-init exits
# go through HAL_DisableIrq).
irqpin.irq(handler=bm_spike.irq_trampoline, trigger=Pin.IRQ_FALLING, hard=True)

# --- 1a. verify through the native HAL ---------------------------------
actual = bm_spike.setup(5_000_000)
print("native SPI0 up: requested 5 MHz, controller reports %d Hz" % actual)
print("-" * 64)

r1, phyid, r2 = bm_spike.verify()
print("-" * 64)

ok = phyid == 0x0283BC91
if ok:
    print("VERDICT A: PHYID over OA via NATIVE HAL -- OK")
else:
    print("VERDICT A: FAIL (r1=%d phyid=0x%08X r2=%d)" % (r1, phyid, r2))
    print("  -> same checklist as bite 1: straps, harness, 3V3; then compare")
    print("     against a --hal mp build to split HAL-vs-fixture")

# --- 1b. IRQ proof (measured semantics, see file header) ---------------
# INT_N asserts from reset (RESETC pending, unmasked -- IMASK0 default
# 0x1FBF) and only W1C raises it; LOFE relatches continuously on this
# bench and must be masked; the falling edge comes from the chip's own
# soft reset relatching RESETC.
print("-" * 64)
bm_spike.write_reg(RESET_REG, 1)           # known state
time.sleep_ms(20)
im = bm_spike.read_reg(IMASK0)
bm_spike.write_reg(IMASK0, im | LOFE_M)    # mask the relatching LOFE
st0 = bm_spike.read_reg(ST0)
bm_spike.write_reg(ST0, st0)               # W1C everything latched
time.sleep_ms(1)
lvl_cleared = irqpin.value()
print("IRQ proof: IMASK0 0x%08X -> LOFE masked; STATUS0 was 0x%08X;"
      % (im, st0))
print("           after W1C -> P5 level %d (want 1)" % lvl_cleared)
# Re-arm AFTER all driver-touching calls: the driver's FAILED init paths
# (expected on a 1110 -- the identity gate) exit via HAL_DisableIrq and
# never re-enable. On a real successful init the driver re-enables IRQ
# itself (adi_mac.c:986/1076) -- bench scaffolding, not a driver bug.
irqpin.irq(handler=bm_spike.irq_trampoline, trigger=Pin.IRQ_FALLING, hard=True)
bm_spike.stats_clear()
bm_spike.write_reg(RESET_REG, 1)           # soft reset -> RESETC -> edge
time.sleep_ms(50)
_, _, _, irqs = bm_spike.stats()
level = irqpin.value()
print("           soft reset -> %d IRQ callback(s); P5 level now %d" %
      (irqs, level))
if lvl_cleared == 1 and irqs >= 1:
    print("VERDICT B: INT_N -> native IRQ path -> driver callback -- OK")
elif lvl_cleared == 0:
    print("VERDICT B: INCONCLUSIVE -- INT_N did not deassert after W1C;")
    try:
        print("  STATUS0=0x%08X -- new unmasked cause pending?"
              % bm_spike.read_reg(ST0))
    except Exception as e:
        print("  (post-mortem read failed: %s)" % e)
    ok = False
else:
    print("VERDICT B: FAIL -- edge existed (P5 rose, soft reset re-asserted)")
    print("  but no callback fired: dispatch is broken (this IS a bite bug)")
    ok = False

# --- 2. bench ladder (LAST -- the 20 MHz rung is garbage-risk) ---------
# Only the 5 MHz rung gates the bite. 10/20 MHz are data: first OA-mode
# runs at speed on this rig; 20 MHz reads garbage (RX sample delay is the
# first suspect) and its misclocked frames can WRITE the chip (PROTE flip
# observed) -- hence this ladder runs after all gating checks.
N = 2000
print("-" * 64)
print("bench: %d PHYID round trips per rung (init excluded)" % N)
for hz in (5_000_000, 10_000_000, 20_000_000):
    actual = bm_spike.setup(hz)
    us, fails, ph = bm_spike.bench(N)
    rate = N * 1_000_000 // us if us else 0
    print("  %2d MHz (actual %8d): %7d us  %5d reads/s  fails=%d  phyid=0x%08X"
          % (hz // 1_000_000, actual, us, rate, fails, ph))
    if fails or ph != 0x0283BC91:
        if hz == 5_000_000:
            ok = False
            print("  ^ FAIL (gating rung)")
        else:
            print("  ^ finding: OA at %d MHz not clean on this rig"
                  % (hz // 1_000_000))
xf, by, stalls, _ = bm_spike.stats()
print("hal stats: %d transfers, %d bytes, %d stalls" % (xf, by, stalls))
if stalls:
    ok = False
    print("FAIL: SPI stalls counted -- wiring/clocking suspect")

# Leave the chip sane for the next session (best effort, native framing).
bm_spike.setup(5_000_000)
try:
    bm_spike.write_reg(RESET_REG, 1)
    time.sleep_ms(20)
    print("exit sanitize: soft reset sent, CONFIG0=0x%08X"
          % bm_spike.read_reg(CONFIG0))
except Exception as e:
    print("exit sanitize FAILED (%s) -- next run's pre-flight will recover" % e)

print("=" * 64)
print("BITE 2 RESULT: %s" % ("PASS (see 20 MHz finding)" if ok else "FAIL"))
