# test_bm_units.py -- host-side unit tests for the S18 bite D systemd
# harness: the two BM bench units, bm-cmd.sh, chain_status.sh and the
# installer. No hardware, no systemd, no Pi.
#
# These assert the properties the units EXIST for (one process, one
# producer, no boot autostart) and the cross-file path agreements that
# would otherwise drift silently -- a FIFO path that disagrees between
# the unit and bm-cmd.sh looks exactly like a wedged bench.
#
# Run:  python3 pi/services/test_bm_units.py

import os
import re
import stat
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.dirname(HERE)

LIGHT = os.path.join(HERE, "bm-light.service")
TELEM = os.path.join(HERE, "bm-telemetry.service")
CMD = os.path.join(PI, "bm_bench", "bm-cmd.sh")
STATUS = os.path.join(PI, "bm_bench", "chain_status.sh")
INSTALLER = os.path.join(PI, "install_stream_service.sh")
LIGHT_TOML = os.path.join(PI, "bm_bench", "light.toml")
DEMO_UP = os.path.join(PI, "bm_bench", "demo_up.sh")

CTL = os.path.join(PI, "bm_bench", "bench-ctl.sh")
CTL_PY = os.path.join(PI, "bm_bench", "bench_ctl.py")

FIFO = "/run/bm/telemetry.cmd"
AE3 = "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00"
BIN = "/home/pi/bm_sbc_s15/build/all/bm_sbc_bench_apps"
# S18 bite B. These two must agree between the unit, the preflight and the
# client's defaults; they are the app's compiled-in defaults too
# (apps/bench_apps/app_main.cpp, S18_CTL_SOCK / S18_CAPTURE_DIR).
CTL_SOCK = "/run/bm/bench.sock"
CAPTURE_DIR = "/home/pi/bench_captures"


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def code(path):
    """read() minus whole-line comments -- so an assertion about what a
    script DOES is not satisfied (or broken) by prose about what it
    deliberately does not do."""
    return "\n".join(l for l in read(path).splitlines()
                     if not l.lstrip().startswith("#"))


def parse_unit(path):
    """[(section, key, value)] -- a list, not a dict: systemd allows
    repeated keys (ExecStartPre, ExecStop) and the counts are what we
    assert on."""
    out, section = [], None
    for raw in read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line:
            key, _, value = line.partition("=")
            out.append((section, key.strip(), value.strip()))
    return out


def values(entries, key, section="Service"):
    return [v for s, k, v in entries if s == section and k == key]


class TestUnitsExist(unittest.TestCase):
    def test_both_units_parse_with_the_three_sections(self):
        for path in (LIGHT, TELEM):
            entries = parse_unit(path)
            sections = {s for s, _, _ in entries}
            self.assertEqual({"Unit", "Service", "Install"}, sections, path)

    def test_units_run_as_pi_not_root(self):
        for path in (LIGHT, TELEM):
            self.assertEqual(["pi"], values(parse_unit(path), "User"), path)

    def test_journal_lines_are_tagged_with_the_unit(self):
        # Measured in rehearsal: without this the telemetry journal reads
        # `sh[<pid>]`, because systemd names the identifier after the
        # binary it launched rather than the one exec replaced it with.
        for path, want in ((LIGHT, "bm-light"), (TELEM, "bm-telemetry")):
            self.assertEqual([want], values(parse_unit(path),
                                            "SyslogIdentifier"), path)


class TestSingletonProperty(unittest.TestCase):
    """One ExecStart, one process. A unit is a singleton by construction
    only if it does not fork a helper alongside the app."""

    def test_exactly_one_execstart_each(self):
        for path in (LIGHT, TELEM):
            self.assertEqual(1, len(values(parse_unit(path), "ExecStart")), path)

    def test_no_pipeline_in_execstart(self):
        # `tail -f cmdfile | app` was the sketched design; it puts a
        # second process in the cgroup, which is the thing being fixed.
        for path in (LIGHT, TELEM):
            line = values(parse_unit(path), "ExecStart")[0]
            self.assertNotIn("|", line, path)
            self.assertNotIn("tail", line, path)

    def test_telemetry_shell_wrapper_execs_away(self):
        # sh -c '... 0<>fifo' is fine ONLY with exec: sh replaces itself,
        # so MainPID is the app and `systemctl stop` reaches it directly.
        line = values(parse_unit(TELEM), "ExecStart")[0]
        self.assertTrue(line.startswith("/bin/sh -c "), line)
        self.assertIn("exec " + BIN, line)

    def test_light_execs_the_binary_directly(self):
        # The light role has no CLI, so it needs no shell at all.
        line = values(parse_unit(LIGHT), "ExecStart")[0]
        self.assertTrue(line.startswith(BIN + " "), line)


