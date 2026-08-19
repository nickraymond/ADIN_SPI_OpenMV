# test_repro_attach.py -- host-side tests for the S23 bite R
# reproducer + boot_report instrument. No hardware, no Pi.
#
# Three layers, mirroring test_bm_units.py's philosophy (assert the
# properties the tools EXIST for + the cross-file agreements that
# would otherwise drift silently):
#   1. classifier unit tests -- the verdict table is the deliverable;
#   2. cross-file constants -- port path, phase-1/quiet-exit timings,
#      demo_up's WANT_MAIN sha, the boot_report sync entry;
#   3. an end-to-end cycle against a fake mpremote (scripted outcomes,
#      TIME_SCALE-collapsed sleeps).
#
# Run:  python3 pi/bm_bench/test_repro_attach.py

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BRIDGE_DIR = os.path.join(REPO, "firmware", "bm_bridge")

sys.path.insert(0, HERE)
import repro_attach as ra  # noqa: E402

DEMO_UP = os.path.join(HERE, "demo_up.sh")
REPRO = os.path.join(HERE, "repro_attach.py")
BM_BRIDGE = os.path.join(BRIDGE_DIR, "bm_bridge.py")
MAIN_BRIDGE = os.path.join(BRIDGE_DIR, "main_bridge.py")
BOOT_REPORT = os.path.join(BRIDGE_DIR, "boot_report.py")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def code(path):
    return "\n".join(l for l in read(path).splitlines()
                     if not l.lstrip().startswith("#"))


class TestClassifier(unittest.TestCase):
    def test_lost_race_timeout(self):
        self.assertTrue(ra.lost_race(124, ""))

    def test_lost_race_signature(self):
        self.assertTrue(ra.lost_race(1, "could not enter raw repl"))

    def test_success_is_not_a_lost_race(self):
        self.assertFalse(ra.lost_race(0, ""))

    def test_other_errors_are_not_lost_races(self):
        self.assertFalse(ra.lost_race(1, "device reports readiness "
                                         "to read but returned no data"))

    def test_retry_failure_inside_horizon_is_state2_candidate(self):
        h = ra.BOOT_WAIT_S + ra.PHASE1_TIMEOUT_S + ra.QUIET_EXIT_S
        self.assertEqual("state2-candidate",
                         ra.classify_retry_failure(h - 1))

    def test_retry_failure_past_horizon_is_true_state1(self):
        h = ra.BOOT_WAIT_S + ra.PHASE1_TIMEOUT_S + ra.QUIET_EXIT_S
        self.assertEqual("true-state1", ra.classify_retry_failure(h))

    def test_exit_kind_quiet(self):
        self.assertEqual("quiet-exit", ra.exit_kind_from_trace(
            "vcp quiet 30000 ms -- pi side gone, exiting"))

    def test_exit_kind_phase1(self):
        self.assertEqual("phase1-timeout", ra.exit_kind_from_trace(
            "phase 1 timeout -- no Pi attach, exiting"))

    def test_exit_kind_survives_crlf(self):
        self.assertEqual("quiet-exit", ra.exit_kind_from_trace(
            "vcp\r\n quiet\r\n".replace(" ", "vcp quiet ", 1)))

    def test_exit_kind_unknown(self):
        self.assertEqual("unknown", ra.exit_kind_from_trace("hello"))


