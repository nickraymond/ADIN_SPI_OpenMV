#!/usr/bin/env python3
# pi/bm_bench/repro_attach.py -- S23 bite R step-1 reproducer.
#
# Run ON nereus000 (tmux/nohup -- the Mac never touches the port):
#   python3 ~/ADIN_SPI_OpenMV/pi/bm_bench/repro_attach.py
# Stop gracefully:  touch ~/repro_attach.stop
#
# Drives scripted cold-boot -> bridge-lifecycle -> attach-ladder cycles
# and classifies every mpremote refusal against the bridge state
# machine BEFORE counting it as anomalous. The two states this bite
# hunts (TRACKER bite R):
#   state 1: raw-repl refusal that persists through a properly-armed
#            45 s quiet-exit window (the v3 demo_up silent-fail).
#   state 2: a linked boot whose VCP RX is dead (bytes sent, bridge
#            never sees them -> it sits in phase 1 to its own timeout).
#
# State machine (sources: bm_bridge.py PHASE1_TIMEOUT_MS/QUIET_EXIT_MS,
# demo_up.sh mpr discipline, DEV_LOG 2026-08-19 evening):
#   reset -> launcher boots the bridge -> phase 1 (holds VCP, kbd_intr
#   off, waits for bytes; exits alone after 600 s) -> our one arming
#   attach fails but its bytes link the bridge -> 30 s of silence ->
#   quiet-exit -> rp.stop -> launcher returns -> REPL. From there every
#   serialized attach MUST land. A refusal is EXPLAINED only by: the
#   arming touch itself, the armed 45 s window, or the boot race.
#
# Verdict table (each refusal gets exactly one):
#   explained-arm        arming touch refused -- that is its job
#   degenerate-no-bridge arming touch SUCCEEDED -- bridge never ran
#   explained-late-exit  ladder refusal, 45 s + one retry landed
#   state2-candidate     retry failed inside the phase-1 horizon
#   state2-reproduced    ...and the post-horizon attach landed with a
#                        "phase 1 timeout" trace = RX-dead boot proven
#   true-state1          retry failed past every legal window
#   usb-drop             device error / by-id gone (ae3-usb-unstick)
#
# On any true anomaly: STOP EVERYTHING. The wedge is the specimen --
# no auto-recovery, no uhubctl, snapshot the Pi-side USB state and
# hand off (exit 3). Exit 0 = all cycles clean; 2 = bad preflight.

import json
import os
import subprocess
import sys
import time

PORT = os.environ.get(
    "REPRO_PORT",
    "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00")
REPO = os.environ.get(
    "REPRO_REPO", os.path.expanduser("~/ADIN_SPI_OpenMV"))
LOG_DIR = os.environ.get(
    "REPRO_LOG_DIR", os.path.expanduser("~/repro_attach_logs"))
STOP_FILE = os.path.expanduser("~/repro_attach.stop")

# Everything in seconds. Sources: BOOT_WAIT = the recorded bring-up
# recipe (mpremote reset -> 95 s); PHASE1/QUIET mirror bm_bridge.py
# (test_repro_attach.py pins the agreement); ARMED_WINDOW = demo_up's
# proven 45 s (> QUIET_EXIT 30 s with margin).
BOOT_WAIT_S = 95
PHASE1_TIMEOUT_S = 600
QUIET_EXIT_S = 30
ARMED_WINDOW_S = 45
ATTACH_TIMEOUT_S = 30
LADDER_SPACING_S = 10
LADDER_ATTACHES = int(os.environ.get("REPRO_LADDER", "8"))
MAX_CYCLES = int(os.environ.get("REPRO_CYCLES", "8"))
MAX_WALL_S = int(os.environ.get("REPRO_WALL", "5400"))

# Test hook: divides every sleep and timeout. 1 on the bench.
TIME_SCALE = float(os.environ.get("REPRO_TIME_SCALE", "1"))

# The one place the recovery recipe is spelled out; the code itself
# never runs it (preserve-the-wedge rule -- test-pinned).
RECOVERY_HINT = ("board preserved WEDGED for post-mortem. When ready: "
                 "sudo uhubctl -l 3 -p 1 -a cycle -d 3, then 5 min of "
                 "zero port contact, then read /flash/bridge_trace.txt "
                 "+ /flash/boot_report.txt via one demo_up-style attach")

