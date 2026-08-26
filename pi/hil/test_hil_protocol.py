"""Host tests for the closed-loop HIL protocol (S8 bite E4).

TEST-FIRST is the bite's contract (Nick, 2026-08-25): the protocol is
proven against a FAKE board covering the measured ugly cases — board
dies mid-phase, garbled/partial lines, one board stalls, host byte lost
(timeout→recover), CRLF translation (the known CDC trap) — before any
hardware rides it. Pure stdlib: runs on the Mac or the Pi, no board.

    python3 -m pytest pi/hil/test_hil_protocol.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_protocol import (BoardStream, Conductor, CMD_GO, CMD_GO_JPEG,
                          CMD_PHASE_END)  # noqa: E402


# ------------------------------------------------------- wire-level fakes
class FakeSerial:
    """The reader side of SerialBoard: scripted lines, then end."""

    def __init__(self, lines, end_reason="eot", last_error=""):
        self.lines = list(lines)
        self.end_reason = end_reason
        self.last_error = last_error
        self.stopped = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return b""

    def stop(self):
        self.stopped = True


def _f(seq, ncells=1, jpg=0, cells=None):
    """A #F header + its payload lines (well-formed)."""
    hdr = {"seq": seq, "ph": 0, "ms": 0, "cap_us": 1000, "prep_us": [1],
           "inf_us": [2], "dec_us": [3], "tiles": [[0, 0]], "jpg": jpg,
           "cells": [ncells], "dropped": [0]}
    body = cells if cells is not None else [[20, 0, 0, 0.1, 0.1, 0.2,
                                             0.2, 0.9, 0.8]] * ncells
    return [("#F " + json.dumps(hdr)).encode() + b"\n",
            json.dumps(body).encode() + b"\n"]


# ------------------------------------------------------ BoardStream: wire
def test_stream_parses_all_tags_and_crlf():
    """CRLF endings (the CDC trap) must parse identically to bare \\n."""
    lines = [b"#I {\"w\": 640}\r\n",
             b"#PH {\"phase\": 0}\r\n",
             b"#W {\"ph\": 0, \"seq\": 0}\r\n"] + _f(0) + \
            [b"#DONE {\"frames\": 1}\r\n"]
    bs = BoardStream(FakeSerial(lines))
    assert bs.next_event()[0] == "info"
    assert bs.next_event() == ("phase", {"phase": 0})
    assert bs.next_event() == ("wait", {"ph": 0, "seq": 0})
    ev, obj = bs.next_event()
    assert ev == "frame" and len(obj["_cells"][0]) == 1
    assert bs.next_event()[0] == "done"


def test_stream_garbled_header_is_skipped_not_fatal():
    lines = [b"#F {not json at all\n"] + _f(1)
    bs = BoardStream(FakeSerial(lines))
    ev, obj = bs.next_event()
    assert ev == "frame" and obj["seq"] == 1


def test_stream_short_cells_payload_yields_skip():
    hdr = _f(2, ncells=3)[0]           # header promises 3 cells
    body = json.dumps([[20, 0, 0, 0.1, 0.1, 0.2, 0.2, 0.9, 0.8]]).encode()
    bs = BoardStream(FakeSerial([hdr, body + b"\n"]))
    ev, obj = bs.next_event()
    assert ev == "skip" and obj["seq"] == 2


def test_stream_jpg_length_mismatch_yields_skip():
    hdr = {"seq": 3, "ph": 0, "ms": 0, "cap_us": 0, "prep_us": [],
           "inf_us": [], "dec_us": [], "tiles": [], "jpg": 100,
           "cells": [], "dropped": []}
    bs = BoardStream(FakeSerial(
        [("#F " + json.dumps(hdr)).encode() + b"\n", b"tooshort\r\n"]))
    ev, obj = bs.next_event()
    assert ev == "skip" and obj["seq"] == 3


