# boot_report.py -- S23 bite R boot-state instrument. Deployed to /flash
# next to the bridge (demo_up syncs it); main_bridge.py calls boot() at
# launcher start, bm_bridge calls dump("post-he-start") right after
# he.start(). Appends raw-value dumps to /flash/boot_report.txt; the
# previous boot's report is kept as .prev.txt (same one-generation rule
# as the bridge traces -- a report you cannot read twice is a boot you
# debug twice).
#
# Dumb by design: raw hex only, decoding is desk work off-board. Every
# step is wrapped -- a diagnostics module must never be the thing that
# crashes the boot.
#
# Register addresses (ARMv8-M MPU), verified against the vendored CM55
# FreeRTOS port in this repo (firmware/he_spike/vendor/freertos/
# portable/GCC/ARM_CM55_NTZ/non_secure/portmacro.h lines 172-189):
#   MPU_TYPE 0xE000ED90  MPU_CTRL 0xE000ED94  MPU_RNR 0xE000ED98
#   MPU_RBAR 0xE000ED9C  MPU_RLAR 0xE000EDA0  MAIR0/1 0xE000EDC0/C4
# SHM addresses (measured facts, DESIGN §S10 + bm_bridge.py): rsc table
# 0x60000000, vring1 0x60000400, vring0 0x60001400, pool 0x60002400;
# the patch-0005 128 K window ends at 0x60020000 (upper half starts
# 0x60010000 -- the half the hardcoded METAL_MPU_REGION_SIZE once left
# cacheable); BM status page 0x600BFE00.

PATH = "/flash/boot_report.txt"
PREV = "/flash/boot_report.prev.txt"

_MPU_TYPE = 0xE000ED90
_MPU_CTRL = 0xE000ED94
_MPU_RNR = 0xE000ED98
_MPU_RBAR = 0xE000ED9C
_MPU_RLAR = 0xE000EDA0
_MPU_MAIR0 = 0xE000EDC0
_MPU_MAIR1 = 0xE000EDC4

# name, address -- first/last word of each structure of interest, plus
# the first word of the grown upper half (the old 64 K boundary).
PROBES = (
    ("shm_rsc", 0x60000000),
    ("shm_vring1", 0x60000400),
    ("shm_vring0", 0x60001400),
    ("shm_pool", 0x60002400),
    ("shm_upper", 0x60010000),
    ("shm_last", 0x6001FFFC),
    ("status_page", 0x600BFE00),
)

try:
    from time import ticks_ms
except ImportError:          # host tests run on CPython
    def ticks_ms():
        return -1


def _mpu_lines(machine):
    """MPU walk, IRQs held off across the RNR banked accesses so a
    concurrent user of the index register cannot interleave."""
    lines = []
    irq = machine.disable_irq()
    try:
        mtype = machine.mem32[_MPU_TYPE] & 0xFFFFFFFF
        mctrl = machine.mem32[_MPU_CTRL] & 0xFFFFFFFF
        mair0 = machine.mem32[_MPU_MAIR0] & 0xFFFFFFFF
        mair1 = machine.mem32[_MPU_MAIR1] & 0xFFFFFFFF
        nreg = (mtype >> 8) & 0xFF
        regions = []
        for r in range(nreg):
            machine.mem32[_MPU_RNR] = r
            regions.append((r,
                            machine.mem32[_MPU_RBAR] & 0xFFFFFFFF,
                            machine.mem32[_MPU_RLAR] & 0xFFFFFFFF))
    finally:
        machine.enable_irq(irq)
    lines.append("mpu type=%08x ctrl=%08x mair0=%08x mair1=%08x"
                 % (mtype, mctrl, mair0, mair1))
    for r, rbar, rlar in regions:
        lines.append("mpu r%02d rbar=%08x rlar=%08x" % (r, rbar, rlar))
    return lines


def dump(tag):
    """Append one tagged report. Never raises."""
    try:
        import machine
        lines = ["== boot_report %s t=%d ==" % (tag, ticks_ms())]
        try:
            lines += _mpu_lines(machine)
        except Exception as e:
            lines.append("mpu walk FAILED %r" % e)
        for name, addr in PROBES:
            try:
                lines.append("probe %s @%08x = %08x"
                             % (name, addr, machine.mem32[addr] & 0xFFFFFFFF))
            except Exception as e:
                lines.append("probe %s @%08x FAILED %r" % (name, addr, e))
        with open(PATH, "a") as f:
            for l in lines:
                f.write(l + "\n")
    except Exception:
        pass


def boot():
    """Rotate (one prior generation) then dump the boot snapshot."""
    try:
        import os
        try:
            os.remove(PREV)
        except OSError:
            pass
        try:
            os.rename(PATH, PREV)
        except OSError:
            pass
    except Exception:
        pass
    dump("boot")
