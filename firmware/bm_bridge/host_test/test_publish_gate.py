#!/usr/bin/env python3
# test_publish_gate.py -- host tests for bm_bridge.PublishGate (CPython, no
# hardware, no HE core).
#
# What this guards. S18 bite B2 nibble 1 measured, off-chain, that a sensor
# re-init is safe with no HE core (12/12) and with the core loaded but idle
# (9/9), and that with the core PUBLISHING the first re-init took the board
# off the USB bus -- no Python exception, nothing to catch. The gate is the
# prevention, so its logic has to be right without a board in the loop.
#
# The barrier under test is the wire's own ordering: the HE consumes the
# inbound vring in order and publishes each WCMD_PUB inline, so a
# WCMD_QUERY posted after a frame's last chunk cannot be answered until
# those chunks are published. These tests drive the gate with a fake clock
# and a fake status_seq, which is exactly the pair the real loop feeds it.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bm_bridge                   # noqa: E402
from bm_bridge import (            # noqa: E402
    PublishGate, GATE_GO, GATE_WAIT, GATE_QUERY, GATE_REFUSE,
    CaptureEngine, CAMERA_MODE_SINGLE, CAMERA_MODE_STREAM, CAMERA_MODE_STOP,
    CAMERA_RES_QVGA, CAMERA_RES_VGA, CAMERA_PF_COLOR, CAMERA_PF_MONO,
    REINIT_DEADLINE_MS, REINIT_REQUERY_MS, REINIT_HEAP_SLACK,
)

checks = 0
fails = 0


def check(cond, what):
    global checks, fails
    checks += 1
    if not cond:
        fails += 1
        print("  FAIL: %s" % what)


def status(heap_free=20000):
    return {"heap_free": heap_free, "tx_frames": 0}


# ---- the open case: nothing published, nothing to wait for --------------
print("idle gate:")
g = PublishGate(min_quiet_ms=0)
check(g.poll(0, 0, None) == GATE_GO,
      "a gate that has seen no chunks opens immediately")
check(g.poll(0, 0, None) == GATE_GO, "and stays open when asked again")
check(g.opens == 0, "an always-open gate is not counted as an open")

g = PublishGate(min_quiet_ms=0)
g.note_chunks(0, 0)
check(g.poll(0, 0, None) == GATE_GO,
      "an empty frame (0 messages) arms nothing")


# ---- the barrier: query, then wait for the reply that must follow it ----
print("barrier sequence:")
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 1000)              # a 3-chunk QVGA frame = 9 rpmsg msgs
check(g.poll(1000, 5, status()) == GATE_QUERY,
      "chunks in flight -> the first ask posts the barrier query")
g.armed(5, 1000)
check(g.poll(1001, 5, status()) == GATE_WAIT,
      "same status_seq as when armed = the reply has NOT come back yet")
check(g.poll(1002, 5, status()) == GATE_WAIT, "still waiting")
check(g.poll(1003, 6, status()) == GATE_QUERY,
      "reply arrived -> our chunks drained -> but ask ONCE MORE: one reply "
      "cannot prove the synthetic stream publisher is idle")
g.armed(6, 1003)
check(g.poll(1004, 7, status()) == GATE_GO,
      "second reply, stream counters unchanged -> the HE is idle -> open")
check(g.opens == 1, "the open is counted")
check(g.poll(1005, 7, status()) == GATE_GO, "and the gate stays open after")

# A status that arrived BEFORE the query proves nothing.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 7, status())
g.armed(7, 0)
check(g.poll(1, 7, status()) == GATE_WAIT,
      "a stale reply (seq unchanged) does not open the barrier")


# ---- a new frame invalidates an outstanding barrier ---------------------
print("re-arming:")
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
g.note_chunks(9, 10)                # another frame went out while waiting
check(g.poll(11, 2, status()) == GATE_QUERY,
      "chunks sent after the query invalidate it -- a new barrier is posted")