TRACE_TAIL_CODE = """
import os
for p in ("/flash/bridge_trace.txt", "/flash/bridge_crash.txt",
          "/flash/boot_report.txt"):
    try:
        n = os.stat(p)[6]
        f = open(p, "rb")
        if n > 4000:
            f.seek(n - 4000)
        print("==== %s (%d bytes) ====" % (p, n))
        print(f.read().decode())
        f.close()
    except OSError:
        print("==== %s MISSING ====" % p)
"""

PREFLIGHT_CODE = """
import os, hashlib
h = hashlib.sha256()
h.update(open("/flash/main.py", "rb").read())
print("MAIN:" + h.digest().hex()[:16])
print("BOOTREP:" + ("yes" if "boot_report.py" in os.listdir("/flash")
                    else "no"))
"""


class _Clock:
    """Logical bench time. Sleeps advance it by their FULL logical
    value even when TIME_SCALE compresses the real wait (tests); all
    other elapsed real time counts 1:1. At scale 1 this is exactly
    wall clock -- the state-machine horizons stay honest either way."""

    def __init__(self):
        self._extra = 0.0

    def sleep(self, s):
        time.sleep(s / TIME_SCALE)
        self._extra += s - s / TIME_SCALE

    def now(self):
        return time.time() + self._extra


CLOCK = _Clock()


def _sleep(s):
    CLOCK.sleep(s)


def lost_race(rc, err):
    """demo_up's _mpr_lost_race, verbatim semantics: a timeout or the
    raw-repl signature means the attach lost to a live bridge (and its
    own bytes armed the quiet-exit)."""
    if rc == 124:
        return True
    return rc != 0 and "could not enter raw repl" in err


def classify_retry_failure(elapsed_since_reset_s):
    """A ladder refusal whose 45 s + retry ALSO failed. Inside the
    phase-1 horizon a live-but-RX-dead bridge still legally holds the
    port; past it nothing legal can."""
    horizon = BOOT_WAIT_S + PHASE1_TIMEOUT_S + QUIET_EXIT_S
    if elapsed_since_reset_s < horizon:
        return "state2-candidate"
    return "true-state1"


def exit_kind_from_trace(text):
    """Which exit the bridge took, from its own trace (CRLF-normalized
    -- the documented mpremote transport trap)."""
    text = text.replace("\r", "")
    if "phase 1 timeout" in text:
        return "phase1-timeout"
    if "vcp quiet" in text:
        return "quiet-exit"
    return "unknown"


