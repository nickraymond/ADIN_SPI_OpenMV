#!/usr/bin/env python3
"""Host tests for bench/s18_matrix.py — no Pi, no chain, no board.

The runner takes injected ctl/clock/sleep/listdir/read_file, so the whole
row logic runs against a scripted fake node. The fake advances a virtual
clock on sleep(), which also drives the scripted status timeline.
"""

import io
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import s18_matrix as M   # noqa: E402

checks = 0
fails = 0


def check(cond, what):
    global checks, fails
    checks += 1
    if not cond:
        fails += 1
        print("FAIL: %s" % what)


def make_jpeg(w, h, ncomp, size=None):
    """Minimal marker-valid JPEG: SOI, APP0, SOF0, EOI — optionally
    padded to an exact byte size with a COM segment."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
    sof = (b"\xff\xc0" + struct.pack(">H", 8 + 3 * ncomp) + b"\x08" +
           struct.pack(">HH", h, w) + bytes([ncomp]) + bytes(3 * ncomp))
    body = b"\xff\xd8" + app0 + sof
    if size is not None:
        pad = size - len(body) - 2 - 4     # EOI + COM marker/len
        assert pad >= 0, "size too small for the fixed markers"
        body += b"\xff\xfe" + struct.pack(">H", pad + 2) + bytes(pad)
    return body + b"\xff\xd9"


# ---- pure helpers --------------------------------------------------------

w, h, n = M.sof_info(make_jpeg(320, 200, 3))
check((w, h, n) == (320, 200, 3), "sof_info reads SOF0 geometry+components")
w, h, n = M.sof_info(make_jpeg(1280, 800, 1))
check((w, h, n) == (1280, 800, 1), "sof_info: HD mono")
try:
    M.sof_info(b"\x00\x01")
    check(False, "sof_info rejects non-JPEG")
except ValueError:
    check(True, "sof_info rejects non-JPEG")

stills, streams, changes = M.plan_stats(M.PLAN)
check(stills == 9, "the plan has exactly 9 still rows (the 9-row table)")
check(streams == 7,
      "7 stream rows: the S17 regression run + 6 ceiling measurements")
check(changes == 6, "one visit per mode: 6 changes incl. the cert rung")
check(M.PLAN[0][:4] == ("still", "qvga", "color", 50),
      "first row is the reef tripwire (qvga color q50)")
check(M.PLAN[-1][0] == "cert", "the cert rung is last")
check(M.PLAN[-2][:3] == ("stream", "hd", "color"),
      "cert fires right after the HD color stream (largest publishes)")

check(M.newest_sidecar(["cap_20260817T010101Z_seq1.json",
                        "cap_20260817T020202Z_seq2.json",
                        "cap_20260817T020202Z_seq2.jpg", "junk.txt"])
      == "cap_20260817T020202Z_seq2", "newest_sidecar picks by UTC name")
check(M.newest_sidecar(["x.jpg"]) is None, "newest_sidecar: none is None")

led0 = {"frames_ok": 10, "gaps": 0, "dropped": 0, "hdr_errs": 0,
        "q_drops": 0, "ingest_fail": 0}
led1 = dict(led0, frames_ok=910)
check(M.ledger_delta(led0, led1)["frames_ok"] == 900, "ledger_delta math")

side = {"req": {"q": 50, "res": "qvga", "pf": "color"},
        "frame": {"size_bytes": None, "chunks": 7},
        "ledger": {"gaps_delta": 0, "dropped_delta": 0}}
jpeg = make_jpeg(320, 200, 3)
side["frame"]["size_bytes"] = len(jpeg)
row = M.check_sidecar(side, jpeg, "qvga", "color", 50)
check(row["bytes"] == len(jpeg) and row["chunks"] == 7,
      "check_sidecar returns the measured row")
try:
    M.check_sidecar(side, jpeg, "qvga", "mono", 50)
    check(False, "check_sidecar rejects a req mismatch")
except ValueError:
    check(True, "check_sidecar rejects a req mismatch")
try:
    M.check_sidecar(side, make_jpeg(320, 200, 1), "qvga", "color", 50)
    check(False, "check_sidecar rejects wrong component count")
except ValueError as e:
    check("components" in str(e), "check_sidecar rejects wrong components")
bad = dict(side, ledger={"gaps_delta": 1, "dropped_delta": 0})
try:
    M.check_sidecar(bad, jpeg, "qvga", "color", 50)
    check(False, "check_sidecar rejects a moved ledger")
except ValueError:
    check(True, "check_sidecar rejects a moved ledger")


# ---- the runner against a scripted fake node -----------------------------

class FakeNode:
    """ctl + clock + sleep + capture dir in one object.

    Behavior: a capture 'delivers' `deliver_after` seconds after the
    command; a stream delivers `stream_fps` frames/s for its duration.
    """

    def __init__(self):
        self.t = 1000.0
        self.saved = 0
        self.errors = 0
        self.frames_ok = 0
        self.pub_bytes = 0
        self.files = {}
        self.deliver_after = 1.0
        self.deliver_after_once = None   # one-shot override (gate hold)
        self.deliver_bytes = 9198
        self.deliver_chunks = 7
        self.dead = False           # a latched sensor: nothing delivers
        self.stream_fps = 15.0
        self._pending = None        # (t_due, res, pf, q)
        self._stream = None         # (t_end, fps, res, pf, q)
        self.seq = 0

    # clock/sleep injected into Matrix
    def clock(self):
        return self.t

    def sleep(self, s):
        step = 0.1
        left = s
        while left > 0:
            self.t += min(step, left)
            left -= step
            self._advance()

    def _advance(self):
        if self._pending and self.t >= self._pending[0] and not self.dead:
            _, res, pf, q = self._pending
            self._pending = None
            self._deliver_still(res, pf, q)
        if self._stream:
            t_end, fps, res, pf, q = self._stream
            due = int(min(self.t, t_end) - self._t_stream0) * fps
            while self.frames_ok - self._frames0 < due:
                self.frames_ok += 1
                self.pub_bytes += self.deliver_bytes
            if self.t >= t_end:
                self._stream = None

    def _deliver_still(self, res, pf, q):
        self.seq += 1
        w, h = M.RES_GEOM[res]
        jpeg = make_jpeg(w, h, M.PF_COMPONENTS[pf], size=self.deliver_bytes)
        stem = "cap_20260817T%06dZ_seq%06d" % (self.seq, self.seq)
        side = {"req": {"q": q, "res": res, "pf": pf},
                "frame": {"size_bytes": len(jpeg),
                          "chunks": self.deliver_chunks},
                "ledger": {"gaps_delta": 0, "dropped_delta": 0}}
        self.files[stem + ".json"] = json.dumps(side).encode()
        self.files[stem + ".jpg"] = jpeg
        self.saved += 1
        self.frames_ok += 1
        self.pub_bytes += len(jpeg)

    # BenchCtl surface
    def status(self):
        self._advance()
        return {"save": {"saved": self.saved, "errors": self.errors},
                "ledger": {"frames_ok": self.frames_ok, "dropped": 0,
                           "gaps": 0, "hdr_errs": 0, "q_drops": 0,
                           "ingest_fail": 0},
                "cam_reply": {"pub_ok": 0, "pub_bytes": self.pub_bytes}}

    def cam_status(self):
        return self.status()

    def capture(self, q=None, res=None, pf=None, save=None):
        if not self.dead:
            delay = self.deliver_after
            if self.deliver_after_once is not None:
                delay = self.deliver_after_once
                self.deliver_after_once = None
            self._pending = (self.t + delay, res, pf, q)
        return {"ok": True}

    def stream(self, mbps=None, fps=None, secs=None, q=None, res=None,
               pf=None):
        if not self.dead:
            self._t_stream0 = self.t
            self._frames0 = self.frames_ok
            self._stream = (self.t + secs, min(fps, self.stream_fps),
                            res, pf, q)
        return {"ok": True}

    def listdir(self):
        return list(self.files)

    def read_file(self, name):
        return self.files[name]


def make_matrix(node):
    return M.Matrix(node, clock=node.clock, sleep=node.sleep,
                    listdir=node.listdir, read_file=node.read_file,
                    log=lambda *a: None)


# a good still row
node = FakeNode()
m = make_matrix(node)
row = m.run_still("qvga", "color", 50)
check(row["ok"], "still row: delivered and verified")
check(row["bytes"] == node.deliver_bytes, "still row bytes from the artifact")
check(row["chunks"] == 7 and row["ncomp"] == 3, "still row measurements")

# a q-only follow-up must not wait the quiet window
t0 = node.t
row = m.run_still("qvga", "color", 90)
check(row["ok"] and node.t - t0 < M.QUIET_S,
      "q change: no settle wait (no re-init)")

# a mode change AFTER a publish waits the quiet window first
t0 = node.t
row = m.run_still("qvga", "mono", 50)
check(row["ok"], "mode-change still delivers")
check(node.t - t0 >= M.QUIET_S - 1, "mode change waited the quiet window")

# a dead camera: timeout recorded, not a crash; two in a row stop the run
node = FakeNode()
node.dead = True
m = make_matrix(node)
row = m.run_still("vga", "color", 50)
check(not row["ok"] and "never moved" in row["reason"],
      "dead camera still: recorded as a timeout row")
m.run_still("vga", "mono", 50)
check(m.fail_streak == 2, "two failures counted")
rows = m.run([("still", "hd", "color", 50, None)])
check(len(rows) == 2, "run() stops at two consecutive failures")

# stream row: delivered fps + Mbps from the deltas
node = FakeNode()
node.stream_fps = 12.0
m = make_matrix(node)
m.cur_mode = ("qvga", "color")
row = m.run_stream("qvga", "color", 50,
                   {"mbps": 4.0, "fps": 30, "secs": 10, "tag": "t"})
check(row["ok"], "stream row ok")
check(abs(row["fps"] - 12.0) <= 1.5,
      "stream fps ~= the node's ceiling, not the commanded 30 (%r)"
      % row["fps"])
check(row["mbps"] > 0, "stream Mbps computed from pub_bytes")

# cert rung: pass path (frame delivers after the held re-init; the
# confirm still afterwards is a normal fast same-mode capture)
node = FakeNode()
node.deliver_after_once = 21.0     # the gate hold, once
m = make_matrix(node)
m.t_last_pub = node.t
row = m.run_cert("qvga", "color", 50)
check(row["ok"] and row["latency_s"] >= 20.0,
      "cert PASS: delivery after the hold, confirm still good")

# cert rung: latch path
node = FakeNode()
node.dead = True
m = make_matrix(node)
row = m.run_cert("qvga", "color", 50)
check(not row["ok"] and "NOT enough" in row["reason"],
      "cert FAIL names the finding and the recovery")

# the reef tripwire aborts the run on dark-scene bytes
node = FakeNode()
node.deliver_bytes = 3900      # dark bench signature
mm = make_matrix(node)
try:
    mm.run([("still", "qvga", "color", 50, None)])
    check(False, "tripwire aborts on dark-scene bytes")
except SystemExit as e:
    check("TRIPWIRE" in str(e), "tripwire aborts on dark-scene bytes")

# ...and lets reef bytes through
node = FakeNode()
node.deliver_bytes = 9198
mm = make_matrix(node)
rows = mm.run([("still", "qvga", "color", 50, None)])
check(rows[0]["ok"], "tripwire passes reef bytes")

# fmt_table renders every row kind without raising
node = FakeNode()
m = make_matrix(node)
m.run_still("qvga", "color", 50)
tbl = M.fmt_table(m.rows)
check("still" in tbl and "|" in tbl, "fmt_table renders")

print("s18_matrix host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
