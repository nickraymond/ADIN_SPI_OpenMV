# `ml/` — training and export host (the Mac)

Set up 2026-08-20 for S8 bite B0. **This directory holds code and released
models only.** Everything heavy lives outside the repo, under `~/nereus_ml/`,
because this project is worked in git worktrees and anything inside the tree
gets duplicated per worktree and can be lost to branch operations.

```
~/nereus_ml/
  venvs/train/     Python 3.11 venv: ultralytics + torch (MPS) + LiteRT export
  datasets/        image sets + labels          (NOT in git -- manifest only)
  runs/            training runs, checkpoints   (NOT in git)
  weights/         downloaded base weights      (NOT in git)
```

Ultralytics is configured to write there rather than into the repo:

```bash
~/nereus_ml/venvs/train/bin/yolo settings \
  datasets_dir=$HOME/nereus_ml/datasets \
  runs_dir=$HOME/nereus_ml/runs \
  weights_dir=$HOME/nereus_ml/weights
```

## Rebuild the environment

```bash
brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv ~/nereus_ml/venvs/train
~/nereus_ml/venvs/train/bin/python -m pip install -r ml/requirements-train.txt
```

Versions are pinned hard (`requirements-train.txt`, 83 packages) because the
export half of this stack is version-fragile — see the trap below.

## Verified on this Mac (M1 Max, macOS 14.6.1, 2026-08-20)

- **Training on MPS works.** yolo11n, 3 epochs on coco8 at 192 px, ~29 s
  including validation. torch 2.12.1, ultralytics 8.4.124.
- **Export runs and quantizes.** `format='litert', quantize=8` produced a
  2.92 MB `.tflite` from a 10.18 MB model (3.5x), and it loads and infers.
- **Output shape `(1, 84, 756)`** = 4 box coords + 80 COCO classes at 756
  anchors for a 192 px input. This independently confirms the reading of the
  ROM model's `(1, 5, 756)` as 4 + ONE class (SPEC, DESIGN §S24).

## The blocker this bite found (B1's subject — do not re-derive)

**The exported model is not deployable to either board as it stands.**

| | our export | the boards' ROM models |
|---|---|---|
| input shape | `(1, 3, 192, 192)` NCHW | `(1, 192, 192, 3)` NHWC |
| input dtype | `float32` | int8 expected for NPU |

`litert_torch` converts straight from torch, which is NCHW-native, and it
never transposes. `onnx2tf` is the tool that produces NHWC — and in
ultralytics **8.4.124 there is no path to it**: the `tflite` format is gone
from `export_formats()` (replaced by `litert`), and `export_saved_model` no
longer calls onnx2tf.

Two candidate routes, and choosing between them is **S8 bite B1**, which is
gated on Nick:

1. Pin an older ultralytics whose `tflite` export goes through onnx2tf and
   emits `*_full_integer_quant.tflite` (NHWC, int8 IO) — the well-trodden
   route for Ethos-U/Vela.
2. Drive the conversion by hand: torch → ONNX → `onnx2tf` → int8 TFLite,
   with onnx2tf pinned independently of ultralytics.

Neither is proven on hardware yet. **"It exported" is not "it runs on the
NPU"** — that is the acceptance trap S8 names, and it is still open.

## Reproducibility trap, measured

Ultralytics **auto-installs export dependencies mid-run**, and that install
downgraded torch 2.13.0 → 2.12.1 *underneath the running interpreter*. The
process kept the old torch in memory while the new `litert_torch` expected
the new one, and the export died with a misleading
`ImportError: cannot import name 'get_cuda_generator_meta_val'`. Re-running
in a fresh process worked with no other change. If an export fails
immediately after a first-ever run, restart the process before debugging
anything else.

## What is committed here

- `requirements-train.txt` — pinned, the exact set that produced the above
- `models/` — RELEASED models only, each with a provenance sidecar naming the
  code commit, dataset manifest hash, metrics, and toolchain versions. Not
  every experiment; those stay in `~/nereus_ml/runs/`.

Datasets are never committed. A manifest (one line per image: sha256, which
board captured it, capture settings, label boxes) is what goes in git, so a
model's training set is identifiable even though the bytes live outside.