# ---- the heap condition: bm_pub returns before the wire is clear --------
print("heap recovery:")
g = PublishGate(min_quiet_ms=0)
g.note_status(status(20712))        # learn the idle high-water
check(g.heap_high == 20712, "the high-water is learned, not hard-coded")
g.note_status(status(19000))
check(g.heap_high == 20712, "a lower reading does not lower the high-water")
g.note_chunks(9, 0)
g.poll(0, 1, status(20712))
g.armed(1, 0)
check(g.poll(1, 2, status(20712)) == GATE_QUERY, "stability sample first")
one_chunk_outstanding = 20712 - 1488
check(g.poll(2, 3, status(one_chunk_outstanding)) != GATE_GO,
      "reply back but a 1,488 B transmit copy still held -> not yet safe")
check(g.poll(3, 4, status(20712 - REINIT_HEAP_SLACK + 1)) == GATE_GO,
      "heap within the slack of the high-water -> safe")

g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, None)
g.armed(1, 0)
check(g.poll(1, 2, None) == GATE_QUERY, "even with no content, sample first")
check(g.poll(2, 3, None) == GATE_GO,
      "no status content to compare against -> the barrier pair alone opens")

g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, {"tx_frames": 3})      # a status carrying no heap_free
g.armed(1, 0)
g.poll(1, 2, {"tx_frames": 4})      # stability sample
check(g.poll(2, 3, {"tx_frames": 5}) == GATE_GO,
      "a status without heap_free does not gate on heap")

# The same reply polled twice must NOT satisfy the stability pair -- if
# the barrier query could not be re-sent, comparing a reply with itself
# would wave a live stream through.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
check(g.poll(1, 2, status()) == GATE_QUERY, "first reply samples")
check(g.poll(2, 2, status()) != GATE_GO,
      "re-polling the SAME reply is not a second observation")


# ---- re-query: a lost reply must not hang the gate ----------------------
print("re-query:")
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
check(g.poll(REINIT_REQUERY_MS - 1, 1, status()) == GATE_WAIT,
      "before the re-query interval the gate just waits")
check(g.poll(REINIT_REQUERY_MS, 1, status()) == GATE_QUERY,
      "a reply that never came is re-asked, not waited on forever")


# ---- the deadline: refuse, never re-init anyway -------------------------
print("deadline:")
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
check(g.poll(REINIT_DEADLINE_MS - 1, 1, status()) in (GATE_WAIT, GATE_QUERY),
      "just inside the deadline the gate is still trying")
check(g.poll(REINIT_DEADLINE_MS, 1, status()) == GATE_REFUSE,
      "at the deadline the command is REFUSED -- never re-init on a guess")
check(g.refusals == 1, "the refusal is counted")
# A refusal must NOT be mistaken for a drain. Those chunks were never
# confirmed published, so the next command posts its own barrier instead
# of inheriting a clean bill of health.
check(g.poll(REINIT_DEADLINE_MS + 1, 1, status()) == GATE_QUERY,
      "after a refusal the NEXT command re-arms -- it is not waved through")
g.armed(1, REINIT_DEADLINE_MS + 1)
check(g.poll(REINIT_DEADLINE_MS + 2, 2, status()) == GATE_QUERY,
      "and needs a fresh stability pair -- the refused wait's samples died "
      "with it")
g.armed(2, REINIT_DEADLINE_MS + 2)
check(g.poll(REINIT_DEADLINE_MS + 3, 3, status()) == GATE_GO,
      "and it can then open normally -- a refusal is not permanent")
check(g.poll(REINIT_DEADLINE_MS + 4, 3, status()) == GATE_GO,
      "once opened, the gate stays open until new chunks are sent")
check(g.refusals == 1 and g.opens == 1,
      "the ledger separates refusals from opens")

# The deadline runs from when the command started waiting, not from when
# the chunks were sent -- a command that arrives long after a frame must
# still get its full barrier attempt.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
check(g.poll(60000, 1, status()) == GATE_QUERY,
      "a command arriving long after the frame is not born expired")
g.armed(1, 60000)
check(g.poll(60001, 1, status()) == GATE_WAIT, "and gets its full deadline")

