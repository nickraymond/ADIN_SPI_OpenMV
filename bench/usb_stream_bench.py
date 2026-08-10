#!/usr/bin/env python3
"""usb_stream_bench.py — measure the AE3->Pi USB JPEG stream (S3 TODO 1).

Runs the framed-JPEG stream for N seconds at each candidate setting, then prints a
table: fps, KB/frame, sustained Mbps, sequence gaps, JPEG integrity — and writes one
sample frame per mode so a human can open the artifact (trust artifacts, not exit
codes). Verdict column checks the SPEC ≤ 8 Mbps T1L video budget per mode; the
actual S3 setting is chosen from this table and recorded in DESIGN.md.

Run on the Pi the AE3 is plugged into:

    python3 bench/usb_stream_bench.py                      # default matrix, 10 s/mode
    python3 bench/usb_stream_bench.py --seconds 30
    python3 bench/usb_stream_bench.py --modes VGA:50,HD:50 --out /tmp/usb_bench
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi", "stream"))
from usb_frame_source import (UsbFrameSource, find_openmv_port,  # noqa: E402
                              has_jpeg_eoi, looks_like_jpeg)

BUDGET_MBPS = 8.0  # SPEC §Link + stream budget: ≤ 8 Mbps sustained on the T1L
DEFAULT_MODES = "QVGA:50,VGA:50,VGA:70,HD:50"


def parse_modes(spec):
    """'VGA:50,HD:70' -> [('VGA', 50), ('HD', 70)]. Raises ValueError on junk."""
    modes = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        framesize, _, quality = part.partition(":")
        if not framesize or not quality:
            raise ValueError("bad mode %r (want FRAMESIZE:QUALITY, e.g. VGA:50)" % part)
        modes.append((framesize.upper(), int(quality)))
    if not modes:
        raise ValueError("no modes in %r" % spec)
    return modes


def summarize(seqs, sizes, t_first, t_last):
    """Pure stats over one run: fps/Mbps use first->last frame wall time."""
    n = len(seqs)
    if n == 0:
        return {"frames": 0, "dropped": 0, "fps": 0.0, "mbps": 0.0, "kb_avg": 0.0}
    span = max(t_last - t_first, 1e-9)
    expected = max(seqs) - min(seqs) + 1
    return {
        "frames": n,
        "dropped": expected - len(set(seqs)),
        "fps": (n - 1) / span if n > 1 else 0.0,
        "mbps": sum(sizes[1:]) * 8 / span / 1e6 if n > 1 else 0.0,
        "kb_avg": sum(sizes) / n / 1024.0,
    }


def run_mode(port, framesize, quality, seconds, outdir):
    """Stream one mode for ``seconds``; return (stats, sample_path, bad_jpeg)."""
    bad_jpeg = 0
    seqs, sizes = [], []
    t_first = t_last = None
    sample_path = os.path.join(outdir, "sample_%s_q%d.jpg" % (framesize, quality))
    src = UsbFrameSource(port, framesize=framesize, jpeg_quality=quality,
                         max_seconds=seconds + 60)
    with src:
        t_start = time.monotonic()
        for frame in src.frames():
            now = time.monotonic()
            if not (looks_like_jpeg(frame.data) and has_jpeg_eoi(frame.data)):
                bad_jpeg += 1
            elif not seqs:
                with open(sample_path, "wb") as f:
                    f.write(frame.data)
            seqs.append(frame.seq)
            sizes.append(len(frame.data))
            t_first = t_first if t_first is not None else now
            t_last = now
            if now - t_start >= seconds:
                break
    stats = summarize(seqs, sizes, t_first or 0.0, t_last or 0.0)
    return stats, sample_path, bad_jpeg


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="auto", help="serial port (default: by-id glob)")
    ap.add_argument("--seconds", type=float, default=10.0, help="seconds per mode")
    ap.add_argument("--modes", default=DEFAULT_MODES, help="FRAMESIZE:QUALITY[,...]")
    ap.add_argument("--out", default="/tmp/usb_stream_bench", help="artifact dir")
    args = ap.parse_args()

    modes = parse_modes(args.modes)
    port = find_openmv_port() if args.port == "auto" else args.port
    os.makedirs(args.out, exist_ok=True)
    print("AE3 USB stream bench — port %s, %.0f s/mode, budget %.1f Mbps"
          % (port, args.seconds, BUDGET_MBPS))

    rows, failures = [], 0
    for framesize, quality in modes:
        label = "%s q%d" % (framesize, quality)
        print("... %s" % label, flush=True)
        try:
            stats, sample, bad_jpeg = run_mode(port, framesize, quality,
                                               args.seconds, args.out)
        except (TimeoutError, RuntimeError) as exc:
            print("!! %s: %s" % (label, exc))
            failures += 1
            continue
        ok = (stats["frames"] > 0 and bad_jpeg == 0 and stats["dropped"] == 0
              and os.path.getsize(sample) > 0)
        if not ok:
            failures += 1
        verdict = "PASS" if (ok and stats["mbps"] <= BUDGET_MBPS) else "FAIL"
        rows.append((label, stats, bad_jpeg, sample, verdict))

    print("\n%-10s %7s %8s %9s %8s %7s %8s  %s"
          % ("mode", "frames", "fps", "KB/frame", "Mbps", "gaps", "bad-jpg", "verdict"))
    for label, s, bad_jpeg, sample, verdict in rows:
        print("%-10s %7d %8.1f %9.1f %8.2f %7d %8d  %s"
              % (label, s["frames"], s["fps"], s["kb_avg"], s["mbps"],
                 s["dropped"], bad_jpeg, verdict))
    print("\nsample frames (open them — that's the artifact check):")
    for label, _s, _b, sample, _v in rows:
        size = os.path.getsize(sample) if os.path.exists(sample) else 0
        print("  %-10s %s (%d bytes)" % (label, sample, size))
    if failures:
        print("\n%d mode(s) FAILED integrity/stream checks" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
