#!/usr/bin/env python3
"""hil_review_run.py -- S8 bite E5: the one-click closed-loop HIL review.

The workbench recipe's argv (cards s8-hil-review-nano / -tiny). Owns
BOTH children of a review session:

  1. starts playback_server.py (the screen half) and waits for :8091
     to answer,
  2. checks hil-lcd.service is active (the closed loop stalls at its
     render-ack without the LCD client),
  3. starts hil_harness.py --closed-loop --review against both boards,
  4. on Stop (SIGINT/SIGTERM from the workbench runner) tears down in
     the ONE order that is safe: harness FIRST (its clean path quits
     the boards, releases the serial ports and scores what was
     collected -- and it needs playback still alive to do it), THEN
     playback.

Each child runs in its OWN process group: the runner signals the
wrapper's group on Stop, and if the children shared it, playback would
die in the same instant as the harness and break the harness's
teardown. The wrapper is the only conductor of the order.

Escalation for a hung child is SIGINT -> grace -> SIGTERM -> grace ->
give up LOUDLY, naming the pid and the manual command -- NEVER SIGKILL
(measured 2026-08-20: force-killing a board holder can take the board
off the USB bus; physical replug required). The exit code says what
happened: 0 clean, 1 a child died on its own, 2 a child needed the
SIGTERM escalation, 3 a child survived both signals (stranded, named).
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
BY_ID_DIR = "/dev/serial/by-id"
LCD_UNIT = "hil-lcd.service"

# Board identities (SPEC §Board identity on nereus000) -- the names are
# backwards from the guess: the "Pyboard" IS the N6. A host test pins
# these against the recipe cards' [[boards]] so they cannot drift apart.
BOARDS = {
    "N6": "usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_"
          "020023000450433547373200-if00",
    "AE3": "usb-OpenMV_OpenMV_Camera_0829c14000000000-if00",
}


class Child:
    """One supervised child in its own process group."""

    def __init__(self, name, argv):
        self.name = name
        self.argv = argv
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        # child lines land in the wrapper's stdout (= the runner's log
        # tail), prefixed so the two streams stay tellable-apart
        for raw in self.proc.stdout:
            sys.stdout.write("[%s] %s"
                             % (self.name, raw.decode("utf-8", "replace")))
            sys.stdout.flush()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def rc(self):
        return None if self.proc is None else self.proc.poll()

    def send(self, sig):
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, sig)   # own session -> pgid == pid
        except OSError:
            try:
                self.proc.send_signal(sig)
            except OSError:
                pass

    def wait_gone(self, secs, poll=0.1):
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            if not self.alive():
                return True
            time.sleep(poll)
        return not self.alive()


class Supervisor:
    """Start playback -> wait for it -> start harness; unwind in reverse."""

    def __init__(self, playback_argv, harness_argv, playback_url,
                 playback_wait=30.0, harness_grace_int=25.0,
                 harness_grace_term=5.0, playback_grace_int=5.0,
                 playback_grace_term=3.0, poll=0.2):
        self.playback = Child("playback", playback_argv)
        self.harness = Child("harness", harness_argv)
        self.playback_url = playback_url
        self.playback_wait = playback_wait
        self.harness_grace_int = harness_grace_int
        self.harness_grace_term = harness_grace_term
        self.playback_grace_int = playback_grace_int
        self.playback_grace_term = playback_grace_term
        self.poll = poll
        self._stop = threading.Event()

    # the signal handler only sets a flag; teardown runs exactly once,
    # from run() -- so a second Stop click cannot land a second SIGINT
    # on a harness that is mid-scoring
    def _on_signal(self, signum, _frame):
        print("wrapper: got %s -- ordered teardown (harness first)"
              % signal.Signals(signum).name, flush=True)
        self.request_stop()

    def request_stop(self):
        self._stop.set()

    def install_signals(self):
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _playback_answers(self):
        try:
            with urllib.request.urlopen(self.playback_url, timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    def _stop_child(self, child, grace_int, grace_term):
        """SIGINT -> SIGTERM -> named strand. Returns worst rc (0/2/3)."""
        if not child.alive():
            return 0
        child.send(signal.SIGINT)
        if child.wait_gone(grace_int):
            print("wrapper: %s stopped cleanly" % child.name, flush=True)
            return 0
        print("wrapper: %s ignored SIGINT for %.0f s -- escalating SIGTERM"
              % (child.name, grace_int), flush=True)
        child.send(signal.SIGTERM)
        if child.wait_gone(grace_term):
            return 2
        print("wrapper: %s (pid %d) SURVIVED SIGINT AND SIGTERM -- leaving "
              "it (never SIGKILL: it can take a board off the USB bus). "
              "Inspect with:  ps -o pid,cmd -g %d ; stop it by hand."
              % (child.name, child.proc.pid, child.proc.pid), flush=True)
        return 3

    def _teardown(self):
        rc_h = self._stop_child(self.harness, self.harness_grace_int,
                                self.harness_grace_term)
        rc_p = self._stop_child(self.playback, self.playback_grace_int,
                                self.playback_grace_term)
        return max(rc_h, rc_p)

    def run(self):
        """Returns the wrapper's exit code."""
        self.playback.start()
        print("wrapper: playback started (pid %d); waiting for %s"
              % (self.playback.proc.pid, self.playback_url), flush=True)
        deadline = time.monotonic() + self.playback_wait
        while not self._playback_answers():
            if self._stop.is_set():
                return self._teardown()
            if not self.playback.alive():
                print("wrapper: playback died during startup (rc=%s) -- "
                      "see its lines above" % self.playback.rc(), flush=True)
                return 1
            if time.monotonic() > deadline:
                print("wrapper: playback never answered %s within %.0f s "
                      "-- stopping it" % (self.playback_url,
                                          self.playback_wait), flush=True)
                return max(1, self._teardown())
            time.sleep(self.poll)

        self.harness.start()
        print("wrapper: harness started (pid %d)" % self.harness.proc.pid,
              flush=True)
        while not self._stop.is_set():
            if not self.harness.alive():
                rc = self.harness.rc()
                print("wrapper: harness exited rc=%s -- stopping playback"
                      % rc, flush=True)
                stop_rc = self._teardown()   # harness gone; playback leg
                return stop_rc if rc == 0 else max(1, stop_rc)
            if not self.playback.alive():
                print("wrapper: playback DIED mid-run (rc=%s) -- ending "
                      "the harness cleanly, scoring what was collected"
                      % self.playback.rc(), flush=True)
                return max(1, self._teardown())
            time.sleep(self.poll)
        return self._teardown()


