#!/usr/bin/env python3
"""s14_relay_counter.py -- Pi end of the S14 relay bench (BENCHSPEC V16).

Talks to firmware/bm_bridge/s14_relay_pump.py running on the AE3 (deployed
as /flash/main.py), drives one rung, decodes the uart_l2 stream with the
SAME codec module the pump used to encode it, and prints a ledger +
verdict. The receiver's ledger is the truth (D21 philosophy).

Usage (on nereus000):
    python3 s14_relay_counter.py --rung B --secs 10
    python3 s14_relay_counter.py --rung C --secs 60 --agg 3
    python3 s14_relay_counter.py --rung C --secs 600 --gate 2.0   # rung D
    python3 s14_relay_counter.py --rung C --secs 60 --crc z       # rung E
    python3 s14_relay_counter.py --quit    # ask the service to exit to REPL

Gate semantics (--gate MBPS, the V16 rung-D run): PASS iff measured L2
throughput >= gate, 0 CRC/decode errors, 0 seq gaps, 0 source gaps/drops,
not aborted, and the pump's own frame count matches ours.
"""

import argparse
import glob
import json
import struct
import sys
import time
from os import path

# uart_codec lives next to this script when deployed to the Pi, or in
# firmware/bm_bridge when run from the repo checkout.
_here = path.dirname(path.abspath(__file__))
for _cand in (_here, path.join(_here, "..", "firmware", "bm_bridge")):
    if path.exists(path.join(_cand, "uart_codec.py")):
        sys.path.insert(0, _cand)
        break
import uart_codec as uc  # noqa: E402

PORT_GLOB = "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*-if00"
BANNER = b"S14-PUMP ready"


def find_port(explicit=None):
    if explicit:
        return explicit
    hits = sorted(glob.glob(PORT_GLOB))
    if not hits:
        raise SystemExit("no AE3 at %s -- is the board plugged in?" % PORT_GLOB)
    return hits[0]


def crc_fn_for(mode):
    if mode == "z":
        from binascii import crc32
        return lambda mv: crc32(mv) & 0xFFFFFFFF
    if mode == "n":
        return lambda mv: 0
    return None


def wait_banner(ser, timeout=15.0):
    """Drain text until the pump's banner (it re-prints after each rung)."""
    buf = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if BANNER in buf:
                return True
        else:
            time.sleep(0.02)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rung", choices=["B", "C"], default="B")
    ap.add_argument("--secs", type=int, default=10)
    ap.add_argument("--unit", type=int, default=480)
    ap.add_argument("--agg", type=int, default=1)
    ap.add_argument("--crc", choices=["c", "z", "n"], default="c")
    ap.add_argument("--gate", type=float, default=None,
                    help="Mbps gate: turns this run into a PASS/FAIL verdict")
    ap.add_argument("--port", default=None)
    ap.add_argument("--quit", action="store_true",
                    help="ask the pump service to exit to the REPL")
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial missing -- sudo apt install python3-serial")

    port = find_port(args.port)
    ser = serial.Serial(port, 115200, timeout=0.2)  # baud ignored on CDC
    print("port   : %s" % port)

    if not wait_banner(ser):
        raise SystemExit(
            "no pump banner. Is main_s14.py deployed + board rebooted? "
            "(firmware/bm_bridge/README.md §Deploy; check /flash/s14_crash.txt)")

    # Handshake: a bare newline makes the pump's readline return an empty
    # line and re-print its banner immediately -- proving the READ loop is
    # live (a boot-buffered banner alone proves nothing; found live when
    # first-rung-after-boot configs vanished).
    ser.reset_input_buffer()
    ser.write(b"\n")
    ser.flush()
    if not wait_banner(ser, timeout=6.0):
        raise SystemExit("banner seen but read-loop handshake failed -- "
                         "service not consuming stdin; warm-reset the board "
                         "and check /flash/s14_trace.txt")

    if args.quit:
        ser.write(b'{"rung":"Q"}\n')
        ser.flush()
        print("sent quit; service exits to REPL.")
        return 0

    cfg = {"rung": args.rung, "secs": args.secs, "unit": args.unit,
           "agg": args.agg, "crc": args.crc}
    ser.write((json.dumps(cfg) + "\n").encode())
    ser.flush()
    print("config : %s" % json.dumps(cfg))

    splitter = uc.StreamSplitter(crc_fn_for(args.crc))
    frames = 0
    l2_bytes = 0
    wire_bytes = 0
    seq_gaps = 0
    last_seq = -1
    summary = None
    t_first = None
    # Text preceding the leading delimiter (banner + CFG echo) decodes as
    # exactly one structural error on every run; only errors AFTER the
    # first valid frame indicate stream corruption.
    errs_at_first_frame = None
    deadline = time.time() + args.secs + 30     # generous rung watchdog

    while time.time() < deadline:
        chunk = ser.read(16384)
        if not chunk:
            continue
        if t_first is None:
            t_first = time.time()
        wire_bytes += len(chunk)
        for l2 in splitter.feed(chunk):
            if l2.startswith(b"S14END"):
                summary = json.loads(l2[6:].decode())
                break
            if l2.startswith(b"S14F") and len(l2) >= 8:
                if errs_at_first_frame is None:
                    errs_at_first_frame = splitter.errors
                seq = struct.unpack_from("<I", l2, 4)[0]
                if last_seq >= 0 and seq != last_seq + 1:
                    seq_gaps += 1
                last_seq = seq
                frames += 1
                l2_bytes += len(l2)
        if summary is not None:
            break
    t_last = time.time()

    if summary is None:
        raise SystemExit("no S14END summary before watchdog -- rung hung? "
                         "(decode errors so far: %d)" % splitter.errors)

    el = summary["secs"] if summary.get("secs") else (t_last - (t_first or t_last))
    mbps_l2 = l2_bytes * 8 / el / 1e6 if el else 0.0
    mbps_wire = wire_bytes * 8 / el / 1e6 if el else 0.0

    print()
    print("rung %s summary (pump): %s" % (args.rung, json.dumps(summary)))
    print("receiver ledger:")
    print("  frames          : %d (pump sent %d)" % (frames, summary["frames"]))
    print("  l2 bytes        : %d  -> %.3f Mbps over %.1f s" % (l2_bytes, mbps_l2, el))
    print("  wire bytes      : %d  -> %.3f Mbps (framing overhead %.1f%%)"
          % (wire_bytes, mbps_wire,
             100.0 * (wire_bytes - l2_bytes) / l2_bytes if l2_bytes else 0))
    stream_errs = splitter.errors - (errs_at_first_frame or 0)
    print("  decode/crc errs : %d in-stream (%d incl. pre-stream text)"
          % (stream_errs, splitter.errors))
    print("  seq gaps        : %d" % seq_gaps)
    if args.rung == "C":
        print("  rpmsg src       : %d msgs, %d gaps, %d queue drops"
              % (summary.get("src_msgs", -1), summary.get("src_gaps", -1),
                 summary.get("q_drops", -1)))

    if args.gate is not None:
        ok = (mbps_l2 >= args.gate
              and stream_errs == 0
              and seq_gaps == 0
              and not summary.get("aborted")
              and frames == summary["frames"]
              and summary.get("src_gaps", 0) == 0
              and summary.get("q_drops", 0) == 0)
        print()
        print("V16 GATE (%.1f Mbps, %d s): %s"
              % (args.gate, args.secs, "PASS" if ok else "FAIL"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
