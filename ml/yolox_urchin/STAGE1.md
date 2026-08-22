# stage1_v1 — single-class urchin backbone (S8 bite E, stage 1)

**Trained 2026-08-22** on corpus_v1 (19,904 imgs / 96,326 boxes: Urchinbot
official train + DUO train + RF100 train; single class `urchin`; Urchinbot
983-img test split NEVER trained — fenced by assertion in
`ml/urchin_data/build_corpus_v1.py`). Architecture: YOLOX-Nano conv-stem
(Apache-2.0, the 2026-08-22 compile-gate pick; `ml/compile_gate_report.md`).

- Run record: `~/nereus_ml/runs/stage1_yolox/stage1_v1/` (config + git sha
  + corpus-manifest sha256 + loss log + checkpoints). 40 epochs, batch 32,
  256-px canvas, box-aware downscale augmentation into the 24–64 px band,
  SGD + cosine, ~3 h on the M1 Max (MPS).
- Weights: `last.pt` (raw final = the stage-1 model). `ema.pt` scored
  lower (0.478) — fixed-decay EMA carries init pollution; ramped decay is
  a queued trainer fix, not a blocker.

## Eval rung A (983-img Urchinbot official test split, COCOeval, 640-px input)

| Model | mAP50 | mAP50-95 |
|---|---|---|
| yolo11n (NOAA, ultralytics protocol) | 0.243 | 0.090 |
| yolo11x (NOAA, ultralytics protocol) | 0.351 | 0.143 |
| stage1_v1 @ epoch 0 | 0.225 | 0.080 |
| stage1_v1 @ epoch 10 | 0.455 | 0.165 |
| stage1_v1 @ epoch 20 | 0.514 | 0.203 |
| **stage1_v1 final (last.pt)** | **0.573** | **0.239** |
| Urchinbot published ceiling (their full-size model) | 0.908 | — |

**+0.222 over the 0.351 bar** — the stage-1 "decisively above" criterion.
Scorer difference noted: ours is pycocotools COCOeval, baselines were
ultralytics `val()`; both at 640 px on identical GT. The remaining gap to
0.908 is a full-size-model ceiling, not this nano's target.

## Deployment artifacts (`~/nereus_ml/exports/stage1_v1/`)

int8 full-integer TFLite (256×256, DUO-frame calibration) through both
compilers via `ml/yolox_urchin/export.py`; placement identical to the
untrained gate: **AE3/Vela = single `ethos-u` op, zero CPU fallback, est
28.1 ms; N6/stedgeai = 117 pure-HW + 2 hybrid + 0 pure-SW of 119 epochs.**

## Owed / not done (recorded, not hidden)

- **int8-vs-float accuracy delta unmeasured** (float 640-px protocol vs
  the 256-px deployment shape are different regimes; the honest number is
  bite C's tiled/on-board eval, pixels-on-target on the axis).
- On-board latency: compiler estimates only; NPU proof = measured
  per-inference time via the S25 workbench (S8 bench sessions, Nick).
- Stage 2 (GBIF auto-box + species head) and rung B not started.