def test_stream_model_frame_with_chunked_b64_jpg(tmp_path):
    """E7: the board now emits the camera JPEG on MODEL frames, built by
    concatenating 3072-byte b64 chunks (the in-place encode path). The
    wire must be indistinguishable from the old single-shot b64 line:
    exact length in the header, one line, decodes to the source bytes."""
    import base64
    import binascii
    raw = bytes(range(256)) * 40                  # 10,240 B fake "jpeg"
    chunks = b"".join(
        binascii.b2a_base64(raw[i:i + 3072]).rstrip(b"\n")
        for i in range(0, len(raw), 3072))
    assert len(chunks) == (len(raw) + 2) // 3 * 4  # the board's jlen math
    hdr = {"seq": 7, "ph": 0, "ms": 0, "cap_us": 1000, "prep_us": [1],
           "inf_us": [2], "dec_us": [3], "tiles": [[0, 0]],
           "jpg": len(chunks), "cells": [1], "dropped": [0]}
    body = json.dumps([[20, 0, 0, 0.1, 0.1, 0.2, 0.2, 0.9, 0.8]])
    bs = BoardStream(FakeSerial(
        [("#F " + json.dumps(hdr)).encode() + b"\n",
         chunks + b"\r\n",                        # CDC CRLF, as on the wire
         body.encode() + b"\n"]))
    ev, obj = bs.next_event()
    assert ev == "frame" and obj["seq"] == 7
    assert base64.b64decode(chunks) == raw
    assert obj["_jpg"] == raw                     # jpg AND cells coexist
    assert len(obj["_cells"][0]) == 1


def test_stream_death_mid_frame_reports_end():
    hdr = _f(4, ncells=2)[0]
    bs = BoardStream(FakeSerial([hdr], end_reason="usb: gone"))
    ev, obj = bs.next_event()
    assert ev == "end" and obj["mid_frame"] == 4
    assert "usb" in obj["reason"]


# ----------------------------------------------------- conductor plumbing
def _mk(labels=("A",), n_model=1, n_stills=2, k=2, mode="auto", **kw):
    phases = [{"kind": "jpeg", "page": "black"},
              {"kind": "jpeg", "page": "calib"}]
    phases += [{"kind": "model", "model": "nano", "mode": "tiled"}
               for _ in range(n_model)]
    c = Conductor(list(labels), phases, stills=list(range(n_stills)), k=k,
                  mode=mode, jpeg_counts={"black": 1, "calib": 1}, now=0.0,
                  jpeg_gap=0.0, **kw)
    return c


def _boot(c, labels=("A",), t=0.0):
    """Bring every board to parked-in-phase-0; return accumulated acts."""
    acts = []
    for lb in labels:
        acts += c.on_event(lb, "info", {"w": 640}, t)
        acts += c.on_event(lb, "phase", {"phase": 0}, t)
        acts += c.on_event(lb, "wait", {"ph": 0, "seq": 0}, t)
    return acts


def _ack_page(c, seq, t):
    """Complete the page-commanded/shown handshake if one is pending."""
    if c.stage != "await_page_cmd":
        return []
    out = c.on_page_commanded(seq, t)
    out += c.on_shown(seq, t)
    return out


def _sends(acts, cmd=None):
    return [a for a in acts if a[0] == "send"
            and (cmd is None or a[2] == cmd)]


def _frame(c, lb, ph, t):
    return c.on_event(lb, "frame", {"seq": 0, "ph": ph, "jpg": 0,
                                    "cells": [1], "dropped": [0]}, t)


def _run_phase_cycle(c, labels, ph, t, target=1):
    """Deliver `target` frames + park per board; return all actions."""
    acts = []
    for _ in range(target):
        for lb in labels:
            acts += _frame(c, lb, ph, t)
            acts += c.on_event(lb, "wait", {"ph": ph, "seq": 0}, t)
    return acts


def _to_model_phase(c, labels, seq0=1):
    """Drive a 2-jpeg-phase conductor into its first model phase; the
    returned actions include the go-bytes for still 0."""
    _boot(c, labels)
    seq = seq0
    for ph in (0, 1):
        _ack_page(c, seq, 0.0)
        seq += 1
        _run_phase_cycle(c, labels, ph, 0.0)
        for lb in labels:
            c.on_event(lb, "phase", {"phase": ph + 1}, 0.0)
            c.on_event(lb, "wait", {"ph": ph + 1, "seq": 0}, 0.0)
    return _ack_page(c, seq, 0.0)