class TestCommandChannel(unittest.TestCase):
    def test_telemetry_opens_the_fifo_read_write(self):
        # `0<>` is the whole trick: never blocks on open, never hits EOF,
        # so bench_apps' poll()-based cli_poll stays live indefinitely.
        line = values(parse_unit(TELEM), "ExecStart")[0]
        self.assertIn("0<>" + FIFO, line)

    def test_telemetry_creates_the_fifo_before_start(self):
        pre = values(parse_unit(TELEM), "ExecStartPre")
        self.assertTrue(any("mkfifo" in p and FIFO in p for p in pre), pre)

    def test_runtime_directory_owns_the_fifo_lifetime(self):
        # /run/bm is created per-start and removed on stop, so a stale
        # FIFO cannot outlive the app that reads it.
        self.assertEqual(["bm"], values(parse_unit(TELEM), "RuntimeDirectory"))

    def test_light_has_no_command_channel(self):
        # bench_apps only polls stdin in the telemetry role.
        text = read(LIGHT)
        self.assertNotIn(FIFO, text)
        self.assertNotIn("mkfifo", text)

    def test_fifo_path_agrees_across_every_file_that_names_it(self):
        for path in (TELEM, CMD, STATUS):
            self.assertIn(FIFO, read(path), path)


class TestStopSemantics(unittest.TestCase):
    def test_stop_tells_the_camera_to_stop_streaming(self):
        # The AE3 keeps running a `stream <secs>` command after the app
        # dies; an S19 run was corrupted by exactly that leftover.
        stops = values(parse_unit(TELEM), "ExecStop")
        self.assertTrue(any("echo stop" in s and FIFO in s for s in stops), stops)

    def test_stop_is_best_effort_and_bounded(self):
        # If the app is already gone there is no FIFO reader, so the
        # write must not be able to hang the stop.
        stops = values(parse_unit(TELEM), "ExecStop")
        self.assertTrue(stops[0].startswith("-"), stops[0])
        self.assertIn("timeout 3", stops[0])
        self.assertEqual(["15"], values(parse_unit(TELEM), "TimeoutStopSec"))

    def test_restart_on_failure_not_always(self):
        # A deliberate stop must stay stopped; on-failure still absorbs
        # the fork's known startup segfault.
        for path in (LIGHT, TELEM):
            self.assertEqual(["on-failure"], values(parse_unit(path), "Restart"), path)


class TestLightPreflight(unittest.TestCase):
    def test_checks_the_ae3_is_on_the_bus(self):
        pre = values(parse_unit(LIGHT), "ExecStartPre")
        self.assertTrue(any(AE3 in p for p in pre), pre)

    def test_ae3_hint_names_the_recovery_ladder(self):
        self.assertIn("ae3-usb-unstick", read(LIGHT))

    def test_led_chmod_runs_as_root_via_the_plus_prefix(self):
        # User=pi cannot chmod root-owned sysfs; '+' is the systemd
        # escape hatch, and it replaces the README's manual per-boot step.
        pre = values(parse_unit(LIGHT), "ExecStartPre")
        led = [p for p in pre if "leds/ACT" in p]
        self.assertEqual(1, len(led), pre)
        self.assertTrue(led[0].startswith("+"), led[0])

    def test_ae3_path_agrees_with_light_toml_and_the_tools(self):
        # One wrong by-id string here is a bench that starts and then
        # fails to see the camera.
        for path in (LIGHT, LIGHT_TOML, STATUS, DEMO_UP):
            self.assertIn(AE3, read(path), path)


