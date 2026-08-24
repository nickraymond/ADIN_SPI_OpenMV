#!/usr/bin/env python3
"""HIL still sampler + pre-labeler (S8 bite E, HIL ground truth).

Samples N frames per Monterey clip, pre-labels them with a stage-1
checkpoint (default: the YOLOX-S labeler), and writes a label-GUI-ready
set. The sampled still list is CANONICAL: the HIL playback page's STEP
mode shows exactly these frames in exactly this order, and the scorer
keys on the same names — once labeling starts, the set is frozen.

Outputs under --out (default ~/nereus_ml/datasets/hil_monterey/stills_v1/):
  frames/<clipstem>_f<idx>.jpg   stills at native clip resolution
  labels.jsonl                   GUI format: {"file","w","h","classes","boxes"}
                                 boxes [ci, x, y, w, h, pixels], ci=0 "urchin";
                                 no "reviewed" flag — the GUI adds it on save
  stills_manifest.json           provenance: clip shas, frame indices, ckpt
                                 sha, thresholds — what the scorer + playback
                                 STEP mode consume

Run on the Mac (no board contact):
  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/hil_stills.py
Then label:
  ~/nereus_ml/venvs/fomo/bin/python ml/fomo/label_gui.py ~/nereus_ml/datasets
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model, RawExport, decode_raw  # noqa: E402

HIL = Path.home() / "nereus_ml" / "datasets" / "hil_monterey"
DEFAULT_CKPT = (Path.home() / "nereus_ml" / "runs" / "stage1_yolox"
                / "stage1_s_labeler" / "last.pt")
IMGSZ = 640          # eval_rung_a / stage2_autobox protocol
MIN_SIDE = 8         # keep small boxes — the px-band analysis wants them


def sha256_file(path, cap_mb=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sample_indices(n_frames, n_want, trim):
    """Evenly spaced frame indices inside [trim, n_frames - trim)."""
    lo, hi = trim, max(trim + 1, n_frames - trim)
    n_want = min(n_want, hi - lo)
    return sorted({int(round(x)) for x in
                   np.linspace(lo, hi - 1, n_want)})


def read_frames(clip, wanted):
    """Sequential read (H.264 index-seek is not exact); -> {idx: bgr}."""
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise SystemExit(f"FAIL: cannot open {clip}")
    want = set(wanted)
    got, idx = {}, 0
    while want:
        ok, frame = cap.read()
        if not ok:
            break
        if idx in want:
            got[idx] = frame
            want.discard(idx)
        idx += 1
    cap.release()
    if want:
        print(f"  WARN {clip.name}: {len(want)} wanted indices past EOF "
              f"(clip has {idx} readable frames) — skipped: {sorted(want)}")
    return got


def preprocess(img_bgr):
    """Top-left 114-gray letterbox at IMGSZ — stage2_autobox's exact path."""
    h0, w0 = img_bgr.shape[:2]
    s = IMGSZ / max(h0, w0)
    rs = cv2.resize(img_bgr, (round(w0 * s), round(h0 * s)),
                    interpolation=cv2.INTER_AREA)
    canvas = np.full((IMGSZ, IMGSZ, 3), 114, np.uint8)
    canvas[:rs.shape[0], :rs.shape[1]] = rs
    return canvas[:, :, ::-1], s          # BGR -> RGB


