#!/usr/bin/env python3
"""S18 reef-scene matrix — the 9-row res x pf x q table plus the first
measured stream numbers, through the real chain.

Run ON nereus001 (the control socket lives there), with the chain up and
the bridge staged in ref-scene mode (`demo_up.sh --scene ref` on
nereus000):

    python3 ~/ADIN_SPI_OpenMV/bench/s18_matrix.py

What it does, in one visit per (res, pf) so the sensor re-inits only 6
times (a mode change is the B2 hazard; q changes are free):

  * 9 stills  — bytes/chunks read from bite B's sidecars, geometry and
    component count verified against the JPEG's OWN SOF header, ledger
    deltas checked zero. These are the page's MEAS numbers, measured.
  * 7 streams — delivered fps from the receiver ledger's frames_ok,
    Mbps from the HE's pub_bytes, gaps/drops must not move. These are
    the page's in-bridge fps numbers, measured.
  * the certification rung — a mode change sent immediately after the
    last HD stream, so the bridge holds it to exactly
    REINIT_MIN_QUIET_MS after ~daylight-HD published bytes. PASS =
    frames deliver afterwards. This is the number the 20 s constant's
    comment owes (it was set from dark VGA frames).

The FIRST still is a tripwire: QVGA color q50 on the reef reference
must land ~9.2 KB (S0). Dark-room bytes (~3.9 KB) mean the ref scene is
NOT staged/active, and the whole run would be silently unrepresentative
— so the driver aborts rather than measure the wrong thing.

Every wait polls status every 2 s: keep-alive (the C1 quiet-exit trap
structurally cannot fire) AND liveness record. No retry loops anywhere;
a row that fails is recorded and the run stops at the second consecutive
total failure (a latched sensor fails everything downstream — stop and
say so instead of producing 10 rows of noise).
"""

import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi", "bm_bench"))

CAPTURE_DIR = os.path.expanduser(os.environ.get("S18_CAPTURE_DIR",
                                                "~/bench_captures"))
POLL_S = 2.0            # status cadence: keep-alive + liveness
QUIET_S = 21.0          # local settle before a mode-change command, so the
                        # bridge gate (20 s) is already open and the fork's
                        # 8 s save window is not consumed by the hold
STILL_SAME_TIMEOUT_S = 15.0
STILL_REINIT_TIMEOUT_S = 40.0   # gate hold + re-init + capture + save
STREAM_SLACK_S = 15.0
CERT_TIMEOUT_S = 45.0

RES_GEOM = {"qvga": (320, 200), "vga": (640, 400), "hd": (1280, 800)}
PF_COMPONENTS = {"color": 3, "mono": 1}

# Reef tripwire for the first still (QVGA color q50): S0 measured 9,198 B
# on the reference, ~3.9 KB on the dark bench. Halfway bands.
REEF_BYTES_MIN, REEF_BYTES_MAX = 6000, 14000

