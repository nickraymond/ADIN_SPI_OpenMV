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
| stage1_v1 final (last.pt) | 0.573 | 0.239 |
| stage1_v2 final (ema.pt) | 0.654 | 0.295 |
| **stage1_tiny_v1 final (last.pt) — CURRENT BEST** | **0.729** | **0.347** |
| Urchinbot published ceiling (their full-size model) | 0.908 | — |

**stage1_v2** (2026-08-22, same corpus): + mosaic 0.75 with 10-epoch
no-aug tail, ramped-decay EMA (the fix — EMA beat raw 0.654 vs 0.650),
120 epochs. Curve 0.441/0.551/0.601/0.617/0.654 at e20/40/60/80/119.
Mosaic + schedule + EMA over v1: **+0.081**. int8 @ native 256: 0.202
(v1: 0.128). Deployment artifacts `~/nereus_ml/exports/stage1_v2/`,
placement identical (AE3 single `ethos-u`; N6 117-HW/2-hybrid/0-SW).
YOLOX-Tiny on the identical recipe queued as the capacity probe.

**stage1_tiny_v1** (2026-08-23, identical v2 recipe, arch only): FINAL
**0.729** (last.pt; ema 0.727 — tie). Curve 0.578/0.630/0.664/0.687/
0.729 at e20/40/60/80/119 — above v2-nano's curve by ~+0.08 at every
matched epoch: capacity was the binding constraint. Compiles clean:
AE3 4.97 MB single `ethos-u` (est 41.7 ms), N6 95-HW/2-hybrid/0-SW.
Training pace 2.03 it/s (~2% under nano — dataloader-bound).

## Nano-vs-Tiny decision (Nick's call; bench measurements pending)

| | nano (stage1_v2) | tiny (stage1_tiny_v1) |
|---|---|---|
| rung-A mAP50 (float, 640) | 0.654 | **0.729** (+0.075) |
| int8 @ native 256 | 0.202 | see run log |
| AE3 est. latency (vela) | 28.1 ms (35.6/s) | 41.7 ms (24.0/s) |
| size on /flash (8 MB) | 1.0 MB | 4.97 MB |
| vela SRAM plan | 512 KB | 1,036 KB (runtime arena = open SPEC q) |
| measured ms + mJ | bench window owed | bench window owed |

Both models' artifacts are staged for the bench. The urchin duty cycle
is an energy problem (TRACKER: frames per minutes-to-hours), so tiny's
+13.6 ms is likely affordable — but the standing rule decides: measured
latency + the power rig's mJ, not estimates. Distillation tiny→nano
remains the documented third door.

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
