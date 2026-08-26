#!/usr/bin/env python3
"""HIL harness (S8 bite E) — drive the screen, the boards, decode, score.

Runs ON nereus000. One board at a time, ONE serial attach per board (the
AE3's bite-R attach budget): the board script runs a phase list — black
frames, calibration frames, then model×mode phases — while this harness
steps the playback page's stills, stamps captures, decodes the raw YOLOX
heads (decode_np — the torch-parity-proven math), maps Nick's labels
through the screen→camera homography, and scores counts/misses/falses.

  python3 pi/hil/hil_harness.py \
      --board N6=/dev/serial/by-id/usb-MicroPython_Pyboard_... \
      --phases nano-whole,nano-tiled --frames-per-still 2 \
      --out ~/hil_runs/dryrun1

Artifacts (trust these, not the exit code): <out>/rows.jsonl (one row per
scored frame), calib_<label>.jpg + marker overlay, overlays/*.jpg (GT
green vs detections yellow), summary table on stdout.
"""
import argparse
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "bench"))
sys.path.insert(0, os.path.join(_ROOT, "ml", "yolox_urchin"))
from decode_np import cells_to_dets, merge_tiles    # noqa: E402
from n6_stream_host import SerialBoard              # noqa: E402
from hil_monitor import Monitor                     # noqa: E402
from hil_protocol import (BoardStream, Conductor,   # noqa: E402
                          CMD_QUIT)

STILL_W, STILL_H = 1920, 1080
IN_W = 256
LETTER_SCALE = 0.4          # board's whole-mode letterbox (VGA * 0.4)
CONF = 0.30
NMS_IOU = 0.45
MATCH_IOU = 0.30


# ---------------------------------------------------------------- playback
class Playback:
    def __init__(self, base):
        self.base = base.rstrip("/")
        st = self.state()
        self.markers = st["markers"]
        self.n_stills = len(st["stills"])
        self.stills = st["stills"]

    def _req(self, path, body=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def state(self):
        return self._req("/api/state")

    def set(self, **kw):
        return self._req("/api/set", kw)


# ------------------------------------------------------------- calibration
def jpeg_gray(jpg_bytes):
    return np.asarray(Image.open(io.BytesIO(jpg_bytes)).convert("L"),
                      np.float32)


def find_markers(calib_gray, black_gray):
    """4 marker centroids (camera px), TL/TR/BR/BL. The black-frame
    subtraction kills the room; each camera-frame quadrant then holds
    exactly one bright blob. Loud failure when a quadrant is dark."""
    diff = np.clip(calib_gray - black_gray, 0, None)
    h, w = diff.shape
    cy, cx = h // 2, w // 2
    quads = {"TL": (slice(0, cy), slice(0, cx)),
             "TR": (slice(0, cy), slice(cx, w)),
             "BR": (slice(cy, h), slice(cx, w)),
             "BL": (slice(cy, h), slice(0, cx))}
    cents = []
    for name in ("TL", "TR", "BR", "BL"):
        ys, xs = quads[name]
        q = diff[ys, xs]
        peak = float(q.max())
        if peak < 30:
            raise SystemExit(
                f"FAIL: calibration marker not visible in camera quadrant "
                f"{name} (peak {peak:.0f} < 30) — is the camera aimed at "
                f"the screen and the screen bright?")
        m = np.where(q > 0.5 * peak, q, 0.0) ** 2
        yy, xx = np.mgrid[0:q.shape[0], 0:q.shape[1]]
        cents.append((float((xx * m).sum() / m.sum()) + xs.start,
                      float((yy * m).sum() / m.sum()) + ys.start))
    return cents


def solve_homography(src, dst):
    """DLT, 4 exact correspondences: src (frac) -> dst (camera px)."""
    A, b = [], []
    for (sx, sy), (dx, dy) in zip(src, dst):
        A.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy])
        b.append(dx)
        A.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy])
        b.append(dy)
    h = np.linalg.solve(np.asarray(A, np.float64), np.asarray(b, np.float64))
    return np.append(h, 1.0).reshape(3, 3)


def map_still_box(H, x, y, w, h):
    """Label box (still px 1920x1080) -> camera-px bounding box."""
    pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                   np.float64)
    pts[:, 0] /= STILL_W
    pts[:, 1] /= STILL_H
    ones = np.ones((4, 1))
    p = (H @ np.hstack([pts, ones]).T).T
    p = p[:, :2] / p[:, 2:3]
    return (float(p[:, 0].min()), float(p[:, 1].min()),
            float(p[:, 0].max()), float(p[:, 1].max()))