# One visit per (res, pf); q rows inside a visit are free (no re-init).
# Streams command fps ABOVE the predicted ceiling so the delivered rate
# IS the ceiling; mbps 4.0 stays under the 5.26 measured relay ceiling.
#
# ORDER IS RISK ORDER (runs 1-2, 2026-08-18): a 30-fps commanded QVGA
# stream (~588 rpmsg msg/s, ~2x the proven S17 regime) wedged the HE
# camera service both times, killing every later row. The QVGA color
# ceiling was measured identically twice before the wedge (28.07 fps)
# and is dropped here; the one remaining ceiling row (QVGA mono) runs
# LAST, sacrificially, after the cert rung — if it wedges the camera
# the matrix is already complete. The wedge itself is filed as its own
# finding, not chased in this bite.
PLAN = [
    ("still",  "qvga", "color", 50, None),
    ("still",  "qvga", "color", 35, None),
    ("still",  "qvga", "color", 90, None),
    ("stream", "qvga", "color", 50,
     {"mbps": 2.0, "fps": 15, "secs": 60, "tag": "regression-s17"}),
    ("still",  "qvga", "mono",  50, None),
    ("still",  "vga",  "color", 50, None),
    ("stream", "vga",  "color", 50,
     {"mbps": 4.0, "fps": 10, "secs": 60, "tag": "ceiling"}),
    ("still",  "vga",  "mono",  50, None),
    # fps 8, not the encoder's ~15: run 3 measured the CHAIN's wedge
    # boundary at roughly 500-600 rpmsg msg/s, and VGA mono at 15 fps
    # commands ~810 (54 msgs/frame) — it broke the ledger (20,436 chunk
    # gaps) and wedged the camera. 8 fps ≈ 430 msg/s stays under it; if
    # it delivers a flat 8.0 the row reports a command-capped floor,
    # not the encoder ceiling, and says so via the tag.
    ("stream", "vga",  "mono",  50,
     {"mbps": 4.0, "fps": 8, "secs": 60, "tag": "ceiling-capped"}),
    ("still",  "hd",   "mono",  50, None),
    ("still",  "hd",   "mono",  90, None),
    ("stream", "hd",   "mono",  50,
     {"mbps": 4.0, "fps": 5, "secs": 60, "tag": "ceiling"}),
    ("still",  "hd",   "color", 50, None),
    ("stream", "hd",   "color", 50,
     {"mbps": 4.0, "fps": 3, "secs": 30, "tag": "ceiling-short"}),
    ("cert",   "qvga", "color", 50, None),
    ("stream", "qvga", "mono",  50,
     {"mbps": 4.0, "fps": 30, "secs": 60, "tag": "ceiling-sacrificial"}),
]

# --resume: the rows still owed after runs 1-4 banked the rest. Run 3's
# preserved bridge trace showed the wedge mechanism: sustained publish
# above ~450-500 rpmsg msg/s silences the HE wire task mid- or
# post-stream (he2pi_frames freezes while the Pi keeps querying) — an
# HE bug, filed separately, NOT the sensor race (re-inits after streams
# trace clean). So the remaining streams are COMMAND-CAPPED into the
# proven-safe zone (regression's 315 msg/s ran clean 4/4) and their
# rows are floors, not ceilings; encoder ceilings come from the traced
# enc-avg numbers instead. HD stills and the cert rung go FIRST, before
# any stream has loaded the HE.
PLAN_RESUME = [
    ("still",  "qvga", "color", 50, None),      # tripwire
    ("still",  "hd",   "mono",  50, None),
    ("still",  "hd",   "mono",  90, None),
    ("still",  "hd",   "color", 50, None),
    ("cert",   "qvga", "color", 50, None),
    ("stream", "hd",   "mono",  50,
     {"mbps": 4.0, "fps": 2.5, "secs": 60, "tag": "floor-capped"}),
    ("stream", "hd",   "color", 50,
     {"mbps": 4.0, "fps": 1.5, "secs": 30, "tag": "floor-capped"}),
    ("stream", "vga",  "mono",  50,
     {"mbps": 4.0, "fps": 8, "secs": 60, "tag": "floor-capped"}),
    ("stream", "qvga", "mono",  50,
     {"mbps": 4.0, "fps": 30, "secs": 60, "tag": "ceiling-sacrificial"}),
]


# ---- pure helpers (host-tested) ------------------------------------------

def plan_stats(plan):
    """(#stills, #streams, #mode-changes incl. the cert rung)."""
    stills = sum(1 for r in plan if r[0] == "still")
    streams = sum(1 for r in plan if r[0] == "stream")
    changes, cur = 0, None
    for kind, res, pf, _q, _x in plan:
        if (res, pf) != cur:
            changes += 1
            cur = (res, pf)
    return stills, streams, changes - 1     # first visit is not a change


