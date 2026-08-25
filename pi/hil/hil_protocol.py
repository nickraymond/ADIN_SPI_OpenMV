"""Closed-loop HIL scheduler (S8 bite E4) — Nick's design as the spec.

The Pi hosts the image, TELLS each camera to start inferring, each
camera reports DONE and waits, and only then does the Pi advance the
image. The control channel is one byte host->board on the VCP:

    b"g"   go — run ONE frame cycle (capture + infer + emit #F...)
    b"p"   phase over — advance to the next phase

Correct-by-construction consequences (vs the retired open-loop path):
capture can only START after the still is up, so settle windows and
frame budgets are deleted, not tuned; both boards run simultaneously
(a still advances when ALL live boards have reported); a phase ends
the moment the host has what it needs (no drain tail).

This module is deliberately I/O-free: boards are anything implementing
read_event(timeout_s)/send(byte)/label, and waits are injected — so the
whole protocol is unit-testable against fake boards (test_hil_protocol
covers dies-mid-phase, stall, garbage, phase-error; the measured
open-loop hazard this replaces is DEV_LOG 2026-08-25: AE3 HD frame-1s
0.33 recall vs frame-2s 0.59).

Failure containment: a board that stalls past its timeout or whose
stream ends is DROPPED — the run continues with the survivors and
scores what was collected. Every drop is loud.
"""
import time


class BoardDead(Exception):
    """Raised internally when a board leaves the run."""


class ClosedLoopScheduler:
    """Drive N boards through a shared phase list, one handshake at a
    time. `boards` is a list of objects with .label, .read_event(
    timeout_s) -> (kind, obj), .send(one_byte). `advance` is called to
    change the screen (still index or page name); `wait` (injectable
    for tests) sleeps the LCD render latency before any 'g'."""

    def __init__(self, boards, advance, render_wait_s=0.8,
                 frame_timeout_s=30.0, phase_timeout_s=60.0,
                 wait=time.sleep, log=print):
        self.live = list(boards)
        self.dead = []
        self.advance = advance
        self.render_wait_s = render_wait_s
        self.frame_timeout_s = frame_timeout_s
        self.phase_timeout_s = phase_timeout_s
        self.wait = wait
        self.log = log

    # ------------------------------------------------------------ plumbing
    def _drop(self, board, why):
        self.log(f"    BOARD DROPPED: {board.label} — {why}")
        self.live.remove(board)
        self.dead.append((board, why))
        if not self.live:
            raise SystemExit(
                "FAIL: every board left the run — nothing to schedule")

    def _read_until(self, board, kinds, timeout_s, on_frame=None):
        """Read a board until an event in `kinds` arrives. Frames seen
        while waiting for a phase marker are passed to on_frame (late
        frames from the previous 'g' are attributed by the CALLER's
        current-still state, which is still correct — the screen has
        not advanced yet). Returns the event or raises BoardDead."""
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoardDead(f"no {kinds} within {timeout_s:.0f}s")
            try:
                kind, obj = board.read_event(timeout_s=remaining)
            except IOError as e:
                raise BoardDead(str(e))
            if kind == "end":
                raise BoardDead(f"stream ended: {obj}")
            if kind == "skip":
                continue                      # corrupted frame, realigned
            if kind in kinds:
                return kind, obj
            if kind == "frame" and on_frame is not None:
                on_frame(board, obj)
            # anything else (stray info etc.) is ignored

    def _send_each(self, boards, byte):
        """Send to exactly these boards (never to a board that error-
        skipped the phase — it is already waiting in its NEXT phase,
        and a stray byte there would end that phase prematurely)."""
        for b in list(boards):
            if b not in self.live:
                continue
            try:
                b.send(byte)
            except Exception as e:
                self._drop(b, f"send failed: {e}")

    # ------------------------------------------------------------- phases
    def phase_barrier(self, phase_i):
        """Collect #PH for phase_i from every live board. Boards whose
        phase errored (e.g. whole-mode-on-HD refusal) sit the phase out
        but stay in the run. -> {label: phase_obj} of participants."""
        joined = {}
        for b in list(self.live):
            try:
                _, obj = self._read_until(b, ("phase",),
                                          self.phase_timeout_s)
            except BoardDead as e:
                self._drop(b, str(e))
                continue
            if obj.get("phase") != phase_i:
                self._drop(b, f"phase desync: board at {obj.get('phase')}"
                              f", host at {phase_i}")
                continue
            if "error" in obj:
                self.log(f"    {b.label}: phase {phase_i} SKIPPED "
                         f"({obj['error']})")
                continue
            joined[b.label] = obj
        return joined

    def _round(self, participants, on_frame):
        """One 'g' broadcast + one frame collected per participant."""
        self._send_each(participants, b"g")
        for b in list(participants):
            if b not in self.live:
                continue
            try:
                _, fr = self._read_until(b, ("frame",),
                                         self.frame_timeout_s)
                on_frame(b, fr)
            except BoardDead as e:
                self._drop(b, str(e))

    def run_model_phase(self, phase_i, joined, stills, k, on_frame,
                        on_still=None):
        """stills: list of playback still indices; k frames per still
        per board. on_frame(board, still_pos, frame_obj, frame_idx)."""
        participants = [b for b in self.live if b.label in joined]
        for pos, still in enumerate(stills):
            if not participants:
                break
            self.advance(still=still)
            self.wait(self.render_wait_s)
            if on_still:
                on_still(pos)
            for fi in range(k):
                self._round(
                    [b for b in participants if b in self.live],
                    lambda b, fr, _p=pos, _fi=fi:
                        on_frame(b, _p, fr, _fi + 1))
            participants = [b for b in participants if b in self.live]
        self._send_each(participants, b"p")

    def run_jpeg_phase(self, phase_i, joined, page, n_frames, on_frame):
        participants = [b for b in self.live if b.label in joined]
        self.advance(page=page)
        self.wait(self.render_wait_s)
        for _ in range(n_frames):
            if not participants:
                break
            self._round([b for b in participants if b in self.live],
                        lambda b, fr: on_frame(b, fr))
            participants = [b for b in participants if b in self.live]
        self._send_each(participants, b"p")