class TestBinaryPath(unittest.TestCase):
    def test_binary_agrees_across_units_and_preflight(self):
        for path in (LIGHT, TELEM, STATUS):
            self.assertIn(BIN, read(path), path)


class TestInstallerKeepsThemDisabled(unittest.TestCase):
    def test_new_roles_are_dispatched(self):
        text = read(INSTALLER)
        self.assertIn("bm-light.service", text)
        self.assertIn("bm-telemetry.service", text)

    def test_bm_roles_marked_no_autostart(self):
        text = read(INSTALLER)
        for role in ("light", "telemetry"):
            m = re.search(r"^\s*%s\)\s+UNIT=\S+;\s+AUTOSTART=(\w+)" % role,
                          text, re.M)
            self.assertIsNotNone(m, role)
            self.assertEqual("no", m.group(1), role)

    def test_stream_roles_keep_their_autostart(self):
        text = read(INSTALLER)
        for role in ("receiver", "sender", "shim"):
            m = re.search(r"^\s*%s\)\s+UNIT=\S+;\s+AUTOSTART=(\w+)" % role,
                          text, re.M)
            self.assertIsNotNone(m, role)
            self.assertEqual("yes", m.group(1), role)

    def test_installer_disables_then_verifies(self):
        # Idempotent even if someone enabled it by hand earlier.
        text = read(INSTALLER)
        self.assertIn('systemctl disable "$UNIT"', text)
        self.assertIn('systemctl is-enabled "$UNIT"', text)

    def test_units_do_not_claim_a_boot_target_by_accident(self):
        # WantedBy is fine -- it only takes effect on `enable`, which the
        # installer refuses for these two. Assert both facts together so
        # the pairing is deliberate rather than an oversight.
        for path in (LIGHT, TELEM):
            self.assertEqual(["multi-user.target"],
                             values(parse_unit(path), "WantedBy", "Install"),
                             path)


AUTOSTART = os.path.join(HERE, "dive-autostart.service")
AUTOSTART_SH = os.path.join(PI, "field", "dive_autostart.sh")