@torch.no_grad()
def prelabel(model, device, frames, conf, nms_iou, batch=8):
    """frames: [(name, bgr)] -> {name: [[0,x,y,w,h,px], ...]}"""
    from torchvision.ops import nms as tv_nms
    out = {}
    for start in range(0, len(frames), batch):
        chunk = frames[start:start + batch]
        imgs, metas = [], []
        for name, img in chunk:
            rgb, s = preprocess(img)
            imgs.append(rgb)
            metas.append((name, img.shape[1], img.shape[0], s))
        x = torch.from_numpy(np.ascontiguousarray(
            np.stack(imgs))).permute(0, 3, 1, 2).float().to(device)
        pred = decode_raw([o.float() for o in model(x)]).cpu()
        for bi, (name, w0, h0, s) in enumerate(metas):
            p = pred[bi]
            score = p[:, 4] * p[:, 5]
            keep = score > conf
            p, score = p[keep], score[keep]
            boxes = []
            if len(p):
                xyxy = torch.stack(
                    [p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2,
                     p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
                ki = tv_nms(xyxy, score, nms_iou)
                for x1, y1, x2, y2 in (xyxy[ki] / s).tolist():
                    x1, y1 = max(0.0, x1), max(0.0, y1)
                    x2, y2 = min(float(w0), x2), min(float(h0), y2)
                    bw, bh = x2 - x1, y2 - y1
                    if bw < MIN_SIDE or bh < MIN_SIDE:
                        continue
                    boxes.append([0, round(x1), round(y1),
                                  round(bw), round(bh), round(bw * bh)])
            out[name] = boxes
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clips", nargs="*",
                    default=sorted(str(p) for p in
                                   HIL.glob("Video_monterey_0*.mov")))
    ap.add_argument("--ckpt", default=str(DEFAULT_CKPT))
    ap.add_argument("--n", type=int, default=40, help="stills per clip")
    ap.add_argument("--trim", type=int, default=15,
                    help="frames skipped at each end of a clip")
    ap.add_argument("--conf", type=float, default=0.25,
                    help="pre-label threshold (recall-biased: deleting a "
                         "false box in the GUI is cheaper than drawing one)")
    ap.add_argument("--nms", type=float, default=0.45)
    ap.add_argument("--out", default=str(HIL / "stills_v1"))
    args = ap.parse_args()

    if not args.clips:
        raise SystemExit(f"FAIL: no clips given and none found in {HIL}")
    out = Path(args.out)
    labels_path = out / "labels.jsonl"
    if labels_path.exists():
        raise SystemExit(
            f"FAIL: {labels_path} already exists — the still set is frozen "
            "once labeling starts. Use a new --out for a fresh set.")
    (out / "frames").mkdir(parents=True, exist_ok=True)

    # model: arch/stem read from the run's own config.json (the stale-arch
    # trap train_ctl already caught once — trust the run record, not a flag)
    ckpt = Path(args.ckpt)
    cfg_path = ckpt.parent / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    arch = cfg.get("arch", "yolox-nano")
    stem = cfg.get("stem", "conv")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"loading {ckpt.name} ({arch}/stem={stem}) on {device}")
    model = build_model(num_classes=1, arch=arch, stem=stem)
    ck = torch.load(str(ckpt), map_location="cpu")
    model.load_state_dict(ck.get("model", ck))
    model = RawExport(model).to(device).eval()

    records, manifest_clips = [], []
    t0 = time.time()
    for clip_s in args.clips:
        clip = Path(clip_s)
        cap = cv2.VideoCapture(str(clip))
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        wanted = sample_indices(n_frames, args.n, args.trim)
        print(f"{clip.name}: {n_frames} frames @ {fps:.2f} fps -> "
              f"sampling {len(wanted)}")
        frames = read_frames(clip, wanted)
        stem_name = clip.stem.lower().replace("video_", "")
        named = []
        for idx in sorted(frames):
            name = f"{stem_name}_f{idx:04d}.jpg"
            fp = out / "frames" / name
            if not cv2.imwrite(str(fp), frames[idx],
                               [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise SystemExit(f"FAIL: could not write {fp}")
            named.append((name, frames[idx]))
        boxes_by_name = prelabel(model, device, named, args.conf, args.nms)
        for name, img in named:
            records.append({"file": f"frames/{name}",
                            "w": img.shape[1], "h": img.shape[0],
                            "classes": ["urchin"],
                            "boxes": boxes_by_name[name]})
        manifest_clips.append({
            "clip": str(clip), "sha256": sha256_file(clip),
            "n_frames": n_frames, "fps": round(fps, 3),
            "sampled_indices": sorted(frames),
            "still_prefix": stem_name})

    with open(labels_path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")

    git_sha = subprocess.run(["git", "rev-parse", "HEAD"],
                             capture_output=True, text=True,
                             cwd=Path(__file__).parent).stdout.strip()
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "clips": manifest_clips,
                "ckpt": str(ckpt), "ckpt_sha256": sha256_file(ckpt),
                "arch": arch, "stem": stem, "imgsz": IMGSZ,
                "conf": args.conf, "nms": args.nms,
                "min_side_px": MIN_SIDE, "repo_git_sha": git_sha,
                "labels_note": "pre-labels from the checkpoint above; "
                               "GT = the GUI-reviewed version"}
    (out / "stills_manifest.json").write_text(json.dumps(manifest, indent=2))

    # verdict — trust the artifacts, not the exit code
    n_imgs = len(list((out / "frames").glob("*.jpg")))
    n_boxes = sum(len(r["boxes"]) for r in records)
    empty = sum(1 for r in records if not r["boxes"])
    print(f"\nPASS: {n_imgs} stills on disk, {len(records)} label records, "
          f"{n_boxes} pre-label boxes ({n_boxes / max(1, len(records)):.1f}"
          f"/frame, {empty} frames with none) in {time.time() - t0:.0f}s")
    print(f"set: {out}")
    print("label:  ~/nereus_ml/venvs/fomo/bin/python ml/fomo/label_gui.py "
          "~/nereus_ml/datasets")


if __name__ == "__main__":
    main()
