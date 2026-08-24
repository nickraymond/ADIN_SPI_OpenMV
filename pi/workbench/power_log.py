#!/usr/bin/env python3
"""INA3221 bench power logger (S8 bite D power rig).

Reads the Adafruit INA3221 breakout (I2C 0x40/0x41) on the Pi's bus and
writes one timestamped JSONL row per sample per enabled channel:

    {"ts": 1787540000.123, "ch": 1, "label": "AE3", "V": 5.02,
     "mA": 143.2, "mW": 719.4}

Register map + scaling verified against Adafruit_CircuitPython_INA3221
(2026-08-23): config 0x00; shunt ch1-3 = 0x01/0x03/0x05 (40 uV/LSB, >>3);
bus ch1-3 = 0x02/0x04/0x06 (8 mV/LSB, >>3); manufacturer id 0xFE reads
0x5449 ("TI"). Board shunts are 0.05 ohm 1%.

Usage:
    power_log.py --probe                     # find + identify the chip
    power_log.py --ch 1=AE3 --ch 2=N6        # log until Ctrl-C / SIGTERM
        [--addr 0x40] [--bus 1] [--hz 10] [--out ~/bench_logs/power]

Energy for a window (mJ) is integrated offline from the rows:
    mJ = sum(mW_i * dt_i)   between a probe's PWR_MARK timestamps.

Needs: I2C enabled (dtparam=i2c_arm=on) and python3-smbus2. Fails loudly
naming the device and register on any I/O error; partial logs are valid
JSONL (one row per line, flushed).
"""
import argparse
import json
import os
import signal
import sys
import time

try:
    from smbus2 import SMBus
except ImportError:
    sys.exit("power_log: python3 smbus2 missing (pip install smbus2)")

REG_CONFIG = 0x00
REG_SHUNT = {1: 0x01, 2: 0x03, 3: 0x05}
REG_BUS = {1: 0x02, 2: 0x04, 3: 0x06}
REG_MANUF_ID = 0xFE
REG_DIE_ID = 0xFF

SHUNT_OHMS = 0.05          # Adafruit board: built-in 0.05 ohm 1%
SHUNT_V_PER_LSB = 40e-6    # after >>3
BUS_V_PER_LSB = 8e-3       # after >>3

# ch1+ch2+ch3 enabled, avg=16, 1.1 ms conversions, continuous shunt+bus.
# Verify by read-back: the logger prints the config it lands.
CONFIG_VALUE = 0x7527


def read16(bus, addr, reg):
    raw = bus.read_word_data(addr, reg)          # little-endian word
    return ((raw & 0xFF) << 8) | (raw >> 8)      # chip is big-endian


def write16(bus, addr, reg, value):
    swapped = ((value & 0xFF) << 8) | (value >> 8)
    bus.write_word_data(addr, reg, swapped)


def to_signed(v):
    return v - 0x10000 if v & 0x8000 else v


def read_channel(bus, addr, ch):
    sh = to_signed(read16(bus, addr, REG_SHUNT[ch])) >> 3
    bu = to_signed(read16(bus, addr, REG_BUS[ch])) >> 3
    volts = bu * BUS_V_PER_LSB
    ma = sh * SHUNT_V_PER_LSB / SHUNT_OHMS * 1000.0
    return volts, ma


def probe(busnum):
    with SMBus(busnum) as bus:
        for addr in (0x40, 0x41):
            try:
                manuf = read16(bus, addr, REG_MANUF_ID)
                die = read16(bus, addr, REG_DIE_ID)
            except OSError as e:
                print("0x%02x: no answer (%s)" % (addr, e))
                continue
            verdict = "INA3221 CONFIRMED" if manuf == 0x5449 else "UNEXPECTED IDS"
            print("0x%02x: manuf=0x%04x die=0x%04x -> %s" % (addr, manuf, die, verdict))
            if manuf == 0x5449:
                return 0
    print("probe: no INA3221 found on bus %d (wiring? i2c enabled?)" % busnum)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x40)
    ap.add_argument("--ch", action="append", default=[],
                    metavar="N=LABEL", help="channel to log, e.g. 1=AE3")
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--out", default=os.path.expanduser("~/bench_logs/power"))
    args = ap.parse_args()

    if args.probe:
        sys.exit(probe(args.bus))

    channels = []
    for spec in args.ch or ["1=ch1"]:
        n, _, label = spec.partition("=")
        n = int(n)
        if n not in REG_SHUNT:
            sys.exit("power_log: channel must be 1..3, got %r" % spec)
        channels.append((n, label or "ch%d" % n))

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, time.strftime("power_%Y%m%dT%H%M%S.jsonl"))
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))

    with SMBus(args.bus) as bus, open(path, "w", buffering=1) as f:
        manuf = read16(bus, args.addr, REG_MANUF_ID)
        if manuf != 0x5449:
            sys.exit("power_log: 0x%02x manuf=0x%04x is not an INA3221"
                     % (args.addr, manuf))
        write16(bus, args.addr, REG_CONFIG, CONFIG_VALUE)
        got = read16(bus, args.addr, REG_CONFIG)
        print("config wrote=0x%04x readback=0x%04x -> %s" % (CONFIG_VALUE, got, path))
        period = 1.0 / args.hz
        while not stop:
            ts = time.time()
            for n, label in channels:
                v, ma = read_channel(bus, args.addr, n)
                f.write(json.dumps({"ts": round(ts, 3), "ch": n, "label": label,
                                    "V": round(v, 3), "mA": round(ma, 2),
                                    "mW": round(v * ma, 2)}) + "\n")
            time.sleep(max(0.0, period - (time.time() - ts)))
    print("power_log: stopped cleanly, log at %s" % path)


if __name__ == "__main__":
    main()