def test_happy_path_solo_full_run():
    """One board, jpeg pages then a model phase over 2 stills × k=2 —
    driven purely by the conductor's actions, like the harness would."""
    c = _mk()
    outbox = list(_boot(c))
    seq = 10
    pages = []
    frames = 0
    guard = 0
    while not c.done and guard < 300:
        guard += 1
        if c.stage == "await_page_cmd":
            spec = [a for a in outbox if a[0] == "set_page"][-1][1]
            pages.append(spec)
            outbox = _ack_page(c, seq, 0.0)
            seq += 1
            continue
        if not outbox:
            outbox = c.on_tick(0.0)
            assert outbox or c.done or c.stage == "await_page_cmd", \
                f"deadlock at stage {c.stage}"
            continue
        a = outbox.pop(0)
        if a[0] == "send" and a[2] in (CMD_GO, CMD_GO_JPEG):
            lb, ph = a[1], c.phase_i
            outbox += _frame(c, lb, ph, 0.0)
            outbox += c.on_event(lb, "wait", {"ph": ph, "seq": 0}, 0.0)
            frames += 1
        elif a[0] == "send" and a[2] == CMD_PHASE_END:
            lb, nxt = a[1], c.phase_i + 1
            if nxt >= len(c.phases):
                outbox += c.on_event(lb, "done", {"frames": frames}, 0.0)
            else:
                outbox += c.on_event(lb, "phase", {"phase": nxt}, 0.0)
                outbox += c.on_event(lb, "wait", {"ph": nxt, "seq": 0},
                                     0.0)
    assert c.done and guard < 300
    assert [p["mode"] for p in pages] == ["black", "calib", "step", "step"]
    assert [p.get("still") for p in pages[2:]] == [0, 1]
    assert frames == 1 + 1 + 2 * 2      # jpeg pages + stills×k
    assert c.settle_discards == 0       # by construction
    assert c.stray_frames == 0


def test_frame_ok_carries_slot_attribution():
    """Scoring rides the conductor's attribution, not arrival timing."""
    c = _mk(n_stills=2, k=1)
    _to_model_phase(c, ("A",))
    acts = _frame(c, "A", 2, 0.0)
    oks = [a for a in acts if a[0] == "frame_ok"]
    assert oks == [("frame_ok", "A", 0, 1)]


def test_barrier_still_advances_only_when_all_report():
    c = _mk(labels=("A", "B"), n_stills=2, k=1)
    acts = _to_model_phase(c, ("A", "B"))
    assert len(_sends(acts, CMD_GO)) == 2
    # A reports; B has not — the still must NOT advance
    _frame(c, "A", 2, 0.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert not any(a[0] == "set_page" for a in acts)
    assert c.still_i == 0
    # B reports — the slot completes on the frame (the DONE report),
    # and the next page is commanded immediately
    acts = _frame(c, "B", 2, 0.0)
    acts += c.on_event("B", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert any(a[0] == "set_page" for a in acts)
    assert c.still_i == 1


def test_board_dies_mid_phase_rest_continue():
    c = _mk(labels=("A", "B"), n_stills=1, k=1)
    acts = _to_model_phase(c, ("A", "B"))
    assert len(_sends(acts, CMD_GO)) == 2
    # B's stream dies mid-phase
    acts = c.on_event("B", "end", {"reason": "usb: gone"}, 1.0)
    assert any(a[0] == "drop" and a[1] == "B" for a in acts)
    # A alone now satisfies the barrier
    _frame(c, "A", 2, 1.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 1.0)
    assert any(a == ("send", "A", CMD_PHASE_END) for a in acts)
    acts = c.on_event("A", "done", {"frames": 3}, 1.0)
    assert c.done and any(a[0] == "finish" for a in acts)


def test_stalled_board_dropped_after_strikes_other_unaffected():
    c = _mk(labels=("A", "B"), n_stills=1, k=1, frame_timeout=10.0,
            max_strikes=3)
    _to_model_phase(c, ("A", "B"))
    _frame(c, "A", 2, 0.0)
    c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    # B never answers: two timeout resends, then the drop
    acts = c.on_tick(11.0)
    assert _sends(acts) and _sends(acts)[0][1] == "B"
    acts = c.on_tick(22.0)
    assert _sends(acts) and _sends(acts)[0][1] == "B"
    acts = c.on_tick(33.0)
    assert any(a[0] == "drop" and a[1] == "B" for a in acts)
    # with B gone, A's completed slot ends the phase
    assert any(a == ("send", "A", CMD_PHASE_END) for a in acts)


def test_lost_go_byte_recovered_via_heartbeat():
    """#W from a board we believe is RUNNING, past grace = lost byte."""
    c = _mk(n_stills=1, k=1)
    _to_model_phase(c, ("A",))
    # heartbeat inside grace: stale, no resend
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.5)
    assert not _sends(acts)
    # heartbeat past grace: resend
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 5.0)
    assert len(_sends(acts, CMD_GO)) == 1
    # frame then arrives once — converged, no duplicates
    _frame(c, "A", 2, 6.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 6.0)
    assert not _sends(acts, CMD_GO)


