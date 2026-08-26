# hil_board.py -- S8 bite E HIL, board side (N6 + AE3, one script).
#
# Pushed into the raw REPL by pi/hil/hil_harness.py (never written to the
# board). Runs a PHASE LIST in a single attach -- the AE3's bite-R attach
# budget is the reason phases exist: model swaps and mode swaps happen
# in-session, never by reattaching.
#
# WIRE DESIGN NOTE (2026-08-25, measured the hard way): shipping the raw
# head tensors via ndarray.tobytes()+b64 HARD-FAULTS stock N6 v5.0.0
# within 1-9 frames (board resets, re-enumerates MSC; reproduced 4x:
# probe-1, dryruns 2/4/5, probe-3 -- probe-3 crashed with the big write
# REMOVED, so the serialization itself is the killer). The FOMO harness's
# per-cell ndarray INDEXING surface has run for minutes on both boards
# (S8 B2), so this script emits SPARSE CELLS instead: every grid cell
# with objectness >= OBJ_THR, values read by indexing, shipped as small
# JSON lines. OBJ_THR 0.10 is a strict superset of any host-side
# conf >= 0.10 cut (conf = obj*cls <= obj). Cap = CELL_CAP cells/tile,
# NEVER silent: the dropped count rides the frame header.
#
# _CFG (prepended by the host):
#   framesize      "VGA"
#   jpeg_quality   50
#   phases         [{"kind":"jpeg","frames":N}                (loop/black/calib)
#                   {"kind":"model","model":"nano"|"tiny",
#                    "mode":"whole"|"tiled","frames":N}, ...]
#   handshake      False -- True = CLOSED LOOP (S8 bite E4): between
#                  frames the board PARKS, prints #W, and polls stdin
#                  for one control byte from the host. frames:0 then
#                  means "until told" -- no budget, no drain tail.
#   idle_s         handshake only: no byte at all for this long ->
#                  treat as 'q' (a dead host must not wedge the board)
#
# Control bytes (host->board, handshake only). Printable ASCII because
# the raw REPL intercepts 0x01-0x04 while a script runs:
#   g  run one frame    j  run one frame + ship the camera JPEG
#   p  end this phase   q  end the run
# The board DRAINS stdin before parking, so a duplicate byte (host
# resend racing a slow frame) is absorbed, never double-run. On 'g'/'j'
# one DISCARD snapshot precedes the scored one: the capture pipeline
# holds one completed frame that may predate the still change.
#
# Wire format (headers are json.dumps -- NEVER %r, repr is not JSON):
#   #I  {...}                    one info line at start
#   #PH {"phase":i,...}          at each phase start (resolved model path)
#   #W  {"ph":i,"seq":n}         handshake only: parked, awaiting a
#                                control byte; repeats every ~5 s as a
#                                heartbeat (parked-alive vs dead)
#   #F  {"seq","ph","ms","cap_us","prep_us":[..],"inf_us":[..],
#        "dec_us":[..],"tiles":[[x,y],..],"jpg":n,"cells":[n,..],
#        "dropped":[n,..]}
#   <jpg b64>\n                  when jpg > 0 (bare b64; CDC turns the
#                                terminator \n into \r\n -- host strips)
#   <cells json>\n               one line PER TILE: [[H,y,x,tx,ty,tw,th,
#                                obj,cls],...]
#   #DONE {"frames":n}
import binascii
import gc
import json
import os
import sys
import time

import csi
import image
import ml

try:
    _CFG
except NameError:
    _CFG = {}

FRAMESIZE = _CFG.get("framesize", "VGA")
QUALITY = _CFG.get("jpeg_quality", 50)
PHASES = _CFG.get("phases", [{"kind": "jpeg", "frames": 3}])
OBJ_THR = _CFG.get("obj_thr", 0.10)
CELL_CAP = _CFG.get("cell_cap", 128)
HANDSHAKE = _CFG.get("handshake", False)
IDLE_S = _CFG.get("idle_s", 300)
# AE-settle discards per go-byte (handshake only). One flushes the
# buffered frame; the REST give the sensor's auto-exposure time to adapt
# to the new still. Measured 2026-08-25 (first closed-loop VGA matrix):
# with 1 discard, frame-1 recall trails frame-2 by 0.05-0.08 on every
# cell of both boards — the open-loop settle window was silently doing
# AE's settling. Explicit and bounded beats silent and wall-clock.
DISCARD = _CFG.get("discard", 5)

if HANDSHAKE:
    import select
    _poll = select.poll()
    _poll.register(sys.stdin, select.POLLIN)


