# n6_usb_throughput -- how fast can bytes leave the N6 for the Pi?
#
# Runs ON the board. With no SD card fitted, USB is the only path off this
# N6, so this number decides whether 30 fps HD is deliverable at all.
#
# Method: push a fixed payload to stdout in chunks and let the HOST time it.
# The board cannot see when the host actually drained the pipe, so the board
# clock would measure buffering, not delivery. The host wall-clock across the
# whole mpremote invocation is the honest number -- it includes CDC framing,
# mpremote's raw-REPL escaping, and the Pi's read loop, which is exactly the
# path a real capture would take.
#
# Reports the board-side view too, purely so the gap between them is visible:
# if board time << host time, the board is filling a buffer faster than the
# link drains it, and the LINK is the constraint.
import sys
import time

TOTAL = 4 * 1024 * 1024
CHUNK = 4096

buf = bytes(CHUNK)
n = 0
t0 = time.ticks_us()
while n < TOTAL:
    sys.stdout.buffer.write(buf)
    n += CHUNK
board_us = time.ticks_diff(time.ticks_us(), t0)

sys.stdout.buffer.write(b"\n")
print("BOARD bytes=%d board_us=%d board_MBps=%.2f"
      % (n, board_us, n / (board_us / 1e6) / (1024 * 1024)))
