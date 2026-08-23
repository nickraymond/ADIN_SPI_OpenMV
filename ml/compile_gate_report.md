# S8 bite E — architecture compile gate (NPU placement report)

**Date:** 2026-08-22 · **Gate:** `docs/urchin_corpus_plan.md` §Decisions #2 —
an Apache-2.0 family (YOLOX/NanoDet class) must push through BOTH board
compilers before stage-1 training. **Verdict: PASS — no AGPL fallback
needed.** Recommendation at the bottom; Nick's pick decides.

Both candidates are **untrained** (random weights, ImageNet init for
NanoDet's backbone), single class `urchin`, **256×256×3** input (top of the
plan's 192–256 window; input size stays a free knob for training), exported
int8 full-integer TFLite (NHWC, int8 in/out) and compiled with the S8 B1
scaffold (`ml/compile_model.sh` → OpenMV `modelc.py`; Vela 5.0.0 for the
AE3 Ethos-U55-256, ST Edge AI Core v4.0.0-20500 for the N6 Neural-ART).
This gate proves **placement**, not accuracy — latency figures are
compiler estimates, and the plan's stage-4 rule stands: only measured
on-board latency counts as the NPU proof.

## Placement verdict

| | YOLOX-Nano (conv stem) | NanoDet-Plus-m |
|---|---|---|
| License | Apache-2.0 (Megvii `6ddff48`) | Apache-2.0 (RangiLyu `be9b4a9`) |
| **AE3 / Vela placement** | **100% NPU** — compiled graph is a single `ethos-u` op, zero CPU fallback | `ethos-u` + **CPU `TRANSPOSE` ops** (ShuffleNetV2 channel shuffle falls back) |
| AE3 Vela est. | 28.1 ms (35.6 inf/s) | 31.9 ms NPU portion (31.3 inf/s) **+ un-modeled CPU transpose cost** |
| AE3 SRAM used | 512 KB | 1,040 KB |
| **N6 / stedgeai epochs** | **116 pure-HW + 2 hybrid (DepthToSpace) + 0 pure-SW** of 118 | 158 pure-HW + **36 hybrid (Transpose) + 2 pure-SW** of 196 |
| N6 binary / params | 1,166,008 B / 911,760 B | 1,527,928 B / 1,156,800 B |
| int8 tflite (input) sha256 | `b7f4d37d9952ae64…` (998 KB vela out) | `14d9e629e590117d…` (1.6 MB vela out) |

**YOLOX-Nano is the cleaner candidate on BOTH boards** — fully NPU-mapped
on the AE3 and the same near-pure-HW class as the vendor's own shipped
models on the N6 (the D2 phase-0 FOMO smoke was 22/24; this is 118/118
with only 2 hybrids). NanoDet's ShuffleNetV2 channel-shuffle transposes
are the one recurring fallback source on both targets; workable, but it
starts the race with a handicap the compiler tables already show.

## Deviations from stock YOLOX-Nano (recorded, not hidden)

1. **Focus stem → plain stride-2 conv** (same out-channels). Vela only
   accepts stride-1 `STRIDED_SLICE`, so the Focus slice trick can never
   place on the Ethos-U55, and onnx2tf mis-rewrites it anyway. This is
   the standard edge adaptation (YOLOX-ti-lite ships exactly this swap);
   we train from scratch so nothing is lost.
2. **Raw per-level head outputs** (three maps: `(1,H/8,W/8,6)`,
   `(1,H/16,W/16,6)`, `(1,H/32,W/32,6)`; 6 = 4 reg + obj + cls, sigmoids
   baked in) instead of the flatten+concat tail — decode runs on-board,
   the B2 FOMO precedent. SiLU activations kept stock; both compilers
   map them fine.

## Repro

- Export/convert script: session scratch `export_candidates.py`, archived
  with full metadata in `~/nereus_ml/runs/compile_gate_2026-08-22.json`
  (repo shas, venv pins, commands, artifact sha256s).
- Toolchain: `~/nereus_ml/venvs/gate` (torch 2.13.0 CPU, TF 2.19.0,
  onnx2tf 1.28.8); quant calibration = 20 DUO train frames at 256 px.
- Artifacts + full compiler logs: `~/nereus_ml/exports/compile_gate/`
  (onnx, int8 tflite, per-board outputs, vela summary CSVs, stedgeai
  `network_generate_report.txt`).
- Two onnx2tf potholes worth knowing: its calibration-npy download is
  broken (pre-seed `calibration_image_sample_data_20x128x128x3_float32.npy`
  in cwd), and NanoDet needs an `onnxsim` pass first or shapes collapse
  to zero-dim.

## Recommendation

**YOLOX-Nano (conv-stem variant) at 256 px.** Clean sweep on both
compilers, smaller SRAM footprint, and the anchor-free decoupled head is
a good fit for the 24–64 px small-target band the corpus is built
around. If capacity proves short on rung A, YOLOX-Tiny is the same
family one notch up (width 0.375 vs 0.25) and rides the identical
export/compile path.