class Log:
    def __init__(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self.dir = os.path.join(log_dir, stamp)
        os.makedirs(self.dir, exist_ok=True)
        self._log = open(os.path.join(self.dir, "run.log"), "a")
        self._events = open(os.path.join(self.dir, "events.jsonl"), "a")

    def say(self, msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        self._log.write(line + "\n")
        self._log.flush()

    def event(self, kind, **kw):
        kw.update(t=round(time.time(), 3), kind=kind)
        self._events.write(json.dumps(kw) + "\n")
        self._events.flush()

    def blob(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "a") as f:
            f.write(text)
        return path


class Mpr:
    """Serialized mpremote runner -- demo_up's mpr discipline. Every
    call is bounded; rc 124 = hung past the timeout (the forever-hang
    class demo_up measured). No retry loops live here: retries are the
    caller's, because they are state-machine decisions."""

    def __init__(self, log):
        self.log = log

    def run(self, *args, timeout=ATTACH_TIMEOUT_S):
        cmd = ["mpremote", "connect", PORT] + list(args)
        # scaled timeouts keep a 2 s floor so a scaled test run still
        # gives the (fake) tool time to start; scale 1 is untouched
        t = timeout / TIME_SCALE
        if TIME_SCALE != 1:
            t = max(t, 2.0)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=t)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc = 124
            out = (e.stdout or b"").decode(errors="replace") \
                if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = (e.stderr or b"").decode(errors="replace") \
                if isinstance(e.stderr, bytes) else (e.stderr or "")
        except FileNotFoundError:
            self.log.say("FATAL: mpremote not on PATH")
            sys.exit(1)
        out = out.replace("\r\n", "\n")
        err = err.replace("\r\n", "\n")
        self.log.event("mpr", args=list(args), rc=rc,
                       err=err.strip()[:200])
        return rc, out, err


def snapshot(log, tag):
    """Pi-side USB state while the wedge is live -- the 'USB died vs
    SHM corrupt' split's host half. Best-effort: every command's rc is
    recorded, none is required to work."""
    name = "snapshot_%s.txt" % tag
    log.say("snapshot -> %s" % name)
    cmds = [
        ["date"], ["uptime"],
        ["ls", "-l", os.path.dirname(PORT) or "/dev/serial/by-id"],
        ["lsusb"], ["lsusb", "-t"],
        ["lsusb", "-v", "-d", "37c5:16e3"],
        ["dmesg"],
    ]
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=20)
            body = p.stdout + p.stderr
            if cmd == ["dmesg"]:
                body = "\n".join(body.splitlines()[-150:])
            log.blob(name, "$ %s (rc=%d)\n%s\n"
                     % (" ".join(cmd), p.returncode, body))
        except Exception as e:
            log.blob(name, "$ %s FAILED %r\n" % (" ".join(cmd), e))
    # sysfs walk: every USB device, key attrs; endpoints for the AE3.
    try:
        root = "/sys/bus/usb/devices"
        lines = []
        for d in sorted(os.listdir(root)):
            base = os.path.join(root, d)

            def attr(n):
                try:
                    with open(os.path.join(base, n)) as f:
                        return f.read().strip()
                except OSError:
                    return "-"
            vid = attr("idVendor")
            lines.append("%s vid=%s pid=%s speed=%s cfg=%s"
                         % (d, vid, attr("idProduct"), attr("speed"),
                            attr("bConfigurationValue")))
            if vid == "37c5":
                for sub in sorted(os.listdir(root)):
                    if sub.startswith(d + ":"):
                        eps = [e for e in
                               os.listdir(os.path.join(root, sub))
                               if e.startswith("ep_")]
                        lines.append("  iface %s eps=%s" % (sub, eps))
        log.blob(name, "sysfs:\n" + "\n".join(lines) + "\n")
    except Exception as e:
        log.blob(name, "sysfs walk FAILED %r\n" % e)


def settle_by_id(log):
    """After a reset: absent -> present -> hold (demo_up's dance).
    Filesystem polls only -- never port opens."""
    for _ in range(20):
        if not os.path.exists(PORT):
            break
        _sleep(0.5)
    for _ in range(60):
        if os.path.exists(PORT):
            break
        _sleep(0.5)
    if not os.path.exists(PORT):
        return False
    _sleep(3)
    return os.path.exists(PORT)


def pull_traces(mpr, log, cycle):
    rc, out, _ = mpr.run("exec", TRACE_TAIL_CODE)
    if rc == 0:
        path = log.blob("cycle%02d_traces.txt" % cycle, out)
        kind = exit_kind_from_trace(out)
        log.say("cycle %d traces pulled (exit kind: %s) -> %s"
                % (cycle, kind, path))
        return kind
    log.say("cycle %d trace pull FAILED rc=%d" % (cycle, rc))
    return None


def stop_requested():
    return os.path.exists(STOP_FILE)