def iou(a, b):
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    ua = ((a[2] - a[0]) * (a[3] - a[1])
          + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / ua if ua > 0 else 0.0


def match_frame(dets, boxes, H, cam_w, cam_h, min_gt_px=0):
    """One frame's scoring: visibility filter, edge filter, greedy IOU
    match, and the pixel-floor IGNORE semantics.

    THE single source of truth — the post-pass (score_pending) and the
    live monitor counters (E6) both call this, so the number on the
    page and the number in rows.jsonl can never disagree.

    Returns {"boxes","gt_cam","dets","pairs","n_match","gt_px"} plus,
    when min_gt_px > 0, {"n_gt_floor","n_match_floor","n_false_floor"}.
    """
    # VISIBILITY FILTER (markers-at-box-D setup): the camera sees only
    # part of the still, so GT outside the frame must not score as
    # misses — keep only GT boxes fully inside the camera frame (2 px
    # margin), and symmetrically drop detections touching the edge.
    m = 2.0
    vis = [(b, g) for b, g in
           ((b, map_still_box(H, b[1], b[2], b[3], b[4]))
            for b in boxes)
           if g[0] >= -m and g[1] >= -m
           and g[2] <= cam_w + m and g[3] <= cam_h + m]
    boxes_vis = [b for b, _g in vis]
    gt_cam = [g for _b, g in vis]
    if len(dets):
        keep = ((dets[:, 0] > m) & (dets[:, 1] > m)
                & (dets[:, 2] < cam_w - m)
                & (dets[:, 3] < cam_h - m))
        dets = dets[keep]
    used = set()
    match = 0
    pairs = {}                  # det idx -> matched gt idx
    order = np.argsort(-dets[:, 4]) if len(dets) else []
    for di in order:
        best, best_j = 0.0, -1
        for j, g in enumerate(gt_cam):
            if j not in used:
                v = iou(dets[di][:4], g)
                if v > best:
                    best, best_j = v, j
        if best >= MATCH_IOU:
            used.add(best_j)
            pairs[int(di)] = best_j
            match += 1
    gt_px = [round(min(g[2] - g[0], g[3] - g[1]), 1) for g in gt_cam]
    out = {"boxes": boxes_vis, "gt_cam": gt_cam, "dets": dets,
           "pairs": pairs, "n_match": match, "gt_px": gt_px}
    if min_gt_px > 0:
        # pixel floor, COCO-style IGNORE semantics: sub-floor GT never
        # count as misses, and detections matched to them leave the
        # false count (deleting them from GT instead would flip correct
        # small detections into falses)
        kept = {j for j in range(len(gt_cam)) if gt_px[j] >= min_gt_px}
        out["n_gt_floor"] = len(kept)
        out["n_match_floor"] = sum(1 for j in pairs.values() if j in kept)
        out["n_false_floor"] = int(len(dets)) - len(pairs)
    return out


# ------------------------------------------------------------ board stream
# BoardStream (the #I/#PH/#W/#F wire parser) moved to hil_protocol.py in
# bite E4 so the fake-board test suite covers it; it now wraps an
# already-started SerialBoard.


def start_stream(port, script, label=""):
    """One serial attach with ONE retry for the raw-repl refusal.

    Measured 2026-08-26 (N6, first card start after a prior run's
    teardown): SerialBoard.start raised TransportError('could not
    enter raw repl') on two consecutive card starts, then a manual
    attach minutes later succeeded first try — a transient
    first-attach state, same class as the sterile-stream retry at the
    call sites. One bounded retry, never a loop (ae3-board-access).
    TransportError is NOT an OSError (n6_stream_host's supervisor
    note), so the attach boundary catches broadly."""
    try:
        return BoardStream(SerialBoard(port).start(script))
    except Exception as e:
        print(f"    {label}: first attach refused ({e}) — one retry "
              f"once the port settles")
        # the failed attach's soft reset can RE-ENUMERATE the device
        # (measured AE3 2026-08-26: by-id link vanished and came back
        # ~30 s later; the 5 s retry hit 'failed to access') — wait,
        # bounded, for the node to exist again before the one retry
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not os.path.exists(port):
            time.sleep(0.5)
        time.sleep(5)
        return BoardStream(SerialBoard(port).start(script))


def frame_detections(fr):
    """Board frame -> detections in CAMERA px (VGA 640x400)."""
    if fr["tiles"] == [[0, 0]] and len(fr["_cells"]) == 1:   # whole mode
        dets = cells_to_dets(fr["_cells"][0], IN_W, conf=CONF,
                             nms_iou=NMS_IOU)
        if len(dets):
            # content occupies 256x160 (letterbox); clip, then unscale
            dets[:, [0, 2]] = np.clip(dets[:, [0, 2]], 0, IN_W) / LETTER_SCALE
            dets[:, [1, 3]] = np.clip(dets[:, [1, 3]], 0, 160) / LETTER_SCALE
            # a box wholly inside the gray pad clips to zero area — drop it
            keep = ((dets[:, 2] - dets[:, 0]) > 2) & \
                   ((dets[:, 3] - dets[:, 1]) > 2)
            dets = dets[keep]
        return dets
    per_tile = [cells_to_dets(c, IN_W, conf=CONF, nms_iou=NMS_IOU)
                for c in fr["_cells"]]
    return merge_tiles(per_tile, [tuple(t) for t in fr["tiles"]],
                       nms_iou=NMS_IOU)


# ------------------------------------------------------- closed loop (E4)
def dets_to_still_frac(dets_cam, Hinv):
    """Camera-px detections -> still-fraction boxes [x1,y1,x2,y2,conf]
    (H maps still fractions -> camera px, so Hinv lands in fractions)."""
    out = []
    for det in dets_cam:
        pts = np.array([[det[0], det[1]], [det[2], det[1]],
                        [det[2], det[3]], [det[0], det[3]]], np.float64)
        p = (Hinv @ np.hstack([pts, np.ones((4, 1))]).T).T
        p = p[:, :2] / p[:, 2:3]
        out.append([float(p[:, 0].min()), float(p[:, 1].min()),
                    float(p[:, 0].max()), float(p[:, 1].max()),
                    float(det[4])])
    return out


class _Reader(threading.Thread):
    """One per board: pump BoardStream events into the shared queue."""

    def __init__(self, label, stream, evq):
        super().__init__(daemon=True)
        self.label = label
        self.stream = stream
        self.evq = evq

    def run(self):
        while True:
            try:
                # parked boards heartbeat #W every ~5 s and an HD tiled
                # frame is ~4 s of legitimate silence — 45 s of nothing
                # means the board is gone
                ev, obj = self.stream.next_event(timeout_s=45)
            except IOError as e:
                self.evq.put((self.label, "end", {"reason": f"silent: {e}"}))
                return
            self.evq.put((self.label, ev, obj))
            if ev in ("done", "end"):
                return


class _BoardRun:
    """Per-board mutable run state for the closed-loop path."""

    def __init__(self, label):
        self.label = label
        self.stats = None
        self.summary = []
        self.jpeg_frames = {"loop": [], "black": [], "calib": []}
        self.pending = []
        self.H = None
        self.Hinv = None
        self.cam_w, self.cam_h = 640, 400
        self.last_frame = None        # (obj, dets) awaiting attribution
        # E6 live accuracy: running totals fed to the monitor per scored
        # frame via match_frame — the SAME function the post-pass uses
        self.live = {"gt": 0, "match": 0, "false": 0}


def run_closed_loop(args, playback, out_dir):
    """E4: all boards at once, per-still handshake, live monitor.

    Phase order is calib-FIRST here (black → calib → models), unlike the
    open-loop path's models-first: the live monitor needs the homography
    while the run is happening. Models-first was insurance against a
    jpeg→model transition fault on the N6 since attributed to the
    unshielded USB cable (SPEC, 2026-08-25) — the two-board acceptance
    A/B is the check that this reordering is safe.
    """
    reviewed = load_reviewed(args.stills_dir, not args.all_stills)
    if not reviewed:
        raise SystemExit("FAIL: no reviewed stills to score")
    k = args.frames_per_still
    phases = [{"kind": "jpeg", "page": "black"},
              {"kind": "jpeg", "page": "calib"}]
    for spec in args.phases.split(","):
        model, mode = spec.strip().split("-")
        phases.append({"kind": "model", "model": model, "mode": mode})
    board_phases = []
    for p in phases:
        bp = {"kind": p["kind"], "frames": 0}     # 0 = until told (E4)
        if p["kind"] == "model":
            # jpeg True (E7): the board now encodes AFTER the last tile,
            # in place in the frame buffer — zero heap beside the model
            # (probe 2026-08-26) — so every scored frame carries the
            # camera view and the panels track the run live. E4's
            # jpeg:False was the full-payload-beside-model caution; the
            # in-place path removes that class by construction.
            bp.update({"model": p["model"], "mode": p["mode"],
                       "jpeg": True})
        board_phases.append(bp)
    mode = "review" if args.review else "auto"
    script = ("_CFG = " + repr({"framesize": args.framesize,
                                "jpeg_quality": 50,
                                "phases": board_phases,
                                "handshake": True,
                                # a review hold can be a long human
                                # pause; never let the board give up
                                # under Nick
                                "idle_s": 3600 if args.review else 300})
              + "\n" + open(os.path.join(_HERE, "hil_board.py")).read())
    board_list = [spec.split("=", 1) for spec in args.board]
    labels = [lb for lb, _p in board_list]
    print(f"\n=== CLOSED LOOP ({mode}): {', '.join(labels)}\n    phases: "
          + ", ".join(p.get("page") or f"{p['model']}-{p['mode']}"
                      for p in phases))

    mon = Monitor(playback_port=int(playback.base.rsplit(":", 1)[1]),
                  still_dir=os.path.join(
                      os.path.expanduser(args.stills_dir), "frames"))
    mon_port = mon.start(port=args.monitor_port)
    print(f"    monitor page: http://0.0.0.0:{mon_port}/  "
          f"(trusted LAN, no auth — view from the Mac)")

    power_proc = None
    plog = os.path.join(_ROOT, "pi", "workbench", "power_log.py")
    if os.path.exists(plog):
        power_proc = subprocess.Popen(
            [sys.executable, plog, "--ch", "1=AE3", "--ch", "3=N6",
             "--hz", "10", "--out", out_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"    power logger up (pid {power_proc.pid})")

    evq = queue.Queue()
    streams = {}
    runs = {lb: _BoardRun(lb) for lb in labels}
    for lb, port in board_list:
        bs = start_stream(port, script, label=lb)
        ev0, obj0 = bs.next_event(timeout_s=60)
        if ev0 == "end":
            print(f"    {lb}: first attach died sterile "
                  f"({obj0.get('reason')}) — one retry in 5 s")
            bs.stop()
            time.sleep(5)
            bs = start_stream(port, script, label=lb)
            ev0, obj0 = bs.next_event(timeout_s=60)
        streams[lb] = bs
        evq.put((lb, ev0, obj0))
        _Reader(lb, bs, evq).start()

    con = Conductor(labels, phases, stills=[r[0] for r in reviewed], k=k,
                    mode=mode, frame_timeout=args.frame_timeout)
    rows_path = os.path.join(out_dir, "rows.jsonl")
    rows_fh = open(rows_path, "a")
    # raw sparse cells per scored frame — the heat-map renderer's input
    # (pi/hil/hil_heatmap.py). Cheap: the cells are already in memory.
    cells_fh = open(os.path.join(out_dir, "cells.jsonl"), "a")
    os.makedirs(os.path.join(out_dir, "overlays"), exist_ok=True)

    t_page_cmd = None
    last_shown_poll = 0.0
    last_tick = 0.0

    def update_monitor():
        snap = con.snapshot()
        mon.set_run(snap)
        if con.phase_i >= 2 and con.stage in ("step", "hold",
                                              "shown_wait",
                                              "await_page_cmd"):
            pb_i, name, boxes = reviewed[con.still_i]
            mon.set_still({
                "name": name, "index": con.still_i,
                "gt": [[b[1] / STILL_W, b[2] / STILL_H,
                        (b[1] + b[3]) / STILL_W, (b[2] + b[4]) / STILL_H]
                       for b in boxes]})
        for lb in labels:
            b = snap["boards"][lb]
            mon.set_board(lb, status=b["status"], got=b["got"],
                          drop_reason=b["drop_reason"])

    def handle(lb, ev, obj):
        """Bookkeeping BEFORE the conductor sees the event."""
        r = runs[lb]
        if ev == "info":
            r.cam_w = int(obj.get("w", r.cam_w))
            r.cam_h = int(obj.get("h", r.cam_h))
            print(f"    {lb}: {obj['board_models']} "
                  f"({obj.get('w')}x{obj.get('h')}, "
                  f"{len(obj.get('tiles', []))} tiles)")
            mon.set_board(lb, model=str(obj.get("board_models")))
            mon.log(f"{lb} up: {obj.get('w')}x{obj.get('h')}")
        elif ev == "phase":
            if r.stats:
                r.summary.append(r.stats)
                r.stats = None
            if "error" in obj:
                print(f"    {lb} PHASE SKIPPED: {obj['error']}")
                mon.log(f"{lb} phase skipped: {obj['error']}")
                return
            ph = phases[obj["phase"]]
            name = ph.get("page") or f"{obj['model']}-{obj['mode']}"
            print(f"    {lb} phase {name}: {obj.get('path') or ''}")
            mon.log(f"{lb} phase {name}")
            r.stats = {"board": lb, "phase": name,
                       "path": obj.get("path"), "frames": 0, "gt": 0,
                       "det": 0, "match": 0, "inf_us": [], "cap_us": [],
                       "prep_us": [], "t0": None, "t1": None}
        elif ev == "frame":
            obj["t_host"] = time.time()          # power-log alignment
            dets = np.zeros((0, 5))
            if obj.get("inf_us"):                # model-phase frame
                dets = frame_detections(obj)
            r.last_frame = (obj, dets)
        elif ev == "skip":
            print(f"    WARN {lb} seq {obj.get('seq')}: corrupt payload "
                  f"— closed loop will re-run the frame")
            mon.log(f"{lb} corrupt frame re-run (seq {obj.get('seq')})")

    def execute(acts):
        nonlocal t_page_cmd
        while acts:
            a = acts.pop(0)
            kind = a[0]
            if kind == "send":
                _k, lb, cmd = a
                try:
                    streams[lb].sb.ser.write(cmd)
                except Exception as e:
                    print(f"    {lb}: control write failed ({e})")
            elif kind == "set_page":
                resp = playback.set(**a[1])
                t_page_cmd = time.monotonic()
                acts += con.on_page_commanded(resp["seq"],
                                              time.monotonic())
            elif kind == "frame_ok":
                _k, lb, slot, n = a
                r = runs[lb]
                obj, dets = r.last_frame
                if isinstance(slot, str):        # jpeg page
                    r.jpeg_frames[slot].append(obj["_jpg"])
                    mon.set_cam(lb, obj["_jpg"])
                    if r.stats:
                        r.stats["frames"] += 1
                    if (slot == "calib" and r.H is None
                            and r.jpeg_frames["black"]
                            and len(r.jpeg_frames["calib"]) >= 2):
                        r.H = solve_board_H(lb, r.jpeg_frames, playback,
                                            out_dir)
                        r.Hinv = np.linalg.inv(r.H)
                        mon.log(f"{lb} homography solved")
                    continue
                pb_i, name, boxes = reviewed[slot]
                r.pending.append({"stats": r.stats, "still": name,
                                  "boxes": boxes, "dets": dets,
                                  "obj": obj, "frame_in_still": n})
                cells_fh.write(json.dumps(
                    {"board": lb,
                     "phase": r.stats["phase"] if r.stats else "",
                     "still": name, "frame_in_still": n,
                     "seq": obj["seq"], "tiles": obj["tiles"],
                     "cells": obj["_cells"],
                     "cam_w": r.cam_w, "cam_h": r.cam_h}) + "\n")
                st = r.stats
                if st is not None:
                    st["frames"] += 1
                    st["inf_us"].append(sum(obj["inf_us"]))
                    st["cap_us"].append(obj["cap_us"])
                    st["prep_us"].append(sum(obj["prep_us"])
                                         + sum(obj["dec_us"]))
                    t = obj["_arrival"]
                    st["t0"] = t if st["t0"] is None else st["t0"]
                    st["t1"] = t
                if any(obj["dropped"]):
                    print(f"    NOTE {lb} seq {obj['seq']}: cell cap "
                          f"dropped {obj['dropped']} (dense frame)")
                acc = None
                if r.H is not None:
                    mf = match_frame(dets, boxes, r.H, r.cam_w, r.cam_h,
                                     args.min_gt_px)
                    lv = r.live
                    if args.min_gt_px > 0:
                        lv["gt"] += mf["n_gt_floor"]
                        lv["match"] += mf["n_match_floor"]
                        lv["false"] += mf["n_false_floor"]
                    else:
                        lv["gt"] += len(mf["boxes"])
                        lv["match"] += mf["n_match"]
                        lv["false"] += int(len(mf["dets"])) - mf["n_match"]
                    denom = lv["match"] + lv["false"]
                    acc = {"recall": round(lv["match"] / lv["gt"], 3)
                           if lv["gt"] else None,
                           "prec": round(lv["match"] / denom, 3)
                           if denom else None,
                           "gt": lv["gt"], "floor": args.min_gt_px}
                inf_ms = round(sum(obj["inf_us"]) / 1000, 1)
                e2e_ms = round((obj["cap_us"] + sum(obj["prep_us"])
                                + sum(obj["inf_us"])
                                + sum(obj["dec_us"])) / 1000, 1)
                mon.set_board(
                    lb, n_det=int(len(dets)), inf_ms=inf_ms,
                    e2e_ms=e2e_ms, acc=acc,
                    # float() every value: numpy float32 leaking into the
                    # monitor state killed /api/monitor with an empty
                    # reply on the first live run (json can't dump it)
                    dets_cam=[[float(d[0]) / r.cam_w,
                               float(d[1]) / r.cam_h,
                               float(d[2]) / r.cam_w,
                               float(d[3]) / r.cam_h,
                               float(d[4])] for d in dets],
                    dets_still=(dets_to_still_frac(dets, r.Hinv)
                                if r.Hinv is not None else None))
                if obj["_jpg"]:
                    mon.set_cam(lb, obj["_jpg"])
            elif kind == "frame_stray":
                mon.log(f"{a[1]} stray frame (no confirmed still) "
                        f"— dropped, not scored")
            elif kind == "hold":
                mon.log("REVIEW HOLD — press Next on the monitor page")
            elif kind == "drop":
                _k, lb, reason = a
                print(f"    !! {lb} LEFT THE BARRIER: {reason} — "
                      f"remaining boards continue solo")
                mon.log(f"{lb} DROPPED: {reason}")
                try:
                    streams[lb].stop()
                except Exception:
                    pass
            elif kind == "finish":
                pass                              # con.done ends the loop

    aborted = False
    try:
        while not con.done:
            now = time.monotonic()
            try:
                lb, ev, obj = evq.get(timeout=0.1)
                handle(lb, ev, obj)
                execute(con.on_event(lb, ev, obj, now))
            except queue.Empty:
                pass
            while True:                           # drain without blocking
                try:
                    lb, ev, obj = evq.get_nowait()
                except queue.Empty:
                    break
                handle(lb, ev, obj)
                execute(con.on_event(lb, ev, obj, now))
            try:
                while True:
                    action, board = mon.review_q.get_nowait()
                    mon.log(f"review: {action}"
                            + (f" ({board})" if board else ""))
                    execute(con.on_review(action, board=board, now=now))
            except queue.Empty:
                pass
            if con.stage == "shown_wait":
                if args.no_shown_ack:
                    # no LCD render-ack available (e.g. dev without the
                    # hil-lcd service): fall back to a fixed render wait
                    if t_page_cmd and now - t_page_cmd > 0.7:
                        execute(con.on_shown(con.page_seq, now))
                elif now - last_shown_poll > 0.25:
                    last_shown_poll = now
                    try:
                        st = playback.state()
                        execute(con.on_shown(st.get("shown_seq", 0), now))
                    except Exception:
                        pass
            if now - last_tick > 0.5:
                last_tick = now
                execute(con.on_tick(now))
                update_monitor()
    except KeyboardInterrupt:
        # Stop means stop (E5): the workbench wrapper sends ONE SIGINT
        # and waits — score, skip the review park, exit
        aborted = True
        print("\n    interrupted — ending boards cleanly, scoring what "
              "was collected")
        for lb in labels:
            try:
                streams[lb].sb.ser.write(CMD_QUIT)
            except Exception:
                pass
        time.sleep(1)
    finally:
        for lb in labels:
            try:
                streams[lb].stop()
            except Exception:
                pass
        try:
            playback.set(mode="loop")
        except OSError:
            # a dead playback must not abort scoring (partial failure
            # never destroys good data)
            print("    (playback gone — loop-mode reset skipped)")
        if power_proc is not None:
            power_proc.terminate()
            try:
                power_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                power_proc.kill()

    snap = con.snapshot()
    print(f"\n    audit: settle_discards={snap['settle_discards']} "
          f"(closed loop — the concept is gone), "
          f"stray_frames={snap['stray_frames']} (named + dropped)")
    for lb in labels:
        r = runs[lb]
        if r.stats:
            r.summary.append(r.stats)
        print(f"\n=== {lb} results")
        score_pending(r.pending, r.H, lb, args, out_dir, rows_fh,
                      r.cam_w, r.cam_h)
        print_summary(r.summary, args)
    rows_fh.close()
    cells_fh.close()
    mon.set_run({**snap, "stage": "finished"})
    print(f"\nrows: {rows_path}")
    # after a COMPLETED review run the monitor stays up for reading;
    # after an abort the stop was the instruction — exit now (a second
    # SIGINT sent blind could land mid-scoring on the next run)
    if args.review and not aborted:
        print("    monitor page stays up until Ctrl-C")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    mon.stop()


# ------------------------------------------------------------------- main
def load_reviewed(stills_dir, reviewed_only=True):
    """-> [(playback_index, file, [boxes]), ...] in playback order."""
    recs = [json.loads(ln)
            for ln in open(os.path.join(stills_dir, "labels.jsonl"))]
    man = json.load(open(os.path.join(stills_dir, "stills_manifest.json")))
    order = []
    for c in man["clips"]:
        for idx in c["sampled_indices"]:
            order.append(f"{c['still_prefix']}_f{idx:04d}.jpg")
    by_name = {r["file"].split("/")[-1]: r for r in recs}
    out = []
    for i, name in enumerate(order):
        r = by_name.get(name)
        if r is None:
            continue
        if reviewed_only and not r.get("reviewed"):
            continue
        out.append((i, name, r["boxes"]))
    return out


def save_still_overlay(path, still_path, dets_cam, boxes, Hinv,
                       cam_wh=None):
    """GT (green, native) + detections mapped camera→still via H⁻¹ (yellow),
    drawn on the SOURCE still — no camera JPEG needed. cam_wh draws the
    camera's field of view as a cyan quadrilateral: GT outside it was
    filtered from scoring, so a 'missed' urchin outside the cyan line is
    not a miss."""
    img = Image.open(still_path).convert("RGB")
    d = ImageDraw.Draw(img)
    if cam_wh is not None:
        cw, ch = cam_wh
        pts = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float64)
        p = (Hinv @ np.hstack([pts, np.ones((4, 1))]).T).T
        p = p[:, :2] / p[:, 2:3]
        poly = [(float(x) * STILL_W, float(y) * STILL_H) for x, y in p]
        d.polygon(poly, outline=(0, 180, 255), width=2)
    for (_ci, x, y, w, h, _px) in boxes:
        d.rectangle([x, y, x + w, y + h], outline=(0, 255, 60), width=3)
    for det in dets_cam:
        pts = np.array([[det[0], det[1]], [det[2], det[1]],
                        [det[2], det[3]], [det[0], det[3]]], np.float64)
        p = (Hinv @ np.hstack([pts, np.ones((4, 1))]).T).T
        p = p[:, :2] / p[:, 2:3]
        x1, y1 = p[:, 0].min() * STILL_W, p[:, 1].min() * STILL_H
        x2, y2 = p[:, 0].max() * STILL_W, p[:, 1].max() * STILL_H
        d.rectangle([x1, y1, x2, y2], outline=(255, 220, 0), width=3)
        d.text((x1 + 3, y1 + 3), f"{det[4]:.2f}", fill=(255, 220, 0))
    img.save(path, quality=88)


def solve_board_H(label, jpeg_frames, playback, out_dir):
    """Save the camera's raw views, find the markers, solve + persist H.

    Evidence is saved FIRST: a failed solve must leave what the camera
    actually saw. Shared by the open-loop and closed-loop paths."""
    open(os.path.join(out_dir, f"calib_{label}.jpg"),
         "wb").write(jpeg_frames["calib"][-1])
    open(os.path.join(out_dir, f"black_{label}.jpg"),
         "wb").write(jpeg_frames["black"][-1])
    if jpeg_frames.get("loop"):
        open(os.path.join(out_dir, f"loopview_{label}.jpg"),
             "wb").write(jpeg_frames["loop"][-1])
    black = jpeg_gray(jpeg_frames["black"][-1])
    calib = jpeg_gray(jpeg_frames["calib"][-1])
    cents = find_markers(calib, black)
    H = solve_homography(playback.markers, cents)
    img = Image.open(io.BytesIO(jpeg_frames["calib"][-1])).convert("RGB")
    d = ImageDraw.Draw(img)
    for cx, cy in cents:
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6],
                  outline=(255, 0, 0), width=3)
    img.save(os.path.join(out_dir, f"calib_{label}_markers.jpg"))
    np.save(os.path.join(out_dir, f"H_{label}.npy"), H)
    print("    homography solved; markers at "
          + ", ".join(f"({cx:.0f},{cy:.0f})" for cx, cy in cents))
    return H


