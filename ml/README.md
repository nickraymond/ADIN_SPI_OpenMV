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

## Compiling for the boards (B1) — SOLVED, and it needs no new tooling

**OpenMV already ships both compilers and the script that drives them.** Do
not build a conversion pipeline; use `ml/compile_model.sh`, which calls
OpenMV's own `tools/modelc.py` — the same script that builds the models in
the boards' ROM.

```bash
ml/compile_model.sh ae3 model.tflite     # ARM Vela      -> Ethos-U55
ml/compile_model.sh n6  model.tflite     # ST Edge AI    -> Neural-ART
```

| | AE3 | N6 |
|---|---|---|
| compiler | `vela 5.0.0` | `ST Edge AI Core 4.0.0` |
| lives in | `~/openmv-sdk-1.6.0-linux-x86_64/python/bin` | `.../stedgeai/Utilities/linux` |
| args (from the board's `romfs_config.json`) | `--system-config RTSS_HP_DTCM_MRAM --accelerator-config ethos-u55-256 --memory-mode Shared_Sram --optimise Performance` | `--target stm32n6`, profile `default` |
| output | Vela-optimised `.tflite` | Neural-ART binary **renamed** `.tflite` |
| reports NPU placement? | **yes** — accelerator config + estimated ms | not directly |

Both are linux-x86_64, so they run in the `firmware-builder` container that
`firmware/openmv_build/` already sets up. **The N6 additionally needs
`/sdk/gcc/bin` on PATH** — `stedgeai --relocatable` links with
`arm-none-eabi-gcc` and dies with a bare `not found` *after* appearing to
generate successfully.

**Verified by reproducing the vendor's own artifacts byte-for-byte** (the
strongest check available without a board):

| model | our N6 compile | shipped in the N6's ROM |
|---|---|---|
| `person_detect.tflite` | 274,272 B | 274,272 B |
| `fomo_face_detection.tflite` | 64,064 B | 64,064 B |

Vela also prints an inference-time estimate at compile time (0.47 ms for
fomo_face_detection at `Ethos_U55_256`), which answers "is it on the NPU?"
*before* anything is deployed.

## Deploy + run on-board: AE3 SOLVED, N6 BLOCKED (measured 2026-08-20)

Compiled `fomo_face_detection` for each board and ran it against the board's
OWN ROM copy of the same model, in one session on one frame — the A/B that
makes the number mean something.

| | AE3 | N6 |
|---|---|---|
| compile matches vendor's | different bytes (36,992 B) | **byte-identical** (64,064 B) |
| copy to `/flash` | works | works |
| `ml.Model()` loads it | **yes** | **NO — `RuntimeError: Failed to load network`** |
| our model's inference | **1.66 ms** | — |
| the ROM copy's inference | 1.81 ms | 2.77 ms |

**The AE3 path is proven end to end**: our own compiled model loads from
`/flash` and runs at 1.66 ms — slightly faster than the vendor's ROM copy of
the same model at 1.81 ms, with identical input/output shapes
(`(1,96,96,3)` → `(1,12,12,2)`). Vela reported `Ethos_U55_256` placement at
compile time, so this is the NPU, not a CPU fallback.

**The N6 rejects the same bytes it already runs.** The file we compiled is
byte-for-byte what sits in the board's ROM, and it loads from `/rom` (2.77 ms)
and fails from `/flash`. Cause is not alignment — `py_ml.c` aligns
file-loaded models to the cache line. The likely reason is in stedgeai's own
output: the relocatable binary places its params in **`xSPI2`** (external
flash, execute-in-place) and activations in `AXISRAM5`. A `/flash` load
copies into heap RAM, which cannot satisfy that. **Unproven — do not treat
as fact until tested.**

### N6 RESOLVED 2026-08-20: USB DFU, ROMFS0 partition only

No ST-LINK needed. The N6's bootloader exposes named DFU partitions at
`37C5:9206` — `BOOTLOADER`(0), `FIRMWARE`(1), `FILESYSTEM`(2), `ROMFS0`(3) —
so ROMFS0 can be written on its own. **Alt 0 is never written, which is what
makes this recoverable**: the DFU window survives a bad ROMFS write, so the
board can always be re-entered and rewritten.

```bash
ml/build_romfs_n6.sh our_model.tflite                 # on the Mac
scp ~/nereus_ml/romfs_n6/out/romfs0.img pi@nereus000:~/bm_bench/models/
ssh pi@nereus000 "mpremote connect <N6-by-id> exec 'import machine; machine.bootloader()'"
ssh pi@nereus000 "dfu-util -a 3 -D ~/bm_bench/models/romfs0.img"
ssh pi@nereus000 "dfu-util -a 2 -U /tmp/fs.img -R"    # any read + -R boots it
```

Verify by read-back, not by the "Download done" message. `-U` runs to the
partition end regardless, so cap the compare at the image length:

