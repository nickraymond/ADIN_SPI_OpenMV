# pi/bm_bench/bench_chain.py -- S23 bite R v2: drive the BM chain up,
# stream real load, and take it back down, so a bridge teardown under
# the reproducer matches the INCIDENT conditions.
#
# Why this exists: bite R's v1 run was 8/8 clean because its cycles
# never streamed a frame (measured -- the exit stats read cap_frames=0,
# cap_chunks=0). All six incidents followed lifecycles that had carried
# real rpmsg traffic. This module adds that traffic.
#
# Topology (fixed by the bench, not by us): the reproducer runs on
# nereus000, which owns the AE3's CDC port and runs bm-light. The
# Telemetry node and its control socket live on nereus001, reached by
# key-auth ssh. Stream commands go through bench-ctl.sh, which answers
# in JSON -- so every step here is verified by a REPLY, never by an
# exit code (CLAUDE.md rule 4).
#
# Load is proven from BOTH ends and neither is trusted alone:
#   * receiver side -- the Telemetry ledger delta (frames_ok > 0);
#   * board side -- the bridge's own exit stats (cap_frames > 0),
#     asserted by the caller against the trace it pulls.
# A cycle that streamed nothing is NOT a load cycle and must never be
# counted as one.

import json
import time

TELEM_HOST = "pi@nereus001"
BENCH_CTL = "/home/pi/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh"
LIGHT_UNIT = "bm-light.service"
TELEM_UNIT = "bm-telemetry.service"

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
       "-o", "StrictHostKeyChecking=accept-new", TELEM_HOST]

# (mbps, fps, secs, q, res, pf) -- mbps generous so fps is the knob;
# values lifted from bench/s22_ceiling_rows.py ROWS so the load here is
# the same shape the sprint measures.
ROWS = {
    "vga-color": (4.0, 15, 50, 50, "vga", "color"),  # 20 chunks/frame
    "hd-mono": (4.0, 6, 50, 50, "hd", "mono"),       # 55 chunks/frame --
                                                  # overflows the 32-slot
                                                  # vring, max SHM pressure
}

LINK_WAIT_S = 45          # units up -> a status reply with a ledger
POLL_EVERY_S = 15


class ChainError(Exception):
    """The chain could not be driven -- the cycle is void, not clean."""


class Chain:
    """Bench chain control. `run` is injected so the tests can drive it
    without a bench: run(cmd_list, timeout) -> (rc, stdout, stderr)."""

    def __init__(self, run, log, sleep=time.sleep):
        self._run = run
        self._log = log
        self._sleep = sleep

    # -- plumbing ---------------------------------------------------
    def _local(self, *args, timeout=30):
        return self._run(list(args), timeout)

    def _remote(self, *args, timeout=30):
        return self._run(SSH + list(args), timeout)

    def ctl(self, *args, timeout=30):
        """bench-ctl.sh on the Telemetry node -> parsed JSON reply.
        A non-JSON answer is returned as {'raw': ...} exactly like
        s22_ceiling_rows.ctl, so a mute node is visible, not fatal."""
        rc, out, err = self._remote(BENCH_CTL, *args, timeout=timeout)
        try:
            return json.loads(out)
        except Exception:
            return {"raw": (out or err)[-200:], "rc": rc}

    def snapshot(self):
        """Receiver-side counters -- the load artifact."""
        d = self.ctl("status")
        led = d.get("ledger", {})
        cam = d.get("cam_reply", {})
        return {"frames_ok": led.get("frames_ok", 0),
                "gaps": led.get("gaps", 0),
                "dropped": led.get("dropped", 0),
                "bytes_ok": led.get("bytes_ok", 0),
                "pub_ok": cam.get("pub_ok", 0),
                "pub_errs": cam.get("pub_errs", 0),
                "cam_state": cam.get("state"),
                "ok": "ledger" in d}

    # -- lifecycle --------------------------------------------------
    def up(self):
        """Start Light (local) then Telemetry (remote), then wait for a
        live control socket. Starting Light is what LINKS the waiting
        phase-1 bridge -- its VCP heartbeat is the first contact."""
        rc, _, err = self._local("sudo", "-n", "systemctl", "start",
                                 LIGHT_UNIT)
        if rc != 0:
            raise ChainError("bm-light start failed rc=%d: %s"
                             % (rc, err.strip()[:200]))
        rc, _, err = self._remote("sudo", "-n", "systemctl", "start",
                                  TELEM_UNIT)
        if rc != 0:
            self.down()
            raise ChainError("bm-telemetry start failed rc=%d: %s"
                             % (rc, err.strip()[:200]))
        waited = 0
        while waited < LINK_WAIT_S:
            snap = self.snapshot()
            if snap["ok"]:
                self._log("chain up (cam=%s, ledger live after %d s)"
                          % (snap["cam_state"], waited))
                return snap
            self._sleep(5)
            waited += 5
        self.down()
        raise ChainError("no ledger reply %d s after units started -- "
                         "chain never came up" % LINK_WAIT_S)

    def stream(self, row, secs=None):
        """Command one stream row and poll it to completion. Returns the
        (before, after) receiver snapshots; the CALLER decides whether
        the delta proves load."""
        mbps, fps, row_secs, q, res, pf = ROWS[row]
        secs = row_secs if secs is None else secs
        before = self.snapshot()
        reply = self.ctl("stream", str(mbps), str(fps), str(secs),
                         str(q), res, pf)
        self._log("stream %s: %s" % (row, reply.get("accepted", reply)))
        t0 = 0
        while t0 < secs + 8:
            self._sleep(POLL_EVERY_S)
            t0 += POLL_EVERY_S
            s = self.snapshot()
            if s["ok"]:
                self._log("  t=%3ds frames=%d gaps=%d dropped=%d"
                          % (t0, s["frames_ok"] - before["frames_ok"],
                             s["gaps"] - before["gaps"],
                             s["dropped"] - before["dropped"]))
        self.ctl("stop")
        self._sleep(8)
        after = self.snapshot()
        return before, after

    def down(self):
        """Stop Telemetry then Light. Stopping Light is what makes the
        VCP go silent, which is what arms the bridge's quiet-exit --
        this is the teardown the incidents followed. Best-effort by
        design: a failure to stop must not mask the run's verdict."""
        rc_t, _, _ = self._remote("sudo", "-n", "systemctl", "stop",
                                  TELEM_UNIT)
        rc_l, _, _ = self._local("sudo", "-n", "systemctl", "stop",
                                 LIGHT_UNIT)
        self._log("chain down (telemetry rc=%d, light rc=%d)"
                  % (rc_t, rc_l))
        return rc_t == 0 and rc_l == 0

    def is_light_active(self):
        rc, out, _ = self._local("systemctl", "is-active", LIGHT_UNIT)
        return out.strip() == "active"


