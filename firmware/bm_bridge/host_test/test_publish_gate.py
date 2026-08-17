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
g = PublishGate()
check(g.poll(0, 0, None) == GATE_GO,
      "a gate that has seen no chunks opens immediately")
check(g.poll(0, 0, None) == GATE_GO, "and stays open when asked again")
check(g.opens == 0, "an always-open gate is not counted as an open")

g = PublishGate()
g.note_chunks(0, 0)
check(g.poll(0, 0, None) == GATE_GO,
      "an empty frame (0 messages) arms nothing")


# ---- the barrier: query, then wait for the reply that must follow it ----
print("barrier sequence:")
g = PublishGate()
g.note_chunks(9, 1000)              # a 3-chunk QVGA frame = 9 rpmsg msgs
check(g.poll(1000, 5, status()) == GATE_QUERY,
      "chunks in flight -> the first ask posts the barrier query")
g.armed(5, 1000)
check(g.poll(1001, 5, status()) == GATE_WAIT,
      "same status_seq as when armed = the reply has NOT come back yet")
check(g.poll(1002, 5, status()) == GATE_WAIT, "still waiting")
check(g.poll(1003, 6, status()) == GATE_GO,
      "status_seq advanced past the barrier -> the HE drained our chunks")
check(g.opens == 1, "the open is counted")
check(g.poll(1004, 6, status()) == GATE_GO, "and the gate stays open after")

# A status that arrived BEFORE the query proves nothing.
g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 7, status())
g.armed(7, 0)
check(g.poll(1, 7, status()) == GATE_WAIT,
      "a stale reply (seq unchanged) does not open the barrier")


# ---- a new frame invalidates an outstanding barrier ---------------------
print("re-arming:")
g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
g.note_chunks(9, 10)                # another frame went out while waiting
check(g.poll(11, 2, status()) == GATE_QUERY,
      "chunks sent after the query invalidate it -- a new barrier is posted")


# ---- the heap condition: bm_pub returns before the wire is clear --------
print("heap recovery:")
g = PublishGate()
g.note_status(status(20712))        # learn the idle high-water
check(g.heap_high == 20712, "the high-water is learned, not hard-coded")
g.note_status(status(19000))
check(g.heap_high == 20712, "a lower reading does not lower the high-water")
g.note_chunks(9, 0)
g.poll(0, 1, status(20712))
g.armed(1, 0)
one_chunk_outstanding = 20712 - 1488
check(g.poll(1, 2, status(one_chunk_outstanding)) != GATE_GO,
      "reply back but a 1,488 B transmit copy still held -> not yet safe")
check(g.poll(1, 2, status(20712 - REINIT_HEAP_SLACK + 1)) == GATE_GO,
      "heap within the slack of the high-water -> safe")

g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 1, None)
g.armed(1, 0)
check(g.poll(1, 2, None) == GATE_GO,
      "no status content to compare against -> the barrier alone opens it")

g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 1, {"tx_frames": 3})      # a status carrying no heap_free
g.armed(1, 0)
check(g.poll(1, 2, {"tx_frames": 4}) == GATE_GO,
      "a status without heap_free does not gate on heap")


# ---- re-query: a lost reply must not hang the gate ----------------------
print("re-query:")
g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
check(g.poll(REINIT_REQUERY_MS - 1, 1, status()) == GATE_WAIT,
      "before the re-query interval the gate just waits")
check(g.poll(REINIT_REQUERY_MS, 1, status()) == GATE_QUERY,
      "a reply that never came is re-asked, not waited on forever")


# ---- the deadline: refuse, never re-init anyway -------------------------
print("deadline:")
g = PublishGate()
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
check(g.poll(REINIT_DEADLINE_MS + 2, 2, status()) == GATE_GO,
      "and it can then open normally -- a refusal is not permanent")
check(g.poll(REINIT_DEADLINE_MS + 3, 2, status()) == GATE_GO,
      "once opened, the gate stays open until new chunks are sent")
check(g.refusals == 1 and g.opens == 1,
      "the ledger separates refusals from opens")

# The deadline runs from when the command started waiting, not from when
# the chunks were sent -- a command that arrives long after a frame must
# still get its full barrier attempt.
g = PublishGate()
g.note_chunks(9, 0)
check(g.poll(60000, 1, status()) == GATE_QUERY,
      "a command arriving long after the frame is not born expired")
g.armed(1, 60000)
check(g.poll(60001, 1, status()) == GATE_WAIT, "and gets its full deadline")

# Worst-case wait is recorded, so a gate that is quietly slow is visible.
g = PublishGate()
g.note_chunks(9, 0)
g.poll(0, 1, status())
g.armed(1, 0)
g.poll(400, 2, status())
check(g.wait_ms_max == 400, "the worst wait is recorded for the exit ledger")


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
g = PublishGate()
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
check(g.poll(140, 5, status(20712)) == GATE_GO,
      "once the HE answers and the heap is back, the re-init proceeds")


# ---- a barrier query that could not be sent must not count -------------
print("unsent barrier:")
# The loop arms only when he.send() succeeded. If the vring was full and
# the send raised, armed() is never called -- the gate must ask again
# rather than wait forever on a barrier that was never posted.
g = PublishGate()
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
g = PublishGate()
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

print("PublishGate host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
