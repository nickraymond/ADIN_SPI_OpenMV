#!/usr/bin/env python3
"""Echo probe (S8 bite E4, step b): PROVE the board-side stdin poll.

The closed-loop handshake's one hard assumption is that bytes written to
the VCP while a raw-REPL script runs reach sys.stdin on the board. This
probe settles it by test, on ONE board, before the full harness rides
it (Nick's contract). Run ON nereus000:

  python3 hil_probe_stdin_host.py /dev/serial/by-id/usb-MicroPython_... \
      [--log ~/hil_runs/e4_probe_stdin.log]

What it proves (each printed as its own verdict):
  P1 select.poll registers sys.stdin at all (board's #P line)
  P2 a byte sent mid-script arrives promptly (echo of 'g' at ~1 s)
  P3 a noise byte is observed, not fatal ('z')
  P4 bytes sent while the board is BUSY (4 s deliberate no-poll window,
     the inference stand-in) are BUFFERED and read afterwards — the
     resend/drain recovery model depends on this
  P5 what the CDC delivers for b"g\\r\\n" host→board — the CRLF trap has
     bitten three times board→host; this measures the other direction
  P6 'q' ends the script cleanly (#DONE)

Trust the artifact: the full echo log is written to --log.
"""
import argparse
import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
from n6_stream_host import SerialBoard              # noqa: E402

# Board side, pushed into the raw REPL (never written to the board).
# Heartbeats every 1 s so the host's readline keeps returning.
BOARD_SCRIPT = r"""
import json, select, sys, time
p = select.poll()
p.register(sys.stdin, select.POLLIN)
print("#P " + json.dumps({"poll_ok": True}))
t0 = time.ticks_ms()
busy_done = False
last_hb = -1000
while True:
    now = time.ticks_diff(time.ticks_ms(), t0)
    if now > 30000:
        break
    if not busy_done and now > 8000:
        # deliberate 4 s WITHOUT polling -- the inference stand-in;
        # bytes sent in this window must be waiting when it ends
        time.sleep_ms(4000)
        busy_done = True
        print("#B " + json.dumps(
            {"busy_end_ms": time.ticks_diff(time.ticks_ms(), t0)}))
        continue
    if now - last_hb >= 1000:
        last_hb = now
        print("#H " + json.dumps({"ms": now}))
    if p.poll(100):
        c = sys.stdin.read(1)
        print("#E " + json.dumps(
            {"ms": time.ticks_diff(time.ticks_ms(), t0), "byte": ord(c)}))
        if c == "q":
            break
print("#DONE " + json.dumps({"busy_done": busy_done}))
"""

# (offset_s, bytes) — 9.5 s lands INSIDE the board's 8–12 s busy window
SENDS = [(1.0, b"g"), (3.0, b"z"), (9.5, b"g"), (14.0, b"g\r\n"),
         (16.0, b"q")]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("port")
    ap.add_argument("--log",
                    default=os.path.expanduser(
                        "~/hil_runs/e4_probe_stdin.log"))
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.log), exist_ok=True)

    sb = SerialBoard(args.port).start(BOARD_SCRIPT)
    t0 = time.monotonic()

    def sender():
        for off, data in SENDS:
            time.sleep(max(0.0, t0 + off - time.monotonic()))
            sb.ser.write(data)
            print(f"  host sent {data!r} at {time.monotonic() - t0:.2f}s")
    threading.Thread(target=sender, daemon=True).start()

    events = []          # (tag, obj) in arrival order
    log = open(args.log, "w")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        line = sb.readline()
        if line == b"":
            log.write(f"[stream end: {sb.end_reason} {sb.last_error}]\n")
            break
        log.write(line.decode("utf-8", "replace"))
        s = line.rstrip(b"\r\n")
        if not s.startswith(b"#"):
            continue
        try:
            tag, payload = s.split(b" ", 1)
            obj = json.loads(payload)
        except ValueError:
            continue
        if tag != b"#H":
            print(f"  board {tag.decode()} {obj} "
                  f"at {time.monotonic() - t0:.2f}s")
        events.append((tag.decode(), obj))
        if tag == b"#DONE":
            break
    log.close()
    sb.stop()

    echoes = [o for t, o in events if t == "#E"]
    busy_end = next((o["busy_end_ms"] for t, o in events if t == "#B"),
                    None)
    done = any(t == "#DONE" for t, _o in events)

    def verdict(name, ok, detail):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
        return ok

    print(f"\n=== probe verdicts (log: {args.log})")
    ok = True
    ok &= verdict("P1 poll registers stdin",
                  any(t == "#P" and o.get("poll_ok")
                      for t, o in events), "board printed #P poll_ok")
    e_g = [e for e in echoes if e["byte"] == ord("g")]
    ok &= verdict("P2 prompt delivery",
                  bool(e_g) and 500 <= e_g[0]["ms"] <= 3000,
                  f"first 'g' echoed at {e_g[0]['ms'] if e_g else '—'} ms "
                  f"(sent at 1000)")
    ok &= verdict("P3 noise byte observed",
                  any(e["byte"] == ord("z") for e in echoes),
                  "'z' echoed, script alive after")
    busy_echo = [e for e in e_g if busy_end and e["ms"] >= busy_end - 100]
    ok &= verdict("P4 buffered across busy window",
                  bool(busy_echo),
                  f"'g' sent at 9500 ms echoed at "
                  f"{busy_echo[0]['ms'] if busy_echo else '—'} ms "
                  f"(busy ended {busy_end} ms)")
    crlf = sorted({e["byte"] for e in echoes if e["byte"] in (10, 13)})
    ok &= verdict("P5 CRLF host→board (measured, informational)",
                  bool(e_g),
                  f"b'g\\r\\n' delivered bytes 103 + {crlf or 'none'} — "
                  f"control bytes are single chars, so noise-tolerance "
                  f"covers whatever arrives")
    ok &= verdict("P6 clean end on 'q'", done, "#DONE seen")
    print(f"\n{'PROBE PASS' if ok else 'PROBE FAIL'} — "
          f"{len(echoes)} bytes echoed total")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