class TestCrossFileAgreements(unittest.TestCase):
    def test_port_path_agrees_with_demo_up(self):
        m = re.search(r"^P=(\S+)", read(DEMO_UP), re.M)
        self.assertEqual(m.group(1), ra.PORT)

    def test_phase1_timeout_agrees_with_the_bridge(self):
        m = re.search(r"^PHASE1_TIMEOUT_MS\s*=\s*(\d+)",
                      read(BM_BRIDGE), re.M)
        self.assertEqual(int(m.group(1)), ra.PHASE1_TIMEOUT_S * 1000)

    def test_quiet_exit_agrees_with_the_bridge(self):
        m = re.search(r"^QUIET_EXIT_MS\s*=\s*(\d+)",
                      read(BM_BRIDGE), re.M)
        self.assertEqual(int(m.group(1)), ra.QUIET_EXIT_S * 1000)

    def test_armed_window_clears_quiet_exit_with_margin(self):
        self.assertGreater(ra.ARMED_WINDOW_S, ra.QUIET_EXIT_S + 10)

    def test_demo_up_want_main_is_the_repo_launcher_sha(self):
        want = re.search(r'WANT_MAIN="([0-9a-f]{16})"',
                         read(DEMO_UP)).group(1)
        h = hashlib.sha256()
        h.update(open(MAIN_BRIDGE, "rb").read())
        self.assertEqual(h.hexdigest()[:16], want)

    def test_demo_up_preserves_boot_report_generations(self):
        # the launcher rotates boot_report at every boot; a demo_up
        # that does not bank it first destroys the incident evidence
        # (same rule that already protects the bridge traces)
        m = re.search(r"^for tf in ([^;]+); do", code(DEMO_UP),
                      re.M | re.S)
        names = m.group(1).replace("\\", " ").split()
        self.assertIn("boot_report.txt", names)
        self.assertIn("boot_report.prev.txt", names)

    def test_demo_up_syncs_boot_report(self):
        m = re.search(r"^for f in ([^;]+); do", code(DEMO_UP), re.M)
        self.assertIn("boot_report.py", m.group(1).split())

    def test_launcher_calls_boot_report_non_fatally(self):
        c = code(MAIN_BRIDGE)
        self.assertIn("boot_report.boot()", c)
        # the call sits in its own try, before the _run() CALL site
        call = re.search(r"^    _run\(\)", c, re.M)
        self.assertLess(c.index("boot_report.boot()"), call.start())

    def test_bridge_dumps_after_he_start(self):
        c = code(BM_BRIDGE)
        self.assertIn('boot_report.dump("post-he-start")', c)
        self.assertLess(c.index("he.start()"),
                        c.index('boot_report.dump("post-he-start")'))

    def test_reproducer_never_runs_the_power_cycle_itself(self):
        # preserve-the-wedge rule: uhubctl/sudo may be NAMED (the one
        # operator-hint constant) but never EXECUTED -- no subprocess
        # invocation may carry either string.
        import ast as _ast
        tree = _ast.parse(read(REPRO))
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Call)
                    and getattr(node.func, "attr", "") == "run"
                    and getattr(getattr(node.func, "value", None),
                                "id", "") == "subprocess"):
                for c in _ast.walk(node):
                    if (isinstance(c, _ast.Constant)
                            and isinstance(c.value, str)):
                        self.assertNotIn("uhubctl", c.value)
                        self.assertNotIn("sudo", c.value)
        # and the hint is the ONLY place either word appears
        body = code(REPRO)
        self.assertEqual(1, body.count("uhubctl"))
        self.assertEqual(1, body.count("sudo"))

    def test_reproducer_is_executable(self):
        self.assertTrue(os.stat(REPRO).st_mode & stat.S_IXUSR)

    def test_verdict_strings_all_documented_in_header(self):
        header = read(REPRO)[:3000]
        for v in ("explained-arm", "degenerate-no-bridge",
                  "explained-late-exit", "state2-candidate",
                  "state2-reproduced", "true-state1", "usb-drop"):
            self.assertIn(v, header)


class FakeMem32:
    """dict-backed mem32 with RNR banking for RBAR/RLAR."""

    def __init__(self, dregion):
        self.vals = {
            0xE000ED90: dregion << 8,      # MPU_TYPE.DREGION
            0xE000ED94: 0x7,               # CTRL
            0xE000EDC0: 0x44AA0000,        # MAIR0
            0xE000EDC4: 0x0,               # MAIR1
            0x60000000: 0x11111111, 0x60000400: 0x22222222,
            0x60001400: 0x33333333, 0x60002400: 0x44444444,
            0x60010000: 0x55555555, 0x6001FFFC: 0x66666666,
            0x600BFE00: 0x424D4845,
        }
        self.rnr = 0

    def __getitem__(self, addr):
        if addr == 0xE000ED9C:
            return 0x60000000 + self.rnr        # RBAR f(region)
        if addr == 0xE000EDA0:
            return 0x1000 + self.rnr            # RLAR f(region)
        return self.vals[addr]

    def __setitem__(self, addr, val):
        assert addr == 0xE000ED98, "only RNR may be written"
        self.rnr = val


class TestBootReport(unittest.TestCase):
    def _load(self, dregion=8):
        fake = types.ModuleType("machine")
        fake.mem32 = FakeMem32(dregion)
        fake.disable_irq = lambda: 0
        fake.enable_irq = lambda s: None
        sys.modules["machine"] = fake
        sys.path.insert(0, BRIDGE_DIR)
        if "boot_report" in sys.modules:
            del sys.modules["boot_report"]
        import boot_report
        return boot_report

    def tearDown(self):
        sys.modules.pop("machine", None)
        sys.modules.pop("boot_report", None)
        if BRIDGE_DIR in sys.path:
            sys.path.remove(BRIDGE_DIR)

    def test_dump_walks_every_region_and_probe(self):
        br = self._load(dregion=8)
        with tempfile.TemporaryDirectory() as d:
            br.PATH = os.path.join(d, "boot_report.txt")
            br.PREV = os.path.join(d, "boot_report.prev.txt")
            br.dump("boot")
            text = read(br.PATH)
        self.assertEqual(8, len(re.findall(r"^mpu r\d\d ", text, re.M)))
        self.assertIn("mpu r03 rbar=60000003 rlar=00001003", text)
        for name, _ in br.PROBES:
            self.assertIn("probe %s " % name, text)
        self.assertIn("probe status_page @600bfe00 = 424d4845", text)

    def test_boot_rotates_one_generation(self):
        br = self._load()
        with tempfile.TemporaryDirectory() as d:
            br.PATH = os.path.join(d, "boot_report.txt")
            br.PREV = os.path.join(d, "boot_report.prev.txt")
            br.dump("gen1")
            br.boot()
            self.assertIn("gen1", read(br.PREV))
            self.assertNotIn("gen1", read(br.PATH))
            self.assertIn("boot_report boot", read(br.PATH))

    def test_dump_never_raises_without_machine(self):
        br = self._load()
        del sys.modules["machine"]
        br.dump("no-machine")   # must be silent


