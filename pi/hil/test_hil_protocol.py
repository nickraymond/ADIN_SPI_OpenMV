#!/usr/bin/env python3
"""Closed-loop protocol tests (bite E4) — the test-first contract:
the communication method is proven against FAKE boards covering the
ugly cases BEFORE any hardware demo.

Run: python3 pi/hil/test_hil_protocol.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hil_protocol import ClosedLoopScheduler  # noqa: E402


class FakeBoard:
    """Mirrors hil_board.py's closed-loop state machine: waits for 'g'
    (emit one frame) or 'p' (advance phase, emit #PH). Failure modes
    injected per test: die_on_g_at (stream ends mid-phase), stall_at
    (goes silent), error_phase (emits a #PH error and sits it out —
    like whole-mode-on-HD)."""

    def __init__(self, label, n_phases, die_on_g_at=None, stall_at=None,
                 error_phase=None):
        self.label = label
        self.n_phases = n_phases
        self.die_on_g_at = die_on_g_at        # (phase_i, g_count)
        self.stall_at = stall_at
        self.error_phase = error_phase
        self.phase_i = 0
        self.g_count = 0
        self.sent = []                        # bytes received, for asserts
        self.pending = []
        self.dead = False
        self._enter_phase(0)

    def _enter_phase(self, i):
        """Queue #PH for phase i; an error phase self-advances exactly
        like the real board's `continue` (no control byte consumed)."""
        self.phase_i = i
        if i >= self.n_phases:
            self.pending.append(("done", {"frames": self.g_count}))
            return
        if i == self.error_phase:
            self.pending.append(
                ("phase", {"phase": i, "error": "sat out (test)"}))
            self._enter_phase(i + 1)
        else:
            self.pending.append(("phase", {"phase": i, "kind": "model"}))

    def send(self, byte):
        if self.dead:
            raise IOError(f"{self.label}: port closed")
        self.sent.append((self.phase_i, byte))
        if byte == b"g":
            self.g_count += 1
            if self.die_on_g_at == (self.phase_i, self.g_count):
                self.dead = True
                self.pending.append(("end", {"reason": "usb"}))
                return
            if self.stall_at == (self.phase_i, self.g_count):
                return                        # never answers
            self.pending.append(
                ("frame", {"seq": self.g_count, "ph": self.phase_i}))
        elif byte == b"p":
            self._enter_phase(self.phase_i + 1)

    def read_event(self, timeout_s):
        if self.pending:
            return self.pending.pop(0)
        raise IOError(f"board silent for {timeout_s:.0f}s")


def drive(boards, stills=(0, 1, 2), k=2, n_phases=2, **kw):
    """Mimic the harness loop: barrier -> model phase, per phase.
    Collects (label, phase, still_pos, frame_idx) tuples."""
    got = []
    sched = ClosedLoopScheduler(
        boards, advance=lambda **a: None, wait=lambda s: None,
        log=lambda *a: None, **kw)
    for ph in range(n_phases):
        joined = sched.phase_barrier(ph)
        sched.run_model_phase(
            ph, joined, list(stills), k,
            lambda b, pos, fr, fi, _ph=ph:
                got.append((b.label, _ph, pos, fi)))
    return sched, got


class TestClosedLoop(unittest.TestCase):
    def test_two_boards_full_run(self):
        a, b = FakeBoard("A", 2), FakeBoard("B", 2)
        sched, got = drive([a, b])
        # every (board, phase, still, frame) cell exactly once
        self.assertEqual(len(got), 2 * 2 * 3 * 2)
        self.assertEqual(len(set(got)), len(got))
        # per phase: one 'g' per frame slot, exactly one 'p'
        for board in (a, b):
            for ph in (0, 1):
                bytes_ph = [by for (p, by) in board.sent if p == ph]
                self.assertEqual(bytes_ph.count(b"g"), 3 * 2)
                self.assertEqual(bytes_ph.count(b"p"), 1)
        self.assertEqual(sched.dead, [])

    def test_board_dies_mid_phase_other_completes(self):
        a = FakeBoard("A", 2, die_on_g_at=(0, 3))
        b = FakeBoard("B", 2)
        sched, got = drive([a, b])
        self.assertEqual(len(sched.dead), 1)
        self.assertEqual(sched.dead[0][0].label, "A")
        # B still delivered every cell
        b_cells = [g for g in got if g[0] == "B"]
        self.assertEqual(len(b_cells), 2 * 3 * 2)
        # A delivered only its pre-death frames
        a_cells = [g for g in got if g[0] == "A"]
        self.assertEqual(len(a_cells), 2)

    def test_board_stall_dropped_by_timeout(self):
        a = FakeBoard("A", 2, stall_at=(0, 2))
        b = FakeBoard("B", 2)
        sched, got = drive([a, b], frame_timeout_s=0.05)
        self.assertEqual([d[0].label for d in sched.dead], ["A"])
        self.assertEqual(len([g for g in got if g[0] == "B"]), 12)

    def test_error_phase_board_sits_out_and_gets_no_bytes(self):
        a = FakeBoard("A", 2, error_phase=0)
        b = FakeBoard("B", 2)
        sched, got = drive([a, b])
        # A got ZERO control bytes during phase 0 (the stray-'p' trap)
        self.assertEqual([by for (p, by) in a.sent if p == 0], [])
        # A fully participates in phase 1
        self.assertEqual(len([g for g in got if g == ("A", 1, 0, 1)]), 1)
        self.assertEqual(len([g for g in got if g[0] == "A" and g[1] == 1]),
                         3 * 2)
        self.assertEqual(sched.dead, [])

    def test_all_boards_dead_is_loud(self):
        a = FakeBoard("A", 1, die_on_g_at=(0, 1))
        with self.assertRaises(SystemExit):
            drive([a], n_phases=1)

    def test_phase_desync_drops_board(self):
        a, b = FakeBoard("A", 2), FakeBoard("B", 2)
        a.pending = [("phase", {"phase": 5, "kind": "model"})]
        sched, got = drive([a, b])
        self.assertEqual([d[0].label for d in sched.dead], ["A"])
        self.assertEqual(len([g for g in got if g[0] == "B"]), 12)

    def test_skip_events_tolerated(self):
        """Corrupted lines surface as 'skip' from the stream layer; the
        scheduler must keep waiting for the real frame."""
        a = FakeBoard("A", 1)
        orig = a.send

        def send(byte):
            orig(byte)
            if byte == b"g" and a.pending:
                a.pending.insert(0, ("skip", {"seq": -1}))
        a.send = send
        sched, got = drive([a], n_phases=1)
        self.assertEqual(len(got), 6)

    def test_error_phase_then_all_boards_sit_out(self):
        """Every board error-skips a phase -> zero cells, nobody dies,
        run continues to the next phase."""
        a = FakeBoard("A", 2, error_phase=0)
        sched, got = drive([a])
        self.assertEqual([g for g in got if g[1] == 0], [])
        self.assertEqual(len([g for g in got if g[1] == 1]), 6)
        self.assertEqual(sched.dead, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