class TestDiveAutostart(unittest.TestCase):
    """Power on = record (S33, Nick 2026-09-10).

    The unit must press the workbench's own Start -- never touch a board --
    and it must be installable and enabled at boot through the same
    installer as every other field fixture.
    """

    def test_unit_parses_and_is_a_oneshot_run_as_pi(self):
        entries = parse_unit(AUTOSTART)
        self.assertEqual({"Unit", "Service", "Install"},
                         {s for s, _, _ in entries})
        self.assertEqual(["oneshot"], values(entries, "Type"))
        self.assertEqual(["pi"], values(entries, "User"))
        self.assertEqual(["multi-user.target"],
                         values(entries, "WantedBy", "Install"))

    def test_unit_orders_itself_after_the_workbench(self):
        entries = parse_unit(AUTOSTART)
        after = " ".join(values(entries, "After", "Unit"))
        self.assertIn("workbench.service", after)
        self.assertIn("workbench.service",
                      " ".join(values(entries, "Wants", "Unit")))

    def test_unit_runs_the_script_the_installer_documents(self):
        entries = parse_unit(AUTOSTART)
        execs = values(entries, "ExecStart")
        self.assertEqual(1, len(execs))
        self.assertIn("pi/field/dive_autostart.sh", execs[0])
        self.assertTrue(os.path.isfile(AUTOSTART_SH))
        self.assertTrue(os.stat(AUTOSTART_SH).st_mode & stat.S_IXUSR)

    def test_script_presses_start_and_waits_for_live(self):
        text = read(AUTOSTART_SH)
        self.assertIn("/api/start", text)
        self.assertIn("ci-record", text)
        self.assertIn("live)", text)           # exit 0 only on LIVE
        self.assertIn("no_dive_autostart", text)  # bench opt-out
        self.assertNotIn("mpremote", text)     # never touches a board
        self.assertNotIn("/dev/serial", text)
        self.assertNotIn("/dev/tty", text)

    def test_script_refuses_a_low_battery(self):
        """2026-09-10: 4 W on a 3.23 V cell with the charger out reset the Pi
        in 12 s and a following reset zeroed the netplan wifi profile."""
        text = read(AUTOSTART_SH)
        self.assertIn("MIN_VBAT_MV", text)
        self.assertIn("/api/dashboard", text)
        self.assertIn("NOT starting the load", text)

    def test_installer_enables_it_at_boot(self):
        text = read(INSTALLER)
        m = re.search(r"^\s*autostart\)\s+UNIT=(\S+);\s+AUTOSTART=(\w+)",
                      text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual("dive-autostart.service", m.group(1))
        self.assertEqual("yes", m.group(2))


AP_UNIT_FILE = os.path.join(HERE, "nereus-ap.service")
AP_SH = os.path.join(PI, "field", "ap_mode.sh")


class TestApSwitch(unittest.TestCase):
    """The rig as its own wifi network, as a systemd switch (S33)."""

    def test_unit_is_a_root_oneshot_that_stays_active(self):
        entries = parse_unit(AP_UNIT_FILE)
        self.assertEqual(["oneshot"], values(entries, "Type"))
        self.assertEqual(["yes"], values(entries, "RemainAfterExit"))
        self.assertEqual([], values(entries, "User"))      # nmcli needs root
        self.assertEqual(["multi-user.target"],
                         values(entries, "WantedBy", "Install"))

    def test_start_and_stop_are_the_two_halves_of_the_script(self):
        entries = parse_unit(AP_UNIT_FILE)
        self.assertIn("ap_mode.sh up", values(entries, "ExecStart")[0])
        self.assertIn("ap_mode.sh down", values(entries, "ExecStop")[0])
        self.assertTrue(os.stat(AP_SH).st_mode & stat.S_IXUSR)

    def test_script_is_the_borrowed_recipe_with_nicks_two_changes(self):
        text = read(AP_SH)
        self.assertIn("ipv4.method shared", text)         # 10.42.0.1 + DHCP
        self.assertIn("mode ap", text)
        self.assertIn('SSID="${AP_SSID:-$(hostname)}"', text)   # SSID = hostname
        self.assertNotIn("wifi-sec.psk", text)            # no password
        self.assertIn("connection.autoconnect no", text)  # systemd owns boot
        self.assertIn('10.42.0.1', text)                  # verified, not assumed

    def test_installer_installs_it_disabled(self):
        text = read(INSTALLER)
        m = re.search(r"^\s*ap\)\s+UNIT=(\S+);\s+AUTOSTART=(\w+)", text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual("nereus-ap.service", m.group(1))
        self.assertEqual("no", m.group(2))

    # -- the fallback: home wifi first, AP if none (Nick, 2026-09-10) --------
    FALLBACK = os.path.join(HERE, "nereus-ap-fallback.service")

    def test_fallback_unit_runs_auto_at_boot(self):
        entries = parse_unit(self.FALLBACK)
        self.assertIn("ap_mode.sh auto", values(entries, "ExecStart")[0])
        self.assertEqual(["multi-user.target"], values(entries, "WantedBy", "Install"))
        self.assertIn("AP_FALLBACK_S=60", " ".join(values(entries, "Environment")))
        m = re.search(r"^\s*ap-fallback\)\s+UNIT=(\S+);\s+AUTOSTART=(\w+)", read(INSTALLER), re.M)
        self.assertIsNotNone(m)
        self.assertEqual("nereus-ap-fallback.service", m.group(1))
        self.assertEqual("yes", m.group(2))

    def _run_auto(self, client_up, tmp):
        """Drive `ap_mode.sh auto` with a fake nmcli/ip/systemctl on PATH."""
        import subprocess
        os.makedirs(os.path.join(tmp, "bin"), exist_ok=True)
        calls = os.path.join(tmp, "calls")
        fake_nmcli = (
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'con show --active'*) %s ;;\n"
            "  *'802-11-wireless.mode con show'*) echo infrastructure ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n" % ("echo 'wlan0-Ford:wlan0'" if client_up else "true"))
        fake_ip = ("#!/bin/sh\n%s\n" % ("echo '2: wlan0 inet 192.168.1.35/24 brd'" if client_up else "true"))
        fake_sysctl = "#!/bin/sh\necho \"systemctl $*\" >> %s\nexit 0\n" % calls
        for name, body in (("nmcli", fake_nmcli), ("ip", fake_ip), ("systemctl", fake_sysctl)):
            pth = os.path.join(tmp, "bin", name)
            with open(pth, "w") as f:
                f.write(body)
            os.chmod(pth, 0o755)
        env = dict(os.environ, PATH=os.path.join(tmp, "bin") + ":" + os.environ["PATH"],
                   AP_FALLBACK_S="1", AP_SSID="rigx")
        r = subprocess.run(["bash", AP_SH, "auto"], env=env, capture_output=True,
                           text=True, timeout=30)
        made = open(calls).read() if os.path.exists(calls) else ""
        return r.returncode, r.stdout, made

    def test_down_hands_wlan0_to_the_client_profile_by_name(self):
        """`nmcli device connect` re-picked the AP as best available
        (2026-09-10), so down must name the client profile it activates."""
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin"))
            calls = os.path.join(d, "calls")
            fake_nmcli = (
                "#!/bin/sh\necho \"nmcli $*\" >> %s\n"
                "case \"$*\" in\n"
                "  '-t -f NAME,TYPE con show') printf 'rigx-ap:802-11-wireless\\nwlan0-Ford:802-11-wireless\\nlo:loopback\\n' ;;\n"
                "  *'802-11-wireless.mode con show rigx-ap'*) echo ap ;;\n"
                "  *'802-11-wireless.mode con show wlan0-Ford'*) echo infrastructure ;;\n"
                "  *'connection.autoconnect con show wlan0-Ford'*) echo yes ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n" % calls)
            for name, body in (("nmcli", fake_nmcli),
                               ("ip", "#!/bin/sh\ntrue\n"),
                               ("systemctl", "#!/bin/sh\nexit 0\n")):
                pth = os.path.join(d, "bin", name)
                with open(pth, "w") as f:
                    f.write(body)
                os.chmod(pth, 0o755)
            env = dict(os.environ, PATH=os.path.join(d, "bin") + ":" + os.environ["PATH"],
                       AP_SSID="rigx")
            r = subprocess.run(["bash", AP_SH, "down"], env=env, capture_output=True,
                               text=True, timeout=30)
            made = open(calls).read()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("nmcli con down rigx-ap", made)
        self.assertIn("nmcli con up wlan0-Ford", made)
        self.assertNotIn("dev connect", made)
        self.assertIn("client profile 'wlan0-Ford'", r.stdout)

    def test_auto_stays_a_client_when_home_wifi_is_up(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, calls = self._run_auto(True, d)
        self.assertEqual(rc, 0, out)
        self.assertIn("home wifi ok", out)
        self.assertNotIn("systemctl start nereus-ap", calls)

    def test_auto_raises_the_ap_when_no_client_appears(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, calls = self._run_auto(False, d)
        self.assertEqual(rc, 0, out)
        self.assertIn("FALLING BACK to AP", out)
        self.assertIn("systemctl start nereus-ap\n", calls)


class TestShellTooling(unittest.TestCase):
    def test_scripts_are_executable(self):
        for path in (CMD, STATUS, CTL):
            mode = os.stat(path).st_mode
            self.assertTrue(mode & stat.S_IXUSR, path)

    def test_scripts_fail_loudly(self):
        self.assertIn("set -euo pipefail", read(CMD))
        self.assertIn("set -euo pipefail", read(CTL))
        # chain_status runs every check before its verdict, so no -e.
        self.assertIn("set -uo pipefail", read(STATUS))

    def test_bm_cmd_refuses_when_nobody_is_reading(self):
        # A command written into an unread FIFO looks exactly like a
        # command that worked.
        text = read(CMD)
        self.assertIn("is-active", text)
        self.assertIn("-p \"$FIFO\"", text)

    def test_bm_cmd_bounds_its_write(self):
        self.assertIn("timeout 3", read(CMD))

    def test_status_finds_processes_by_exe_not_by_pattern(self):
        # `pkill -f`/`pgrep -f` patterns matched the driving SSH command
        # line in S19. /proc/<pid>/exe cannot.
        text = code(STATUS)
        self.assertIn("/exe", text)
        self.assertNotIn("pgrep -f", text)
        self.assertNotIn("pkill", text)

    def test_status_checks_the_single_producer_invariant(self):
        text = code(STATUS)
        self.assertIn(":8081", text)
        # 0 ends = idle, 2 = one producer, anything else = the wedge.
        self.assertRegex(text, r"(?m)^\s*0\)\s*pass")
        self.assertRegex(text, r"(?m)^\s*2\)\s*pass")

    def test_status_flags_an_enabled_unit_as_a_failure(self):
        self.assertIn("ENABLED at boot", read(STATUS))

    def test_status_exits_nonzero_on_failure(self):
        self.assertIn("exit 1", read(STATUS))


class TestControlSocketPlumbing(unittest.TestCase):
    """S18 bite B. The socket and the capture directory are named in four
    places (the unit, the preflight, the client, the app's own defaults). A
    disagreement gives a bench that starts and then quietly does nothing --
    exactly the failure the FIFO path agreements already guard against."""

    def test_capture_dir_is_set_explicitly_in_the_unit(self):
        # Not derived from $HOME: a service's environment is not a login
        # shell's, and evidence must land where the tools look for it.
        env = values(parse_unit(TELEM), "Environment")
        self.assertTrue(any(e == "S18_CAPTURE_DIR=" + CAPTURE_DIR for e in env),
                        env)

    def test_light_has_no_control_socket(self):
        # Only the telemetry role serves it, same as the CLI (the light
        # role's loop() never polls either).
        text = read(LIGHT)
        self.assertNotIn(CTL_SOCK, text)
        self.assertNotIn("S18_CAPTURE_DIR", text)

    def test_socket_path_agrees_across_every_file_that_names_it(self):
        for path in (STATUS, CTL_PY):
            self.assertIn(CTL_SOCK, read(path), path)

    def test_capture_dir_agrees_between_unit_and_preflight(self):
        self.assertIn(CAPTURE_DIR, code(STATUS))

    def test_socket_lives_in_the_units_runtime_directory(self):
        # Same lifetime rule as the FIFO: a socket that outlives its reader
        # accepts commands nobody will ever act on.
        self.assertTrue(CTL_SOCK.startswith("/run/bm/"), CTL_SOCK)
        self.assertIn("bm", values(parse_unit(TELEM), "RuntimeDirectory"))

    def test_bench_ctl_refuses_when_nobody_is_listening(self):
        # bm-cmd.sh's rule, applied to the socket.
        text = code(CTL)
        self.assertIn("is-active", text)
        self.assertIn("-S \"$SOCK\"", text)

    def test_preflight_checks_socket_and_capture_dir(self):
        text = code(STATUS)
        self.assertIn("$CTL_SOCK", text)
        self.assertIn("$CAPTURE_DIR", text)

    def test_client_binds_its_own_address(self):
        # An unbound DGRAM sender has no address, so the node cannot reply
        # and the command reads as a timeout.
        self.assertIn("s.bind(", read(CTL_PY))

    def test_client_matches_the_echoed_id(self):
        # Datagrams: a late reply to a timed-out request must not be
        # returned as the answer to the next one.
        self.assertIn('rep.get("id")', read(CTL_PY))

    def test_client_is_stdlib_only(self):
        # Same rule as the frozen S3 stream server: nothing to install on
        # the Pi, nothing to drift.
        for line in read(CTL_PY).splitlines():
            if line.startswith("import ") or line.startswith("from "):
                mod = line.split()[1].split(".")[0]
                self.assertIn(mod, ("__future__", "errno", "json", "os",
                                    "socket", "sys", "tempfile"), line)


if __name__ == "__main__":
    unittest.main()