def load_delta(before, after):
    """Receiver-side load proof. Returns (frames, clean, detail)."""
    frames = after["frames_ok"] - before["frames_ok"]
    gaps = after["gaps"] - before["gaps"]
    dropped = after["dropped"] - before["dropped"]
    errs = after["pub_errs"] - before["pub_errs"]
    clean = frames > 0 and gaps == 0 and dropped == 0 and errs == 0
    return frames, clean, ("frames=%d gaps=%d dropped=%d pub_errs=%d "
                           "cam=%s" % (frames, gaps, dropped, errs,
                                       after["cam_state"]))


# The HE's status-page magic, "BMHE" (bm_bridge.BM_STATUS_PAGE gate).
HE_MAGIC = "424d4845"


def sram_state_at_boot(text):
    """'warm' | 'cold' | 'unknown' from a boot_report.

    Measured 2026-08-19 (bite R): after `mpremote reset` the SHM probes
    come up carrying the PREVIOUS generation's structures -- rsc=1,
    vring/pool offsets, and the HE magic already at the status page
    BEFORE he.start ever runs. After a physical unplug the same
    addresses read as non-repeating garbage with no magic.

    So a warm reset does NOT clear SRAM9 and a physical unplug does:
    the two are not the same boot, whatever the ops recipe says. This
    reads the FIRST (pre-he-start) dump only -- after he.start the
    live HE writes the magic on every boot, warm or cold.
    """
    text = text.replace(chr(13), "")
    section, seen_boot = [], False
    for line in text.splitlines():
        if line.startswith("== boot_report "):
            if seen_boot:
                break
            seen_boot = "boot_report boot" in line
            continue
        if seen_boot:
            section.append(line)
    for line in section:
        if "probe status_page" in line and "=" in line:
            val = line.rsplit("=", 1)[1].strip().lower()
            return "warm" if val == HE_MAGIC else "cold"
    return "unknown"


def cap_frames_from_trace(text):
    """Board-side load proof: cap_frames out of the bridge's own exit
    stats line. Returns None when no exit stats are present (a trace we
    cannot read must not silently pass as zero load)."""
    text = text.replace("\r", "")
    best = None
    for line in text.splitlines():
        i = line.find("exit stats ")
        if i < 0:
            continue
        j = line.find("{", i)
        k = line.rfind("}")
        if j < 0 or k < j:
            continue
        try:
            best = json.loads(line[j:k + 1]).get("cap_frames")
        except Exception:
            continue
    return best
