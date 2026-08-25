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
import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
sys.path.insert(0, os.path.join(_ROOT, "ml", "yolox_urchin"))
from decode_np import cells_to_dets, merge_tiles    # noqa: E402
from n6_stream_host import SerialBoard              # noqa: E402

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


# ------------------------------------------------------------ board stream
class BoardStream:
    """Parse the hil_board.py wire: #I/#PH/#F headers + b64 payload lines."""

    def __init__(self, port, script_text):
        self.sb = SerialBoard(port)
        self.sb.start(script_text)
        self.info = None

    def next_event(self, timeout_s=30):
        """-> ('info'|'phase'|'frame'|'done'|'end', payload)."""
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
                        print(f"    WARN seq {obj['seq']}: jpg payload "
                              f"{len(jpg_line)} != {obj['jpg']}, "
                              f"frame skipped")
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
                        print(f"    WARN seq {obj['seq']}: cells payload "
                              f"corrupt/short (want {ncell}), frame skipped")
                        return "skip", obj
                    tile_cells.append(cells)
                obj["_jpg"] = jpg
                obj["_cells"] = tile_cells
                obj["_arrival"] = time.monotonic()
                return "frame", obj
        raise IOError(f"board silent for {timeout_s}s")

    def stop(self):
        self.sb.stop()


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


# ------------------------------------------------- closed loop (bite E4)
class LiveBoard:
    """hil_protocol Board interface over a real BoardStream. The send
    side is the raw pyserial handle — one control byte, no terminator
    (the CDC CRLF trap is why _ctl() board-side ignores CR/LF)."""

    def __init__(self, label, stream):
        self.label = label
        self.stream = stream

    def read_event(self, timeout_s):
        return self.stream.next_event(timeout_s=timeout_s)

    def send(self, byte):
        self.stream.sb.ser.write(byte)