def lcd_active(unit=LCD_UNIT):
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "--value"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="nano", choices=("nano", "tiny"),
                    help="which deployed model to review (the card's UI "
                         "toggle lands here as --model <choice>)")
    ap.add_argument("--phases", default=None,
                    help="explicit harness phase list; overrides --model "
                         "(manual use only)")
    ap.add_argument("--board", action="append",
                    metavar="LABEL=BY_ID_NAME",
                    help="override the default two boards (by-id name, "
                         "not a path)")
    ap.add_argument("--min-gt-px", default="30")
    ap.add_argument("--framesize", default="VGA", choices=("VGA", "HD"))
    ap.add_argument("--out-base", default="~/hil_runs")
    ap.add_argument("--playback-port", type=int, default=8091)
    ap.add_argument("--skip-lcd-check", action="store_true",
                    help="dev only: run without the hil-lcd render-ack "
                         "service check")
    args = ap.parse_args(argv)

    if not args.skip_lcd_check and not lcd_active():
        raise SystemExit(
            "FAIL: %s is not active -- the closed loop waits forever for "
            "its render-ack. Fix:  sudo systemctl start %s   (then click "
            "Start again)" % (LCD_UNIT, LCD_UNIT))

    boards = args.board or ["%s=%s" % (lb, name)
                            for lb, name in BOARDS.items()]
    phases = args.phases or ("%s-tiled" % args.model)
    out_dir = os.path.join(os.path.expanduser(args.out_base),
                           "review_" + time.strftime("%Y%m%d_%H%M%S"))
    playback_argv = [sys.executable, "-u",
                     os.path.join(_HERE, "playback_server.py"),
                     "--port", str(args.playback_port)]
    harness_argv = [sys.executable, "-u",
                    os.path.join(_HERE, "hil_harness.py"),
                    "--closed-loop", "--review",
                    "--playback", "http://127.0.0.1:%d" % args.playback_port,
                    "--phases", phases,
                    "--framesize", args.framesize,
                    "--min-gt-px", str(args.min_gt_px),
                    "--out", out_dir]
    for spec in boards:
        label, name = spec.split("=", 1)
        dev = name if name.startswith("/") else os.path.join(BY_ID_DIR, name)
        harness_argv += ["--board", "%s=%s" % (label, dev)]

    print("wrapper: phases=%s framesize=%s out=%s"
          % (phases, args.framesize, out_dir), flush=True)
    sup = Supervisor(playback_argv, harness_argv,
                     "http://127.0.0.1:%d/api/state" % args.playback_port)
    sup.install_signals()
    rc = sup.run()
    print("wrapper: done rc=%d (artifacts: %s)" % (rc, out_dir), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
