#!/usr/bin/env python3
"""S28 bite 1 — locked-burst capture, host side. Runs ON nereus000.

ONE serial attach for the whole plan (bite-R attach budget); the board
runs s28_board_burst.py from RAM (never written to the board) and is
command-driven over stdin. Frames land as raw .bin + meta.jsonl rows
under --out; s28_burst_stats.py turns a run dir into verdicts.

Stages (--plan full | quick | pwm):
  calib   fresh per-run screen->camera calibration (Nick moved the
          bench, 2026-09-01): black frame + 9-marker frame at HD gray,
          solve_cam_map (E11) -> calib_AE3.json + diag printed. A
          missing marker FAILS LOUDLY naming the dark cell — that IS
          the aim check.
  bursts  per pixformat x framesize: converge (autos on, sampled) ->
          lock -> paced burst; plus one tight (in-heap, true
          back-to-back) BAYER VGA burst.
  expo    exposure-range table: commanded vs readback across framerates
          (bite-3's feasibility gate).
  bracket +0/+2/+3 EV shutter-only rungs at lowered fps, BAYER.
  pwm     LCD flicker sweep: short manual exposures on the uniform gray
          still. With --no-lcd the LCD stages are skipped (real-object
          control run: aim the camera at a static object instead).

Bench rules: by-id port only; check the workbench is idle first
(http://nereus000:8088) — this tool does not fight for the port, it
fails. AE3 settle discipline applies after any prior stream.
"""
import argparse
import base64
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "hil"))
sys.path.insert(0, os.path.join(_ROOT, "bench"))

import numpy as np                                   # noqa: E402
from PIL import Image                                # noqa: E402

from s28_session import (BurstSession, gray_plane, lock_verdict)  # noqa: E402
from s28_patch_card import STILL_CARD, STILL_GRAY    # noqa: E402

BOARD_SCRIPT = os.path.join(_HERE, "s28_board_burst.py")

# The default matrix. BAYER leads (the linear domain the sprint is
# about); RGB565 rides for the deployed-path comparison.
FULL_MATRIX = [("BAYER", "VGA"), ("RGB565", "VGA"),
               ("BAYER", "HD"), ("RGB565", "HD")]
QUICK_MATRIX = [("BAYER", "VGA"), ("RGB565", "VGA")]
EXPO_FPS = [30, 10, 5, 2, 1]
EXPO_TARGETS = [5000, 10000, 20000, 50000, 100000, 200000,
                400000, 800000, 990000]
PWM_EXPOSURES = [500, 1000, 2000, 5000, 10000]


# --------------------------------------------------------------- plumbing
def workbench_idle_or_die(url):
    """One owner per board port, ever: refuse to attach while a
    workbench demo is live. Down/unreachable workbench = a warning
    (the service may legitimately be stopped), never a silent pass."""
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/runner",
                                    timeout=5) as r:
            st = json.loads(r.read())
    except Exception as e:
        print("WARN: workbench unreachable (%s) — verify no demo owns "
              "the boards before continuing" % e)
        return
    state = st.get("state", "?")
    if state not in ("idle", "?"):
        raise SystemExit(
            "FAIL: workbench runner is %r (recipe %s) — stop it from "
            "the page first, never around it" % (state, st.get("recipe")))
    settle = int(st.get("settle_s", 0) or 0)
    if settle > 0:
        raise SystemExit(
            "FAIL: boards are in the post-stop settle window (%d s left)"
            " — attaching now risks the raw-repl refusal; wait it out"
            % settle)


def attach(port, script_text, log):
    """SerialBoard attach with ONE bounded retry (start_stream's measured
    pattern; TransportError is not OSError so catch broadly)."""
    from n6_stream_host import SerialBoard
    try:
        return SerialBoard(port).start(script_text)
    except Exception as e:
        log("first attach refused (%s) — one retry once the port "
            "settles" % e)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not os.path.exists(port):
            time.sleep(0.5)
        time.sleep(5)
        return SerialBoard(port).start(script_text)