# Worst-case wait is recorded, so a gate that is quietly slow is visible.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
g.poll(400, 2, status())            # stability sample
g.poll(401, 3, status())            # opens
check(g.wait_ms_max == 401, "the worst wait is recorded for the exit ledger")


# ---- needs_reinit: only a real delta pays the gate ----------------------
print("needs_reinit:")
e = CaptureEngine()
e.cur_res, e.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
check(e.needs_reinit(None) is False, "no command, no re-init")
check(e.needs_reinit({"mode": CAMERA_MODE_STOP}) is False,
      "stop never touches the sensor, so stop is never gated")
check(e.needs_reinit({"mode": CAMERA_MODE_SINGLE, "res": CAMERA_RES_QVGA,
                      "pf": CAMERA_PF_COLOR}) is False,
      "a repeat capture at the same settings needs no re-init")
check(e.needs_reinit({"mode": CAMERA_MODE_SINGLE, "res": CAMERA_RES_QVGA,
                      "pf": CAMERA_PF_MONO}) is True,
      "colour -> mono re-inits: the exact transition bite B tripped over")
check(e.needs_reinit({"mode": CAMERA_MODE_STREAM, "res": CAMERA_RES_VGA,
                      "pf": CAMERA_PF_COLOR}) is True,
      "a resolution change re-inits")
e.cur_res = e.cur_pf = None         # geometry unknown after a failure
check(e.needs_reinit({"mode": CAMERA_MODE_SINGLE, "res": CAMERA_RES_QVGA,
                      "pf": CAMERA_PF_COLOR}) is True,
      "unknown geometry always re-applies, so it is always gated")


# ---- the sequence the bench actually performs ---------------------------
print("end-to-end: the bite B failure, replayed:")
# capture qvga colour -> chunks out -> immediately command qvga mono.
# Before B2 this re-initialised while the HE was publishing. Now:
g = PublishGate(min_quiet_ms=0)
e = CaptureEngine()
e.cur_res, e.cur_pf = CAMERA_RES_QVGA, CAMERA_PF_COLOR
cmd = {"mode": CAMERA_MODE_SINGLE, "res": CAMERA_RES_QVGA,
       "pf": CAMERA_PF_MONO}
g.note_chunks(9, 100)               # the QVGA frame's 3 chunks go to the HE
check(e.needs_reinit(cmd) is True, "the follow-up command is a re-init")
v = g.poll(100, 4, status(20712))
check(v == GATE_QUERY, "so it is gated, not applied: the barrier goes out")
g.armed(4, 100)
check(g.poll(101, 4, status(19000)) == GATE_WAIT,
      "at +1 ms -- where the board died -- the gate holds the command")
check(g.poll(140, 5, status(20712)) == GATE_QUERY,
      "the HE answered: our chunks drained; confirm the stream is idle")
g.armed(5, 140)
check(g.poll(145, 6, status(20712)) == GATE_GO,
      "second matching reply, heap back -> the re-init proceeds")


# ---- the synthetic stream publisher (the amendment) --------------------
print("synthetic stream publisher:")
# The HE's WCMD_STREAM relay stream publishes with NO bridge involvement,
# so the barrier alone cannot see it. stream_sent/stream_errs advancing
# between two replies is the tell.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
s = status()
s["stream_sent"] = 100
check(g.poll(5, 2, s) == GATE_QUERY, "first reply samples the counters")
s2 = status()
s2["stream_sent"] = 180
check(g.poll(300, 3, s2) != GATE_GO,
      "stream_sent advanced between replies -> the synthetic publisher is "
      "LIVE -> a re-init now is rung C -> hold")
s3 = status()
s3["stream_sent"] = 260
s3["stream_errs"] = 1
check(g.poll(600, 4, s3) != GATE_GO,
      "stream_errs counts too -- a failing publisher is still a publisher")
s4 = status()
s4["stream_sent"] = 260
s4["stream_errs"] = 1
check(g.poll(900, 5, s4) == GATE_GO,
      "two consecutive matching replies -> the stream went idle -> open")

