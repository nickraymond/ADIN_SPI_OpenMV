#!/usr/bin/env python3
"""Drive n6_h264_probe.py on the N6 and record the result. S31 bench leg.

Runs from the Pi that owns the board (or from the Mac over a local port).
Answers: what does one frame actually cost in MJPEG vs hardware H.264, on the
same scene, on the same board, in the same session.

Intended shape of the session -- the before/after is the whole point:

    # 1. BEFORE flashing, on the firmware already on the board:
    n6_h264_run.py --port <by-id> --out ~/h264_doe/before.json
    #    -> MJPEG cells only (no codec module). This is the leg-0 control.

    # 2. Flash per firmware/openmv_build/N6_H264_FLASH.md.

    # 3. AFTER flashing, SAME SCENE, camera untouched:
    n6_h264_run.py --port <by-id> --out ~/h264_doe/after.json
    #    -> MJPEG cells (must reproduce step 1) + H.264 cells.

    # 4. Compare:
    n6_h264_run.py --compare ~/h264_doe/before.json ~/h264_doe/after.json

Bench discipline this enforces, because the rule has been broken before:
one owner per board port. It refuses to start while the workbench reports a
demo running, and it never kills a foreign port holder -- it names it and
stops.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "n6_h264_probe.py")

DEFAULT_PLAN = {
    "frames": 90,
    "sizes": ["QVGA", "VGA", "HD"],
    # Reconcile with S30's ladder before quoting a ratio against its table.
    "mjpeg_quality": [30, 70, 90],
    "h264_quality": [30, 70, 90],
    "h264_bitrate": [1000000],
    "keyframe_interval": 30,
}


def preflight(workbench):
    """Refuse to touch a port the workbench is driving."""
    if not workbench:
        print("WARN: --workbench not given; skipping the runner check. "
              "Two owners on one port wedges the board.", file=sys.stderr)
        return
    url = workbench.rstrip("/") + "/api/runner"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            state = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SystemExit(
            "FAIL: could not reach the workbench runner at %s (%s).\n"
            "      Check it by hand before opening the port -- do not guess." % (url, e)
        )
    running = state.get("running") or state.get("recipe") or state.get("demo")
    if running:
        raise SystemExit(
            "FAIL: the workbench reports a demo running (%r).\n"
            "      Stop it FROM THE PAGE, never by killing its process, then\n"
            "      wait out the 35 s settle window before retrying." % (running,)
        )
    print("preflight: workbench runner idle")


def build_script(plan):
    """probe source + a call, so nothing is written to the board's /flash."""
    with open(PROBE) as f:
        src = f.read()
    return src + "\n\nrun(%s)\n" % json.dumps(plan)


def drive(port, plan, timeout, mpremote="mpremote"):
    script = build_script(plan)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(script)
        path = tf.name
    try:
        cmd = [mpremote, "connect", port, "run", path]
        print("running: %s" % " ".join(cmd))
        # ONE attempt. Retrying against a board that refused the REPL is how
        # this bench wedges boards -- contact restarts the quiet-exit timer.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    finally:
        os.unlink(path)

    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            "FAIL: mpremote exited %d. Do NOT retry immediately -- if it "
            "refused the raw REPL, give the port 35 s of silence first."
            % proc.returncode
        )
    return proc.stdout


def parse(stdout):
    info, results, errors = [], [], []
    for line in stdout.splitlines():
        line = line.strip()
        for tag, sink in (("RESULT ", results), ("INFO ", info), ("ERROR ", errors)):
            if line.startswith(tag):
                try:
                    sink.append(json.loads(line[len(tag):]))
                except ValueError:
                    errors.append({"unparsed": line})
                break
    return info, results, errors


def show(results, errors):
    if not results:
        print("no cells returned")
    hdr = "%-6s %-6s %5s  %10s  %9s  %9s  %s"
    print(hdr % ("codec", "size", "q", "B/frame", "enc ms", "fps", "true-motion"))
    for r in results:
        print(hdr % (
            r["codec"], r["size"],
            "-" if r["quality"] is None else r["quality"],
            "%d" % int(r["bytes_per_frame"]),
            "%.2f" % r["encode_ms_per_frame"],
            "%.1f" % r["achieved_fps"],
            "yes" if r["true_motion"] else "NO -- ratio invalid",
        ))
    for e in errors:
        print("ERROR: %s" % json.dumps(e))