def score_pending(pending, H, label, args, out_dir, rows_fh, cam_w, cam_h):
    """Post-pass: score every buffered model frame against H, write rows
    + per-still overlays, and roll totals into each frame's stats dict.
    Shared by the open-loop and closed-loop paths."""
    if pending and H is None:
        print(f"    WARNING: {len(pending)} frames collected but NO "
              f"homography (calib phase never completed) — timers reported, "
              f"accuracy NOT scored")
    if H is None:
        return
    Hinv = np.linalg.inv(H)
    overlaid = set()
    for p in pending:
        boxes, dets, obj = p["boxes"], p["dets"], p["obj"]
        st = p["stats"]
        mf = match_frame(dets, boxes, H, cam_w, cam_h, args.min_gt_px)
        boxes, gt_cam, dets = mf["boxes"], mf["gt_cam"], mf["dets"]
        pairs, match, gt_px = mf["pairs"], mf["n_match"], mf["gt_px"]
        row = {"board": label, "phase": st["phase"],
               "still": p["still"],
               "frame_in_still": p["frame_in_still"],
               "seq": obj["seq"], "t_host": obj.get("t_host"),
               "cap_us": obj["cap_us"],
               "prep_us": obj["prep_us"], "inf_us": obj["inf_us"],
               "dec_us": obj["dec_us"], "dropped": obj["dropped"],
               "n_gt": len(boxes), "n_det": int(len(dets)),
               "n_match": match, "n_miss": len(boxes) - match,
               "n_false": int(len(dets)) - match,
               "gt_px_cam": gt_px,
               "det_conf": [round(float(d[4]), 3) for d in dets],
               "dets_cam": [[round(float(v), 1) for v in d[:5]]
                            for d in dets]}
        if args.min_gt_px > 0:
            row.update({"floor_px": args.min_gt_px,
                        "n_gt_floor": mf["n_gt_floor"],
                        "n_match_floor": mf["n_match_floor"],
                        "n_false_floor": mf["n_false_floor"]})
        rows_fh.write(json.dumps(row) + "\n")
        st["gt"] += len(boxes)
        st["det"] += int(len(dets))
        st["match"] += match
        if args.min_gt_px > 0:
            st["gt_floor"] = st.get("gt_floor", 0) + row["n_gt_floor"]
            st["match_floor"] = (st.get("match_floor", 0)
                                 + row["n_match_floor"])
            st["false_floor"] = (st.get("false_floor", 0)
                                 + row["n_false_floor"])
        key = (st["phase"], p["still"])
        if key not in overlaid:
            overlaid.add(key)
            save_still_overlay(
                os.path.join(out_dir, "overlays",
                             f"{label}_{st['phase']}_{p['still']}"),
                os.path.join(args.stills_dir, "frames", p["still"]),
                dets, boxes, Hinv, cam_wh=(cam_w, cam_h))


