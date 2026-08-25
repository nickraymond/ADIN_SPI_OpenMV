"""Closed-loop HIL protocol (S8 bite E4) — wire parser + conductor.

Nick's design (TRACKER bite E4): the Pi hosts the image, TELLS each
camera to start inferring, each camera reports DONE and waits, and only
then does the Pi advance the still. One control byte host→board on the
VCP; the board polls stdin between frames. The same plumbing gives the
phase barrier, simultaneous multi-board runs, and early phase exit.

This module is the TESTABLE CORE: no serial, no HTTP, no numpy. The
harness injects transports; the test suite injects fakes. Two pieces:

  BoardStream  parses one board's wire (#I/#PH/#W/#F/#DONE + payload
               lines) into events, over any reader with readline().
  Conductor    a pure state machine over N boards: decides which
               control bytes to send, when the still may advance, and
               who has dropped out of the barrier. All I/O is returned
               as action tuples for the caller to execute.

Control bytes (host→board). Printable ASCII on purpose: the raw REPL
intercepts 0x01–0x04 (^A–^D) while a script runs, so control bytes must
never collide with them.

  g   run one frame against the current still
  j   run one frame AND ship the camera JPEG (review's "show me")
  p   end the current phase now (early exit — the drain tail deleted)
  q   end the run

Recovery model, built for the measured failure modes:
  - The board DRAINS stdin before parking, so a duplicate byte (host
    resend racing a slow frame) is absorbed, never double-run.
  - A parked board heartbeats `#W` every ~5 s. A heartbeat from a board
    the conductor believes is RUNNING (past a grace window) means the
    command byte was lost → resend. Timeouts also resend; either path
    converges because of the drain.
  - A board that dies (stream end / silence / premature #DONE) drops
    out of the barrier; the rest continue solo and the run scores what
    was collected — same containment as the open-loop harness.
"""
import base64
import json
import time

CMD_GO = b"g"
CMD_GO_JPEG = b"j"
CMD_PHASE_END = b"p"
CMD_QUIT = b"q"