def _drain_stdin():
    while _poll.poll(0):
        sys.stdin.read(1)


def _wait_cmd(ph_i, seq):
    """Park until the host commands. -> 'g'|'j'|'p'|'q'.

    Drains stdin FIRST (duplicate-absorption -- see module note), then
    heartbeats #W every ~5 s so the host can tell parked-alive from
    dead. IDLE_S with no byte at all degrades to 'q': end cleanly at a
    usable REPL rather than wedging the board on a dead host."""
    _drain_stdin()
    t0 = time.ticks_ms()
    while True:
        print("#W " + json.dumps({"ph": ph_i, "seq": seq}))
        hb = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), hb) < 5000:
            if _poll.poll(100):
                c = sys.stdin.read(1)
                if c in ("g", "j", "p", "q"):
                    return c
                # anything else is line noise -- ignore, keep parking
            if time.ticks_diff(time.ticks_ms(), t0) > IDLE_S * 1000:
                return "q"

# 256-px tiles at native px, computed from the ACTUAL sensor geometry
# (framesize is a _CFG knob now; HD = 1280x800 -> 7x5 = 35 tiles). Max
# stride 192/144 keeps the VGA overlap contract: an urchin on a seam
# appears whole in at least one tile; the host's global NMS dedups.
# VGA reproduces the original grid exactly: (0,192,384) x (0,144).
def make_tiles(w, h, tile=256, sx=192, sy=144):
    import math
    def axis(span, s):
        if span <= tile:
            return [0]
        n = math.ceil((span - tile) / s) + 1
        return [round(i * (span - tile) / (n - 1)) for i in range(n)]
    return [(x, y) for y in axis(h, sy) for x in axis(w, sx)]


LETTER_SCALE = 0.4          # VGA * 0.4 = 256x160, top-left, gray pad
IN_W = 256


def b64line(raw):
    """Bare b64, NO terminator (the CDC CRLF trap -- see module note)."""
    e = binascii.b2a_base64(raw)
    return e[:-1] if e[-1:] == b"\n" else e


def find_models():
    """Stage-1 artifacts by SIZE: nano is the small one, tiny the big one.
    Names differ per board image, so resolve, never hard-code. /flash is
    included (AE3 carries nano there too) but /rom wins ties -- memory-
    mapped beats heap-copied."""
    cands = []
    for root in ("/rom", "/flash"):
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for f in names:
            low = f.lower()
            if low.endswith(".tflite") and (
                    "stage1" in low or "urchin" in low or "yolox" in low):
                cands.append((root + "/" + f, os.stat(root + "/" + f)[6]))
    rom_first = sorted(cands, key=lambda c: (not c[0].startswith("/rom"),))
    by_size = sorted(rom_first, key=lambda c: c[1])
    out = {}
    if by_size:
        out["nano"] = by_size[0][0]
        out["tiny"] = by_size[-1][0]
        if out["tiny"] == out["nano"]:
            del out["tiny"]
    return out


def extract_cells(outs, thr, cap):
    """Sparse candidate cells from the raw heads, by INDEXING only (the
    FOMO-proven surface). -> ([[H,y,x,tx,ty,tw,th,obj,cls],...], dropped)"""
    rows = []
    for o in outs:
        g = o[0]                          # (H, W, 6)
        hh = len(g)
        for y in range(hh):
            row = g[y]                    # (W, 6)
            for x in range(len(row)):
                c = row[x]                # (6,)
                ob = float(c[4])
                if ob >= thr:
                    rows.append([hh, y, x,
                                 round(float(c[0]), 4),
                                 round(float(c[1]), 4),
                                 round(float(c[2]), 4),
                                 round(float(c[3]), 4),
                                 round(ob, 4),
                                 round(float(c[5]), 4)])
    dropped = 0
    if len(rows) > cap:
        rows.sort(key=lambda r: -(r[7] * r[8]))
        dropped = len(rows) - cap
        del rows[cap:]
    return rows, dropped


MODELS = find_models()

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(getattr(csi, FRAMESIZE))
for _ in range(3):
    csi0.snapshot()                     # let AE settle before anything counts

canvas = image.Image(IN_W, IN_W, image.RGB565)   # ONE alloc, reused always

_first = csi0.snapshot()
TILES = make_tiles(_first.width(), _first.height())
print("#I " + json.dumps({"fw": sys.version, "board_models": MODELS,
                          "w": _first.width(), "h": _first.height(),
                          "tiles": TILES, "quality": QUALITY,
                          "obj_thr": OBJ_THR, "cell_cap": CELL_CAP}))