def test_duplicate_frame_between_stills_is_stray_never_scored():
    """The exact open-loop hazard: a frame with no confirmed still under
    it must be named stray and dropped, not scored against the next
    still — and the next still is then re-run properly."""
    c = _mk(n_stills=2, k=1)
    _to_model_phase(c, ("A",))
    acts = _frame(c, "A", 2, 0.0)
    acts += c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert any(a[0] == "set_page" for a in acts)
    assert c.still_i == 1
    # duplicate arrives while the next page is still being commanded
    acts = _frame(c, "A", 2, 0.1)
    assert any(a[0] == "frame_stray" for a in acts)
    assert not any(a[0] == "frame_ok" for a in acts)
    assert c.stray_frames == 1
    c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.1)
    # page confirmed → still 1 runs with a fresh go-byte
    acts = _ack_page(c, 20, 0.2)
    assert len(_sends(acts, CMD_GO)) == 1
    assert c.still_i == 1


def test_garbled_frame_gets_rerun():
    """A 'skip' (corrupt payload) leaves got < k; stepping resends."""
    c = _mk(n_stills=1, k=1)
    _to_model_phase(c, ("A",))
    c.on_event("A", "skip", {"seq": 0}, 0.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert len(_sends(acts, CMD_GO)) == 1     # replacement requested


def test_review_mode_holds_until_next():
    c = _mk(n_stills=2, k=1, mode="review")
    _to_model_phase(c, ("A",))
    acts = _frame(c, "A", 2, 0.0)
    acts += c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert any(a[0] == "hold" for a in acts)
    assert not any(a[0] == "set_page" for a in acts)
    # jpeg grab while holding: board gets 'j', barrier unchanged
    acts = c.on_review("jpeg", board="A", now=1.0)
    assert any(a == ("send", "A", CMD_GO_JPEG) for a in acts)
    acts = _frame(c, "A", 2, 1.5)
    assert any(a[0] == "frame_ok" for a in acts)   # scoreable: still up
    c.on_event("A", "wait", {"ph": 2, "seq": 0}, 1.5)
    assert c.stage == "hold"
    # the human advances
    acts = c.on_review("next", now=2.0)
    assert any(a[0] == "set_page" for a in acts)
    assert c.still_i == 1


def test_review_pause_blocks_sends_resume_releases():
    c = _mk(n_stills=1, k=1, mode="review")
    _boot(c, ("A",))
    c.on_review("pause", now=0.0)
    acts = _ack_page(c, 1, 0.0)          # black page confirmed, paused
    assert not _sends(acts)
    acts = c.on_event("A", "wait", {"ph": 0, "seq": 0}, 6.0)
    assert not _sends(acts)
    acts = c.on_review("resume", now=7.0)
    assert _sends(acts, CMD_GO)


def test_phase_end_byte_lost_is_resent():
    c = _mk(n_stills=1, k=1)
    _to_model_phase(c, ("A",))
    _frame(c, "A", 2, 0.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert any(a == ("send", "A", CMD_PHASE_END) for a in acts)
    # board still parked in the OLD phase past grace → 'p' was lost
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 5.0)
    assert any(a == ("send", "A", CMD_PHASE_END) for a in acts)


def test_premature_done_drops_board():
    c = _mk(labels=("A", "B"), n_stills=1, k=1)
    _to_model_phase(c, ("A", "B"))
    acts = c.on_event("B", "done", {"reason": "idle"}, 1.0)
    assert any(a[0] == "drop" and a[1] == "B" for a in acts)
    b = c.snapshot()["boards"]["B"]
    assert b["status"] == "dead" and "premature" in b["drop_reason"]


def test_phase_error_board_waits_ahead_and_rejoins():
    """AE3-without-tiny shape: a board that refuses a phase (#PH error →
    next #PH) leaves that phase's barrier and rejoins at the phase it
    announced."""
    c = _mk(labels=("A", "B"), n_model=2, n_stills=1, k=1)
    _boot(c, ("A", "B"))
    seq = 1
    for ph in (0, 1):
        _ack_page(c, seq, 0.0)
        seq += 1
        _run_phase_cycle(c, ("A", "B"), ph, 0.0)
        if ph == 0:
            for lb in ("A", "B"):
                c.on_event(lb, "phase", {"phase": 1}, 0.0)
                c.on_event(lb, "wait", {"ph": 1, "seq": 0}, 0.0)
    # at the calib→model transition: A enters phase 2; B refuses it and
    # announces phase 3
    c.on_event("A", "phase", {"phase": 2}, 0.0)
    c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    c.on_event("B", "phase", {"phase": 2, "error": "no tiny"}, 0.0)
    c.on_event("B", "phase", {"phase": 3}, 0.0)
    c.on_event("B", "wait", {"ph": 3, "seq": 0}, 0.0)
    acts = _ack_page(c, seq, 0.0)
    seq += 1
    # phase 2: A alone is commanded
    assert [a[1] for a in _sends(acts, CMD_GO)] == ["A"]
    _frame(c, "A", 2, 0.0)
    acts = c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0)
    assert any(a == ("send", "A", CMD_PHASE_END) for a in acts)
    c.on_event("A", "phase", {"phase": 3}, 0.0)
    c.on_event("A", "wait", {"ph": 3, "seq": 0}, 0.0)
    acts = _ack_page(c, seq, 0.0)
    # phase 3: both boards commanded — B rejoined
    assert sorted(a[1] for a in _sends(acts, CMD_GO)) == ["A", "B"]