FAKE_MPREMOTE = r"""#!/usr/bin/env python3
# scripted mpremote: pops one outcome per invocation from outcomes.txt
import os, sys, time
d = os.environ["FAKE_MPR_DIR"]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
with open(os.path.join(d, "outcomes.txt")) as f:
    outcomes = f.read().splitlines()
n_path = os.path.join(d, "n.txt")
n = int(open(n_path).read()) if os.path.exists(n_path) else 0
open(n_path, "w").write(str(n + 1))
kind = outcomes[n] if n < len(outcomes) else "ok"
if kind == "raw":
    sys.stderr.write("could not enter raw repl\n"); sys.exit(1)
if kind == "hang":
    time.sleep(999)
if kind == "trace-quiet":
    print("==== /flash/bridge_trace.txt (100 bytes) ====")
    print("vcp quiet 30000 ms -- pi side gone, exiting")
if kind == "trace-p1":
    print("==== /flash/bridge_trace.txt (100 bytes) ====")
    print("phase 1 timeout -- no Pi attach, exiting")
if kind.startswith("print:"):
    print(kind[6:].replace("|", "\n"))
sys.exit(0)
"""


class TestEndToEnd(unittest.TestCase):
    """One scripted run: cycle 1 clean, cycle 2 wedges into the
    state2 discriminator and lands state2-reproduced -> exit 3."""

    def _run(self, outcomes, cycles=2, ladder=2):
        d = tempfile.mkdtemp()
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        mp = os.path.join(bindir, "mpremote")
        with open(mp, "w") as f:
            f.write(FAKE_MPREMOTE)
        os.chmod(mp, 0o755)
        port = os.path.join(d, "port")
        open(port, "w").write("")
        with open(os.path.join(d, "outcomes.txt"), "w") as f:
            f.write("\n".join(outcomes) + "\n")
        launcher_sha = hashlib.sha256(
            open(MAIN_BRIDGE, "rb").read()).hexdigest()[:16]
        # patch the preflight outcome in-place
        with open(os.path.join(d, "outcomes.txt"), "w") as f:
            f.write("\n".join(outcomes).replace(
                "PREFLIGHT", "print:MAIN:%s|BOOTREP:yes" % launcher_sha)
                + "\n")
        env = dict(os.environ)
        env.update(FAKE_MPR_DIR=d, REPRO_PORT=port,
                   REPRO_REPO=REPO, REPRO_LOG_DIR=os.path.join(d, "logs"),
                   REPRO_TIME_SCALE="10000",
                   REPRO_CYCLES=str(cycles), REPRO_LADDER=str(ladder),
                   PATH=bindir + os.pathsep + env.get("PATH", ""),
                   HOME=d)
        p = subprocess.run([sys.executable, REPRO], env=env,
                           capture_output=True, text=True, timeout=120)
        events = []
        logs = os.path.join(d, "logs")
        for root, _, files in os.walk(logs):
            for fn in files:
                if fn == "events.jsonl":
                    with open(os.path.join(root, fn)) as f:
                        events = [json.loads(l) for l in f]
        return p, events

    def test_clean_then_state2(self):
        outcomes = [
            "PREFLIGHT",       # preflight exec
            # cycle 1: reset, arm(raw), ladder ok x2 (+1 trace pull)
            "ok", "raw", "ok", "trace-quiet", "ok",
            # cycle 2: reset, arm(raw), ladder1 raw, retry raw,
            # post-horizon ok, trace shows phase-1 timeout
            "ok", "raw", "raw", "raw", "ok", "trace-p1",
        ]
        p, events = self._run(outcomes)
        self.assertEqual(3, p.returncode, p.stdout + p.stderr)
        verdicts = [e for e in events if e["kind"] == "verdict"]
        by_cycle = {}
        for e in verdicts:
            by_cycle.setdefault(e["cycle"], []).append(e["verdict"])
        self.assertIn("explained-arm", by_cycle[1])
        self.assertIn("state2-candidate", by_cycle[2])
        self.assertIn("state2-reproduced", by_cycle[2])
        end = [e for e in events if e["kind"] == "run-end"]
        self.assertEqual("state2-reproduced", end[0]["stopped_on"])

    def test_all_clean_exits_zero(self):
        outcomes = [
            "PREFLIGHT",
            "ok", "raw", "ok", "trace-quiet", "ok",   # cycle 1
            "ok", "raw", "ok", "trace-quiet", "ok",   # cycle 2
        ]
        p, events = self._run(outcomes)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        end = [e for e in events if e["kind"] == "run-end"]
        self.assertIsNone(end[0]["stopped_on"])
        self.assertEqual(["clean", "clean"], end[0]["verdicts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
