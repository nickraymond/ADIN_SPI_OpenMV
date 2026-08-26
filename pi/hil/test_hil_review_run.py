"""Host tests for hil_review_run.py (S8 bite E5) -- fake children only.

The wrapper's whole job is ORDER: playback up before the harness starts,
harness down (clean path complete) before playback is touched. Both
orders are pinned here with fake child processes that log every signal
with a wall-clock stamp, so a regression shows up as a timestamp
inversion, not a bench wedge.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_review_run                                   # noqa: E402
from hil_review_run import Supervisor                   # noqa: E402

# fake harness: logs start/sigint/sigterm/exit; modes --
#   clean  exits 0 on SIGINT (the real harness's happy path)
#   term   ignores SIGINT, exits on SIGTERM (escalation succeeds)
#   hang   ignores both (strand; self-exits after 8 s as test hygiene)
#   die    exits rc=7 on its own after 0.4 s (crash propagation)
FAKE_HARNESS = r"""
import signal, sys, time
log = open(sys.argv[1], "a", buffering=1)
mode = sys.argv[2]
def w(ev): log.write("%s %.6f\n" % (ev, time.time()))
def on_int(s, f):
    w("sigint")
    if mode == "clean":
        w("exit"); sys.exit(0)
def on_term(s, f):
    w("sigterm")
    if mode in ("clean", "term"):
        w("exit"); sys.exit(0)
signal.signal(signal.SIGINT, on_int)
signal.signal(signal.SIGTERM, on_term)
w("start")
end = time.time() + (0.4 if mode == "die" else 8)
while time.time() < end:
    time.sleep(0.05)
w("selfexit")
sys.exit(7 if mode == "die" else 9)
"""

# fake playback: binds the given port and answers 200; modes --
#   serve  serves until signalled (clean exit on SIGINT/SIGTERM)
#   slow   0.6 s delay before binding (start-order pin)
#   die    serves, then dies rc=5 on its own after 0.7 s
#   never  never binds (startup-timeout pin)
FAKE_PLAYBACK = r"""
import signal, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
log = open(sys.argv[1], "a", buffering=1)
port, mode = int(sys.argv[2]), sys.argv[3]
def w(ev): log.write("%s %.6f\n" % (ev, time.time()))
def bye(s, f):
    w("sigint" if s == signal.SIGINT else "sigterm")
    w("exit"); sys.exit(0)
signal.signal(signal.SIGINT, bye)
signal.signal(signal.SIGTERM, bye)
w("start")
if mode == "never":
    time.sleep(8); sys.exit(9)
if mode == "slow":
    time.sleep(0.6)
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
w("serving")
end = time.time() + (0.7 if mode == "die" else 8)
while time.time() < end:
    time.sleep(0.05)