def compare(before_path, after_path):
    before = json.load(open(before_path))
    after = json.load(open(after_path))

    def index(doc, codec):
        return {(r["size"], r["quality"]): r
                for r in doc["results"] if r["codec"] == codec}

    mj_b, mj_a = index(before, "mjpeg"), index(after, "mjpeg")
    h264 = index(after, "h264")

    print("== leg 0: MJPEG regression across the firmware change ==")
    print("   (if these do not reproduce, every H.264 number below is contaminated)")
    for k in sorted(set(mj_b) & set(mj_a), key=str):
        b, a = mj_b[k]["bytes_per_frame"], mj_a[k]["bytes_per_frame"]
        drift = 100.0 * (a - b) / b if b else 0.0
        flag = "" if abs(drift) < 5.0 else "   <-- CHECK"
        print("   %-6s q%-3s  %8.0f -> %8.0f B/frame  (%+.1f%%)%s"
              % (k[0], k[1], b, a, drift, flag))

    print()
    print("== leg 1: MJPEG vs hardware H.264, same scene, same nominal quality ==")
    print("   NOMINAL quality is not perceptual parity. A defensible ratio needs")
    print("   the clips decoded and paired by eye; this is the first cut.")
    for k in sorted(set(mj_a) & set(h264), key=str):
        m, h = mj_a[k], h264[k]
        if not (m["true_motion"] and h["true_motion"]):
            print("   %-6s q%-3s  SKIPPED -- loop did not sustain 25 fps, so the "
                  "frames are not true-motion" % (k[0], k[1]))
            continue
        ratio = m["bytes_per_frame"] / h["bytes_per_frame"]
        print("   %-6s q%-3s  MJPEG %8.0f B/f  ->  H.264 %8.0f B/f   = %.2fx"
              % (k[0], k[1], m["bytes_per_frame"], h["bytes_per_frame"], ratio))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="/dev/serial/by-id/... for the N6. NEVER ttyACM<n>.")
    ap.add_argument("--out", help="write results JSON here")
    ap.add_argument("--workbench", default="http://nereus000:8088",
                    help="workbench base URL for the single-owner check")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the runner check (only when the workbench is not up)")
    ap.add_argument("--frames", type=int, default=DEFAULT_PLAN["frames"])
    ap.add_argument("--sizes", default=",".join(DEFAULT_PLAN["sizes"]))
    ap.add_argument("--timeout", type=int, default=900)
    # Debian 13 is PEP 668, so the bench Pis keep mpremote in a venv at ~/mpv.
    # Bare python3 cannot import it and --break-system-packages is not the fix.
    ap.add_argument("--mpremote", default=os.environ.get("MPREMOTE", "mpremote"),
                    help="path to mpremote (bench Pis: ~/mpv/bin/mpremote)")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--plan", help="JSON file overriding the default cell plan")
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)

    if not args.port:
        ap.error("--port is required (or use --compare)")
    if "by-id" not in args.port:
        ap.error("refusing a non-by-id port: ttyACM<n> is enumeration order, "
                 "and this bench has two OpenMV boards on one bus")

    if not args.no_preflight:
        preflight(args.workbench)

    plan = dict(DEFAULT_PLAN)
    if args.plan:
        with open(args.plan) as f:
            plan.update(json.load(f))
    else:
        plan["frames"] = args.frames
        plan["sizes"] = [s.strip() for s in args.sizes.split(",") if s.strip()]

    stdout = drive(args.port, plan, args.timeout, args.mpremote)
    info, results, errors = parse(stdout)

    doc = {"port": args.port, "plan": plan, "info": info,
           "results": results, "errors": errors}
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(doc, f, indent=2)
        print("wrote %s (%d cells, %d errors)" % (args.out, len(results), len(errors)))

    for i in info:
        print("INFO %s" % json.dumps(i))
    show(results, errors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