def test_error_skipped_board_receives_zero_bytes_for_that_phase():
    """Pin (credit: the parallel E4 implementation on
    claude/e4-closed-loop-hil, ceded 2026-08-25): a board that
    error-skips a phase must receive NO control byte at all during that
    phase — a stray 'g' or 'p' would sit in its stdin, survive into the
    next phase's drain window, and could kill the phase it actually
    announced. Every byte to the skipped board is asserted, not just
    the phase-end routing."""
    c = _mk(labels=("A", "B"), n_model=2, n_stills=1, k=1)
    _boot(c, ("A", "B"))
    seq = 1
    for ph in (0, 1):
        _ack_page(c, seq, 0.0)
        seq += 1
        _run_phase_cycle(c, ("A", "B"), ph, 0.0)
        if ph == 0:
            for lb in ("A", "B"):
                c.on_event(lb, "phase", {"phase": 1}, 0.0)
                c.on_event(lb, "wait", {"ph": 1, "seq": 0}, 0.0)
    to_b = []
    c.on_event("A", "phase", {"phase": 2}, 0.0)
    to_b += _sends(c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0))
    to_b += _sends(c.on_event(
        "B", "phase", {"phase": 2, "error": "no tiny"}, 0.0))
    to_b += _sends(c.on_event("B", "phase", {"phase": 3}, 0.0))
    to_b += _sends(c.on_event("B", "wait", {"ph": 3, "seq": 0}, 0.0))
    to_b += _sends(_ack_page(c, seq, 0.0))
    to_b += _sends(_frame(c, "A", 2, 0.0))
    to_b += _sends(c.on_event("A", "wait", {"ph": 2, "seq": 0}, 0.0))
    # B heartbeats while parked ahead — still nothing may go to it
    to_b += _sends(c.on_event("B", "wait", {"ph": 3, "seq": 0}, 5.0))
    to_b += _sends(c.on_tick(6.0))
    assert [a for a in to_b if a[1] == "B"] == []
    # and once the conductor reaches phase 3, B is commanded again
    c.on_event("A", "phase", {"phase": 3}, 6.0)
    c.on_event("A", "wait", {"ph": 3, "seq": 0}, 6.0)
    acts = _ack_page(c, seq + 1, 6.0)
    assert any(a[1] == "B" for a in _sends(acts, CMD_GO))


