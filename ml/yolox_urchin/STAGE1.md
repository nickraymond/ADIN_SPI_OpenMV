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

**MEASURED 2026-08-24 (S8 bench window, COMPLETE — INA3221 rig,
load-signature-verified channels, 300-run windows, artifacts
sha/partition-verified before timing):**

| measured | AE3 nano | AE3 tiny | N6 nano | N6 tiny |
|---|---|---|---|---|
| ms/inference | 26.35 /rom (24.13–25.22 /flash) | **58.40** (/rom only) | **10.55** | 31.17 |
| inferences/s | 38–41 | 17.1 | 94.8 | 32.1 |
| mJ/inf gross | **6.69** | 17.61 | 11.50 | 38.25 |
| mJ/inf net-over-idle | 1.90 | 7.01 | 3.22 | 13.15 |
| idle mW (sensor on) | 181 | 181 | 781–804 | 781–804 |
| load mW | 253 | 301 | 1085 | 1225 |

| static | nano (stage1_v2) | tiny (stage1_tiny_v1) |
|---|---|---|
| rung-A mAP50 (float, 640) | 0.654 | **0.729** (+0.075) |
| int8 @ native 256 | 0.202 | **0.248** |
| AE3 deploy route | /flash (1.0 MB) or /rom | **/rom ONLY** (4.97 MB > ~4.09 MB heap; /flash load = MemoryError) |
| vela est. (DTCM/MRAM cfg) | 28.1 ms | 41.7 ms |

Reading the table:
- **The N6 wins throughput ~2.5× (nano) / 1.9× (tiny); the AE3 wins
  energy 1.7× (nano) / 2.2× (tiny) gross.** The S24-era "AE3 4.3×
  better mJ" shrinks to 1.7× once the model confound is removed — but
  the **idle floor stays 4.3× apart (181 vs ~790 mW)**, and at urchin
  duty cycles (1 frame per minutes-to-hours) idle, not inference,
  dominates the battery.
- **Tiny-on-AE3 works via ROMFS** (2026-08-24: combined ROMFS0 image,
  vela RTSS_HP_SRAM_OSPI profile, DFU alt "ROMFS0", read-back
  sha-verified; /rom is memory-mapped so the heap limit vanishes).
  OSPI XIP costs latency: tiny 58.4 ms vs the 41.7 ms DTCM-config
  estimate; nano 26.35 /rom vs 24.13 /flash.
- Method: INA3221 CH1=AE3 / CH3=N6 @10 Hz, both channels identified by
  load signature (idle→window step). Gross = board_mW × window ÷ N (the
  duty-cycle number); net subtracts sensor-on idle. Raw logs:
  `~/nereus_ml/runs/bench_2026-08-24/`.

Measurement notes (2026-08-23 bench window, 30-run means, QVGA frame,
sha/partition read-back verified before timing):
- AE3 nano beat its vela estimate (24.13 vs 28.1 ms) — the 2.7×-optimism
  precedent did NOT repeat on this architecture; both boards' numbers are
  NPU-consistent (CPU fallback would be 10×+).
- N6 deploy = ONE combined ROMFS image carrying both candidates + all
  vendor models (75.7% of 24 MiB), so A/B needs no reflash.
- **tiny CANNOT RUN on the AE3 as deployed (measured 2026-08-23):**
  sha-verified on /flash, but `ml.Model()` raises
  `MemoryError('Out of memory')` — a /flash model is copied into heap
  (only /rom models are memory-mapped; DESIGN/S8 "model load ~2.2 ms"
  fact), and 4.97 MB exceeds the ~4.09 MB free heap on the S18 build.
  The vela SRAM-arena question was never even reached. **The documented
  door for tiny-on-AE3 is the ROMFS route** (24 MB /rom, memory-mapped;
  needs an AE3 ROMFS image build + DFU flash — a bench decision, not
  attempted this window). Also: tiny only *stored* after clearing the
  5.38 MB ref_scene fixture from the 8 MB /flash (0 B free as found;
  1.1 MB free with both models aboard).
- AE3 free-heap before load: 4.09 MB (gc.mem_free, S18 patched build).

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