```
read back 25,165,828 B -> sha256 of the first 12,641,204 == source. MATCH.
```

**Measured after flashing** — our model in ROM, running, vendor models intact:

| model in `/rom` | bytes | inference |
|---|---|---|
| **`nereus_fomo.tflite` (ours)** | 64,064 | **2.75 ms** |
| `fomo_face_detection.tflite` (vendor's) | 64,064 | 2.76 ms |
| `yolov8n_192.tflite` (vendor's, untouched) | 3,233,408 | 19.48 ms |

18 entries in `/rom`, every vendor model and cascade present at its original
size. Ours runs within 0.01 ms of the vendor's own copy of the same model.

**So the end-to-end route is proven on BOTH boards.** AE3 deploys by copying
a file to `/flash`; the N6 needs a ROMFS image flashed over DFU. That asymmetry
is the deployment story, and it is a property of the compilers' output, not of
our tooling.

Superseded routes (kept so they are not retried):
- Get the model into ROMFS. `mpremote romfs query` reports the 24 MB
  `ROMFS0` partition but reads `ROMFS image size: 0` — it does not
  understand OpenMV's image, so **`mpremote romfs deploy` would likely
  overwrite the vendor's nine models and need a firmware reflash to
  recover. Not attempted.**
- Rebuild the firmware ROMFS with our model included, via `tools/mkromfs.py`
  and the docker build — the supported path, and the one OpenMV's own IDE
  automates.
- Check whether `ml.Model` accepts a pre-loaded buffer placed in the right
  memory.

## The remaining gap: getting OUR model into that shape

`modelc.py` takes an int8/uint8 **NHWC** `.tflite`. OpenMV's own source
models look like this — the spec by example:

| | our ultralytics export | OpenMV's `yolov8n_192.tflite` source |
|---|---|---|
| input | `(1, 3, 192, 192)` float32 NCHW | `(1, 192, 192, 3)` **uint8**, scale 1/255, zp 0 |
| output | `(1, 84, 756)` float32 | `(1, 5, 756)` float32 |

**Known-hard, per OpenMV's own maintainers:** stock Ultralytics INT8 export
emits unquantized layers and ST's compiler rejects the result (`Oauto did not
find valid compile options`). They point at ST's `YOLOv8-STEdgeAI` example
and Roboflow's `ultralytics-openmv` fork instead. Sources:
[N6/Ultralytics thread](https://forums.openmv.io/t/openmv-n6-yolo8n-model-trained-with-ultralytics-issues/11571),
[hard-fault thread](https://forums.openmv.io/t/custom-yolov8-model-hard-faults-on-n6-when-loaded-via-ml-model/11633),
[roboflow/ultralytics-openmv](https://github.com/roboflow/ultralytics-openmv).

**So YOLO is the wrong first target.** The sprint's goal is to prove
train → compile → deploy → test, and a small classifier or FOMO-style
detector clears that path with far less toolchain risk — `fomo_face_detection`
is 57 KB and compiles for both boards today.

## Older notes (B0)

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

## Blur-tolerance fine-tune (bite E2 fix path)

Root cause context: the AE3's soft HIL capture collapses tiny ~4× while
nano is blur-immune (`~/nereus_ml/runs/e2_anomaly_2026-08-25/FINDINGS.md`).
The training-side fix is a blur-augmented fine-tune of the EXISTING tiny
checkpoint — it keeps everything the model knows and only forces
blur-tolerant features; from-scratch would relearn all of it first.

```bash
# fine-tune tiny with blur aug (hours, not the ~1-day full retrain).
# --resume restores the epoch counter (tiny ended at e119), so the
# continuation is expressed as --epochs 160 = 40 MORE epochs, riding the
# cosine tail's low LR (a natural fine-tune rate). Corpus stays v1 (the
# original run's) so blur is the ONLY changed variable.
~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train.py \
    --arch yolox-tiny --epochs 160 --batch 32 --mosaic 0.75 --blur 0.5 \
    --resume ~/nereus_ml/runs/stage1_yolox/stage1_tiny_v1/last.pt \
    --run-name stage1_tiny_v1_blurft

# acceptance: the blur curve, tiny-vs-nano (gate venv has TF+torch)
~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/eval_rung_a.py \
    <run>/ema.pt --arch yolox-tiny --blur-curve 0,0.8,1.2,1.6,2.2
```

**Pass bar:** tiny's mAP50 stays above nano's at every sigma AND the
sharp (σ=0) score lands within ~0.02 of the pre-fine-tune 0.729.
Sharp-image regression bigger than that → fall back to the full v2
recipe with `--blur 0.5` from scratch. Blur is label-preserving, rides
its own rng stream (enabling it cannot move a seed's boxes — pinned by
`test_data_aug.py`), and deliberately excludes haze (the E2 haze sweep
hurt only nano). Final proof is the bench: rerun the AE3 tiled HIL legs
with the re-exported int8 artifact and check tiny re-orders above nano.

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
