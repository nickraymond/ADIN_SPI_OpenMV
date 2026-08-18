#!/usr/bin/env python3
# s22_ceiling_rows.py -- S22 bite 1 nibble 3: drive the stream rows the
# flood bug blocked, one at a time, with ledger deltas and mute
# detection. Runs ON nereus001 next to bench_ctl.py (the control
# socket); row isolation = stop + settle + snapshot, every wait polls
# status (keep-alive + liveness), and the verdict for a row is the
# RECEIVER ledger delta -- never the exit code (CLAUDE.md rule 4).
#
# Usage: python3 s22_ceiling_rows.py row1 [row2 ...]
#        python3 s22_ceiling_rows.py all

import json
import subprocess
import sys
import time

BENCH_CTL = ["/home/pi/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh"]

# (name, mbps, fps, secs, q, res, pf) -- mbps generous so fps is the knob.
ROWS = {
    "hd50-capture": None,                              # capture control row
    "qvga-mono-30": (4.0, 30, 60, 50, "qvga", "mono"),  # broke run 1 pre-fix
    "vga-mono-15": (4.0, 15, 60, 50, "vga", "mono"),    # broke run 3 pre-fix
    "vga-color-15": (4.0, 15, 60, 50, "vga", "color"),  # past old 10fps cap
    "qvga-color-10min": (4.0, 30, 600, 50, "qvga", "color"),  # the demo line
    "vga-color-10min": (4.0, 15, 600, 50, "vga", "color"),    # new ceiling
    "hd-mono-6": (4.0, 6, 60, 50, "hd", "mono"),   # Nick's HD target probe
}
ORDER = ["hd50-capture", "qvga-mono-30", "vga-mono-15", "vga-color-15",
         "qvga-color-10min", "vga-color-10min"]


def ctl(*args):
    out = subprocess.run(BENCH_CTL + list(args), capture_output=True,
                         text=True, timeout=30)
    try:
        return json.loads(out.stdout)
    except Exception:
        return {"raw": out.stdout[-200:], "rc": out.returncode}


def snap():
    d = ctl("status")
    led = d.get("ledger", {})
    cam = d.get("cam_reply", {})
    return {"frames_ok": led.get("frames_ok", 0), "gaps": led.get("gaps", 0),
            "dropped": led.get("dropped", 0),
            "ingest_ok": led.get("ingest_ok", 0),
            "bytes_ok": led.get("bytes_ok", 0),
            "cam_state": cam.get("state"), "pub_ok": cam.get("pub_ok", 0),
            "pub_errs": cam.get("pub_errs", 0),
            "pub_bytes": cam.get("pub_bytes", 0)}


def wait_row(secs):
    """Poll status every 10 s for the row's duration; two consecutive
    dead polls = the mute. Returns (survived, polls_failed)."""
    fails = 0
    t0 = time.time()
    while time.time() - t0 < secs + 8:
        time.sleep(10)
        d = ctl("status")
        if "ledger" not in d:
            fails += 1
            print("  POLL FAILED #%d at t=%ds: %s"
                  % (fails, int(time.time() - t0), d), flush=True)
            if fails >= 2:
                return False, fails
        else:
            cam = d.get("cam_reply", {})
            led = d.get("ledger", {})
            print("  t=%4ds cam=%s fps=%.2f frames=%d gaps=%d drop=%d"
                  % (time.time() - t0, cam.get("state"),
                     led.get("fps", 0.0), led.get("frames_ok", 0),
                     led.get("gaps", 0), led.get("dropped", 0)), flush=True)
            if cam.get("state") == "timeout":
                fails += 1
                if fails >= 2:
                    return False, fails
            else:
                fails = 0
    return True, fails


def run_row(name):
    print("== ROW %s ==" % name, flush=True)
    before = snap()
    if name == "hd50-capture":
        r = ctl("capture", "50", "hd", "mono")
        print("  cmd: %s" % r.get("accepted", r), flush=True)
        time.sleep(25)
    else:
        mbps, fps, secs, q, res, pf = ROWS[name]
        r = ctl("stream", str(mbps), str(fps), str(secs), str(q), res, pf)
        print("  cmd: %s" % r.get("accepted", r), flush=True)
        ok, fails = wait_row(secs)
        if not ok:
            print("  ROW %s: NODE MUTE (poll fails=%d)" % (name, fails),
                  flush=True)
    ctl("stop")
    time.sleep(8)
    after = snap()
    d = {k: after[k] - before[k] for k in
         ("frames_ok", "gaps", "dropped", "pub_ok", "pub_errs", "pub_bytes",
          "bytes_ok")}
    dur = ROWS[name][2] if ROWS[name] else 0
    fps = d["frames_ok"] / float(dur) if dur else 0.0
    mbps = d["bytes_ok"] * 8 / 1e6 / dur if dur else 0.0
    print("  DELTA %s: frames_ok=%d (%.2f fps) gaps=%d dropped=%d "
          "pub_ok=%d pub_errs=%d bytes_ok=%d (%.2f Mbps) cam=%s"
          % (name, d["frames_ok"], fps, d["gaps"], d["dropped"], d["pub_ok"],
             d["pub_errs"], d["bytes_ok"], mbps, after["cam_state"]),
          flush=True)
    verdict = "CLEAN" if (d["gaps"] == 0 and d["dropped"] == 0 and
                          d["pub_errs"] == 0 and
                          after["cam_state"] == "ok") else "NOT CLEAN"
    print("  VERDICT %s: %s" % (name, verdict), flush=True)
    return verdict


def main():
    want = sys.argv[1:]
    names = ORDER if want == ["all"] else want
    results = {}
    for n in names:
        if n not in ROWS:
            print("unknown row %s" % n)
            continue
        results[n] = run_row(n)
        time.sleep(12)          # inter-row settle
    print("== SUMMARY ==")
    for n, v in results.items():
        print("  %-18s %s" % (n, v))


if __name__ == "__main__":
    main()