def sof_info(jpeg):
    """(width, height, components) from the JPEG's own SOF marker.

    The artifact is the authority — never trust a status field about
    geometry when the image itself says (bite B discipline).
    """
    if len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG (no SOI)")
    i = 2
    while i + 4 <= len(jpeg):
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (seglen,) = struct.unpack(">H", jpeg[i + 2:i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", jpeg[i + 5:i + 9])
            ncomp = jpeg[i + 9]
            return w, h, ncomp
        i += 2 + seglen
    raise ValueError("no SOF marker found")


def counters(status):
    """(saved, errors) from a status reply — the arm/release counters."""
    save = (status or {}).get("save") or {}
    return int(save.get("saved") or 0), int(save.get("errors") or 0)


def ledger_of(status):
    return (status or {}).get("ledger") or {}


def ledger_delta(led0, led1, keys=("frames_ok", "dropped", "gaps",
                                   "hdr_errs", "q_drops", "ingest_fail")):
    return {k: int(led1.get(k) or 0) - int(led0.get(k) or 0) for k in keys}


def cam_pub(status):
    """(pub_ok, pub_bytes) from the camera reply carried in status.

    NOTE the socket's cam-status verb is ASYNC — it acks immediately and
    the reply lands in the NEXT status's cam_reply (measured live,
    2026-08-17). So callers nudge with cam_status() and then read a
    fresh status(); never parse the ack itself.
    """
    cam = (status or {}).get("cam_reply") or {}
    return int(cam.get("pub_ok") or 0), int(cam.get("pub_bytes") or 0)


def newest_sidecar(names):
    """Newest cap_*.json stem from a directory listing (UTC-named, so
    lexicographic order IS capture order)."""
    stems = sorted(n[:-5] for n in names
                   if n.startswith("cap_") and n.endswith(".json"))
    return stems[-1] if stems else None


def check_sidecar(side, jpeg, res, pf, q):
    """Verify one saved still against its own artifacts; return the row
    measurements. Raises ValueError with a reason on any mismatch."""
    req = side.get("req") or {}
    if (req.get("q"), req.get("res"), req.get("pf")) != (q, res, pf):
        raise ValueError("sidecar req %r is not the commanded (%d,%s,%s)"
                         % (req, q, res, pf))
    frame = side.get("frame") or {}
    led = side.get("ledger") or {}
    w, h, ncomp = sof_info(jpeg)
    want_w, want_h = RES_GEOM[res]
    if (w, h) != (want_w, want_h):
        raise ValueError("SOF says %dx%d, mode says %dx%d" % (w, h, want_w, want_h))
    if ncomp != PF_COMPONENTS[pf]:
        raise ValueError("SOF says %d components, pf %s needs %d"
                         % (ncomp, pf, PF_COMPONENTS[pf]))
    size = int(frame.get("size_bytes") or 0)
    if size != len(jpeg):
        raise ValueError("sidecar size_bytes %d != file %d" % (size, len(jpeg)))
    if int(led.get("gaps_delta") or 0) or int(led.get("dropped_delta") or 0):
        raise ValueError("ledger moved during the still: %r" % led)
    return {"bytes": size, "chunks": int(frame.get("chunks") or 0),
            "w": w, "h": h, "ncomp": ncomp}


def fmt_table(rows):
    """The 9-row table + stream rows as markdown."""
    out = ["| kind | mode | q | result |", "|---|---|---|---|"]
    for r in rows:
        mode = "%s %s" % (r["res"], r["pf"])
        if r["kind"] == "still" and r.get("ok"):
            res = "%d B, %d chunks (%dx%d/%d)" % (
                r["bytes"], r["chunks"], r["w"], r["h"], r["ncomp"])
        elif r["kind"] == "stream" and r.get("ok"):
            res = "%.2f fps, %.2f Mbps (%s, cmd %g fps)" % (
                r["fps"], r["mbps"], r.get("tag", ""), r["cmd_fps"])
        elif r["kind"] == "cert":
            res = "PASS (delivered %.1f s after command)" % r["latency_s"] \
                if r.get("ok") else "FAIL — " + r.get("reason", "?")
        else:
            res = "FAIL — " + r.get("reason", "?")
        out.append("| %s | %s | %d | %s |" % (r["kind"], mode, r["q"], res))
    return "\n".join(out)


# ---- the runner ----------------------------------------------------------

class Matrix:
    """Injected ctl/clock/sleep/listdir/reader so the logic is host-
    testable; production wiring is in main()."""

    def __init__(self, ctl, clock=time.monotonic, sleep=time.sleep,
                 listdir=None, read_file=None, log=None):
        self.ctl = ctl
        self.clock = clock
        self.sleep = sleep
        self.listdir = listdir or (lambda: os.listdir(CAPTURE_DIR))
        self.read_file = read_file or self._read_file
        self.log = log if log is not None else self._log
        self.rows = []
        self.cur_mode = None        # (res, pf) the sensor holds
        self.t_last_pub = None      # when the last frame finished arriving
        self.fail_streak = 0

    @staticmethod
    def _log(msg):
        # Timestamped AND flushed: the first live run produced an empty
        # log for 5 minutes (block-buffered print into a file — the S19
        # stdout trap, driving-side edition) and the post-mortem had to
        # be reconstructed from journals.
        print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

    @staticmethod
    def _read_file(name):
        with open(os.path.join(CAPTURE_DIR, name), "rb") as fh:
            return fh.read()

    # -- plumbing ----------------------------------------------------------
    def _poll(self, seconds):
        """Sleep `seconds` in POLL_S steps, polling status as keep-alive.

        cam_status too: it crosses the CDC leg, which is what actually
        keeps the bridge's quiet-exit timer fed (B2 ladder pattern) — a
        local socket status never leaves the Pi. Returns the last status.
        """
        end = self.clock() + seconds
        st = None
        while self.clock() < end:
            self.sleep(min(POLL_S, max(0.1, end - self.clock())))
            st = self.ctl.status()
            self.ctl.cam_status()
        return st

    def _wait_quiet(self, mode_change):
        if not mode_change or self.t_last_pub is None:
            return
        left = QUIET_S - (self.clock() - self.t_last_pub)
        if left > 0:
            self.log("  settle %.0f s (gate quiet before the re-init)" % left)
            self._poll(left)

    def _wait_save(self, save0, timeout_s):
        """Poll until the save counters move; (verdict, status)."""
        t0 = self.clock()
        while self.clock() - t0 < timeout_s:
            self.sleep(POLL_S)
            st = self.ctl.status()
            self.ctl.cam_status()      # CDC-leg keep-alive (B2 pattern)
            saved, errors = counters(st)
            if saved > save0[0]:
                return "saved", st
            if errors > save0[1]:
                return "save-error", st
        return "timeout", None

    def _quiesce(self, label, timeout_s=90.0):
        """Refuse to start a row until the chain is provably idle.

        THE fix for the first live run's failure mode: rows measured
        against windows that overlapped a previous row's activity
        (frames still flowing, a save landing late), which corrupted
        three rows and stopped the run. stop is never gated, so send it,
        then require frames_ok AND the save counters static across two
        consecutive polls. Returns the settled status (the row's true
        baseline) or None on timeout.
        """
        self.ctl.stop()
        t0 = self.clock()
        prev = None
        while self.clock() - t0 < timeout_s:
            self.sleep(POLL_S)
            st = self.ctl.status()
            self.ctl.cam_status()
            now = (int(ledger_of(st).get("frames_ok") or 0), counters(st))
            if prev is not None and now == prev:
                return st
            prev = now
        self.log("  %s: chain never went quiet in %.0f s" % (label, timeout_s))
        return None

    def _record(self, row):
        self.rows.append(row)
        if row.get("ok"):
            self.fail_streak = 0
        else:
            self.fail_streak += 1
        self.log("  -> %s" % json.dumps(row))

    # -- rows --------------------------------------------------------------
    def _find_capture(self, res, pf, q):
        """Newest sidecar whose req matches the command. Scans a few
        newest: the FIRST live run failed a row on someone else's save
        (a stream tail frame) — matching by req makes that impossible."""
        stems = sorted((n[:-5] for n in self.listdir()
                        if n.startswith("cap_") and n.endswith(".json")),
                       reverse=True)
        for stem in stems[:5]:
            try:
                side = json.loads(self.read_file(stem + ".json"))
            except (ValueError, OSError):
                continue
            req = side.get("req") or {}
            if (req.get("q"), req.get("res"), req.get("pf")) == (q, res, pf):
                return stem, side
        return None, None

    def run_still(self, res, pf, q):
        self.log("STILL %s %s q%d" % (res, pf, q))
        row = {"kind": "still", "res": res, "pf": pf, "q": q, "ok": False,
               "t": self.clock()}
        if self._quiesce("still") is None:
            row["reason"] = "chain would not go quiet before the row"
            self._record(row)
            return row
        mode_change = (res, pf) != self.cur_mode
        self._wait_quiet(mode_change)
        save0 = counters(self.ctl.status())
        self.ctl.capture(q=q, res=res, pf=pf)
        verdict, _st = self._wait_save(
            save0, STILL_REINIT_TIMEOUT_S if mode_change
            else STILL_SAME_TIMEOUT_S)
        if verdict != "saved":
            row["reason"] = ("save counters never moved (%s) — camera dead "
                             "or command refused (HE may still say ok=1)"
                             % verdict)
            self._record(row)
            return row
        stem, side = self._find_capture(res, pf, q)
        if stem is None:
            row["reason"] = "a save landed but no sidecar matches the command"
            self._record(row)
            return row
        try:
            jpeg = self.read_file(stem + ".jpg")
            row.update(check_sidecar(side, jpeg, res, pf, q))
            row["ok"] = True
            row["stem"] = stem
        except (ValueError, OSError, TypeError) as e:
            row["reason"] = str(e)
        self.cur_mode = (res, pf)
        self.t_last_pub = self.clock()
        self._record(row)
        return row

    def run_stream(self, res, pf, q, opts):
        self.log("STREAM %s %s q%d %s" % (res, pf, q, opts))
        row = {"kind": "stream", "res": res, "pf": pf, "q": q,
               "cmd_fps": opts["fps"], "cmd_mbps": opts["mbps"],
               "secs": opts["secs"], "tag": opts.get("tag", ""), "ok": False,
               "t": self.clock()}
        st0 = self._quiesce("stream-start")
        if st0 is None:
            row["reason"] = "chain would not go quiet before the row"
            self._record(row)
            return row
        mode_change = (res, pf) != self.cur_mode
        self._wait_quiet(mode_change)
        led0, pub0 = ledger_of(st0), cam_pub(st0)
        self.ctl.stream(mbps=opts["mbps"], fps=opts["fps"],
                        secs=opts["secs"], q=q, res=res, pf=pf)
        extra = QUIET_S if mode_change else 0    # gate holds the start
        self._poll(opts["secs"] + STREAM_SLACK_S + extra)
        # End on a PROVEN-idle chain, so the delta contains exactly this
        # stream — the first live run's windows overlapped rows and it
        # cost three of them.
        st1 = self._quiesce("stream-end")
        if st1 is None:
            row["reason"] = "stream never went quiet after its window"
            self._record(row)
            return row
        led1, pub1 = ledger_of(st1), cam_pub(st1)
        d = ledger_delta(led0, led1)
        frames = d["frames_ok"]
        pub_bytes = pub1[1] - pub0[1]
        row["frames"] = frames
        row["fps"] = frames / float(opts["secs"])
        row["mbps"] = pub_bytes * 8.0 / opts["secs"] / 1e6
        row["pub_bytes"] = pub_bytes
        row["ledger_delta"] = d
        bad = {k: v for k, v in d.items() if k != "frames_ok" and v}
        if frames <= 0:
            row["reason"] = "no frames delivered"
        elif bad:
            row["reason"] = "ledger moved: %r" % bad
        else:
            row["ok"] = True
        self.cur_mode = (res, pf)
        self.t_last_pub = self.clock()
        self._record(row)
        return row

    def run_cert(self, res, pf, q):
        """The 20 s constant vs daylight-HD bytes, self-contained.

        Generate the hazard's exact shape on demand: one HD colour still
        (the largest publish the bench can produce, ~93 KB reef) and
        then IMMEDIATELY a mode-changing capture. The bridge holds the
        re-init to its quiet window measured from that publish; whether
        anything delivers afterwards is the certification. Self-timed so
        it does not depend on the previous row's schedule (the first
        live run proved rows must not share windows).
        """
        self.log("CERT: HD publish, then a mode change at the gate window")
        row = {"kind": "cert", "res": res, "pf": pf, "q": q, "ok": False,
               "t": self.clock()}
        if self._quiesce("cert") is None:
            row["reason"] = "chain would not go quiet before the rung"
            self._record(row)
            return row
        hd = self.run_still("hd", "color", 50)      # the hot publish
        if not hd.get("ok"):
            row["reason"] = "could not produce the HD source publish"
            self._record(row)
            return row
        t0 = self.clock()
        led0 = ledger_of(self.ctl.status())
        # No save-based verdict here: the fork's save arm times out at
        # 8 s while the gate holds ~20 s. frames_ok is the truth.
        self.ctl.capture(q=q, res=res, pf=pf)
        while self.clock() - t0 < CERT_TIMEOUT_S:
            self.sleep(POLL_S)
            st = self.ctl.status()
            self.ctl.cam_status()
            if ledger_delta(led0, ledger_of(st))["frames_ok"] > 0:
                row["latency_s"] = self.clock() - t0
                break
        if "latency_s" not in row:
            row["reason"] = ("no frame within %.0f s of the held re-init — "
                             "20 s is NOT enough after daylight-HD publishes "
                             "(sensor likely latched; recovery: reboot "
                             "nereus000 + demo_up.sh)" % CERT_TIMEOUT_S)
            self._record(row)
            return row
        # Prove the sensor is actually alive with a normal same-mode still.
        self.cur_mode = (res, pf)
        self.t_last_pub = self.clock()
        confirm = self.run_still(res, pf, q)
        row["ok"] = bool(confirm.get("ok"))
        if not row["ok"]:
            row["reason"] = "held re-init delivered, but the confirm still failed"
        self._record(row)
        return row

    # -- the run -----------------------------------------------------------
    def run(self, plan=PLAN):
        st = self.ctl.status()
        if not st:
            raise SystemExit("no status from the control socket — chain up?")
        self.log("chain status ok; ledger %s" % json.dumps(ledger_of(st)))
        first_still_checked = False
        for kind, res, pf, q, opts in plan:
            if self.fail_streak >= 2:
                self.log("TWO consecutive failures — stopping (a latched "
                         "sensor fails everything downstream)")
                break
            if kind == "still":
                row = self.run_still(res, pf, q)
                if not first_still_checked and row.get("ok") \
                        and (res, pf, q) == ("qvga", "color", 50):
                    first_still_checked = True
                    if not (REEF_BYTES_MIN <= row["bytes"] <= REEF_BYTES_MAX):
                        raise SystemExit(
                            "TRIPWIRE: first still (qvga color q50) = %d B; "
                            "reef reference is ~9.2 KB, dark bench ~3.9 KB. "
                            "The ref scene is NOT active — stage it with "
                            "demo_up.sh --scene ref and rerun. No table was "
                            "produced on the wrong scene." % row["bytes"])
            elif kind == "stream":
                self.run_stream(res, pf, q, opts)
            elif kind == "cert":
                self.run_cert(res, pf, q)
        return self.rows


def main():
    from bench_ctl import BenchCtl   # imported late: host tests fake it
    plan = PLAN_RESUME if "--resume" in sys.argv[1:] else PLAN
    stills, streams, changes = plan_stats(plan)
    print("S18 reef matrix (%s): %d stills, %d streams, %d mode changes"
          % ("resume" if plan is PLAN_RESUME else "full",
             stills, streams, changes), flush=True)
    with BenchCtl() as ctl:
        m = Matrix(ctl)
        try:
            rows = m.run(plan)
        finally:
            out = os.path.join(
                CAPTURE_DIR, "matrix_%s.json"
                % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
            with open(out, "w") as fh:
                json.dump({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime()),
                           "plan": [list(r) for r in PLAN],
                           "rows": m.rows}, fh, indent=1)
            print("\nwrote %s" % out)
    print("\n" + fmt_table(rows))
    ok = sum(1 for r in rows if r.get("ok"))
    print("\nVERDICT: %d/%d rows ok%s"
          % (ok, len(rows), "" if ok == len(rows) else " — INCOMPLETE"))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