def lcd_show(pb, timeout_s=10, **kw):
    """Set playback state and wait for the LCD client's render ack
    (POST /api/shown) — the E4 rule: acked-set is not shown-yet."""
    snap = pb.set(**kw)
    seq = snap["seq"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pb.state().get("shown_seq", 0) >= seq:
            return
        time.sleep(0.2)
    raise SystemExit("FAIL: LCD never acked seq %d in %ds — is "
                     "hil-lcd running against this playback?"
                     % (seq, timeout_s))


class Run:
    def __init__(self, sess, pb, out_dir):
        self.sess = sess
        self.pb = pb
        self.out = out_dir
        os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)
        self.meta_fh = open(os.path.join(out_dir, "meta.jsonl"), "a")
        self.log_fh = open(os.path.join(out_dir, "events.log"), "a")
        self.expo_fh = None

    def log(self, msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print("  " + msg)
        self.log_fh.write(line + "\n")
        self.log_fh.flush()

    def _one(self, tag, replies, op):
        hits = [obj for t, obj in replies if t == tag]
        if not hits:
            raise SystemExit("FAIL: op %s returned no %s (got %s)"
                             % (op, tag, [t for t, _ in replies]))
        return hits[0]

    def cfg(self, pixformat, framesize, fps=30):
        replies = self.sess.command(op="cfg", pixformat=pixformat,
                                    framesize=framesize, fps=fps)
        ok = self._one("ok", replies, "cfg")
        self.log("cfg %s %s fps=%d -> %dx%d free=%d"
                 % (pixformat, framesize, fps, ok["w"], ok["h"],
                    ok["mem_free"]))
        return ok

    def converge(self, secs=6):
        replies = self.sess.command(op="conv", secs=secs,
                                    timeout_s=secs + 30)
        conv = self._one("conv", replies, "conv")
        rows = conv["rows"]
        tail = rows[-3:]
        exp_spread = max(r[1] for r in tail) - min(r[1] for r in tail)
        self.log("converge %ds: exposure %s us (last-3 spread %d us), "
                 "gain %s dB" % (secs, tail[-1][1], exp_spread,
                                 tail[-1][2]))
        if exp_spread > 0.05 * max(tail[-1][1], 1):
            self.log("WARN: AE still moving at lock time — scene or "
                     "lighting unstable")
        return rows

    def lock(self):
        lk = self._one("lock", self.sess.command(op="lock"), "lock")
        self.log("locked: exp=%d us gain=%.2f dB wb=%s"
                 % (lk["exp_us"], lk["gain_db"], lk["rgb_gain_db"]))
        return lk

    def manual(self, exposure_us, fps=None, gain_db=None):
        cmd = {"op": "manual", "exposure_us": int(exposure_us)}
        if fps is not None:
            cmd["fps"] = fps
        if gain_db is not None:
            cmd["gain_db"] = gain_db
        lk = self._one("lock", self.sess.command(**cmd), "manual")
        self.log("manual: asked %d us (fps=%s) -> got %d us"
                 % (exposure_us, fps, lk["exp_us"]))
        return lk

    def burst(self, stage, geom, n, mode="paced", timeout_s=300):
        """Run one burst; frames -> frames/<stage>/fNNN.bin + meta rows.
        -> list of meta rows (with '_data' stripped)."""
        sdir = os.path.join(self.out, "frames", stage)
        os.makedirs(sdir, exist_ok=True)
        replies = self.sess.command(op="burst", n=n, mode=mode,
                                    timeout_s=timeout_s)
        frames = [obj for t, obj in replies if t == "frame"]
        rows = []
        for fr in frames:
            fname = "f%03d.bin" % fr["seq"]
            with open(os.path.join(sdir, fname), "wb") as fh:
                fh.write(fr["_data"])
            row = {k: v for k, v in fr.items() if not k.startswith("_")}
            row.update({"stage": stage, "file": "frames/%s/%s"
                        % (stage, fname), "host_t": time.time(),
                        "w": geom["w"], "h": geom["h"],
                        "pixformat": geom["pixformat"]})
            self.meta_fh.write(json.dumps(row) + "\n")
            rows.append(row)
        self.meta_fh.flush()
        ok, detail = lock_verdict(rows)
        self.log("burst %s n=%d/%d %s: lock %s exp=%s gaps(ms)=%s"
                 % (stage, len(frames), n, mode,
                    "HELD" if ok else "LEAKED " + json.dumps(detail),
                    detail.get("exp_us"), detail.get("gaps_ms")))
        return rows

    def close(self):
        self.meta_fh.close()
        self.log_fh.close()


# ----------------------------------------------------------------- stages
def stage_calib(run, cam_label):
    """Black + markers frames at HD GRAYSCALE -> CamMap (E11 solver).
    The fresh-per-run calibration IS the moved-bench answer."""
    from hil_harness import find_markers, solve_cam_map
    geom = run.cfg("GRAYSCALE", "HD", fps=30)
    # Converge + lock ON THE MARKER PATTERN, then shoot markers AND
    # black under the SAME lock — converging on black slams AE to max
    # exposure and blooms the marker blobs (centroid bias).
    lcd_show(run.pb, mode="calib")
    run.converge(3)
    run.lock()
    mark = run.burst("calib_markers", dict(geom, pixformat="GRAYSCALE"), 1)
    lcd_show(run.pb, mode="black")
    time.sleep(1.0)
    black = run.burst("calib_black", dict(geom, pixformat="GRAYSCALE"), 1)

    def _load(row):
        p = os.path.join(run.out, row["file"])
        return gray_plane(open(p, "rb").read(), row["w"], row["h"])

    cents = find_markers(_load(mark[0]), _load(black[0]))
    M, diag = solve_cam_map(run.pb.markers, cents, geom["w"], geom["h"])
    with open(os.path.join(run.out, "calib_%s.json" % cam_label),
              "w") as fh:
        json.dump(dict(M.to_dict(), cam_w=geom["w"], cam_h=geom["h"],
                       diag=diag), fh)
    img = _load(mark[0]).astype(np.uint8)
    Image.fromarray(img, "L").save(
        os.path.join(run.out, "calib_%s.png" % cam_label))
    run.log("calib solved: %s" % json.dumps(diag))
    return M


def stage_bursts(run, matrix, n, tight_n):
    lcd_show(run.pb, mode="step", still=STILL_CARD)
    for pf, fs in matrix:
        geom = run.cfg(pf, fs, fps=30)
        run.converge()
        run.lock()
        run.burst("card_%s_%s" % (pf.lower(), fs.lower()), geom, n)
    if tight_n:
        geom = run.cfg("BAYER", "VGA", fps=30)
        run.converge()
        run.lock()
        run.burst("card_bayer_vga_tight", geom, tight_n, mode="tight")


def stage_expo(run):
    run.cfg("BAYER", "VGA", fps=30)
    rows = []
    for fps in EXPO_FPS:
        replies = run.sess.command(op="expo_probe", fps=fps,
                                   targets=EXPO_TARGETS, timeout_s=60)
        rows += [obj for t, obj in replies if t == "table"]
        run.log("expo fps=%d: max readback %d us"
                % (fps, max(r["got"] for r in rows if r["fps"] == fps)))
    with open(os.path.join(run.out, "expo_rows.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def stage_bracket(run, has_lcd):
    if has_lcd:
        lcd_show(run.pb, mode="step", still=STILL_CARD)
    geom = run.cfg("BAYER", "VGA", fps=30)
    run.converge()
    lk = run.lock()
    base = max(lk["exp_us"], 200)
    for ev, mult in ((0, 1), (2, 4), (3, 8)):
        want = base * mult
        # frame time must exceed exposure + margin (bite-0 clamp);
        # integer fps floor, and never faster than the sensor allows
        fps = max(1, min(30, int(1e6 // (want + 5000))))
        run.manual(want, fps=fps, gain_db=lk["gain_db"])
        run.burst("bracket_ev%d" % ev, geom, 3)


def stage_smoke(run):
    """Minimal first-contact: no LCD, no calib, no HD, no GRAYSCALE —
    just prove attach + stdin round-trip + cfg + converge + lock + a
    small BAYER VGA burst land frames with settings HELD. The one-
    variable-at-a-time first rung before anything else is trusted."""
    geom = run.cfg("BAYER", "VGA", fps=30)
    run.converge(4)
    run.lock()
    run.burst("smoke_bayer_vga", geom, 4)


def stage_pwm(run, still, scene_name):
    if still is not None:
        lcd_show(run.pb, mode="step", still=still)
    geom = run.cfg("BAYER", "VGA", fps=30)
    run.converge()
    lk = run.lock()
    for exp in PWM_EXPOSURES:
        run.manual(exp, fps=30, gain_db=lk["gain_db"])
        run.burst("pwm_%s_e%05d" % (scene_name, exp), geom, 8)


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True,
                    help="board serial port — ALWAYS /dev/serial/by-id/…")
    ap.add_argument("--label", default="AE3",
                    help="board label for calib_<label>.json")
    ap.add_argument("--playback", default="http://127.0.0.1:8091")
    ap.add_argument("--out", required=True, help="run dir (created)")
    ap.add_argument("--plan", choices=("smoke", "full", "quick", "pwm"),
                    default="full",
                    help="smoke = no LCD/calib/HD/gray, just prove the "
                         "core capture+lock path first")
    ap.add_argument("--n", type=int, default=16, help="paced burst size")
    ap.add_argument("--tight-n", type=int, default=6)
    ap.add_argument("--no-lcd", action="store_true",
                    help="scene is NOT the LCD (real-object control run):"
                         " skips playback, calib, and card stages")
    ap.add_argument("--scene", default="lcd",
                    help="scene note recorded in run.json (lighting is a"
                         " measured condition — say what it was)")
    ap.add_argument("--skip-calib", action="store_true")
    ap.add_argument("--workbench", default="http://127.0.0.1:8088",
                    help="workbench URL for the idle preflight; "
                         "'none' skips it (bench rules still apply)")
    args = ap.parse_args()

    if args.workbench != "none":
        workbench_idle_or_die(args.workbench)

    out_dir = os.path.expanduser(args.out)
    os.makedirs(out_dir, exist_ok=True)

    pb = None
    need_lcd = not args.no_lcd and args.plan != "smoke"
    if need_lcd:
        from hil_harness import Playback
        pb = Playback(args.playback)
        if pb.n_stills < 2:
            raise SystemExit(
                "FAIL: playback at %s serves %d stills — start it with "
                "the S28 media dir (python3 pi/s28/s28_patch_card.py; "
                "playback_server.py --media ~/s28_media)"
                % (args.playback, pb.n_stills))

    script = open(BOARD_SCRIPT).read()
    print("attaching %s" % args.port)
    sb = attach(args.port, script, print)
    sess = BurstSession(sb, sb.ser.write)
    run = Run(sess, pb, out_dir)
    try:
        info = sess.wait_info(timeout_s=30)
        run.log("board up: %s (%dx%d, free %d)"
                % (info.get("fw", "?"), info["w"], info["h"],
                   info["mem_free"]))
        with open(os.path.join(out_dir, "run.json"), "w") as fh:
            json.dump({"args": vars(args), "board": info,
                       "t0": time.time()}, fh)

        if args.plan == "smoke":
            stage_smoke(run)
        elif args.no_lcd:
            # real-object control: PWM-shaped sweep on whatever static
            # scene the camera is aimed at
            stage_pwm(run, None, "real")
        elif args.plan == "pwm":
            stage_pwm(run, STILL_GRAY, "lcd")
        else:
            if not args.skip_calib:
                stage_calib(run, args.label)
            matrix = FULL_MATRIX if args.plan == "full" else QUICK_MATRIX
            stage_bursts(run, matrix, args.n, args.tight_n)
            stage_expo(run)
            stage_bracket(run, has_lcd=True)
            stage_pwm(run, STILL_GRAY, "lcd")

        sess.quit()
        run.log("run complete; skips=%d resends=%d"
                % (sess.skips, sess.resends))
        print("\nscore it:  python3 pi/s28/s28_burst_stats.py --run %s"
              % out_dir)
    finally:
        run.close()
        sb.stop()


if __name__ == "__main__":
    main()