w("selfexit")
sys.exit(5)
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestSupervisor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hilrev_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for name, src in (("fake_harness.py", FAKE_HARNESS),
                          ("fake_playback.py", FAKE_PLAYBACK)):
            with open(os.path.join(self.dir, name), "w") as fh:
                fh.write(src)
        self.hlog = os.path.join(self.dir, "harness.log")
        self.plog = os.path.join(self.dir, "playback.log")
        self.port = free_port()

    def sup(self, pmode="serve", hmode="clean", **over):
        kw = dict(playback_wait=5.0, harness_grace_int=1.0,
                  harness_grace_term=1.0, playback_grace_int=1.0,
                  playback_grace_term=1.0, poll=0.05)
        kw.update(over)
        s = Supervisor(
            [sys.executable, os.path.join(self.dir, "fake_playback.py"),
             self.plog, str(self.port), pmode],
            [sys.executable, os.path.join(self.dir, "fake_harness.py"),
             self.hlog, hmode],
            "http://127.0.0.1:%d/" % self.port, **kw)
        self.addCleanup(self._reap, s)
        return s

    def _reap(self, s):
        # Test hygiene only -- the product never SIGKILLs.
        for child in (s.harness, s.playback):
            if child.proc is not None:
                try:
                    os.killpg(child.proc.pid, signal.SIGKILL)
                except OSError:
                    pass

    def run_sup(self, s):
        result = {}
        t = threading.Thread(target=lambda: result.update(rc=s.run()),
                             daemon=True)
        t.start()
        return t, result

    def finish(self, t, result, timeout=15):
        t.join(timeout)
        self.assertFalse(t.is_alive(), "supervisor never returned")
        return result["rc"]

    def events(self, path):
        """[(event, t), ...] from a fake child's log."""
        try:
            lines = open(path).read().splitlines()
        except OSError:
            return []
        return [(ln.split()[0], float(ln.split()[1])) for ln in lines]

    def t_of(self, path, ev):
        for e, t in self.events(path):
            if e == ev:
                return t
        self.fail("event %r never logged in %s: %r"
                  % (ev, os.path.basename(path), self.events(path)))

    def wait_event(self, path, ev, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(e == ev for e, _t in self.events(path)):
                return
            time.sleep(0.02)
        self.fail("event %r never appeared in %s" % (ev, path))

    # -- the two orders ---------------------------------------------------
    def test_harness_starts_only_after_playback_answers(self):
        s = self.sup(pmode="slow")
        t, res = self.run_sup(s)
        self.wait_event(self.hlog, "start")
        s.request_stop()
        self.assertEqual(self.finish(t, res), 0)
        self.assertLess(self.t_of(self.plog, "serving"),
                        self.t_of(self.hlog, "start"))

    def test_stop_tears_down_harness_first_then_playback(self):
        s = self.sup()
        t, res = self.run_sup(s)
        self.wait_event(self.hlog, "start")
        s.request_stop()
        self.assertEqual(self.finish(t, res), 0)
        # the PINNED order: harness fully gone before playback is signalled
        self.assertLess(self.t_of(self.hlog, "exit"),
                        self.t_of(self.plog, "sigint"))

    # -- escalation ladder ------------------------------------------------
    def test_sigint_deaf_harness_gets_sigterm_rc2(self):
        s = self.sup(hmode="term")
        t, res = self.run_sup(s)
        self.wait_event(self.hlog, "start")
        s.request_stop()
        self.assertEqual(self.finish(t, res), 2)
        self.assertLess(self.t_of(self.hlog, "exit"),
                        self.t_of(self.plog, "sigint"))

    def test_hung_harness_is_stranded_rc3_playback_still_stopped(self):
        s = self.sup(hmode="hang")
        t, res = self.run_sup(s)
        self.wait_event(self.hlog, "start")
        s.request_stop()
        self.assertEqual(self.finish(t, res), 3)
        # never SIGKILLed: the hung child must still be alive
        self.assertTrue(s.harness.alive())
        # ...and playback was still shut down, not stranded with it
        self.assertIn("sigint", [e for e, _t in self.events(self.plog)])

    # -- child death propagation ------------------------------------------
    def test_harness_death_stops_playback_and_fails(self):
        s = self.sup(hmode="die")
        t, res = self.run_sup(s)
        rc = self.finish(t, res)
        self.assertEqual(rc, 1)
        self.assertIn("sigint", [e for e, _t in self.events(self.plog)])

    def test_playback_death_ends_harness_cleanly_first(self):
        s = self.sup(pmode="die")
        t, res = self.run_sup(s)
        rc = self.finish(t, res)
        self.assertEqual(rc, 1)
        # the harness was ended via its clean path, not abandoned
        self.assertIn("sigint", [e for e, _t in self.events(self.hlog)])
        self.assertIn("exit", [e for e, _t in self.events(self.hlog)])

    def test_playback_that_never_answers_times_out_harness_never_starts(self):
        s = self.sup(pmode="never", playback_wait=0.8)
        t, res = self.run_sup(s)
        rc = self.finish(t, res)
        self.assertGreaterEqual(rc, 1)
        self.assertEqual(self.events(self.hlog), [])   # never started

    # -- signal plumbing --------------------------------------------------
    def test_signals_map_to_request_stop_only(self):
        s = self.sup()
        s._on_signal(signal.SIGTERM, None)
        self.assertTrue(s._stop.is_set())


class TestRecipeCards(unittest.TestCase):
    """The two cards and the wrapper must agree on the board identities
    and differ ONLY in the phase list -- pinned here so they cannot
    drift apart (the by-id names live in both places by design)."""

    RECIPES = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))),
        "pi", "workbench", "recipes")

    CARDS = {"s8_hil_review_nano.toml": "nano-tiled",
             "s8_hil_review_tiny.toml": "tiny-tiled"}

    def load(self, fname):
        import tomllib
        with open(os.path.join(self.RECIPES, fname), "rb") as fh:
            return tomllib.load(fh)

    def test_cards_match_wrapper_board_identities(self):
        for fname in self.CARDS:
            obj = self.load(fname)
            self.assertEqual({b["by_id"] for b in obj["boards"]},
                             set(hil_review_run.BOARDS.values()),
                             "%s boards drifted from hil_review_run.BOARDS"
                             % fname)

    def test_cards_run_the_wrapper_with_their_phase(self):
        for fname, phases in self.CARDS.items():
            argv = self.load(fname)["run"]["argv"]
            self.assertIn("pi/hil/hil_review_run.py", argv)
            self.assertEqual(argv[argv.index("--phases") + 1], phases,
                             "%s phase list wrong" % fname)

    def test_cards_declare_stop_grace_and_monitor_health(self):
        for fname in self.CARDS:
            obj = self.load(fname)
            self.assertGreaterEqual(obj["run"]["stop_grace"], 40)
            self.assertIn(":8092", obj["health"]["http"])
            self.assertEqual(obj["opens"], ":8092")
            self.assertIn("hil-lcd.service", obj["services"])


if __name__ == "__main__":
    unittest.main()