# A stream that never goes idle: the command is refused at the deadline,
# never applied. There is no safe moment during a live stream to guess at.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
n, seq, t, v = 100, 2, 5, None
verdicts = set()
while t < REINIT_DEADLINE_MS + 500:
    s = status()
    s["stream_sent"] = n
    v = g.poll(t, seq, s)
    verdicts.add(v)
    if v == GATE_REFUSE:
        break
    n += 37
    seq += 1
    t += 137
check(GATE_GO not in verdicts,
      "a stream that never pauses NEVER lets a re-init through")
check(v == GATE_REFUSE, "it is refused at the deadline instead")

# Stability is proven per wait, never remembered across one.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
g.poll(1, 2, status())
check(g.poll(2, 3, status()) == GATE_GO, "first wait opens on its pair")
g.note_chunks(9, 10)
check(g.poll(10, 3, status()) == GATE_QUERY, "new chunks -> new barrier")
g.armed(3, 10)
check(g.poll(11, 4, status()) == GATE_QUERY,
      "and a fresh stability pair -- the old samples are gone")


# ---- a barrier query that could not be sent must not count -------------
print("unsent barrier:")
# The loop arms only when he.send() succeeded. If the vring was full and
# the send raised, armed() is never called -- the gate must ask again
# rather than wait forever on a barrier that was never posted.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
check(g.poll(0, 1, status()) == GATE_QUERY, "first ask posts a query")
check(g.poll(1, 1, status()) == GATE_QUERY,
      "still unarmed (send failed) -> ask again, do not wait on nothing")
check(g.poll(2, 5, status()) == GATE_QUERY,
      "and an unrelated status arriving does not open an unposted barrier")


# ---- the whole point, stated as a test ---------------------------------
print("invariant:")
# There is no input sequence in which a gate with chunks in flight and no
# confirming reply returns GO. If this ever passes something through, the
# board is what pays.
g = PublishGate(min_quiet_ms=0)
g.note_chunks(9, 0)
g.armed(3, 0)
leaked = []
for t in range(0, REINIT_DEADLINE_MS, 137):
    for seq in (0, 1, 2, 3):        # every seq at or BELOW the barrier
        if g.poll(t, seq, status(20712)) == GATE_GO:
            leaked.append((t, seq))
    g.armed(3, t)                   # keep the barrier at seq 3
check(not leaked,
      "no unconfirmed re-init is ever allowed through (%d leaks)" % len(leaked))


# ---- the quiet window: THE binding condition (rungs C-F) ---------------
print("quiet window:")
check(bm_bridge.REINIT_MIN_QUIET_MS == 20000,
      "20 s: safe side of the measured VGA boundary (10 s FAIL / 15 s "
      "PASS on-chain, dark frames); HD unmeasured -- matrix revisits")
check(bm_bridge.REINIT_DEADLINE_MS > bm_bridge.REINIT_MIN_QUIET_MS,
      "the deadline budgets barrier time BEYOND the quiet window")

g = PublishGate(min_quiet_ms=1000)
g.note_chunks(9, 0)
check(g.poll(0, 1, status()) == GATE_WAIT,
      "inside the quiet window: WAIT, no barrier query is wasted")
check(g.poll(999, 1, status()) == GATE_WAIT, "still inside")
check(g.poll(1000, 1, status()) == GATE_QUERY,
      "window elapsed -> the barrier starts")
g.armed(1, 1000)
g.poll(1001, 2, status())
g.armed(2, 1001)
check(g.poll(1002, 3, status()) == GATE_GO, "then opens as before")

# The clock runs from the PUBLISH, not from the command: a command at a
# human pace finds the window already elapsed and pays only the barrier.
g = PublishGate(min_quiet_ms=1000)
g.note_chunks(9, 0)
check(g.poll(5000, 1, status()) == GATE_QUERY,
      "human-pace command: window already elapsed, straight to barrier")

# A later frame restarts the clock -- quiet since the LAST publish.
g = PublishGate(min_quiet_ms=1000)
g.note_chunks(9, 0)
g.note_chunks(9, 800)
check(g.poll(1000, 1, status()) == GATE_WAIT,
      "a later frame restarts the quiet clock")