def preflight(mpr, log):
    """Board must be at a REPL carrying demo_up's staged launcher +
    boot_report. A refused preflight is NOT counted -- its provenance
    is unknown; report and hand off."""
    for p in subprocess.run(["ps", "-eo", "args"], capture_output=True,
                            text=True).stdout.splitlines():
        if "bm_sbc_s15/build/all" in p:
            log.say("FATAL: a bm_sbc app is running -- it owns the tty")
            return False
    if not os.path.exists(PORT):
        log.say("FATAL: AE3 not on USB at %s" % PORT)
        return False
    rc, out, err = mpr.run("exec", PREFLIGHT_CODE)
    if lost_race(rc, err):
        log.say("preflight attach refused -- %d s armed window, one "
                "retry" % ARMED_WINDOW_S)
        _sleep(ARMED_WINDOW_S)
        rc, out, err = mpr.run("exec", PREFLIGHT_CODE)
        if rc != 0:
            log.say("preflight refused twice: board already in an "
                    "unknown state -- NOT counting it (provenance "
                    "unknown). " + RECOVERY_HINT)
            snapshot(log, "preflight")
            return False
    if rc != 0:
        log.say("preflight error rc=%d: %s" % (rc, err.strip()))
        return False
    want = None
    launcher = os.path.join(REPO, "firmware/bm_bridge/main_bridge.py")
    try:
        import hashlib
        h = hashlib.sha256()
        h.update(open(launcher, "rb").read())
        want = h.hexdigest()[:16]
    except OSError:
        log.say("FATAL: %s missing -- repo checkout stale?" % launcher)
        return False
    if ("MAIN:" + want) not in out or "BOOTREP:yes" not in out:
        log.say("FATAL: staged files stale (want MAIN:%s + BOOTREP:yes,"
                " got: %s) -- run demo_up.sh first" % (want, out.strip()))
        return False
    log.say("preflight ok (launcher %s, boot_report staged)" % want)
    return True