def run_closed_loop(board_specs, args, playback, out_dir):
    """All boards in ONE run, every frame host-paced (bite E4). The
    legacy free-run path in run_board stays available until this path's
    bench acceptance passes; the scoring post-pass here is the same
    math (shared helpers), applied per board."""
    from hil_protocol import ClosedLoopScheduler

    reviewed = load_reviewed(args.stills_dir, not args.all_stills)
    if not reviewed:
        raise SystemExit("FAIL: no reviewed stills to score")
    k = args.frames_per_still
    phases = []
    for spec in args.phases.split(","):
        model, mode = spec.strip().split("-")
        phases.append({"kind": "model", "model": model, "mode": mode,
                       "jpeg": False})
    phases += [{"kind": "jpeg", "page": "loop"},
               {"kind": "jpeg", "page": "black"},
               {"kind": "jpeg", "page": "calib"}]
    board_phases = [{k2: v for k2, v in p.items() if k2 != "page"}
                    for p in phases]
    script = ("_CFG = " + repr({"framesize": args.framesize,
                                "jpeg_quality": 50, "closed_loop": True,
                                "phases": board_phases}) + "\n"
              + open(os.path.join(_HERE, "hil_board.py")).read())

    print(f"\n=== CLOSED-LOOP: {', '.join(l for l, _ in board_specs)}"
          f"\n    phases: "
          + ", ".join(p.get("page") or f"{p['model']}-{p['mode']}"
                      for p in phases))
    power_proc = None
    plog = os.path.join(_ROOT, "pi", "workbench", "power_log.py")
    if os.path.exists(plog):
        power_proc = subprocess.Popen(
            [sys.executable, plog, "--ch", "1=AE3", "--ch", "3=N6",
             "--hz", "10", "--out", out_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"    power logger up (pid {power_proc.pid})")

    boards = []
    caps = {}                           # label -> (cam_w, cam_h)
    for label, port in board_specs:
        bs = BoardStream(port, script)
        ev, obj = bs.next_event(timeout_s=60)
        if ev == "end":                 # sterile first attach — one retry
            print(f"    {label}: first attach died sterile "
                  f"({obj.get('reason')}) — one retry in 5 s")
            bs.stop()
            time.sleep(5)
            bs = BoardStream(port, script)
            ev, obj = bs.next_event(timeout_s=60)
        if ev != "info":
            raise SystemExit(f"FAIL: {label} produced {ev} instead of "
                             f"#I at startup: {obj}")
        print(f"    {label}: {obj['board_models']} "
              f"({obj.get('w')}x{obj.get('h')}, "
              f"{len(obj.get('tiles', []))} tiles)")
        caps[label] = (int(obj.get("w", 640)), int(obj.get("h", 400)))
        boards.append(LiveBoard(label, bs))

    rows_fh = open(os.path.join(out_dir, "rows.jsonl"), "a")
    os.makedirs(os.path.join(out_dir, "overlays"), exist_ok=True)
    pending = {b.label: [] for b in boards}
    jpegs = {b.label: {"loop": [], "black": [], "calib": []}
             for b in boards}
    stats = {}                          # (label, phase_name) accumulators

    def advance(still=None, page=None):
        if still is not None:
            playback.set(mode="step", still=still)
        else:
            playback.set(mode=page)

    sched = ClosedLoopScheduler(
        boards, advance, render_wait_s=args.render_wait,
        frame_timeout_s=args.frame_timeout)

    def on_model_frame(name):
        def cb(board, pos, fr, frame_idx):
            pb_i, still, boxes = reviewed[pos]
            dets = frame_detections(fr)
            fr["t_host"] = time.time()
            pending[board.label].append(
                {"phase": name, "still": still, "boxes": boxes,
                 "dets": dets, "obj": fr, "frame_in_still": frame_idx})
            st = stats.setdefault((board.label, name),
                                  {"frames": 0, "inf": 0.0, "e2e": 0.0})
            st["frames"] += 1
            st["inf"] += sum(fr["inf_us"])
            st["e2e"] += (fr["cap_us"] + sum(fr["prep_us"])
                          + sum(fr["inf_us"]) + sum(fr["dec_us"]))
            if any(fr["dropped"]):
                print(f"    NOTE {board.label} seq {fr['seq']}: cell cap "
                      f"dropped {fr['dropped']} (dense frame)")
        return cb

    try:
        for ph_i, ph in enumerate(phases):
            joined = sched.phase_barrier(ph_i)
            name = ph.get("page") or f"{ph['model']}-{ph['mode']}"
            print(f"    phase {name}: {sorted(joined)}")
            if ph["kind"] == "model":
                sched.run_model_phase(
                    ph_i, joined, list(range(len(reviewed))), k,
                    on_model_frame(name),
                    on_still=lambda pos: None)
            else:
                sched.run_jpeg_phase(
                    ph_i, joined, ph["page"], args.jpeg_frames,
                    lambda b, fr, _pg=ph["page"]:
                        jpegs[b.label][_pg].append(fr["_jpg"]))
    finally:
        for b in boards:
            b.stream.stop()
        playback.set(mode="loop")
        if power_proc is not None:
            power_proc.terminate()
            try:
                power_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                power_proc.kill()

    # ---- per-board calibration + scoring (same math as the legacy path)
    for b in boards:
        label = b.label
        jf = jpegs[label]
        if not (jf["black"] and jf["calib"]):
            print(f"    WARNING {label}: no calibration frames — "
                  f"timers only, accuracy NOT scored")
            continue
        open(os.path.join(out_dir, f"calib_{label}.jpg"), "wb").write(
            jf["calib"][-1])
        open(os.path.join(out_dir, f"black_{label}.jpg"), "wb").write(
            jf["black"][-1])
        if jf["loop"]:
            open(os.path.join(out_dir, f"loopview_{label}.jpg"),
                 "wb").write(jf["loop"][-1])
        cents = find_markers(jpeg_gray(jf["calib"][-1]),
                             jpeg_gray(jf["black"][-1]))
        H = solve_homography(playback.markers, cents)
        np.save(os.path.join(out_dir, f"H_{label}.npy"), H)
        img = Image.open(io.BytesIO(jf["calib"][-1])).convert("RGB")
        d = ImageDraw.Draw(img)
        for cx, cy in cents:
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6],
                      outline=(255, 0, 0), width=3)
        img.save(os.path.join(out_dir, f"calib_{label}_markers.jpg"))
        print(f"    {label}: homography solved; markers at "
              + ", ".join(f"({cx:.0f},{cy:.0f})" for cx, cy in cents))
        cam_w, cam_h = caps[label]
        Hinv = np.linalg.inv(H)
        overlaid = set()
        agg = {}
        for p in pending[label]:
            boxes, dets, obj = p["boxes"], p["dets"], p["obj"]
            m = 2.0
            vis = [(bx, g) for bx, g in
                   ((bx, map_still_box(H, bx[1], bx[2], bx[3], bx[4]))
                    for bx in boxes)
                   if g[0] >= -m and g[1] >= -m
                   and g[2] <= cam_w + m and g[3] <= cam_h + m]
            boxes = [bx for bx, _g in vis]
            gt_cam = [g for _b, g in vis]
            if len(dets):
                keep = ((dets[:, 0] > m) & (dets[:, 1] > m)
                        & (dets[:, 2] < cam_w - m)
                        & (dets[:, 3] < cam_h - m))
                dets = dets[keep]
            used = set()
            pairs = {}
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
            match = len(pairs)
            gt_px = [round(min(g[2] - g[0], g[3] - g[1]), 1)
                     for g in gt_cam]
            row = {"board": label, "phase": p["phase"],
                   "still": p["still"],
                   "frame_in_still": p["frame_in_still"],
                   "seq": obj["seq"], "t_host": obj.get("t_host"),
                   "cap_us": obj["cap_us"], "prep_us": obj["prep_us"],
                   "inf_us": obj["inf_us"], "dec_us": obj["dec_us"],
                   "dropped": obj["dropped"], "n_gt": len(boxes),
                   "n_det": int(len(dets)), "n_match": match,
                   "n_miss": len(boxes) - match,
                   "n_false": int(len(dets)) - match,
                   "gt_px_cam": gt_px,
                   "det_conf": [round(float(dd[4]), 3) for dd in dets],
                   "dets_cam": [[round(float(v), 1) for v in dd[:5]]
                                for dd in dets]}
            if args.min_gt_px > 0:
                fl = args.min_gt_px
                kept = {j for j in range(len(gt_cam)) if gt_px[j] >= fl}
                m_f = sum(1 for j in pairs.values() if j in kept)
                row.update({"floor_px": fl, "n_gt_floor": len(kept),
                            "n_match_floor": m_f,
                            "n_false_floor": int(len(dets)) - len(pairs)})
            rows_fh.write(json.dumps(row) + "\n")
            a = agg.setdefault(p["phase"],
                               {"gt": 0, "det": 0, "match": 0,
                                "gt_f": 0, "match_f": 0, "false_f": 0})
            a["gt"] += len(boxes)
            a["det"] += int(len(dets))
            a["match"] += match
            if args.min_gt_px > 0:
                a["gt_f"] += row["n_gt_floor"]
                a["match_f"] += row["n_match_floor"]
                a["false_f"] += row["n_false_floor"]
            key = (p["phase"], p["still"])
            if key not in overlaid:
                overlaid.add(key)
                save_still_overlay(
                    os.path.join(out_dir, "overlays",
                                 f"{label}_{p['phase']}_{p['still']}"),
                    os.path.join(args.stills_dir, "frames", p["still"]),
                    dets, boxes, Hinv, cam_w=cam_w, cam_h=cam_h)
        print(f"\n    {label:<5} {'phase':<12} {'frames':>6} {'GT':>5} "
              f"{'det':>5} {'match':>5} {'recall':>7} {'prec':>6}")
        for ph_name, a in agg.items():
            st = stats.get((label, ph_name), {"frames": 0, "inf": 0,
                                              "e2e": 0})
            n = max(st["frames"], 1)
            rec = a["match"] / a["gt"] if a["gt"] else 0.0
            prec = a["match"] / a["det"] if a["det"] else 0.0
            line = (f"    {label:<5} {ph_name:<12} {st['frames']:>6} "
                    f"{a['gt']:>5} {a['det']:>5} {a['match']:>5} "
                    f"{rec:>7.2f} {prec:>6.2f}  "
                    f"inf {st['inf']/n/1000:.1f}ms e2e "
                    f"{st['e2e']/n/1000:.1f}ms")
            if args.min_gt_px > 0 and a["gt_f"]:
                pf = a["match_f"] / (a["match_f"] + a["false_f"]) \
                    if a["match_f"] + a["false_f"] else 0.0
                line += (f" | >={args.min_gt_px:g}px: recall "
                         f"{a['match_f']/a['gt_f']:.2f} prec {pf:.2f}")
            print(line)
    rows_fh.close()
    if sched.dead:
        print("\n    DROPPED BOARDS: "
              + ", ".join(f"{b.label} ({why})" for b, why in sched.dead))


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


