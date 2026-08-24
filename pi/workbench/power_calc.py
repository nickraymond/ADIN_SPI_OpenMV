#!/usr/bin/env python3
"""mJ/inference from a power_log.py JSONL + a stamped probe log.

The probe prints PWR_MARK <label>_start / <label>_end lines; piping it
through stamp_lines.py puts host unix time in column 1, the same clock
power_log.py stamps its rows with. This tool integrates channel power
over each marked window and subtracts the idle baseline.

    power_calc.py --log ~/bench_logs/power/power_X.jsonl --ch 1 \
        --run probe_run.log --n 30 [--idle-s 10]

For each <label> it prints: window s, mean mW (load), idle mW baseline
(mean over --idle-s seconds ending 1 s before the window), net mJ per
inference ((load-idle) * window / n), and gross mJ per inference.
"""
import argparse
import json
import re
import sys

MARK = re.compile(r"^(\d+\.\d+)\s+PWR_MARK\s+(\w+)_(start|end)\b")


def load_rows(path, ch):
    rows = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ch") == ch:
                rows.append((r["ts"], r["mW"]))
    if not rows:
        sys.exit("power_calc: no rows for ch=%d in %s" % (ch, path))
    return rows


def mean_mw(rows, t0, t1):
    sel = [mw for ts, mw in rows if t0 <= ts <= t1]
    return (sum(sel) / len(sel), len(sel)) if sel else (None, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--ch", type=int, required=True)
    ap.add_argument("--run", required=True, help="stamped probe output")
    ap.add_argument("--n", type=int, required=True, help="inferences per window")
    ap.add_argument("--idle-s", type=float, default=10.0)
    args = ap.parse_args()

    rows = load_rows(args.log, args.ch)
    marks = {}
    with open(args.run) as f:
        for line in f:
            m = MARK.match(line)
            if m:
                marks.setdefault(m.group(2), {})[m.group(3)] = float(m.group(1))

    if not marks:
        sys.exit("power_calc: no PWR_MARK lines in %s (stamped?)" % args.run)

    for label, w in sorted(marks.items()):
        if "start" not in w or "end" not in w:
            print("%s: incomplete window %r -- skipped" % (label, w))
            continue
        t0, t1 = w["start"], w["end"]
        load, n_load = mean_mw(rows, t0, t1)
        idle, n_idle = mean_mw(rows, t0 - 1.0 - args.idle_s, t0 - 1.0)
        if load is None:
            print("%s: no power rows in window %.3f..%.3f" % (label, t0, t1))
            continue
        dur = t1 - t0
        gross = load * dur / args.n
        line = "%s: %.2f s window, load %.1f mW (%d rows)" % (label, dur, load, n_load)
        if idle is not None:
            net = (load - idle) * dur / args.n
            line += ", idle %.1f mW (%d rows), net %.2f mJ/inf" % (idle, n_idle, net)
        line += ", gross %.2f mJ/inf" % gross
        print(line)


if __name__ == "__main__":
    main()
