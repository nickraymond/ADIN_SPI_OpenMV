#!/usr/bin/env python3
"""Measure the N6 -> Pi CDC data rate WITHOUT mpremote in the path.

Why this exists: measuring through `mpremote run` gave 0.01 MB/s, because
mpremote's raw REPL escapes and re-frames every byte. That number describes
mpremote, not the link, and a real capture path would never use it.

This drives the board's normal REPL with pyserial, then reads the bulk
payload straight off the CDC port in large chunks -- which is what a real
frame-pump on the Pi would do.

Run ON the Pi that owns the board:
    python3 usb_throughput_direct.py /dev/serial/by-id/usb-...-if00
"""
import sys
import time

import serial

PORT = sys.argv[1]
MB = int(sys.argv[2]) if len(sys.argv) > 2 else 8
CHUNK = 4096
TOTAL = MB * 1024 * 1024

if "by-id" not in PORT:
    raise SystemExit("refusing a non-by-id port")

s = serial.Serial(PORT, timeout=15)
s.write(b"\x03\x03")                      # Ctrl-C: stop anything running
time.sleep(0.4)
s.reset_input_buffer()
s.write(b"\x02")                          # ensure friendly REPL, not raw
time.sleep(0.3)
s.reset_input_buffer()

cmd = ("import sys;b=bytes(%d);_=[sys.stdout.buffer.write(b) for _ in range(%d)]\r\n"
       % (CHUNK, TOTAL // CHUNK))
s.write(cmd.encode())
s.flush()

# Drop the echoed command line, then time from the first payload byte.
deadline = time.time() + 20
while time.time() < deadline:
    if s.read_until(b"\n"):
        break

got = 0
first = None
last = None
while got < TOTAL:
    d = s.read(min(65536, TOTAL - got))
    if not d:
        break
    now = time.time()
    if first is None:
        first = now
    last = now
    got += len(d)

s.write(b"\x03")
s.close()

if not first or got < TOTAL // 2:
    raise SystemExit("FAIL: only %d of %d bytes arrived -- board may not have run "
                     "the command. Do NOT retry immediately." % (got, TOTAL))

elapsed = last - first
rate = got / elapsed / (1024 * 1024)
print("bytes       %d" % got)
print("elapsed     %.2f s" % elapsed)
print("RATE        %.2f MB/s  (%.1f Mbps)" % (rate, rate * 8))
print()
print("HD 1280x800 @ 30 fps, against measured bytes/frame:")
for name, bpf in (("MJPEG q90", 426720), ("H.264 quality-matched", 279007),
                  ("H.264 32 Mbps", 135260), ("H.264 16 Mbps", 69636),
                  ("H.264 8 Mbps", 35884)):
    need = bpf * 30 / (1024 * 1024)
    v = "FITS" if need <= rate * 0.85 else ("MARGINAL" if need <= rate else "DOES NOT FIT")
    print("  %-24s needs %6.2f MB/s   %s" % (name, need, v))