def print_summary(summary, args):
    print(f"\n    {'phase':<12} {'frames':>6} {'GT':>5} {'det':>5} "
          f"{'match':>5} {'miss':>5} {'false':>5} {'inf ms':>7} "
          f"{'e2e ms/frame':>12}")
    for s in summary:
        if s["phase"] in ("black", "calib"):
            print(f"    ({s['phase']}: {s['frames']} post-settle frames)")
            continue
        if not s["frames"]:
            continue
        n = s["frames"]
        inf = sum(s["inf_us"]) / n / 1000
        e2e = (sum(s["cap_us"]) + sum(s["prep_us"])
               + sum(s["inf_us"])) / n / 1000
        line = (f"    {s['phase']:<12} {n:>6} {s['gt']:>5} {s['det']:>5} "
                f"{s['match']:>5} {s['gt'] - s['match']:>5} "
                f"{s['det'] - s['match']:>5} {inf:>7.1f} {e2e:>12.1f}")
        if args.min_gt_px > 0 and s.get("gt_floor"):
            gf, mf = s["gt_floor"], s["match_floor"]
            ff = s["false_floor"]
            prec = mf / (mf + ff) if mf + ff else 0.0
            line += (f"   | >={args.min_gt_px:g}px: recall "
                     f"{mf / gf if gf else 0:.2f} prec {prec:.2f} "
                     f"(GT {gf})")
        if s.get("t0") is not None and s.get("t1") is not None:
            line += f"   wall {s['t1'] - s['t0']:.1f}s"
        print(line)