def test_all_boards_dead_finishes_scoring_what_was_collected():
    c = _mk(labels=("A",), n_stills=1, k=1)
    _to_model_phase(c, ("A",))
    acts = c.on_event("A", "end", {"reason": "usb: gone"}, 1.0)
    assert any(a[0] == "drop" for a in acts)
    assert any(a[0] == "finish" for a in acts)
    assert c.done


def test_boot_timeout_drops_silent_board():
    c = _mk(labels=("A", "B"), boot_timeout=90.0)
    c.on_event("A", "info", {"w": 640}, 0.0)
    c.on_event("A", "phase", {"phase": 0}, 0.0)
    c.on_event("A", "wait", {"ph": 0, "seq": 0}, 0.0)
    acts = c.on_tick(100.0)
    assert any(a[0] == "drop" and a[1] == "B" for a in acts)
    # A proceeds alone: the run enters phase 0 and pages
    assert any(a[0] == "set_page" for a in acts)


def test_jpeg_all_toggle_switches_go_bytes():
    c = _mk(n_stills=1, k=1)
    c.on_review("jpeg_all", now=0.0)
    acts = _to_model_phase(c, ("A",))
    assert _sends(acts, CMD_GO_JPEG) and not _sends(acts, CMD_GO)


def test_abort_ends_run_and_survives_drain_race():
    """Abort sends 'q'; a 'q' eaten by the board's drain (board was
    mid-frame) is resent on its next heartbeat; #DONE then finishes."""
    from hil_protocol import CMD_QUIT
    c = _mk(labels=("A", "B"), n_stills=2, k=1)
    _to_model_phase(c, ("A", "B"))
    acts = c.on_review("abort", now=1.0)
    assert sorted(a[1] for a in _sends(acts, CMD_QUIT)) == ["A", "B"]
    # A's 'q' landed: #DONE is expected, not a premature drop
    acts = c.on_event("A", "done", {"frames": 5}, 1.5)
    assert not any(a[0] == "drop" for a in acts)
    # B was mid-frame and drained the 'q'; its park heartbeat → resend
    acts = _frame(c, "B", 2, 2.0)
    acts = c.on_event("B", "wait", {"ph": 2, "seq": 0}, 2.0)
    assert _sends(acts, CMD_QUIT) and _sends(acts, CMD_QUIT)[0][1] == "B"
    acts = c.on_event("B", "done", {"frames": 6}, 2.5)
    assert c.done and any(a[0] == "finish" for a in acts)


def test_monitor_snapshot_survives_numpy_like_scalars():
    """A stray non-JSON numeric (numpy float32 stand-in) must degrade to
    a number, not kill the page — the first live run's bug."""
    from hil_monitor import Monitor

    class F32:                      # quacks like np.float32
        def __float__(self):
            return 0.25

    m = Monitor()
    m.set_board("N6", dets_cam=[[F32(), F32(), F32(), F32(), F32()]])
    snap = m.snapshot()
    assert snap["boards"]["N6"]["dets_cam"][0][0] == 0.25