def run_cycle(mpr, log, cycle):
    """One reset -> lifecycle -> ladder cycle. Returns a verdict string
    ('clean', or the anomaly that stopped the run)."""
    log.say("== cycle %d: cold boot (mpremote reset) ==" % cycle)
    mpr.run("reset")          # rc unreliable across the reboot; the
    reset_t = CLOCK.now()     # settle dance is the real verdict
    if not settle_by_id(log):
        log.event("verdict", cycle=cycle, verdict="usb-drop",
                  where="post-reset settle")
        log.say("by-id never returned after reset -- usb-drop. "
                + RECOVERY_HINT)
        snapshot(log, "cycle%02d_usbdrop" % cycle)
        return "usb-drop"
    log.say("boot wait %d s (launcher -> bridge phase 1)" % BOOT_WAIT_S)
    _sleep(BOOT_WAIT_S)

    def elapsed():
        return CLOCK.now() - reset_t

    # Arming touch: MUST fail against a live phase-1 bridge.
    rc, _, err = mpr.run("exec", "print('arm')")
    if lost_race(rc, err):
        log.event("verdict", cycle=cycle, verdict="explained-arm")
        log.say("arming touch refused as expected (bridge alive, "
                "quiet-exit armed)")
        bridge_ran = True
    elif rc == 0:
        log.event("verdict", cycle=cycle, verdict="degenerate-no-bridge")
        log.say("arming touch SUCCEEDED -- bridge never ran this boot; "
                "ladder still runs but the cycle is degenerate")
        bridge_ran = False
    else:
        log.event("verdict", cycle=cycle, verdict="usb-drop",
                  where="arm", err=err.strip()[:200])
        log.say("arming touch real error rc=%d: %s" % (rc, err.strip())
                + " -- " + RECOVERY_HINT)
        snapshot(log, "cycle%02d_armerr" % cycle)
        return "usb-drop"
    if bridge_ran:
        log.say("armed window: %d s untouched" % ARMED_WINDOW_S)
        _sleep(ARMED_WINDOW_S)

    # Attach ladder: every serialized attach must now land.
    traces_pulled = False
    for i in range(1, LADDER_ATTACHES + 1):
        if stop_requested():
            log.say("stop file seen -- ending after ladder step")
            return "clean"
        rc, _, err = mpr.run("exec", "print(%d, %d)" % (cycle, i))
        if rc == 0:
            log.event("attach-ok", cycle=cycle, attach=i)
            if not traces_pulled:
                kind = pull_traces(mpr, log, cycle)
                traces_pulled = True
                if bridge_ran and kind == "phase1-timeout":
                    # Cannot legally happen this early in one boot
                    # generation (600 s has not elapsed) -- flag it
                    # loudly, do not stop on it: it is a trace-rotation
                    # or clock oddity, not a proven state 2.
                    log.event("trace-anomaly", cycle=cycle,
                              note="phase1-timeout in a quiet-exit-"
                                   "aged trace")
                    log.say("cycle %d TRACE ANOMALY: phase-1-timeout "
                            "line where only quiet-exit fits -- "
                            "flagged, continuing" % cycle)
            _sleep(LADDER_SPACING_S)
            continue
        if not lost_race(rc, err):
            log.event("verdict", cycle=cycle, verdict="usb-drop",
                      where="ladder", attach=i, err=err.strip()[:200])
            log.say("ladder attach %d real error rc=%d: %s -- %s"
                    % (i, rc, err.strip(), RECOVERY_HINT))
            snapshot(log, "cycle%02d_ladder%02d_err" % (cycle, i))
            return "usb-drop"
        # Raw-repl refusal where the state machine says REPL. Apply
        # the proven protocol once: armed window, ONE retry.
        log.say("ladder attach %d REFUSED (rc=%d) -- %d s armed "
                "window, one retry" % (i, rc, ARMED_WINDOW_S))
        _sleep(ARMED_WINDOW_S)
        rc2, _, err2 = mpr.run("exec", "print(%d, %d)" % (cycle, i))
        if rc2 == 0:
            log.event("verdict", cycle=cycle, attach=i,
                      verdict="explained-late-exit")
            log.say("retry landed -- explained-late-exit (bridge exit "
                    "was slow); ladder continues")
            _sleep(LADDER_SPACING_S)
            continue
        verdict = classify_retry_failure(elapsed())
        log.event("verdict", cycle=cycle, attach=i, verdict=verdict,
                  elapsed_s=round(elapsed()))
        if verdict == "state2-candidate":
            # A live-but-RX-dead bridge legally holds the port until
            # its own phase-1 timeout. Wait the horizon out, then ONE
            # discriminating attach.
            wait = (BOOT_WAIT_S + PHASE1_TIMEOUT_S + 60) - elapsed()
            log.say("state2-candidate: waiting out the phase-1 "
                    "horizon (%d s) for the discriminator" % max(0, wait))
            if wait > 0:
                _sleep(wait)
            rc3, _, err3 = mpr.run("exec", "print('post-horizon')")
            if rc3 == 0:
                kind = pull_traces(mpr, log, cycle)
                verdict = ("state2-reproduced"
                           if kind == "phase1-timeout"
                           else "late-recovery-unexplained")
                log.event("verdict", cycle=cycle, verdict=verdict,
                          via="post-horizon", exit_kind=kind or "?")
                log.say("post-horizon attach landed, trace exit=%s -> %s"
                        % (kind, verdict))
            else:
                verdict = "true-state1"
                log.event("verdict", cycle=cycle, verdict=verdict,
                          via="post-horizon-refused")
                log.say("refusal SURVIVED the phase-1 horizon -- "
                        "true-state1")
        if verdict == "true-state1":
            log.say("TRUE ANOMALY reproduced at cycle %d attach %d "
                    "(%d attaches-to-refusal). %s"
                    % (cycle, i, i - 1, RECOVERY_HINT))
        snapshot(log, "cycle%02d_%s" % (cycle, verdict))
        return verdict
    log.event("cycle-clean", cycle=cycle, attaches=LADDER_ATTACHES,
              degenerate=not bridge_ran)
    log.say("cycle %d CLEAN (%d attaches landed)"
            % (cycle, LADDER_ATTACHES))
    return "clean"


def main():
    log = Log(LOG_DIR)
    log.say("repro_attach start: port=%s cycles=%d ladder=%d scale=%g"
            % (PORT, MAX_CYCLES, LADDER_ATTACHES, TIME_SCALE))
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    mpr = Mpr(log)
    if not preflight(mpr, log):
        return 2
    t0 = CLOCK.now()
    verdicts = []
    for cycle in range(1, MAX_CYCLES + 1):
        if stop_requested():
            log.say("stop file seen -- done")
            break
        if CLOCK.now() - t0 > MAX_WALL_S:
            log.say("wall-clock budget spent -- done")
            break
        v = run_cycle(mpr, log, cycle)
        verdicts.append(v)
        if v not in ("clean",):
            log.say("run STOPPED on '%s' -- specimen preserved, logs in "
                    "%s" % (v, log.dir))
            log.event("run-end", verdicts=verdicts, stopped_on=v)
            return 3
    log.say("run complete, no true anomaly: %s" % verdicts)
    log.event("run-end", verdicts=verdicts, stopped_on=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