check(g.poll(1800, 1, status()) == GATE_QUERY,
      "which elapses from ITS publish time")

# The deadline still binds with the window active.
g = PublishGate(min_quiet_ms=1000, deadline_ms=1500)
g.note_chunks(9, 0)
g.poll(0, 1, status())
check(g.poll(1500, 1, status()) == GATE_REFUSE,
      "a window the deadline outruns still ends in a refusal, not a guess")


# ---- self-heal: the backstop for the measured wedge --------------------
print("self-heal:")
import types                        # noqa: E402
fake = types.ModuleType("sensor")
fake.QVGA, fake.VGA, fake.HD = 1, 2, 3
fake.RGB565, fake.GRAYSCALE = 10, 11
calls = {"reset": 0, "fail_next_fb": 0}
fake.reset = lambda: calls.__setitem__("reset", calls["reset"] + 1)
fake.set_pixformat = lambda v: None
fake.set_framesize = lambda v: None


def _fb(n):
    if calls["fail_next_fb"] > 0:
        calls["fail_next_fb"] -= 1
        raise RuntimeError("Sensor control failed.")


fake.set_framebuffers = _fb
fake.skip_frames = lambda time=0: None
sys.modules["sensor"] = fake

e = CaptureEngine()
check(e.bootstrap() is True, "fake bootstrap claims the ceiling")
check(e.cur_res == bm_bridge.CAMERA_RES_HD, "at the HD ceiling")
# Rung E's wedge, replayed: one 'Sensor control failed.' on the
# framebuffer call. The engine must reset, re-bootstrap and retry
# instead of marking the sensor dead for the rest of the run.
calls["fail_next_fb"] = 1
r0 = calls["reset"]
check(e._ensure_sensor(CAMERA_RES_QVGA, CAMERA_PF_MONO) is True,
      "one failing set_framebuffers -> self-heal -> command succeeds")
check(calls["reset"] == r0 + 1, "the heal really did reset the sensor")
check(e.cur_res == CAMERA_RES_QVGA and e.cur_pf == CAMERA_PF_MONO,
      "geometry lands where commanded after the heal")
# A wedge the reset cannot clear: refuse the command, never loop.
calls["fail_next_fb"] = 99
check(e._ensure_sensor(CAMERA_RES_VGA, CAMERA_PF_COLOR) is False,
      "a heal that fails refuses the command -- no retry loop")
check(e.cur_res is None, "and leaves geometry marked unknown")
check(e.booted is False,
      "a failed heal-bootstrap latches the camera off (allocator rule)")
del sys.modules["sensor"]


# ---- reset-on-change: the mode file must round-trip and reject junk ---
print("mode file (reset-on-change):")
from bm_bridge import mode_file_json, parse_mode_file  # noqa: E402
ok_modes = [(r, p) for r in (bm_bridge.CAMERA_RES_QVGA,
                             bm_bridge.CAMERA_RES_VGA,
                             bm_bridge.CAMERA_RES_HD)
            for p in (CAMERA_PF_COLOR, CAMERA_PF_MONO)]
for r, p in ok_modes:
    check(parse_mode_file(mode_file_json(r, p)) == (r, p),
          "round-trip res=%d pf=%d" % (r, p))
check(parse_mode_file("") is None, "empty file -> None, never a crash")
check(parse_mode_file("not json") is None, "garbage -> None")
check(parse_mode_file('{"res": 9, "pf": 1}') is None,
      "unknown res (a FUTURE bridge's mode) -> None -> ceiling boot")
check(parse_mode_file('{"res": 1, "pf": 7}') is None, "unknown pf -> None")
check(parse_mode_file('{"res": "hd"}') is None,
      "missing/typed-wrong fields -> None")
check(parse_mode_file('{"res": 3, "pf": 2}') ==
      (bm_bridge.CAMERA_RES_HD, CAMERA_PF_MONO),
      "the HD-mono video mode survives the trip")

print("PublishGate host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
