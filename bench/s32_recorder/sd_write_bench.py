#!/usr/bin/env python3
"""S32 bite 0 -- can this Pi's SD card sustain an HD MJPEG recording?

Measures what the recorder will actually do: append ~364 KB frames to one file
through the page cache, for long enough that the cache fills and the card's
real sustained write rate is what you see. Reports per-second throughput, the
slowest second, and write() stall latencies -- a stall longer than the pump's
buffer is a dropped frame.

Two phases:
  1. dd oflag=direct  -- raw card sequential write, bypassing the cache.
  2. python append    -- the recorder's shape: buffered writes, N GB, then
                         fsync, with per-call latency histogram.

Run ON the Pi:  python3 sd_write_bench.py --gb 6 --out sd_write.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time


def dd_direct(path, mib=1024):
    cmd = ["dd", "if=/dev/zero", "of=%s" % path, "bs=4M",
           "count=%d" % (mib // 4), "oflag=direct", "conv=fsync"]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.monotonic() - t0
    size = os.path.getsize(path) if os.path.exists(path) else 0
    os.remove(path) if os.path.exists(path) else None
    m = re.search(r"([\d.]+) (MB|GB)/s", r.stderr)
    return {"cmd": " ".join(cmd), "rc": r.returncode, "bytes": size,
            "wall_s": round(wall, 2),
            "MBps_measured": round(size / wall / 1e6, 2) if wall else None,
            "dd_reported": m.group(0) if m else r.stderr.strip()[-200:]}


def meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1]) * 1024
    return None


def append_bench(path, gb, frame_bytes, rate_fps=None):
    """Append frames until gb GB written. rate_fps paces like a recorder."""
    frame = bytearray(os.urandom(frame_bytes))     # incompressible
    total = int(gb * 1e9)
    n_frames = total // frame_bytes
    lat = []                                        # per-write seconds
    per_sec = []                                    # bytes per wall second
    written = 0
    sec_start = time.monotonic()
    sec_bytes = 0
    t0 = time.monotonic()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "wb", buffering=0) as f:
            for i in range(n_frames):
                # rotate the buffer one byte per frame so no two frames match
                frame[0] = (frame[0] + 1) & 0xFF
                w0 = time.monotonic()
                f.write(frame)
                w1 = time.monotonic()
                lat.append(w1 - w0)
                written += frame_bytes
                sec_bytes += frame_bytes
                if w1 - sec_start >= 1.0:
                    per_sec.append(sec_bytes / (w1 - sec_start))
                    sec_start = w1
                    sec_bytes = 0
                if rate_fps:
                    due = t0 + (i + 1) / rate_fps
                    now = time.monotonic()
                    if due > now:
                        time.sleep(due - now)
            dirty_before_fsync = meminfo("Dirty")
            s0 = time.monotonic()
            os.fsync(f.fileno())
            fsync_s = time.monotonic() - s0
    finally:
        wall = time.monotonic() - t0
    size = os.path.getsize(path)
    os.remove(path)
    lat.sort()
    def pct(p):
        return lat[min(len(lat) - 1, int(p * len(lat)))]
    return {
        "frame_bytes": frame_bytes, "frames": n_frames, "bytes": size,
        "paced_fps": rate_fps,
        "wall_s": round(wall, 2), "fsync_s": round(fsync_s, 2),
        "dirty_bytes_before_fsync": dirty_before_fsync,
        "MBps_mean_incl_fsync": round(size / wall / 1e6, 2),
        "MBps_mean_writes_only": round(size / (wall - fsync_s) / 1e6, 2),
        "per_sec_MBps_min": round(min(per_sec) / 1e6, 2) if per_sec else None,
        "per_sec_MBps_p10": round(sorted(per_sec)[len(per_sec) // 10] / 1e6, 2) if per_sec else None,
        "per_sec_MBps_median": round(sorted(per_sec)[len(per_sec) // 2] / 1e6, 2) if per_sec else None,
        "write_ms_p50": round(pct(0.50) * 1e3, 2),
        "write_ms_p99": round(pct(0.99) * 1e3, 2),
        "write_ms_max": round(lat[-1] * 1e3, 1),
        "stalls_gt_100ms": sum(1 for x in lat if x > 0.1),
        "stalls_gt_500ms": sum(1 for x in lat if x > 0.5),
        "stalls_gt_1s": sum(1 for x in lat if x > 1.0),
        "per_sec_MBps_trace": [round(x / 1e6, 1) for x in per_sec],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/s32_bite0"))
    ap.add_argument("--gb", type=float, default=6.0)
    ap.add_argument("--frame-bytes", type=int, default=364 * 1024)
    ap.add_argument("--paced-fps", type=float, default=0.0,
                    help="0 = as fast as the card takes it")
    ap.add_argument("--skip-dd", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    st = os.statvfs(a.dir)
    res = {"host": os.uname().nodename, "dir": a.dir,
           "free_gb_before": round(st.f_bavail * st.f_frsize / 1e9, 1),
           "when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if st.f_bavail * st.f_frsize < (a.gb + 2) * 1e9:
        sys.exit("FAIL: not enough free space for a %.1f GB test" % a.gb)
    if not a.skip_dd:
        res["dd_direct_1GiB"] = dd_direct(os.path.join(a.dir, "dd_direct.bin"))
        print("dd direct:", json.dumps(res["dd_direct_1GiB"]), flush=True)
    res["append"] = append_bench(os.path.join(a.dir, "append.bin"), a.gb,
                                 a.frame_bytes, a.paced_fps or None)
    trace = res["append"].pop("per_sec_MBps_trace")
    print("append:", json.dumps(res["append"]), flush=True)
    res["append"]["per_sec_MBps_trace"] = trace
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