seq = 0
quit_all = False
for ph_i, ph in enumerate(PHASES):
    if quit_all:
        break
    kind = ph.get("kind", "jpeg")
    mode = ph.get("mode", "")
    model = None
    mpath = ""
    if kind == "model":
        if mode == "whole" and _first.width() != 640:
            # the whole-mode letterbox (LETTER_SCALE 0.4 -> 256x160) is
            # VGA arithmetic; refuse loudly rather than mis-scale
            print("#PH " + json.dumps(
                {"phase": ph_i,
                 "error": "whole mode is VGA-only (got %dpx wide)"
                 % _first.width()}))
            continue
        mpath = MODELS.get(ph.get("model", ""))
        if not mpath:
            print("#PH " + json.dumps(
                {"phase": ph_i, "error": "no %s model on this board"
                 % ph.get("model")}))
            continue
        gc.collect()
        model = ml.Model(mpath)
    print("#PH " + json.dumps(
        {"phase": ph_i, "kind": kind, "model": ph.get("model", ""),
         "path": mpath, "mode": mode, "frames": ph.get("frames", 0)}))
    frames_left = ph.get("frames", 0)
    while True:
        if HANDSHAKE:
            cmd = _wait_cmd(ph_i, seq)
            if cmd == "p":
                break
            if cmd == "q":
                quit_all = True
                break
            want_jpeg = (kind == "jpeg" or cmd == "j"
                         or (kind == "model" and ph.get("jpeg", False)))
            # discard snapshots: the first flushes the pipeline's
            # buffered frame (exposed BEFORE the still the host just
            # confirmed); the rest are the AE settle (see DISCARD note)
            for _ in range(DISCARD):
                csi0.snapshot()
        else:
            if frames_left <= 0:
                break
            frames_left -= 1
            want_jpeg = (kind == "jpeg"
                         or (kind == "model" and ph.get("jpeg", False)))
        t0 = time.ticks_us()
        img = csi0.snapshot()
        cap_us = time.ticks_diff(time.ticks_us(), t0)
        now_ms = time.ticks_ms()

        jb = b""
        if want_jpeg:
            jb = b64line(img.to_jpeg(quality=QUALITY, copy=True).bytearray())

        prep_us, inf_us, dec_us = [], [], []
        cell_lines, ncells, ndropped, tiles = [], [], [], []
        if kind == "model":
            if mode == "whole":
                rois = [None]
            else:
                rois = TILES

            for roi in rois:
                t0 = time.ticks_us()
                if roi is None:
                    canvas.draw_rectangle((0, 0, IN_W, IN_W),
                                          color=(114, 114, 114), fill=True)
                    canvas.draw_image(img, 0, 0, x_scale=LETTER_SCALE,
                                      y_scale=LETTER_SCALE)
                    tiles.append([0, 0])
                else:
                    canvas.draw_image(img, 0, 0,
                                      roi=(roi[0], roi[1], IN_W, IN_W))
                    tiles.append([roi[0], roi[1]])
                prep_us.append(time.ticks_diff(time.ticks_us(), t0))
                t0 = time.ticks_us()
                out = model.predict([canvas])
                inf_us.append(time.ticks_diff(time.ticks_us(), t0))
                t0 = time.ticks_us()
                cells, dropped = extract_cells(out, OBJ_THR, CELL_CAP)
                dec_us.append(time.ticks_diff(time.ticks_us(), t0))
                line = json.dumps(cells)
                cell_lines.append(line)
                ncells.append(len(cells))
                ndropped.append(dropped)

        print("#F " + json.dumps(
            {"seq": seq, "ph": ph_i, "ms": now_ms, "cap_us": cap_us,
             "prep_us": prep_us, "inf_us": inf_us, "dec_us": dec_us,
             "tiles": tiles, "jpg": len(jb), "cells": ncells,
             "dropped": ndropped}))
        if jb:
            sys.stdout.write(jb)
            sys.stdout.write("\n")
        for line in cell_lines:
            print(line)
        seq += 1
        gc.collect()
        if kind == "jpeg" and not HANDSHAKE:
            # pace jpeg phases: a fast board (N6 black-screen jpegs are
            # ~3 KB) burns the whole phase before the host's page-settle
            # window opens, starving the calibration of usable frames.
            # Closed loop needs no pacing -- the host paces by go-byte.
            time.sleep_ms(150)
    if model is not None:
        del model
        gc.collect()

print("#DONE " + json.dumps({"frames": seq}))