# ------------------------------------------------------------ wire parsing
class BoardStream:
    """Parse the hil_board.py wire into events, over an injected reader.

    The reader contract (satisfied by n6_stream_host.SerialBoard and by
    the test fakes): readline() → one line ending b"\\n", or b"" at end
    of stream; attributes end_reason / last_error describe the end.
    """

    def __init__(self, sb):
        self.sb = sb
        self.info = None

    def next_event(self, timeout_s=30):
        """-> ('info'|'phase'|'wait'|'frame'|'skip'|'done'|'end', payload)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            line = self.sb.readline()
            if line == b"":
                return "end", {"reason": self.sb.end_reason,
                               "error": self.sb.last_error}
            if not line.startswith(b"#"):
                continue                      # stray output — ignore
            try:
                tag, payload = line.split(b" ", 1)
                obj = json.loads(payload)
            except ValueError:
                continue
            if tag == b"#I":
                self.info = obj
                return "info", obj
            if tag == b"#PH":
                return "phase", obj
            if tag == b"#W":
                return "wait", obj
            if tag == b"#DONE":
                return "done", obj
            if tag == b"#F":
                jpg = b""
                if obj["jpg"]:
                    # header lengths are BARE b64; the CDC turns the
                    # terminator \n into \r\n, so strip before comparing
                    raw_line = self.sb.readline()
                    if raw_line == b"":       # board died mid-frame
                        return "end", {"reason": self.sb.end_reason,
                                       "error": self.sb.last_error,
                                       "mid_frame": obj["seq"]}
                    jpg_line = raw_line.rstrip(b"\r\n")
                    if len(jpg_line) != obj["jpg"]:
                        # one corrupted line 1,270 frames into an
                        # otherwise-clean run killed a whole leg — the
                        # stream is line-oriented, so skip and realign
                        return "skip", obj
                    jpg = base64.b64decode(jpg_line)
                tile_cells = []
                for ncell in obj["cells"]:
                    raw_line = self.sb.readline()
                    if raw_line == b"":
                        return "end", {"reason": self.sb.end_reason,
                                       "error": self.sb.last_error,
                                       "mid_frame": obj["seq"]}
                    try:
                        cells = json.loads(raw_line)
                    except ValueError:
                        cells = None
                    if cells is None or len(cells) != ncell:
                        return "skip", obj
                    tile_cells.append(cells)
                obj["_jpg"] = jpg
                obj["_cells"] = tile_cells
                obj["_arrival"] = time.monotonic()
                return "frame", obj
        raise IOError(f"board silent for {timeout_s}s")

    def stop(self):
        self.sb.stop()


# --------------------------------------------------------------- conductor
class _Board:
    def __init__(self, label):
        self.label = label
        self.status = "wait_info"   # wait_info|parked|running|dead|done
        self.phase_i = -1           # last #PH index this board announced
        self.parked_ph = -1         # phase of the last #W
        self.cmd = None             # outstanding control byte
        self.cmd_t = 0.0
        self.frame_since_cmd = True
        self.p_sent_t = None        # phase-end byte sent (None = not yet)
        self.strikes = 0
        self.got = 0                # frames delivered for the current slot
        self.next_send_t = 0.0      # jpeg-phase pacing (AE settle)
        self.info = None
        self.drop_reason = ""

    @property
    def alive(self):
        return self.status not in ("dead", "done")


class Conductor:
    """Pure barrier/still scheduler. Feed it events; execute its actions.

    Actions (tuples, executed by the harness):
      ("send", label, cmd_byte)      write one control byte to that board
      ("set_page", spec_dict)        drive the playback server; the
                                     harness must call on_page_commanded()
                                     with the resulting state seq
      ("frame_ok", label, slot, n)   frame is attributable: score it
                                     against `slot` (still index, or the
                                     jpeg page name) as frame n
      ("frame_stray", label)         frame arrived while no still was
                                     confirmed on screen — UNSCOREABLE.
                                     The open-loop harness would have
                                     scored this against the wrong
                                     still; here it is named and dropped
      ("drop", label, reason)        board left the barrier — tear down
      ("hold", info_dict)            review mode: waiting for the human
      ("finish",)                    run over — score what was collected
    """

    HB_GRACE = 1.5     # s — a #W younger than this after a send is a
                       # stale heartbeat already in flight, not a lost byte

    def __init__(self, labels, phases, stills, k, mode="auto",
                 frame_timeout=30.0, max_strikes=3, boot_timeout=90.0,
                 jpeg_counts=None, jpeg_gap=0.4, now=None):
        self.boards = {lb: _Board(lb) for lb in labels}
        self.phases = phases          # [{"kind":"jpeg","page":..}|{"kind":"model",..}]
        self.stills = stills          # opaque playback indices, in order
        self.k = k
        self.mode = mode              # "auto" | "review"
        self.frame_timeout = frame_timeout
        self.max_strikes = max_strikes
        self.boot_timeout = boot_timeout
        self.jpeg_counts = jpeg_counts or {"black": 2, "calib": 3, "loop": 1}
        self.jpeg_gap = jpeg_gap
        self.jpeg_all = False         # review toggle: every 'g' becomes 'j'
        self.paused = False
        self.stage = "boot"   # boot|page|await_page_cmd|shown_wait|step|
        #                       hold|phase_end|done
        self.phase_i = -1
        self.still_i = 0
        self.page_seq = None
        self.t0 = now if now is not None else time.monotonic()
        self.settle_discards = 0      # stays 0 by construction — the audit
        self.stray_frames = 0         # frames outside a confirmed still

    # ---- helpers -------------------------------------------------------
    def _phase(self):
        return self.phases[self.phase_i]

    def _participants(self):
        """Boards commanded in the CURRENT phase: alive and announced it.
        A board that skipped this phase (#PH error → next #PH) has
        phase_i > ours and simply waits ahead until we catch up."""
        return [b for b in self.boards.values()
                if b.alive and b.phase_i == self.phase_i]

    def _slot_target(self):
        ph = self._phase()
        if ph["kind"] == "jpeg":
            return self.jpeg_counts.get(ph["page"], 1)
        return self.k

    def _enter_phase(self, i, now):
        self.phase_i = i
        self.still_i = 0
        for b in self.boards.values():
            b.got = 0
            b.p_sent_t = None
        self.stage = "page"
        return self._pump(now)

    def _drop(self, b, reason, now):
        b.status = "dead"
        b.drop_reason = reason
        return [("drop", b.label, reason)]

    def _go_byte(self):
        return CMD_GO_JPEG if self.jpeg_all else CMD_GO

    def _send(self, b, cmd, now):
        b.cmd = cmd
        b.cmd_t = now
        b.frame_since_cmd = False
        b.status = "running"
        return ("send", b.label, cmd)

    # ---- inputs --------------------------------------------------------
    def on_event(self, label, ev, obj, now):
        b = self.boards[label]
        acts = []
        if ev == "info":
            b.info = obj
            b.status = "parked" if b.status == "wait_info" else b.status
        elif ev == "phase":
            if "error" in obj:
                # the board refuses this phase (no model / whole@HD) and
                # will announce the next one; it just leaves this
                # phase's barrier
                pass
            b.phase_i = obj["phase"]
        elif ev == "wait":
            b.parked_ph = obj.get("ph", b.phase_i)
            if (b.status == "running" and not b.frame_since_cmd
                    and now - b.cmd_t > self.HB_GRACE):
                # parked heartbeat while we believe it is running past
                # grace = the command byte never arrived → resend (the
                # board-side drain makes a wrong guess harmless)
                b.strikes += 1
                if b.strikes >= self.max_strikes:
                    acts += self._drop(b, "unresponsive to commands", now)
                else:
                    acts.append(self._send(b, b.cmd, now))
            elif b.status == "running" and not b.frame_since_cmd:
                pass                      # stale heartbeat in flight
            else:
                b.status = "parked"
                b.strikes = 0
            if (self.stage == "phase_end" and b.p_sent_t is not None
                    and b.parked_ph == self.phase_i
                    and now - b.p_sent_t > self.HB_GRACE):
                b.p_sent_t = now          # 'p' lost → resend
                acts.append(("send", b.label, CMD_PHASE_END))
        elif ev == "frame":
            b.frame_since_cmd = True
            b.strikes = 0
            ph = self._phase() if self.phase_i >= 0 else None
            if ph and ph["kind"] == "jpeg":
                b.next_send_t = now + self.jpeg_gap
            if self.stage in ("step", "hold"):
                # attributable by construction: the still was confirmed
                # on screen before this board's go-byte was sent
                b.got += 1
                slot = ph["page"] if ph and ph["kind"] == "jpeg" \
                    else self.still_i
                acts.append(("frame_ok", label, slot, b.got))
            else:
                # a frame with no confirmed still under it (duplicate
                # from a resend race, or a straggler across a page
                # change) — the exact frame class open-loop mis-scored
                self.stray_frames += 1
                acts.append(("frame_stray", label))
        elif ev == "skip":
            # corrupt payload: the board ran the frame but we lost it —
            # it will park, and the normal stepping resends because
            # got < target. Closed loop turns corruption into a re-run.
            b.frame_since_cmd = True
        elif ev == "done":
            expected = (self.stage == "phase_end"
                        and self.phase_i == len(self.phases) - 1)
            if expected:
                b.status = "done"
            else:
                acts += self._drop(
                    b, f"premature #DONE ({obj.get('reason', '?')})", now)
        elif ev == "end":
            if b.status != "done":
                acts += self._drop(
                    b, f"stream end: {obj.get('reason', '?')}", now)
        return acts + self._pump(now)

    def on_page_commanded(self, seq, now):
        self.page_seq = seq
        self.stage = "shown_wait"
        return self._pump(now)

    def on_shown(self, shown_seq, now):
        if (self.stage == "shown_wait" and self.page_seq is not None
                and shown_seq >= self.page_seq):
            self.stage = "step"
        return self._pump(now)

    def on_review(self, action, board=None, now=None):
        now = now if now is not None else time.monotonic()
        acts = []
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "auto":
            self.mode = "auto"
            if self.stage == "hold":
                acts += self._advance_slot(now)
        elif action == "next" and self.stage == "hold":
            acts += self._advance_slot(now)
        elif action == "jpeg" and board in self.boards:
            b = self.boards[board]
            if b.alive and b.status == "parked" \
                    and b.parked_ph == self.phase_i:
                # extra evidence frame — counted as a row, never
                # required by the barrier (got may exceed target)
                acts.append(self._send(b, CMD_GO_JPEG, now))
        elif action == "jpeg_all":
            self.jpeg_all = not self.jpeg_all
        return acts + self._pump(now)

    def on_tick(self, now):
        acts = []
        if self.stage == "boot" and now - self.t0 > self.boot_timeout:
            for b in self.boards.values():
                if b.alive and b.parked_ph < 0:
                    acts += self._drop(b, "never came up (boot timeout)",
                                       now)
        for b in self.boards.values():
            if (b.alive and b.status == "running"
                    and now - b.cmd_t > self.frame_timeout):
                b.strikes += 1
                if b.strikes >= self.max_strikes:
                    acts += self._drop(b, "frame timeout", now)
                else:
                    b.cmd_t = now
                    acts.append(("send", b.label, b.cmd))
        return acts + self._pump(now)

    # ---- the pump: one place decides what happens next -----------------
    def _pump(self, now):
        acts = []
        if self.stage == "done":
            return acts
        alive = [b for b in self.boards.values() if b.alive]
        if not alive:
            self.stage = "done"
            return [("finish",)]

        if self.stage == "boot":
            ready = [b for b in alive if b.phase_i == 0 and b.parked_ph == 0]
            if ready and len(ready) == len(alive):
                return acts + self._enter_phase(0, now)
            return acts

        if self.stage == "page":
            ph = self._phase()
            if ph["kind"] == "jpeg":
                spec = {"mode": ph["page"]}
            else:
                spec = {"mode": "step", "still": self.stills[self.still_i]}
            self.stage = "await_page_cmd"
            return acts + [("set_page", spec)]

        if self.stage == "step":
            target = self._slot_target()
            parts = self._participants()
            if not self.paused:
                for b in parts:
                    if (b.status == "parked" and b.got < target
                            and b.parked_ph == self.phase_i
                            and now >= b.next_send_t):
                        acts.append(self._send(b, self._go_byte(), now))
            if parts and all(b.got >= target for b in parts):
                ph = self._phase()
                if ph["kind"] == "model" and self.mode == "review":
                    self.stage = "hold"
                    acts.append(("hold", {"phase": self.phase_i,
                                          "still_i": self.still_i}))
                else:
                    acts += self._advance_slot(now)
            elif not parts:
                # every remaining board skipped this phase — move on
                acts += self._end_phase(now)
            return acts

        if self.stage == "phase_end":
            for b in self._participants():
                if b.status == "parked" and b.p_sent_t is None:
                    b.p_sent_t = now
                    acts.append(("send", b.label, CMD_PHASE_END))
            nxt = self.phase_i + 1
            if nxt >= len(self.phases):
                if all(b.status == "done" for b in self.boards.values()
                       if b.alive):
                    self.stage = "done"
                    acts.append(("finish",))
            else:
                moved = [b for b in alive
                         if b.phase_i >= nxt and b.status == "parked"]
                if len(moved) == len(alive):
                    acts += self._enter_phase(nxt, now)
            return acts

        return acts    # await_page_cmd / shown_wait / hold: caller-driven

    def _advance_slot(self, now):
        ph = self._phase()
        for b in self.boards.values():
            b.got = 0
        if ph["kind"] == "model" and self.still_i + 1 < len(self.stills):
            self.still_i += 1
            self.stage = "page"
            return self._pump(now)
        return self._end_phase(now)

    def _end_phase(self, now):
        self.stage = "phase_end"
        return self._pump(now)

    @property
    def done(self):
        return self.stage == "done"

    def snapshot(self):
        """Monitor-page view of the run. Data only, no actions."""
        ph = self.phases[self.phase_i] if self.phase_i >= 0 else None
        return {
            "stage": self.stage, "mode": self.mode, "paused": self.paused,
            "jpeg_all": self.jpeg_all,
            "phase_i": self.phase_i, "n_phases": len(self.phases),
            "phase": (ph.get("page") or
                      f"{ph.get('model')}-{ph.get('mode')}") if ph else "",
            "still_i": self.still_i, "n_stills": len(self.stills),
            "settle_discards": self.settle_discards,
            "stray_frames": self.stray_frames,
            "boards": {lb: {"status": b.status, "phase_i": b.phase_i,
                            "got": b.got, "strikes": b.strikes,
                            "drop_reason": b.drop_reason}
                       for lb, b in self.boards.items()},
        }