def save_still_overlay(path, still_path, dets_cam, boxes, Hinv):
    """GT (green, native) + detections mapped camera→still via H⁻¹ (yellow),
    drawn on the SOURCE still — no camera JPEG needed."""
    img = Image.open(still_path).convert("RGB")
    d = ImageDraw.Draw(img)
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

    bs = BoardStream(port, script)
    # the FIRST attach after a board crash-reset tends to die instantly
    # (the soft reset re-enumerates the port under the fresh connection);
    # one retry, only when the stream produced NOTHING
    ev0, obj0 = bs.next_event(timeout_s=60)
    if ev0 == "end":
        print(f"    first attach died sterile ({obj0.get('reason')}) — "
              f"one retry in 5 s")
        bs.stop()
        time.sleep(5)
        bs = BoardStream(port, script)
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
                continue                    # corrupted frame, realigned
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
                    # save the raw views FIRST: a failed solve must leave
                    # the evidence (what did the camera actually see?)
                    open(os.path.join(out_dir, f"calib_{label}.jpg"),
                         "wb").write(jpeg_frames["calib"][-1])
                    open(os.path.join(out_dir, f"black_{label}.jpg"),
                         "wb").write(jpeg_frames["black"][-1])
                    if jpeg_frames["loop"]:
                        open(os.path.join(out_dir, f"loopview_{label}.jpg"),
                             "wb").write(jpeg_frames["loop"][-1])
                    black = jpeg_gray(jpeg_frames["black"][-1])
                    calib = jpeg_gray(jpeg_frames["calib"][-1])
                    cents = find_markers(calib, black)
                    H = solve_homography(playback.markers, cents)
                    img = Image.open(io.BytesIO(
                        jpeg_frames["calib"][-1])).convert("RGB")
                    d = ImageDraw.Draw(img)
                    for cx, cy in cents:
                        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6],
                                  outline=(255, 0, 0), width=3)
                    img.save(os.path.join(out_dir,
                                          f"calib_{label}_markers.jpg"))
                    np.save(os.path.join(out_dir, f"H_{label}.npy"), H)
                    print(f"    homography solved; markers at "
                          + ", ".join(f"({cx:.0f},{cy:.0f})"
                                      for cx, cy in cents))
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
        playback.set(mode="loop")
        if power_proc is not None:
            power_proc.terminate()
            try:
                power_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                power_proc.kill()

    # ---- post-pass: score every buffered frame against the homography ----
    if pending and H is None:
        print(f"    WARNING: {len(pending)} frames collected but NO "
              f"homography (calib phase never completed) — timers reported, "
              f"accuracy NOT scored")
    if H is not None:
        Hinv = np.linalg.inv(H)
        overlaid = set()
        for p in pending:
            boxes, dets, obj = p["boxes"], p["dets"], p["obj"]
            st = p["stats"]
            # VISIBILITY FILTER (markers-at-box-D setup): the camera sees
            # only part of the still, so GT outside the frame must not
            # score as misses — keep only GT boxes fully inside the
            # camera frame (2 px margin), and symmetrically drop
            # detections touching the frame edge.
            m = 2.0
            vis = [(b, g) for b, g in
                   ((b, map_still_box(H, b[1], b[2], b[3], b[4]))
                    for b in boxes)
                   if g[0] >= -m and g[1] >= -m
                   and g[2] <= cam_w + m and g[3] <= cam_h + m]
            boxes = [b for b, _g in vis]
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
            gt_px = [round(min(g[2] - g[0], g[3] - g[1]), 1)
                     for g in gt_cam]
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
                # pixel floor, COCO-style IGNORE semantics: sub-floor GT
                # never count as misses, and detections matched to them
                # leave the false count (deleting them from GT instead
                # would flip correct small detections into falses)
                fl = args.min_gt_px
                kept = {j for j in range(len(gt_cam))
                        if gt_px[j] >= fl}
                m_f = sum(1 for j in pairs.values() if j in kept)
                false_f = int(len(dets)) - len(pairs)
                row.update({"floor_px": fl, "n_gt_floor": len(kept),
                            "n_match_floor": m_f, "n_false_floor": false_f})
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
                    dets, boxes, Hinv)
    rows_fh.close()

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
        print(line)
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
                         "is seconds and surplus drains as dead time "
                         "(legacy open-loop path only)")
    ap.add_argument("--closed-loop", action="store_true",
                    help="bite E4: host paces every frame (g/p control "
                         "bytes), ALL boards run simultaneously, no "
                         "settle windows or frame budgets — sync errors "
                         "impossible by construction")
    ap.add_argument("--render-wait", type=float, default=0.8,
                    help="closed-loop: seconds after a page/still change "
                         "before the first 'g' (covers the LCD client's "
                         "~300 ms poll + render; capture always STARTS "
                         "after this, so it can never straddle a change)")
    ap.add_argument("--frame-timeout", type=float, default=30.0,
                    help="closed-loop: seconds to wait for a board's "
                         "frame after 'g' before dropping the board")
    ap.add_argument("--jpeg-frames", type=int, default=8,
                    help="closed-loop: jpeg frames per page (calib needs "
                         ">=2 calib + >=1 black)")
    ap.add_argument("--all-stills", action="store_true",
                    help="score every still, not just reviewed ones")
    ap.add_argument("--min-gt-px", type=float, default=0,
                    help="GT pixel floor (min-side, camera px): sub-floor "
                         "urchins are IGNORED (not misses, and matches to "
                         "them are not falses) — the T2 24-32 px band; "
                         "Nick's 2026-08-25 call is 30. Raw counts stay "
                         "in every row; floored counts ride alongside")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_dir = os.path.expanduser(args.out)
    os.makedirs(out_dir, exist_ok=True)
    playback = Playback(args.playback)
    n_rev = len(load_reviewed(args.stills_dir, not args.all_stills))
    print(f"scoring {n_rev} stills, {args.frames_per_still} frames each; "
          f"phases {args.phases}; out {out_dir}")

    if args.closed_loop:
        run_closed_loop([spec.split("=", 1) for spec in args.board],
                        args, playback, out_dir)
    else:
        for spec in args.board:
            label, port = spec.split("=", 1)
            run_board(label, port, args, playback, out_dir)
    print(f"\nrows: {os.path.join(out_dir, 'rows.jsonl')}")


if __name__ == "__main__":
    main()
