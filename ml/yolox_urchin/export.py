#!/usr/bin/env python3
"""Export a trained stage-1 checkpoint -> int8 TFLite -> both board compiles.

Same route the 2026-08-22 compile gate proved: RawExport (per-level maps,
sigmoids baked) -> ONNX (classic exporter) -> onnx2tf full-integer quant
(DUO-frame calibration) -> ml/compile_model.sh for ae3 (Vela) and n6
(stedgeai). Deployment input defaults to 256 (the gated shape).

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/export.py \
      ~/nereus_ml/runs/stage1_yolox/<run>/last.pt --name stage1_v1 [--size 256]
"""
import argparse
import glob
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import build_model, RawExport  # noqa: E402

EXPORTS = Path.home() / "nereus_ml" / "exports"
REPO_ML = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt")
    ap.add_argument("--name", required=True)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--arch", default="yolox-nano")
    ap.add_argument("--skip-compile", action="store_true")
    args = ap.parse_args()

    out = EXPORTS / args.name
    out.mkdir(parents=True, exist_ok=True)

    model = build_model(num_classes=1, arch=args.arch)
    ck = torch.load(args.ckpt, map_location="cpu")
    model.load_state_dict(ck.get("model", ck))
    raw = RawExport(model).eval()

    onnx_path = out / f"{args.name}_{args.size}.onnx"
    torch.onnx.export(raw, torch.randn(1, 3, args.size, args.size),
                      str(onnx_path), opset_version=13, dynamo=False,
                      input_names=["images"],
                      output_names=["out_s8", "out_s16", "out_s32"])
    print("onnx:", onnx_path)

    # calibration: 20 DUO train frames (raw 0-255 RGB -- training used
    # unnormalized pixel values, so calibrate the same way)
    import cv2
    imgs = sorted(glob.glob(str(Path.home() /
        "nereus_ml/datasets/duo/extracted/DUO/DUO/images/train/*.jpg")))[:20]
    arr = np.stack([
        cv2.resize(cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2RGB),
                   (args.size, args.size)).astype(np.float32)
        for p in imgs])
    calib = out / f"calib_{args.size}.npy"
    np.save(calib, arr)
    np.save(out / "calibration_image_sample_data_20x128x128x3_float32.npy",
            np.stack([cv2.resize(a, (128, 128)) for a in arr]) / 255.0)

    tfdir = out / "tf"
    cmd = [str(Path.home() / "nereus_ml/venvs/gate/bin/onnx2tf"),
           "-i", str(onnx_path), "-o", str(tfdir), "-oiqt", "--non_verbose",
           "-cind", "images", str(calib),
           "[[[[0.0,0.0,0.0]]]]", "[[[[1.0,1.0,1.0]]]]"]
    subprocess.run(cmd, cwd=out, check=True,
                   stdout=open(out / "onnx2tf.log", "w"),
                   stderr=subprocess.STDOUT)
    quants = list(tfdir.glob("*_full_integer_quant.tflite"))
    assert len(quants) == 1, quants
    tflite = out / f"{args.name}_{args.size}_int8.tflite"
    tflite.write_bytes(quants[0].read_bytes())
    print("int8 tflite:", tflite, tflite.stat().st_size, "bytes")

    if args.skip_compile:
        return
    for target in ("ae3", "n6"):
        log = out / f"compile_{target}.log"
        r = subprocess.run([str(REPO_ML / "compile_model.sh"), target,
                            str(tflite), str(out / target)],
                           stdout=open(log, "w"), stderr=subprocess.STDOUT)
        outs = list((out / target).glob("*.tflite"))
        print(f"{target}: rc={r.returncode} artifacts={[(p.name, p.stat().st_size) for p in outs]}")
        assert r.returncode == 0 and outs, f"{target} compile failed, see {log}"


if __name__ == "__main__":
    main()