def run_board(label, port, args, playback, out_dir):
    reviewed = load_reviewed(args.stills_dir, not args.all_stills)
    if not reviewed:
        raise SystemExit("FAIL: no reviewed stills to score")
    k = args.frames_per_still
    # settle discards ~8-10 frames/still on a fast whole-mode phase —
    # budget generously; the host drains any surplus. At HD a frame is
    # seconds, so the surplus must shrink (--budget-slack) or the drain
    # burns minutes of dead wall time per phase.
    frames_model = len(reviewed) * (k + args.budget_slack) + 15
    # MODEL PHASES FIRST, on a clean heap: the N6 hard-faulted twice at or
    # after the jpeg→model transition (2026-08-25) — ordering models before
    # any to_jpeg churn isolates whether model+tensor-emit alone is stable
    # (the D38 fb-alloc defect class; the N6 is stock, unpatched).
    # Scoring no longer needs H at frame time — it is a post-pass.
    phases = []
    for spec in args.phases.split(","):
        model, mode = spec.strip().split("-")
        # jpeg False: the ~50 KB/frame camera JPEG is dropped from model
        # phases after the 2026-08-25 N6 hard-fault under full payload;
        # overlays are rendered onto the SOURCE stills via H⁻¹ instead
        phases.append({"kind": "model", "model": model, "mode": mode,
                       "frames": frames_model, "jpeg": False})
    # jpeg phases: selection is by arrival stamp (page poll 300 ms +
    # render + settle), so the phase must OUTLAST the settle window on a
    # FAST board — the N6 streams jpeg frames at ~4 ms encode and burned
    # all 30 frames before settle opened (matrix_d70_1: "calib phase
    # never completed"). 90 frames ≈ 6-9 s on the N6, ~12-18 s on the AE3.
    phases += [{"kind": "jpeg", "frames": 90, "page": "loop"},
               {"kind": "jpeg", "frames": 90, "page": "black"},
               {"kind": "jpeg", "frames": 90, "page": "calib"}]

    board_phases = [{k2: v for k2, v in p.items() if k2 != "page"}
                    for p in phases]
    script = ("_CFG = " + repr({"framesize": args.framesize,
                                "jpeg_quality": 50,
                                "phases": board_phases}) + "\n"
              + open(os.path.join(_HERE, "hil_board.py")).read())

    print(f"\n=== {label} on {port}\n    phases: "
          + ", ".join(p.get("page") or f"{p['model']}-{p['mode']}"
                      for p in phases))
    # power column (free, INA3221 CH1=AE3/CH3=N6): one logger per board
    # run, JSONL beside rows.jsonl; frame rows carry t_host for alignment
    power_proc = None
    plog = os.path.join(_ROOT, "pi", "workbench", "power_log.py")
    if os.path.exists(plog):
        power_proc = subprocess.Popen(
            [sys.executable, plog, "--ch", "1=AE3", "--ch", "3=N6",
             "--hz", "10", "--out", out_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"    power logger up (pid {power_proc.pid})")

    bs = start_stream(port, script, label=label)
    # the FIRST attach after a board crash-reset tends to die instantly
    # (the soft reset re-enumerates the port under the fresh connection);
    # one retry, only when the stream produced NOTHING
    ev0, obj0 = bs.next_event(timeout_s=60)
    if ev0 == "end":
        print(f"    first attach died sterile ({obj0.get('reason')}) — "
              f"one retry in 5 s")
        bs.stop()
        time.sleep(5)
        bs = start_stream(port, script, label=label)
        ev0, obj0 = bs.next_event(timeout_s=60)
    first_event = (ev0, obj0)
    rows_path = os.path.join(out_dir, "rows.jsonl")
    rows_fh = open(rows_path, "a")
    os.makedirs(os.path.join(out_dir, "overlays"), exist_ok=True)

    H = None
    cam_w, cam_h = 640, 400        # overwritten by the board's #I line
    jpeg_frames = {"loop": [], "black": [], "calib": []}  # post-settle only
    t_page = 0.0
    cur = None                      # current phase dict
    cur_i = -1
    # per-model-phase still stepping state
    still_i = 0
    t_step = 0.0
    got_for_still = 0
    summary = []
    stats = None
    pending = []                    # decoded model frames awaiting H

    def start_still(i):
        nonlocal t_step, got_for_still
        playback.set(mode="step", still=reviewed[i][0])
        t_step = time.monotonic()
        got_for_still = 0

    try:
        while True:
            if first_event is not None:
                ev, obj = first_event
                first_event = None
            else:
                ev, obj = bs.next_event(timeout_s=60)
            if ev == "skip":
                # corrupted payload line; the parser realigned (the WARN
                # print moved here when BoardStream went to hil_protocol)
                print(f"    WARN seq {obj.get('seq')}: corrupt payload, "
                      f"frame skipped")
                continue
            if ev == "info":
                print(f"    board: {obj['board_models']} "
                      f"({obj.get('w')}x{obj.get('h')}, "
                      f"{len(obj.get('tiles', []))} tiles)")
                cam_w = int(obj.get("w", cam_w))
                cam_h = int(obj.get("h", cam_h))
                continue
            if ev in ("done", "end"):
                print(f"    stream {ev}: {obj}")
                break
            if ev == "phase":
                if stats:
                    summary.append(stats)
                if "error" in obj:
                    print(f"    PHASE SKIPPED: {obj['error']}")
                    stats = None
                    cur = None
                    continue
                cur_i = obj["phase"]
                cur = phases[cur_i]
                name = cur.get("page") or f"{obj['model']}-{obj['mode']}"
                print(f"    phase {name}: {obj.get('path') or ''}")
                stats = {"board": label, "phase": name,
                         "path": obj.get("path"), "frames": 0, "gt": 0,
                         "det": 0, "match": 0, "inf_us": [], "cap_us": [],
                         "prep_us": []}
                if cur["kind"] == "jpeg":
                    playback.set(mode=cur["page"])
                    t_page = time.monotonic()
                else:
                    still_i = 0
                    start_still(0)
                continue
            # frame
            if cur is None:
                continue
            if cur["kind"] == "jpeg":
                # only frames captured comfortably after the page switch
                if obj["_arrival"] < t_page + args.settle:
                    continue
                jpeg_frames[cur["page"]].append(obj["_jpg"])
                stats["frames"] += 1
                if (cur["page"] == "calib" and H is None
                        and jpeg_frames["black"]
                        and len(jpeg_frames["calib"]) >= 2):
                    H = solve_board_H(label, jpeg_frames, playback,
                                      out_dir)
                continue
            # model-phase frame: only frames captured comfortably after the
            # still went up count; decode now, score in the post-pass once
            # the homography exists
            if obj["_arrival"] < t_step + args.settle:
                continue
            if got_for_still >= k:
                continue                    # extras while page steps
            got_for_still += 1
            pb_i, name, boxes = reviewed[still_i]
            dets = frame_detections(obj)
            obj["t_host"] = time.time()      # power-log alignment
            pending.append({"stats": stats, "still": name, "boxes": boxes,
                            "dets": dets, "obj": obj,
                            "frame_in_still": got_for_still})
            stats["frames"] += 1
            stats["inf_us"].append(sum(obj["inf_us"]))
            stats["cap_us"].append(obj["cap_us"])
            stats["prep_us"].append(sum(obj["prep_us"]) + sum(obj["dec_us"]))
            if any(obj["dropped"]):
                print(f"    NOTE seq {obj['seq']}: cell cap dropped "
                      f"{obj['dropped']} (dense frame)")
            if got_for_still >= k:
                still_i += 1
                if still_i < len(reviewed):
                    start_still(still_i)
                # else: drain the phase's remaining frames unscored
    except IOError as e:
        # a dead stream must not discard the frames already collected —
        # fall through to the post-pass with whatever we have
        print(f"    STREAM ERROR (scoring what was collected): {e}")
    finally:
        if stats:
            summary.append(stats)
        bs.stop()
        try:
            playback.set(mode="loop")
        except OSError:
            print("    (playback gone — loop-mode reset skipped)")
        if power_proc is not None:
            power_proc.terminate()
            try:
                power_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                power_proc.kill()

    # ---- post-pass: score every buffered frame against the homography ----
    score_pending(pending, H, label, args, out_dir, rows_fh, cam_w, cam_h)
    rows_fh.close()

    print_summary(summary, args)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", action="append", required=True,
                    metavar="LABEL=/dev/serial/by-id/...")
    ap.add_argument("--phases", default="nano-whole,nano-tiled")
    ap.add_argument("--stills-dir",
                    default=os.path.expanduser("~/hil_monterey/stills"))
    ap.add_argument("--playback", default="http://localhost:8091")
    ap.add_argument("--frames-per-still", type=int, default=2)
    ap.add_argument("--settle", type=float, default=1.2,
                    help="seconds after a still change before captures count")
    ap.add_argument("--framesize", default="VGA", choices=("VGA", "HD"),
                    help="board capture size; HD = 1280x800, tiles "
                         "computed from geometry (whole mode VGA-only)")
    ap.add_argument("--budget-slack", type=int, default=10,
                    help="surplus frames budgeted per still beyond "
                         "frames-per-still; use ~4 at HD where a frame "
                         "is seconds and surplus drains as dead time")
    ap.add_argument("--all-stills", action="store_true",
                    help="score every still, not just reviewed ones")
    ap.add_argument("--min-gt-px", type=float, default=0,
                    help="GT pixel floor (min-side, camera px): sub-floor "
                         "urchins are IGNORED (not misses, and matches to "
                         "them are not falses) — the T2 24-32 px band; "
                         "Nick's 2026-08-25 call is 30. Raw counts stay "
                         "in every row; floored counts ride alongside")
    ap.add_argument("--closed-loop", action="store_true",
                    help="E4 handshake mode: ALL boards at once, "
                         "per-still go/done barrier, zero settle "
                         "discards by construction, live monitor page")
    ap.add_argument("--review", action="store_true",
                    help="closed-loop only: hold at each still until "
                         "Next is pressed on the monitor page — Nick in "
                         "the loop before any automated run")
    ap.add_argument("--monitor-port", type=int, default=8092)
    ap.add_argument("--frame-timeout", type=float, default=30.0,
                    help="closed-loop: seconds a board may sit on one "
                         "go-byte before a resend (3 strikes = dropped)")
    ap.add_argument("--no-shown-ack", action="store_true",
                    help="closed-loop: no hil-lcd render ack available; "
                         "fall back to a fixed 0.7 s render wait")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_dir = os.path.expanduser(args.out)
    os.makedirs(out_dir, exist_ok=True)
    try:
        playback = Playback(args.playback)
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(
            f"FAIL: no playback server at {args.playback} ({e}).\n"
            f"Start the 'Urchin HIL — Monterey playback' recipe "
            f"(s8-hil-urchin) on http://nereus000:8088 first — it owns "
            f"the screen and the board locks.")
    n_rev = len(load_reviewed(args.stills_dir, not args.all_stills))
    print(f"scoring {n_rev} stills, {args.frames_per_still} frames each; "
          f"phases {args.phases}; out {out_dir}")

    if args.closed_loop:
        run_closed_loop(args, playback, out_dir)
        return
    if args.review:
        raise SystemExit("FAIL: --review needs --closed-loop (the "
                         "open-loop path has no hold point)")
    for spec in args.board:
        label, port = spec.split("=", 1)
        run_board(label, port, args, playback, out_dir)
    print(f"\nrows: {os.path.join(out_dir, 'rows.jsonl')}")


if __name__ == "__main__":
    main()
