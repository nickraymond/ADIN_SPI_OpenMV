#!/usr/bin/env python3
"""E4 contract step (b): prove the host->board control byte on REAL
hardware before the full harness rides it. Runs ON nereus000.

Board side (pushed via raw REPL): waits for bytes, echoes each back as
'#ECHO <hex>' with its own wait time — proving (1) stdin.buffer.read(1)
works inside the raw REPL on this firmware, (2) our bytes arrive intact
(no CDC mangling), (3) round-trip latency is milliseconds.

  python3 pi/hil/hil_probe_stdin.py /dev/serial/by-id/usb-...
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
from n6_stream_host import SerialBoard  # noqa: E402

BOARD_SCRIPT = r"""
import sys, time
print("#READY")
for i in range(12):
    b = sys.stdin.buffer.read(1)
    print("#ECHO %d %s %d" % (i, b.hex() if b else "none",
                              time.ticks_ms()))
print("#DONE")
"""


def main():
    port = sys.argv[1]
    sb = SerialBoard(port)
    sb.start(BOARD_SCRIPT)
    deadline = time.monotonic() + 20
    ready = False
    while time.monotonic() < deadline:
        line = sb.readline()
        if line == b"":
            raise SystemExit(f"FAIL: stream ended ({sb.end_reason}) "
                             f"before #READY")
        if line.strip() == b"#READY":
            ready = True
            break
    if not ready:
        raise SystemExit("FAIL: no #READY in 20 s")
    print("board ready — sending 12 control bytes")
    sent = []
    ok = 0
    for i in range(12):
        byte = b"g" if i % 3 else b"p"
        t0 = time.monotonic()
        sb.ser.write(byte)
        sent.append(byte)
        while True:
            line = sb.readline()
            if line == b"":
                raise SystemExit(f"FAIL: stream died at byte {i}")
            if line.startswith(b"#ECHO"):
                dt_ms = (time.monotonic() - t0) * 1000
                parts = line.split()
                got = bytes.fromhex(parts[2].decode())
                verdict = "OK" if got == byte else "MISMATCH"
                if got == byte:
                    ok += 1
                print(f"  byte {i}: sent {byte} got {got} "
                      f"rtt {dt_ms:.1f} ms  {verdict}")
                break
    sb.stop()
    print(f"\n{'PASS' if ok == 12 else 'FAIL'}: {ok}/12 bytes echoed "
          f"intact")
    sys.exit(0 if ok == 12 else 1)


if __name__ == "__main__":
    main()
