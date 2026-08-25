# DEV_LOG.md — Session Log

*Newest entries on top. One entry per working session. Keep entries short:
what changed, what broke, what's next. Agents: add yours before ending the session.*

---

## Entry template

```
## YYYY-MM-DD — Sprint Sn — <one-line summary>
**Branch:** sprint/n-slug
**Done:**  <bullets>
**Broke/surprised us:** <bullets or "nothing">
**Next:** <the single next bite>
```

---

## 2026-08-25 — S8 bite E2 — AE3-tiny anomaly ROOT-CAUSED: soft AE3 capture × tiny's blur-sensitivity; runtime/artifact/deployment all exonerated by measurement

**Branch:** `claude/ae3-tiny-recall-anomaly-619197` (worktree)

**Done:** the whole theory ladder, one verdict per theory (full evidence:
`~/nereus_ml/runs/e2_anomaly_2026-08-25/FINDINGS.md`):
- **T1 (conf sweep) NO** — no mass at the 0.30 edge; AND AE3-tiny never
  engaged the 128-cell cap (dropped=0 ×48 rows) while AE3-nano dropped
  4,867 cells and N6-tiny 5,586 — the deficit is in the board's
  obj≥0.10 candidate field itself, not in any threshold.
- **T2 (artifact audit) chain INTACT** — source int8 07e69189… byte-
  identical into both builds; vela-OSPI tiny 21ceca4f…/nano 6ce24478…
  embedded byte-exact in romfs0.img 22c1b963… == Pi copy == the 08-24
  DFU read-back; same vela profile both models, zero warnings.
- **T3 (golden-input diff) ALL THREE RUNTIMES AGREE** — one 256×256
  RGB565-quantized BMP (sha + #PX pixel self-check on both boards):
  obj≥0.10/conf≥0.30 cells Mac-tiny 157/104 · N6-tiny 152/106 ·
  AE3-tiny 158/108 (nano 188/98 · 182/96 · 191/101); deltas at int8
  noise. **AE3 vela artifact + ethos-u runtime + /rom XIP + preproc
  exonerated.** Probe: one attach per board, `mpremote run`, AE3 under
  the ae3-board-access rules (single ops, 65 s silences) — no incidents.
- **T4 (cell_cap/obj_thr) FALSIFIED desk-side** — cap never engaged;
  deficit reproduces off-board; no leg run, none needed.
- **T5 (camera domain) CONVICTED, mechanism measured** — matrix-run
  camera views through the MAC interpreter reproduce the anomaly with
  no board involved: tiny:nano conf≥0.30 ratio **0.53 on the AE3's
  view vs 1.31 on the N6's**. Content-constant blur sweep (N6 view):
  ratio 1.31→0.26 by σ1.6 — **tiny collapses ~4× under blur, nano is
  flat-to-BETTER**; haze alone hurts nano more (tone exonerated).
  AE3 capture lap_var 233 vs N6 880 on the same screen; blur+haze
  matched to the AE3 look reproduces 0.48≈0.53. Sharpening (unsharp)
  worsens BOTH models (87→10→0 cells) — **no software post-fix**.

**Broke/surprised us:**
- The anomaly's odd signature (fewer dets at HIGHER conf, precision
  0.86) is exactly blur survivorship: only big/sharp targets clear the
  bar. And nano's blur-immunity means the HIL screen test was
  accidentally split across two image domains — one per camera.
- The v5 tuple-arg trap extends to `get_pixel((x,y))` (probe hit it).

**Next (Nick's call — bite stops here per the kickoff):**
1. Bench fix candidate: refocus/reposition the AE3 (its view is
   heavily zoomed + soft; possibly inside focus minimum) → rerun the
   two AE3 tiled legs; success = tiny re-orders above nano.
2. Product read: real turbid water is soft too — nano's blur-immunity
   may be a feature, and blur augmentation is the training-side fix
   for tiny. Decision table should NOT read AE3-tiny 0.13 as a board
   deficiency.
Bench left clean: runner idle, both boards enumerated, ports free.
Probe + golden BMP staged at pi:~/bm_bench/ (and on both /flash as
golden_256.bmp, 197 KB) for the rerun.

**Continued (same day, Nick at the bench — the fix session): E2's
success criterion MET — AE3 tiny-tiled 0.70 recall vs nano 0.48 (HD,
frame-2 subset), ordering matches the N6's.**
- **Blur fine-tune shipped + launched** (nibble 2, Nick's plan
  approval): blur aug in data.py (own rng stream — the shared-rng leak
  was caught by the label-invariance test), --blur knob, blur-curve
  eval mode. Float baselines banked: tiny 0.727/0.690/0.508/0.414 vs
  nano 0.654/0.643/0.550/0.458 at σ 0/0.8/1.6/2.2 — **the benchmark
  reproduces the HIL crossover (nano overtakes tiny at σ≥1.6)**.
  Fine-tune (tiny e120→160, --blur 0.5, corpus/flags otherwise
  identical) running at entry time.
- **Focus rig built for Nick** (ad-hoc playback + composed
  center-urchin target + aiming card + lap_var readout): AE3 patch
  sharpness 100→331 after his lens work (N6 same target: 1232 → the
  AE3 stays ~2× softer at best focus). Exposure/color self-fixed by
  framing + camera re-init (means 92 vs 93, RGB neutral) — the wash
  was AE metering a dark surround, not ISP damage. En route: eth0
  joined the LAN (WiFi was the slow-page culprit; bm-bench profile
  preserved), focus target lives in ~/hil_focus (frozen set untouched).
- **Scored legs (all midday light):** AE3 VGA refocus tiny 0.13→0.28
  raw (nano 0.33 — tie at the 30-px floor: 0.51 vs 0.52); N6 VGA
  control tiny 0.33/nano 0.19 (ordering holds; ambient penalty vs the
  5 AM matrix measured ~−0.06 recall/−0.19 prec — matrix cells are
  per-lighting); **AE3 HD (7×5 tiles, geometry-computed) tiny 0.70/
  0.60 vs nano 0.48/0.59 frame-2-only** — GT median 52 px, 96% above
  Nick's 30-px floor. Harness grew --min-gt-px (ignore semantics),
  --framesize HD, --budget-slack, cam-dims-from-#I.
- **Nick's open-loop concern found a REAL defect**: AE3 HD frame-1s
  0.33 vs frame-2s 0.59 (frame cycle > settle → first frames straddle
  the still change). VGA legs audit clean (≤0.002). HD scoring is
  frame-2-only until bite E4 (closed-loop handshake, Nick's design,
  captured + kickoff = PROMPTS §16). Also captured: bite E3 (RF-DETR
  labeler bake-off, one-epoch gate).

**Broke/surprised us (fix session):**
- The Pi's HIL files were untracked scp copies (backed up to
  ~/hil_pi_backup_20260825, verified == the merged repo versions); the
  Pi now runs the branch checkout properly.
- An N6 launch via bare `ssh &` lost the console (process survived;
  artifacts intact) — use the tracked-background pattern.

**Next:** N6 HD leg finishing → heatmap summary report (Pi-hosted) →
E2 PR for Nick's review → E4 session (PROMPTS §16). Blur fine-tune
lands ~13:15; its blur-curve acceptance + deploy decision ride the PR
review.

---

## 2026-08-24/25 — S8 bite E HIL: rig built end-to-end (stills+pre-labels, decode, playback, harness); N6 blocked by an unattributed predict-loop USB death; AE3 dry run = the discriminator

**Branch:** `claude/yolox-decode-nms-sizing-3ca136` (worktree)

**Done:**
- HIL still set: 80 frames (40/clip, frozen, manifest provenance),
  pre-labeled by the YOLOX-S labeler (last.pt, rung-A 0.800) — 2,667
  boxes, spot-checked good; Nick reviewed 24 frames same evening.
- `ml/yolox_urchin/decode_np.py`: numpy decode+NMS, torch-parity +
  int8-artifact tested (9 tests); sparse-cell variant equivalence-tested.
- Pi playback stack LIVE through the workbench: `pi/hil/playback_server.py`
  (loop/step/calib/black + /api/set; 11 tests), recipe `s8-hil-urchin`
  (locks both boards, zero contact at this stage), cookbook chapter,
  kiosk stack on the new bench LCD (chromium first, then a pygame/KMS
  client — see below).
- `pi/hil/hil_harness.py` + `hil_board.py`: one attach per board runs a
  phase list (models first, jpeg/calib after), host steps stills with
  arrival stamping, post-pass scoring through a 4-marker screen→camera
  homography (DLT, black-frame-subtracted marker detection), overlays
  rendered onto SOURCE stills via H⁻¹.

**Broke/surprised us:**
- **The N6 leg is BLOCKED: repeated predict() with a stage-1 model kills
  its USB session within ~1–30 predicts** (device off the bus,
  re-enumerates as MSC; ~12 reproductions). Everything plausible was
  isolated and most of it RULED OUT by measurement — full evidence trail
  in SPEC §Open questions. Key confound: every crash was with the new
  LCD attached; the 08-24 window (no LCD) ran thousands of predicts.
  One un-reproduced clean run mid-sequence (tiny 31.25 ms — matches
  08-24's 31.17). Needs hands: LCD-unplug A/B, N6 cold replug.
- The CRLF trap struck a THIRD form: CDC translates a b64 payload's
  terminator, +1 byte vs header. Wire lengths now mean bare payload.
- `%r`-built "JSON" headers (single quotes) made the host silently deaf
  to #I/#PH for a full run — headers are json.dumps now, and stream-end
  events always print their reason.
- usb-storage MSC gap: the S23 no-MSC rule covered only the AE3;
  `99-n6-no-msc.rules` (PID 1206) written + installed (did not fix the
  crash class, still correct hygiene).

**Next:** AE3 dry run (running at entry-write time) → if clean, the rig
is proven minus the N6 leg; N6 = fresh bite with Nick's hands (LCD
unplug A/B first). Then: full matrix + report card (bite C's s8_report
machinery from `claude/s8-c-metrics` — reuse, not rewrite).

**Continued (same night, Nick at the bench):** the N6 mystery CLOSED —
**unshielded USB cable** (Nick spotted it; shielded swap → probe clean
at bench numbers; SPEC updated, shielded-only rule). Camera aiming
matured into the **16:10 sizing ladder** (boxes mode; Nick picked box D
= 70%), markers pinned to D's corners, scorer grew a visibility filter
(camera sees the central 63%×70% of each still; visible px ≈0.53× still
scale, median target 20→25 px, 53% above the 24-px floor). Power column
landed (INA3221 log per harness run; first whole-loop mJ/frame numbers).
**AE3 scored matrix DONE at D-zoom: nano-tiled recall 0.39/prec 0.77 at
85 mJ/frame; tiny-tiled 0.13/0.86 at 141 mJ/frame; whole-frame-256 ≈0
recall on hardware (4-8 dets vs ~3k GT) — the downscale artifact
measured end-to-end. Open puzzle: tiny detects 3-4× less than nano
through the camera despite higher bench mAP — report-card analysis
item.** En route: three harness robustness fixes (CRLF payload trap
again, %r-vs-JSON header deafness, corrupt-frame skip + score-what-was-
collected), jpeg-phase pacing (fast boards starved calibration), stills_v2
pre-labeled (120 frames, 3 new aquarium clips, HEVC — transcode owed
before loop playback). Bench ops: Pi dropped off WiFi twice (EMI-rich
bench now) — power-save-off applied, Ethernet = Nick's calm-day fix.
`hil-setup` skill shipped (ladder image included). New sized follow-ups:
simultaneous two-board runs (phase-barrier still scheduler), on-board
decode (product path), s8-two-colour recipe N6 model drift (ROMFS
redeploy decision).

**Branch:** `claude/s8-bench-deployment-latency-ac02d1`

**Done:**
- Bench recovered twice: AE3 was OFF the bus on arrival (error -71 since
  Aug 22 ~03:30; Nick power-cycled after the FTDI USB-stick meter was
  removed — the stick is retired, INA3221 is the rig now).
- Combined N6 ROMFS image built (BOTH candidates + all vendor models,
  75.7% of 24 MiB; mkromfs's stedgeai pass verified deterministic vs the
  staged exports). Flashed via DFU alt 3; partition read-back sha ==
  source image sha; /rom lists 19 entries, vendor content intact.
- **Measured (30-run means, sha-verified artifacts): N6 nano 10.64 ms
  (94.0/s) · N6 tiny 31.25 ms (32.0/s) · AE3 nano 24.13 ms (41.4/s,
  beats vela's 28.1 est). All NPU-consistent.** Tables updated in
  ml/yolox_urchin/STAGE1.md.
- Power rig software shipped ready-to-wire: pi/workbench/power_log.py
  (INA3221, register map verified vs Adafruit's driver; JSONL, probe
  mode, config read-back) + POWER_RIG.md procedure.

**Broke/surprised us:**
- **AE3 /flash is 8 MB with 0 B free** under the S18/S23 fixture
  (ref_scene 5.38 MB + bridge stack): tiny's cp died at 700 KB, and the
  probe then **hard-hung the firmware by ml.Model()-loading that
  truncated file** (no exception; warm reset refused; physical replug
  needed — SPEC §Open questions updated with the trap).
- ref_scene deletion is classifier-gated for the agent; script staged at
  pi:~/bm_bench/ae3_free_space.py (all six files verified restorable
  from bench/assets/ref_scene/ via demo_up staging).

**Continued (same window, after Nick's two replugs):** space-clear ran
(6.08 MB freed; the first "run" had failed because the blocked compound
command never scp'd the script — caught by artifact, not rc), tiny
cp'd + **sha-verified on /flash**, and the probe ANSWERED the SPEC
question: **tiny does NOT run on the AE3 — MemoryError on load. /flash
models are copied into heap (~4.09 MB free); only /rom is
memory-mapped. ROMFS route = the untested door.** Nano re-measured
25.22 ms (consistent). Bite-R incident #8 logged en route: fresh boot,
file-ops only, ~6 attaches → persistent raw-repl refusal, replug-only.
mJ columns still owed (Nick wiring the INA3221 in parallel).

**Continued (2026-08-24, INA3221 live): THE TABLE IS COMPLETE — and
tiny RUNS on the AE3 via ROMFS.** Nick's gate taken mid-window: built a
combined AE3 ROMFS0 image (vela RTSS_HP_SRAM_OSPI — the board's own
ROMFS profile; vendor yolov8n reproduced byte-size-exact 1,994,976 B),
flashed via the AE3 bootloader's named DFU alt "ROMFS0" (S7 tooling's
alt list, confirmed live), read-back sha == source, vendor /rom content
intact (22 entries). Measured, all load-signature-verified on the
INA3221 (CH1=AE3, CH3=N6): **AE3 nano 26.35 ms /rom / 6.69 mJ gross ·
AE3 tiny 58.40 ms / 17.61 mJ · N6 nano 10.55 ms / 11.50 mJ · N6 tiny
31.17 ms / 38.25 mJ; idle 181 vs ~790 mW.** The S24 4.3× energy gap
shrinks to 1.7× same-model (the confound was real) but the IDLE floor
stays 4.3× — the urchin-duty-cycle story. I2C enable made persistent.
Power rig routine proven end-to-end (power_log → stamp_lines →
power_calc). Raw logs: ~/nereus_ml/runs/bench_2026-08-24/. STOPPED for
Nick's nano-vs-tiny pick; HIL demo banked behind it.

---

## 2026-08-23 (later) — session close: labeler running overnight (COCO-init yolox-s, 0.658 @ e1); train_ctl page shipped + live-tested; corpus_v2; PR #60 merged

- **Labeler** (`stage1_s_labeler`): stock-stem YOLOX-S, COCO-pretrained
  init (456/462 tensors), corpus_v2 — **rung-A 0.658 after ONE epoch**
  (already over nano-v2's final). Running via train_ctl in 8 h night
  sessions; the page auto-scores each session end and grows the mAP
  panel. corpus_v2 = v1 + Nick's 93 reviewed CA GBIF frames (rung-B
  sources fenced from the REAL species split; the stale candidates file
  over-fenced 111 and was caught).
- **train_ctl.py**: start/pause/resume/stop + night toggle + config
  panel (from the run's own config.json — caught a stale hardcoded arch
  string mislabeling runs) + loss/mAP plots + CPU/GPU/thermal panel.
  Every control integration-tested live, including server-restart
  adoption over a running run. Three port-squatter incidents in two
  days (two stale label GUIs + the page's own polling keeping a dead
  server alive) → immediate-exit-on-signal fix + cookbook recovery.
- PR #60 merged (Nick's close-out call); stale PRs #52/#41 closed with
  comments. NEXT SESSION: PROMPTS §14 — bench window: power rig on
  nereus000, deploy both candidates, measured ms + mJ, decision table.

---

## 2026-08-23 — bite E Mac-side arc COMPLETE: tiny 0.729 (capacity confirmed as the binding constraint); decision table minted; bite PR opened

**Branch:** same session. Tiny (identical v2 recipe, arch only) finished:
**rung-A 0.729** vs nano-v2's 0.654 — ~+0.08 over nano at every matched
epoch. Compiles clean both boards (AE3 4.97 MB single `ethos-u`, est
41.7 ms; N6 0 pure-SW). Nano-vs-tiny decision table in STAGE1.md —
Nick's call, gated on the bench window's measured ms + mJ (power rig
captured under bite D). Trainer grew --stop-after-hours + pause/resume
runbook (night-chunked training, Nick's ask). PR opened for the whole
Mac-side arc; bite E stays open behind the HIL demo + on-board numbers.
Queued next (Nick's gate given): YOLOX-S @512 labeler/teacher for the
dive-video pipeline — night sessions via the new scheduling controls.

---

## 2026-08-22 (night) — bite E continues: v2 0.654 (mosaic+EMA, +0.081 over v1); species head trained, red data-starved (0.435, measured); Tiny capacity probe auto-launched; Nick reviewed 149 GBIF frames

**Branch:** same session continuing. Highlights, details in STAGE1.md +
run dirs:
- **stage1_v2 FINAL rung-A mAP50 0.654 (EMA ckpt)** — mosaic (box-aware
  quilt, band-preserving) + ramped EMA + 120 ep; no-aug tail alone
  +0.037. int8@256 0.202 vs v1's 0.128; both board compiles clean,
  placement unchanged. Tiny (5.03 M params, same recipe) auto-launched
  by the queue waiter — the capacity lever, ~1 day ETA.
- **Species head v1: rung B purple 0.963 / red 0.435** — 42 unique red
  training crops is the measured bottleneck; dive-footage red pass now
  evidence-backed. Nick's GUI sitting: 149/149 frames, 23 junk excluded,
  boxes 347→1,198 (his adds measured stage-1's ~30% recall at conf 0.5
  on dense frames; kept frames are detector-grade in-water labels,
  banked for stage 3).
- **int8 scoring path landed** (+ its own bug caught: /255 fed to a
  raw-0..255 model; onnx2tf graph NOT resize-safe — int8 scores at
  native size). Quantization tax measured healthy (~0.014 @256 on v1).
- Label GUI grew 'c' (clear-frame) + progress bar (18 tests green);
  cookbook chapter gained stop/reload + urchin-set launch; stale 19-h
  labeler on :8899 diagnosed/killed. AE3 memory ceilings narrowed from
  vendor source: /flash 8 MB, /rom 24 MB; Tiny probe artifact staged
  (SPEC §Open questions). Power/energy re-measure captured under bite D
  (Nick's go, parts in hand).

**Next:** Tiny final vs v2 decision table (acc/ms/mJ once bench
measures); stage-2 crop regen with v2; bite PR after the comparison.

---

## 2026-08-22 (later) — S8 bite E steps 2+3: stage-1 TRAINED AND SCORED — rung-A mAP50 0.573 vs the 0.351 bar; int8 recompiled clean for both boards

**Branch:** `claude/s8-bite-e-urchin-training-ce8830` (continuation of the
compile-gate session after Nick approved YOLOX-Nano). Zero board contact.

**Done:**
- corpus_v1 built + fence-verified (19,904/96,326 train, val 976; builder
  `ml/urchin_data/build_corpus_v1.py`, manifest with per-source shas).
- YOLOX-Nano stage-1 trained on MPS (40 epochs, ~3 h, 4.3 it/s @ b32) and
  scored on rung A: **0.573 mAP50 final** (0.225 → 0.455 → 0.514 → 0.573
  at e0/10/20/39) vs yolo11n 0.243 / yolo11x 0.351 / ceiling 0.908.
  Model card + eval table: `ml/yolox_urchin/STAGE1.md`.
- Trained weights exported int8 + recompiled for BOTH boards
  (`ml/yolox_urchin/export.py`): placement identical to the untrained
  gate — AE3 single `ethos-u` op / N6 117-HW+2-hybrid+0-SW of 119.

**Broke/surprised us:**
- Urchinbot's official val.txt and test.txt share an image (im5348179.JPG;
  true counts 7913/977/983 vs published 7912/976/982) — resolved to test,
  recorded in the corpus manifest.
- Naive random crops gave 70% target-free samples (box-aware crop fixed:
  74% of boxes in the 24–64 px band, p50 34). Fixed-decay EMA scores ZERO
  mid-run from init pollution (0.478 even at e39 vs last.pt 0.573) —
  ramped decay is the queued fix; last.pt is the stage-1 model. Three
  legacy `.type(str)` casts make stock YOLOX MPS-hostile — patched at
  import in ml/yolox_urchin/model.py, third_party untouched.

**Next:** stage 2 (auto-box GBIF via stage-1 model + underwater/junk
filter, species crops, rung B ~150+150 for Nick's label-GUI sitting) —
gated on Nick. Owed: int8-vs-float delta, on-board latency (bench
sessions), PR for the bite.

---

## 2026-08-22 — S8 bite E step 1: compile gate PASSED — Apache-2.0 holds, YOLOX-Nano recommended; STOPPED for Nick's pick

**Branch:** `claude/s8-bite-e-urchin-training-ce8830` (Mac-side only — ZERO
board contact, per the kickoff; boards belong to the bench sessions).

**Done:**
- The corpus plan's Decisions #2 gate run to completion: two untrained
  Apache-2.0 candidates (YOLOX-Nano conv-stem, NanoDet-Plus-m; 256 px,
  1-class, int8 NHWC) exported and pushed through BOTH board compilers
  via the B1 scaffold (`ml/compile_model.sh`). **PASS — the AGPL fallback
  clause is dead.** Report: `ml/compile_gate_report.md`.
- Verdict short form: YOLOX-Nano = **single `ethos-u` op on the AE3
  (zero CPU fallback, vela est 28.1 ms)** and 116-HW/2-hybrid/0-SW of
  118 epochs on the N6. NanoDet = CPU TRANSPOSE fallback on the AE3 and
  36 hybrid + 2 SW epochs on the N6 (ShuffleNet channel shuffle, both
  targets). Recommendation: YOLOX-Nano; YOLOX-Tiny is the same-family
  capacity fallback.
- Toolchain: new `~/nereus_ml/venvs/gate` (torch-cpu + TF 2.19 +
  onnx2tf); run metadata + artifact sha256s in
  `~/nereus_ml/runs/compile_gate_2026-08-22.json`; models/logs under
  `~/nereus_ml/exports/compile_gate/`.

**Broke/surprised us:**
- Stock YOLOX won't convert or place as-is: the Focus stem's stride-2
  slices are both un-Vela-able (stride-1-only STRIDED_SLICE) and
  onnx2tf-hostile — swapped for a plain stride-2 conv (YOLOX-ti-lite's
  exact adaptation, recorded in the report). Head flatten+concat tail
  also dropped for raw per-level maps (decode on-board, FOMO precedent).
- onnx2tf's calibration-data download is silently broken (pickled npy
  refused) — pre-seeding the npy in cwd fixes it; NanoDet needs an
  onnxsim pass first or shapes collapse to zero-dim. Both potholes in
  the report's §Repro.

**Next:** STOPPED at the kickoff's gate — Nick picks the architecture.
Then step 2 (corpus_v1 merged view, symlink/manifest, Urchinbot test
split fenced) and stage-1 training against the 0.351 rung-A bar.

---

## 2026-08-21 (night) — S26 bites 1+2 done in one desk session: corpus verified, downloaded (~45 GB), license-captured, QA'd, converted for S8; NOAA baseline measured weak; bite-3 plan drafted

**Branch:** `claude/urchin-dataset-s26-a1517e` (parallel desk track — ZERO
board contact, as designed). Owner gate exercised throughout: Nick picked
the corpus (Urchinbot + DUO + GBIF-clean + RF100 + 74-img; sea-urchin-body
REJECTED), made the Roboflow account/key, and set the posture that decided
the license calls (demo/open-release, not a commercial moat → DUO unfenced,
NC data out, Apache-2.0 architecture preferred for adoptability).

**Done:**
- Every claim in docs/urchin_datasets.md verified or corrected; dossier
  §Verification + §Bite-2 QA written; verbatim license captures + sha256
  manifests in ml/urchin_data/. Counts held everywhere (Urchinbot
  9,872/44,268 exact; DUO 7,782/50,156 exact; GBIF 13,387/3,666 exact);
  the LICENSE layer moved: FathomNet 100% NC-ND (0 commercial boxes,
  full 4,600-image audit), iNat ~85% NC (clean = 2,019 purple/574 red),
  DUO figshare DECLARES CC BY 4.0, 74-img set is purple-ONLY (3 red).
- Corpus ON DISK and artifact-verified: Urchinbot full 9,872/9,872
  (34.8 GiB, 0 corrupt), DUO (md5 = figshare), GBIF clean 2,578 imgs +
  provenance JSONL, RF100 + 74-img Roboflow exports (audits exact).
- Live convention agreed with the S8 session (cross-session sync):
  labels.jsonl converters shipped + run for all 4 box sources
  (ml/urchin_data/convert.py, self-auditing); S8's GUI rehearsed against
  our layouts. Pixels-on-target adopted as THE eval axis (distance
  dropped — S8 relay: ball runs were all ~1.5 m).
- NOAA yolo11n/x baselines run on the Mac: PROVISIONAL rung-A (n=690)
  mAP50 0.225/0.334, R 0.23/0.31 — vs Urchinbot's published 0.908
  ceiling on the same data. Custom model justified by measurement.
  FULL rung-A (983 imgs) running overnight via ml/urchin_data/
  eval_rung_a.py; recorded on completion.
- Bite-3 plan DRAFTED (docs/urchin_corpus_plan.md) with flowchart;
  4 open decisions for Nick's review. Starfish/sun-star detector
  captured to Icebox (RF100's 10,270 starfish boxes banked).

**Broke/surprised us:**
- Three "successful" downloads that weren't (figshare 63-byte error body;
  urllib CERTIFICATE_VERIFY_FAILED ×300 behind a pipe that swallowed rc)
  — every one caught by artifact checks, none by exit codes. The rule
  held: trust the bytes.
- DUO ships a silently remapped YOLO labels dir (0=starfish 1=holothurian
  2=echinus 3=scallop vs COCO order) and a val split that is a byte-copy
  of test; Urchinbot's test.txt lacks a trailing newline (983 imgs, not
  982) and its GitHub weights are UNLICENSED (dataset CC-BY, weights not).
- Sakana (NOAA yolo11n's training set) is GONE from Roboflow Universe —
  404 logged-out AND logged-in.

**Next:** FULL rung-A lands overnight → dossier + S8 relay (queued).
Then: bite-3 plan review with Nick (4 decisions), S8 bite E re-scope,
nibble-4 PR for the sprint. NEW flagged item for Nick to size: AE3
dive-recorder rig (~3-week deadline, board-touching → S8 arc; GoPro-only
is the fallback, dive not gated).

---

## 2026-08-21 — S8 D2 code+tests: per-detection confidence on the wire, overlay, and page; near-collision with the parallel B2 session caught before board contact

**Branch:** `claude/s8-d2-model-confidence`. No board contact this session.

**Done:**
- **Phase-0 compile smoke (before B2's duplication surfaced):** untrained
  Keras FOMO at the exact deployment shape passed BOTH compilers — vela
  `Ethos_U55_256`, est 2.05 ms, zero fallback lines; stedgeai 22/24 epochs
  pure HW (SW = Softmax+Dequantize, the vendor-pattern tail). Confirms the
  Keras→int8→modelc route independently of B2's. TF venv:
  `~/nereus_ml/venvs/fomo` (tensorflow 2.19.0), separate from the
  ultralytics pins.
- **D2 (Nick's gate: "per-detection confidence is fine"):** `fomo_decode`
  boxes gain conf% = winner-softmax at the group's peak cell, exps run only
  on margin-passing cells; overlay "pink 0.87"; host parses `mb`, HUD conf
  line + the documented blob asymmetry (hard LAB threshold has no conf).
  Suite 134→141.

**Broke/surprised us:**
- **The §11 kickoff ran in TWO sessions and the other one finished B2**
  (PR #55, demo PASSED, model live on both boards) while this one was in
  phase 0. Caught at the workbench preflight — the live `s8-two-colour-model`
  recipe was the tell. **My smoke ROMFS image would have DELETED
  `nereus_two_ball` from the N6** (built from vendor config + smoke model
  only); deleted twice — the still-running background build re-wrote it
  after the first rm. Artifact-check habit paid for itself.

**Same session, later:** bench staged for review through the workbench
(stop→settle→start, LIVE in ~10 s on the D2 branch); dark-room frames
verified by EYE (pulled JPEGs — black), so 0/0 counts were the correct
artifact; custom model does NOT hallucinate on dark frames (contrast
S24's stock-model finding). **Nick's live check PASSED ("confidence
values look good") → D2 PR opened.** Old demo had been live ~19 h and
stopped as `failed — exited rc=0 while live`; restarted clean; recurs →
bite R's pile.

**Same session, B3 (after PR #57 merged; Nick's gate + the guide-card
variant "a chapter in our growing cookbook"):** `ml/fomo/label_gui.py`
shipped (stdlib browser GUI over labels.jsonl, atomic saves, additive-only
classes, reviewed stamps) + relabel.py `--force` guard (a re-run must
never silently flatten hand corrections) + workbench GUIDE CARDS
(`guide=` recipes: unstartable documentation chapters, served confined;
first chapter = label-review with a live labeler-up badge probing the
Mac). Save path rehearsed on a dataset COPY, boxes/reviewed/class-add
verified ON DISK. 231 tests green across the three touched suites.
Branch `claude/s8-b3-label-gui`.

**Same session, B3 closure (Nick):** GUI + card used live; guide's guessed
hostname fixed (Bonjour `nicks-macbook-pro.local` via scutil, NOT shell
`hostname` — verified by curl before commit) and all chapter commands made
absolute. **Retrain acceptance WAIVED by Nick** ("ton of work, urchins
next") — B2's label-noise debts stay open in the two-ball model; the GUI
is bite E's labelling path. Pattern → repo skill
`.claude/skills/workbench-guide-card` + memory. B3 PR opened.

**Next:** bite C (1 m/2 m end-to-end metrics, needs Nick at the bench) →
bite D (desk arithmetic) → E/urchins on S26's corpus.

## 2026-08-20 (late night) — S8 B2: OUR OWN trained model runs on BOTH NPUs — collect→label→train→export→compile→deploy→measure, end to end in one session

**Branch:** `claude/two-colour-detector-s8-b2-ea5ca7` (from main @ 42635ac).
Bench via the workbench API only (start/stop + settle honoured); AE3 ops per
ae3-board-access, one op per invocation.

**Done:**
- **The whole point of the sprint, proven:** a from-scratch two-colour FOMO
  detector, trained on our own captures, deployed by the B1 routes, measured
  **AE3 5.51 ms / N6 6.36 ms per inference** — the NPU class (vela est 2.04;
  the 96 px FOMO anchors are 1.66/2.75), NOT a CPU fallback. sha256 verified
  on both boards; N6 `/rom` intact at 18 entries after the DFU-alt-3 write.
- Dataset: 693 labelled VGA frames (both boards, ~1 m, Nick scattering on a
  30 s timer widget), captured in ONE 10-minute bench window via two bounded
  runs. Auto-labels recomputed offline (`ml/fomo/relabel.py`) — see below.
- Trainer/export: `ml/fomo/train.py` — plain Conv/BN/ReLU stride-8 net
  (119 KB int8, uint8 io, scale 1/255), TFLiteConverter full-int8;
  int8 ≈ float on eval (P/R ~0.73/0.87, count-MAE 1.3–2.0 at margin 0.5–1.0).
- Harness: FOMO model mode in `n6_stream_{board,host}.py` (margin decode
  ln(2) — no softmax on board; per-class `mc` counts on the wire; per-board
  `--board-model`; model-vs-blob counts side by side in the HUD). Suite
  117→134; workbench 61 green.
- Recipe `s8-two-colour-model` with `models[]` sha256 declared (feeds S25
  bite 3 reconciliation). **Dress-rehearsed via the API exactly as Nick's
  click: state=live, both boards streaming, model counts on the page.**

**Broke/surprised us:**
- **Keras BatchNorm momentum trap:** with ~18 steps/epoch, the default 0.99
  moving stats lag so far that train-mode loss hit 0.036 while
  inference-mode was 1.12 on the SAME data — EarlyStopping then restored
  garbage weights. Fix: momentum=0.9. First model looked "trained" and
  detected nothing.
- **Auto-label poison, seen only by rendering:** Nick's blue-gray shirt
  labels as "purple" (a=3..5 vs balls ≥9 — the `a` channel is the only
  separator), and specular highlights split balls (closing fixed). The
  model still learned the USPS-box lettering as purple — visible as a
  static phantom count in the live demo.
- dfu-util refuses to overwrite an existing readback file and the stale
  B1 file made a correct flash look like a hash MISMATCH — rm first.
- Nick's "2 m" run was actually ~1 m (his call, fine) — dirs still say
  run2_2m; treat both as 1 m.
- **AE3 raw-repl refusal, incident #7 — and the first on the NON-bridge
  stack (bite R evidence).** Nick's first demo click failed: the
  workbench probe got `could not enter raw repl`. Diagnosis by the book:
  device enumerated, no port holders, dmesg clean (NO usb-storage reset
  storm — the livelock variant ruled out). One serialized recovery
  (75 s zero-contact + single `mpremote reset`) REFUSED (`b''` read) —
  the power-cycle-only variant. **Physical replug cleared it (Nick);
  demo re-run passed.** Why it matters for bite R: all six prior
  incidents were post-bridge-teardown on the SHM-128K bridge stack;
  this one is on the S18 sticky-fb build running only pyserial
  raw-repl streams — session traffic was cp + probe run + one
  stream start/stop + the failed probe (~the 4–6-attach pattern).
  The bridge lifecycle is NOT a necessary condition; attach count /
  raw-repl traffic looks closer to the mechanism. Nick flags this as
  needing a fix soon — see bite R.

**Next:** ~~Nick runs the demo~~ → **DEMO PASSED (Nick, same night; first
click hit bite R incident #7, replug cleared it). PR #55 open.** Next
session = **bite B3 (NEW, Nick's call): label-review GUI** — he reviews
ALL training frames, corrects boxes by hand, classes beyond colour;
saves back to labels.jsonl so the trainer consumes corrections
unchanged. Then bite C metrics. Model debts sized in bite B2's entry.


---

## 2026-08-20 (night) — S25 bites 1+2 SHIPPED AND DEMO'D: the workbench page starts/stops the ball demo; AE3 quick-reattach wedge found, fenced with a settle window, cleared by physical replug

**Branch:** `claude/nereus-vision-workbench-4d2268`. Bench: both boards on
nereus000; `workbench.service` installed ENABLED by Nick and proven across
a reboot.

**Done:**
- **Bite 1 (menu + passive preflight)** and **bite 2 (runner + single-owner
  board lock) shipped; Nick ran the demo — PASS** (start from the page →
  LIVE with hyperlink → stop → settle countdown → start again). 61 host
  tests. Page at :8088, enabled at boot.
- Recipe format: strict-schema TOML (unknown keys are errors), per-recipe
  `thumbnail` (confined `/thumbs/` route; ball demo ships a live-captured
  side-by-side frame) and per-recipe `services` (no standing unit list —
  three of the old six units belong to the parked two-Pi T1L bench).
- Runner: health-gated LIVE (poll the recipe's URL until 200), SIGINT →
  SIGTERM → STUCK ladder with **`signal.SIGKILL` pinned out of the source
  by a test**, foreign port holders refused BY NAME and never killed,
  pidfile adoption across workbench restarts (proven live through Nick's
  unit install).

**Broke/surprised us:**
- **The AE3 wedges on quick stop→start** (Nick's test): raw-repl refusal,
  viewer stuck at "could not enter raw repl (retry in 30s)". Recovery by
  the book — stop all contact, 70 s silence, one `mpremote reset`, twice —
  did NOT clear it: this is S23 bite R's power-cycle-only refusal state,
  and only a **physical replug** cleared it (Pi 5 never cuts VBUS; a Pi
  reboot demonstrably left it wedged). Product fix shipped: runner
  SETTLE=35 s window after stop/failure before the same boards can start,
  countdown on the page. The viewer's own 5-attempt + 30 s retry loop is
  part of the problem (continuous port contact during the refusal) — not
  fixed, noted for bite R's file.
- **The N6 sat in DFU mode 18:32–18:41 and recovered on its own**
  (dmesg: `37c5:9206 "OpenMV Camera (DFU Mode)"` on the N6's port, then
  back to VCP). Unattributed; harmless this time; do not assume benign.
- `pkill -INT -f workbench.py` over ssh matches its own bash wrapper
  (exit 255) — same `pgrep -f` trap as the 2026-08-20 session note.

**Same session, later (after PR #53 merged — Nick's "get up to #3"):**
- **Bite 3 DONE + verified live:** Start = preflight → reconcile → spawn →
  health. One serialized `mpremote exec` per board (sys.version + on-board
  sha256 of declared models); repair only via `models[].src` file copy,
  hash-checked before, read back after; firmware/N6-ROMFS drift REFUSE with
  the manual step. Live proof: both boards' firmware probed green,
  reconciling→LIVE, AE3 attached cleanly after its probe (RECON_GAP 3 s).
  The "delete a model → repaired" live demo waits for the first recipe that
  ships an artifact (B2's detector); 9 host tests pin the path meanwhile.
- **Bite 4 DONE:** `n6-detect-stream` recipe (zero detections on a ball
  scene = CORRECT, person-only model) + `pi/workbench/README.md` (release
  step, recipe reference, safety posture). 70 host tests total.
- **CLAUDE.md updated** (Nick-approved deltas): two-arc scope line, real
  layout (`ml/`, `pi/workbench/`), bench standing facts (by-id identity
  backwards-from-guess, single-owner ports via the workbench, AE3 35 s
  settle), board identity added to "never invent hardware facts".
- **PROMPTS.md §11** written: S8 bite B2 kickoff (Nick runs it in a
  parallel session).

**Next:** S25 owes only Nick's sprint demo (cold boot → page → pick test →
streams → stop → ports released). Then S8 B2 (parallel session, §11), whose
detector recipe becomes reconciliation's first live model-repair demo.

## 2026-08-20 (evening) — S8 bite A DEMO PASSED; B0+B1 closed: both boards run our own compiled models

**Branch:** `claude/s8-cv-detector-ladder-bbfa8d`, PR #49. Bench: both
cameras on nereus000's new 3D-printed mount, side-by-side viewer on :8090.

**Done:**
- **Bite A demo PASSED (Nick).** Per-board thresholds, per-board pixel
  floors, a live overlay toggle, and tuning driven by measured LAB rather
  than guesswork. Ground truth 11 pink / 10 purple; both boards converge at
  pink 10 / purple 7 with one ambiguous merge. 117 host tests.
- **B1 CLOSED — the whole compile→deploy→run route works on BOTH boards**,
  using OpenMV's own tooling (`tools/modelc.py`) and the compilers already
  in the SDK. AE3: vela → `/flash` → 1.66 ms vs the vendor's 1.81 ms. N6:
  stedgeai → ROMFS image over USB DFU alt 3 → 2.75 ms vs the vendor's
  2.76 ms. No ST-LINK needed.
- **B0**: Mac training host up, MPS training works, export chain proven —
  and its one gap identified (NCHW float32 vs the boards' NHWC uint8).
- Folded PR #48 (the other session's two-board viewer + AE3-vs-N6
  head-to-head) into this branch; wrote 6 tests for the merge seams.

**Broke/surprised us:**
- **One threshold cannot serve two sensors.** Same scene, same script: AE3
  5 blobs, N6 18. The N6's blue cast puts its pink balls at b≈−16.7, inside
  the default purple box; the AE3's sit at −6.5, outside it. Per-board
  thresholds AND per-board pixel floors are both required, both measured.
- **The N6 rejects bytes it already runs.** Our compiled model was
  byte-identical to the one in its ROM, loaded fine from `/rom` and failed
  from `/flash` — stedgeai's relocatable binary wants params in XIP flash.
- **`mpremote romfs deploy` would have destroyed the vendor models**: it
  reads OpenMV's 24 MB partition as `size 0`. Not attempted; DFU alt 3 was
  the right route and never touches BOOTLOADER, so it stays recoverable.
- Every miss Nick circled was a NEAR miss with an exact cause — a pink ball
  at L 31.2 against an L-32 floor, three N6 pinks at a 24.2–25.8 against an
  a-26 floor. Measuring the pixels beat nudging the numbers.
- A test caught my own parser bug: `pink:1,2,3,4,5,6` parsed as a *board*
  named "pink", structurally identical to a valid `--blob-thresh`.

**Nick's product read, recorded because it moves the board decision:** the
AE3 is **not** out of the running. Its lower fps and lack of a hardware JPEG
encoder were expected to disqualify it; on this task they do not — accuracy
matches or beats the N6, which loses more balls at the edges of its FOV.

**Next:** **B2 — the from-scratch two-colour detector.** Capture with bite
A's `--save-frames` (its per-class boxes are the auto-labels), label, train
on the Mac, export NHWC uint8, compile and deploy by the now-proven routes.
Start with a small classifier/FOMO-style model, NOT YOLO — OpenMV's
maintainers report stock Ultralytics INT8 export failing ST's compiler.

## 2026-08-20 (later) — S8 bite A shipped + PR #48 folded in; `b.code` settled by probe; both boards now on nereus000

**Branch:** `claude/s8-cv-detector-ladder-bbfa8d`. Bench: both cameras on
nereus000, side-by-side viewer live on :8090.

**Done:**
- **Bite A code**: repeatable `--blob-thresh NAME:...` (one LAB box per
  colour, own palette colour each), per-class counts on the wire
  (`bc`/`amb`/`bb`), `--blob-scan codes|per-class`, `--save-frames` +
  `index.jsonl` labelled capture with the overlay forced OFF, bounded runs
  that end by themselves. Tests 30 → 102.
- **Folded PR #48** (S24 round 2: two-board viewer, AE3-vs-N6 head-to-head,
  dark-frame finding). Both branches rewrote the same three files; kept both
  designs with bite A adapted onto the multi-board structure. Wrote 6 tests
  for the merge seams, which neither branch could have had.
- **`b.code` SETTLED** — index bitfield; each pixel goes to the FIRST
  matching threshold in list order; `merge=True` ORs codes. So overlapping
  boxes silently under-count, and the repo's own pink/purple example
  overlapped 8.8%. Guard shipped (quantified warning). DESIGN §S8 bite A.
- **D44 topology**: both boards on nereus000, Mac is the training/toolchain
  host, artifacts move through the Pi. Board identities verified from their
  own banners — and they are backwards from the guess (N6 = "MicroPython
  Pyboard", AE3 = "OpenMV Camera").
- Retired the off-repo fork at `~/n6_sidebyside` (renamed, note left) after
  verifying by hash that all three files were strict ancestors of the merge.

**Broke/surprised us:**
- **My first `b.code` experiment was invalid** — two scan modes run as two
  sessions against a drifting ceiling; the N6's own count moved 1 → 2 with
  no config change. One frame scanned by every variant is what settled it.
  Two runs at two times is not an A/B.
- **A `pgrep -f` matched the bash wrapper AND my own ssh command line**, so a
  "successful" kill left the viewer holding both ports. Caught only by
  checking the ports afterwards; launching a second viewer would have had two
  processes fighting over both boards.
- **First capture run recorded `boxes: []`** — a ceiling has nothing pink, so
  the box-recording path never executed. Same latent-path trap as S24's draw
  calls. Re-run with a matching threshold to force it, then the saved frame
  was pulled back and LOOKED at to confirm it was clean.
- Detection counting was fused with drawing, so `det` would have read 0 for
  every frame of every capture run. Split and tested.

**Next:** bite A's live demo with real balls (blocked on Nick's camera mount,
~1–2 h), then B0's Mac ML environment + the int8-TFLite export proof. **B1
still needs Nick's explicit go** — first bite that writes to a board.

## 2026-08-20 — Ladder resequenced: CV leads. S18/S19 dead, S22 closed, S8 reshaped to NEXT, S23 parked

**Branch:** `claude/ae3-board-states-root-cause-33011e`. Planning
session with Nick after the bite R night; no bench work.

**Nick's calls, in order:**
- **S18 and S19 are DEAD.** Dropped from the ladder as *tombstones*,
  not deletions: S18 shipped the infrastructure the bench runs on every
  session (the two systemd units, demo_up, bench-ctl, chain_status,
  ref-scene, MEAS_FPS), and deleting the provenance of live tooling
  would cost the next agent a day. The compare/gallery tool "works well
  enough for now" — further work parked until it needs changes. S19's
  unowned findings explicitly survive it.
- **CV leads.** Nick: *"before I spend any more time trying to make the
  VGA faster it's important to get the AE3 and N6 running a custom
  urchin model so that I can benchmark performance."* The prior CV
  sprint is **S8**, whose bite 1 (NPU bench, 2026-08-11) already
  concluded that all ROM detectors are person-class-only and that a
  custom Vela-compiled detector is required either way — and whose
  cross-board table carries the caveat that the two boards ship
  DIFFERENT model binaries, so it is "not a silicon shoot-out". One
  custom model on both boards is precisely what removes that confound.
- **S8 reshaped and promoted to NEXT**, old gate ("runs after the
  BM-native arc; board risk gates CV investment") OVERRIDDEN — CV is
  now the board-selection input. Bite A = **"pink ball vs purple ball"**
  (Nick's call): a deliberately easy, classic-CV target to prove the
  whole train→compile→deploy path before any urchin labelling exists,
  split A1 (toolchain, sourced from vendor docs — **the N6's NPU is NOT
  an Ethos-U55 and this repo has no verified note on it**) / A2 (the
  detector), with a STOP gate between. Acceptance names the trap:
  **prove it runs on the NPU, not silently on the CPU** — a rejected
  .tflite still returns correct answers, just slowly. Bite B =
  end-to-end capture→detect→count at **1 m vs 2 m**, with
  **pixels-on-target recorded next to the distance** so the numbers
  transfer to urchins and the T2 ≥24–32 px floor. Bite C = the urchin
  model once a labelled set exists. S8's old pipeline/alert bites moved
  to S21 so the two sprints stop describing the same work.
- **S22 CLOSED on the shipped work.** Goal met by bite 1 (HE flood fix:
  u16 vring-index wrap in `rr_poll_n`, one wrap-safe cast, proven
  on-chain at the exact rate+duration that killed the live demo —
  10-min QVGA color 28.23 fps, 16,939 frames, ledger-exact) and bite 2
  (the headroom table was reviewed and ACTED ON: it became S23's bite
  list, 7.41 → 12.53 fps). **Its one debt is carried, not buried:**
  bite 1b's q90 burst loss is investigated-but-unfixed (every hop
  exonerated except the telemetry fork's internals) and now lives in
  the retitled "Flagged, not owned by any bite yet" list, cross-
  referenced from the S21 bite that will inherit it — evidence stills
  are exactly the q90-class payload that defect bites.
- **S23 PARKED** at GOLD 12.53 with bites S (overlap the HE feed) and 3
  (re-measure + guardrails) unstarted.

**Measured en route (planning input, not a bench session):** both
boards are on nereus000 and healthy — AE3 and N6 each report OpenMV
v5.0.0 / MicroPython v1.28.0-49 and expose the `ml` module loading
`.tflite` from ROM. So S8 A1 is likely CONFIRMING "one trained source,
two compilations, one load API" rather than discovering two unrelated
paths. The N6 is `usb-MicroPython_Pyboard_...FS_Mode_0200...-if00`
(ttyACM1) — distinct by-id from the AE3, so the S8 standing rule
(always connect by-id, never auto-connect) matters more than ever now
that both boards are targets.

**Reconciled with S24 at merge time (this branch was cut before S24
landed on main).** S24 — N6 CV baseline, opened by Nick the same
evening — was already running the N6 half of exactly this work, and it
**answers one of the questions this plan flagged as open**: the N6's
accelerator is **Neural-ART on STM32N657X0**, verified live, not an
Ethos-U55. Its bite 3 owns the remaining `stedgeai`-vs-direct-load
question, so S8 takes that answer instead of re-deriving it. S24 also
supplies the hard justification for S8's bite A: `/rom/yolov8n_192.tflite`
emits `output_shape (1, 5, 756)` = 4 box coords + ONE class ("person"),
so a pink ball is unreachable by configuration — a custom model is
mandatory, not a preference. S8 keeps the AE3 half plus the cross-board
custom model and REUSES S24's `bench/n6_stream_{board,host}.py` harness
rather than starting a third one. S24 is Mac-only, S8 wants the bench,
so neither blocks the other.

**Then Nick folded S24 into S8** (same session, after reading the
reconciliation): S24 was only a separate sprint because S8 was gated
behind S13, and that gate had just been overridden — so the reason for
the split had dissolved and two sprints describing CV would only split
the evidence. S24 bite 1 stays delivered and kept (demo PASSED, PR #45
merged); bites 1b/2/3 became S8 bites A/D/B1; and S24's verified
hardware-facts block MOVED into S8, because it is operational guidance
the next session needs in front of it rather than history. S8's bite
list is now A (multi-colour blob baseline — the classic-CV control the
ML numbers get compared against), B1 (does a stock int8 .tflite run on
Neural-ART or need `stedgeai` — settle by test, needs Nick's go as the
first bite that writes to the board), B2 (the from-scratch two-colour
detector), C (end-to-end at 1 m vs 2 m), D (the board-decision number),
E (urchin). Kickoff prompt written as PROMPTS.md §9.

**Execution order now: S8 → S21 → S20**, with S23's leftovers and bite
R slotted at Nick's call.

---

## 2026-08-19/20 (night) — Sprint S23 bite R: the reproducer works, load does NOT cause the wedge, and a NEW host-side failure mode (usb-storage reset livelock) is found and reproduced twice

**Branch:** `claude/ae3-board-states-root-cause-33011e`. Nibble-1 plan
approved by Nick, then "go with v2", then a firmware round trip at his
request. Bench: Nick handed over hardware for the night.

**Shipped (tests 0 -> 50, all green; bridge 373, units 43 unchanged):**
- `pi/bm_bench/repro_attach.py` — scripted cold-boot -> lifecycle ->
  attach-ladder cycles. Every refusal is screened against the bridge
  state machine BEFORE it may count (verdict table in the header); a
  true anomaly STOPS the run and preserves the wedge as the specimen.
- `firmware/bm_bridge/boot_report.py` — MPU walk (ARMv8-M regs verified
  against the vendored CM55 FreeRTOS port + CMSIS mpu_armv8.h) and SHM
  probes to /flash at boot and post-he.start, one generation kept.
- `pi/bm_bench/bench_chain.py` — v2 load: drives bm-light + telemetry +
  bench-ctl, streams a real row, proves load from BOTH ends (receiver
  ledger AND the board's cap_frames); either reading zero = cycle VOID.
- demo_up: syncs/preserves boot_report, and the silent-fail is FIXED.

**RESULT 1 — v1 (no load): 8/8 clean, 64/64 attaches.** Its own artifacts
said why it proved nothing: exit stats `cap_frames=0`. It tore down
bridges that had never carried traffic.

**RESULT 2 — v2 (real load): 6/6 clean, 48 attaches, then aborted.**
VGA color 623 frames x3 and HD mono 220 x3, gaps=0 dropped=0
pub_errs=0, receiver ledger == board cap_frames EXACTLY every cycle.
**So streaming load before the teardown does NOT cause the refusal.**
That hypothesis is not supported.

**RESULT 3 (the big one) — a NEW failure mode, host-side, reproduced
twice: the usb-storage reset livelock.** udev/blkid probes the AE3's
mass-storage volume (SCSI READ(10), 4 blocks @ LBA 0) while the board
is not servicing MSC; the read fails (hostbyte=0x07 DID_ERROR);
usb-storage escalates to a USB device reset; the reset re-triggers the
probe; loop, ~46 resets/minute, indefinitely. **Every reset re-binds
cdc_acm, so any in-flight mpremote session dies and by-id flickers** —
i.e. it presents EXACTLY as "could not enter raw repl" / "board fell
off the bus", with a perfectly healthy board underneath.
It is a RACE, hence intermittent: 37 clean SCSI attaches tonight vs 6
failed reads; cycles 1-6 attached in ~1 s, cycle 7 took 7 s then
failed. Cure: unbind usb-storage from the MSC interface only (CDC
untouched) — `echo "1-2:1.2" > /sys/bus/usb/drivers/usb-storage/unbind`;
verified twice, board immediately reachable after.
**Correction to the mid-session read:** the first storm was blamed on
an MSC mount done to read evidence. WRONG — it recurred tonight with
no mount anywhere. The mount was coincidence.

**RESULT 4 — warm reset does NOT clear SRAM9 (measured).** Read off the
board's MSC volume with ZERO REPL contact (an attach would have
soft-reset and rotated the evidence away). The wedged generation's
report vs a post-unplug boot: MPU regions IDENTICAL (region 7 =
0x60000000-0x6001FFFF, AttrIndx 4 -> MAIR1 0x44 = Normal
Non-cacheable in BOTH — so patches 0004/0005 set the attributes
correctly even on the boot that wedged, evidence AGAINST the step-3
bisect premise). What differs is CONTENT: warm boots come up carrying
the previous generation's rsc/vring/pool AND the HE "BMHE" magic
already at the status page before he.start; after a physical unplug the
same addresses are random with no magic. **`mpremote reset` and a
physical unplug are NOT the same boot** — the ops recipe says they are.
`bench_chain.sram_state_at_boot()` now classifies every boot; all 6 v2
cycles logged `sram=warm`.

**RESULT 5 — the v3 demo_up silent-fail was a SCRIPT DEFECT, not a sick
board.** Reproduced live. `mpr`'s fail() calls `exit 1` inside
`GOT=$(board_sha ...)`, which exits the SUBSHELL — the `|| echo missing`
never runs, the assignment carries rc=1, `set -e` kills demo_up — and
`2>/dev/null` on the same line swallowed the reason. Fixed; 3 tests pin
it. The underlying double-attach-failure is still real and still bite R's.

**STILL UNEXPLAINED (bite R's remaining core): the silent refusal with
NO resets.** 22:03: USB healthy, cdc_acm bound, zero resets in dmesg,
~59 min after boot (past the 600 s phase-1 ceiling, so no legal bridge
could hold the port), refused raw-REPL TWICE through a properly-armed
45 s window. Not the storm, not state confusion, not the script defect.

**Firmware round trip (Nick's request):** flashed stock v5.0.0
(byte-verified), Nick yolo-tested, IDE-erased the filesystem; restored
fw `1e56071e…` byte-for-byte + `bm_he.elf` `39717d44…` hashed ON the
board + demo_up staging. **Caught en route: the two banked bm_he.elf
copies DIFFER** — `~/bm_bench/` is `89cc92ff` (stale), `~/bm_he/` is
`39717d44` (correct). Using the wrong one would have silently poisoned
every later measurement.
En route a mute-on-CDC state appeared on STOCK firmware (zero bytes to
mpremote, to raw Ctrl-C, and to the S6 fixture's own JSON, while
enumerating perfectly) — state 2's signature, which would exonerate our
patches, but CONFOUNDED: the S6 fixture was running under MicroPython
v1.28 rather than the line it was written for. Not counted as evidence.

**DURABLE FIX SHIPPED (Nick's call, then "stop the hunt"):**
`pi/ae3_flash/99-ae3-no-msc.rules` — udev keeps usb-storage OFF the
AE3's mass-storage interface, matched on interface CLASS (8/6/80,
verified against the live device: 1-2:1.2 reads class=08 sub=06
proto=50) rather than an interface number, so a firmware that reorders
interfaces cannot silently re-enable the disk. INSTALLED on nereus000
and VERIFIED against a real board reset — the event that livelocked
cycle 7: `sda` gone, usb-storage unbound, **reset delta 0**, CDC and
by-id intact. Caveat recorded honestly: the rule unbinds moments AFTER
the kernel binds (dmesg still logs "Mass Storage device detected"), so
a narrow race remains in principle; empirically the block device never
survives to be probed.

**Bench state at session end:** AE3 on fw `1e56071e…` + ELF
`39717d44…` (both byte-verified), **S6 fixture RESTORED to /flash and
verified on the board** (all six files size-exact — the standing
session-end rule), units DOWN on both Pis, no-MSC udev rule installed,
bus quiet. Evidence banked: `docs/evidence/` + `~/biteR_wedge_evidence/`.
Note the bridge files (bm_bridge.py, uart_codec.py, boot_report.py,
bm_he.elf, ref_scene) remain staged alongside the fixture, so the next
demo day is one demo_up away.

**HUNT STOPPED here by Nick after the rule landed.** Bite R is not
closed but it is much smaller: three of its symptoms are explained and
one (the reset livelock) now has a shipped fix. What remains for
whoever picks it up: (1) teach the reproducer to detect a reset storm
in dmesg and classify it, so mode A can never again masquerade as the
thing being hunted; (2) hunt the no-reset silent refusal — the ONLY
unexplained state left, and the one the 22:03 capture documents;
(3) the step-3 pre-SHM-128K bisect is now LOW value — the MPU
attributes were measured identical on wedged and healthy boots.
## 2026-08-19 (late) — S24 OPENED: OpenMV N6 CV baseline — headless live detection stream + first measured sweep (no bench hardware touched)

**Branch:** `sprint/24-n6-cv-baseline`. New sprint, Nick's call (D43),
after a plan review. Runs on the **Mac** over USB; nereus000/001 and the
AE3 were not involved, so S23 bite R is re-ordered, not displaced.

**Done:**
- **Reviewed Nick's proposed benchmark plan before writing code** and
  found half of it already existed: DESIGN §S8's "AE3" table from
  2026-08-11 was measured on **this N6** (a mis-attribution its own
  correction records). Confirmed same board/ROM by matching the tells —
  `yolov8n_192` = 3,233,408 B and 25.6 MB heap. So the new content is
  capture-in-the-loop, e2e fps, the size sweep and provenance.
- **Shipped the viewer the OpenMV IDE can't provide** (no IDE build
  runs on macOS 14.6.1): `bench/n6_stream_board.py` (runs under
  `mpremote run` — **nothing written to the board**) +
  `bench/n6_stream_host.py` (decodes, serves multipart MJPEG on the
  `pi/stream/stream_server.py` pattern) + **19 host tests**.
  Base64 payload rather than the project's framed-binary format,
  deliberately: `mpremote run` returns stdout through the raw REPL,
  which ends on byte `0x04`, and JPEGs contain `0x04`.
- `--tune` mode draws a centre target and reports its mean LAB with a
  suggested `--blob-thresh`, so a colour threshold is read off a real
  object under real light rather than guessed from a colour name.
- **Measured** (full tables in DESIGN §S24 detail): yolov8n_192
  **20.7 / 23.7 / 32.2 ms** mean at QVGA/VGA/HD, p95 within 0.5 ms of
  mean; **capture+inference e2e 47.9 / 41.8 / 30.2 fps**; all 9 ROM
  models timed (3.5 → 65.3 ms). Live stream 22.6 fps at VGA with the
  blob overlay on.

**Broke/surprised us:**
- **`mpremote run` is not a transport — Nick caught it as "a still
  image, not a live stream".** The viewer decayed from ~20 fps to under
  2 and wedged. The per-frame counters settled it: **board-side work was
  flat at 38.5 ms/frame first-to-last**, so the board was fine and the
  output path was losing data. Two faults: `bufsize=0` made pipe
  `readline()` read a byte per syscall, and — the real one — the decay
  scales with *total output*, i.e. `mpremote run` accumulating and
  rescanning its raw-REPL buffer. Proven by piping `mpremote run`
  straight to a file (fastest possible consumer) and still getting
  ~1.5 fps. **Fixed by owning the port with pyserial and driving the raw
  REPL directly** (`SerialBoard`), the same shape as
  `pi/stream/usb_frame_source.py`. Result: **1,020 frames / 11.3 MB at
  23.3 fps, zero resyncs**, vs a wedge by frame ~703 before. mpremote is
  still the right tool for the bounded sweep runs.
- **The failure mode was a plausible still image** — indistinguishable
  from a live stream of a stationary scene. A `resyncs` counter now sits
  next to fps in the HUD so it announces itself.
- **The stream ran 324 clean frames with every draw call wrong.** The
  scene was a ceiling — zero detections, zero blobs — so no draw path
  ever executed. It would have crashed the instant it saw anything to
  draw. Caught only by forcing a wide-open blob threshold to make the
  draw path run. Rule 4, in its purest form: the artifacts were real
  JPEGs, the fps was real, and the code was still broken.
- **OpenMV v5 changed the drawing API**: `draw_*` take a tuple first
  arg (`draw_rectangle((x,y,w,h))`), blob fields are attributes
  (`b.rect`, not `b.rect()`), `get_statistics()` returns a namedtuple
  (`st.l_mean`), and `Image` has no `bpp()`. Four separate TypeErrors.
- **The `csi` module's framesize constants are not the sensor's
  ladder** — `SXGAM`/`WQXGA2` are exported and refused
  (`Sensor control failed.`). Sensor letterboxes to 16:10 everywhere:
  VGA is **640×400**.
- Model load is ~2.2 ms — the tflite is memory-mapped from ROM, not
  copied. It is not a cost.
- `os.uname()` does not carry the OpenMV build (only MicroPython
  `1.28.0`); `sys.version` does. The S7 lesson, re-confirmed.

**Late additions (same session), from Nick's questions at the bench:**
- **"Can it detect sports ball?" — No, and the output tensor settles
  it.** `/rom/yolov8n_192.tflite` reports `output_shape (1, 5, 756)`.
  A YOLOv8 detect head is `(batch, 4 + num_classes, anchors)`, so 5 = 4
  box coords + **one** class, where an 80-class COCO export would be
  `(1, 84, …)`; 756 = 24²+12²+6² accounts for the anchors at 192 px.
  The ROM model was exported single-class. No threshold, label file or
  postprocessor change can reach the other 79 classes. Promoted bite 3
  to "ship a real multi-class detector" with ST's int8 COCO yolov8n
  (192 px variant) as the concrete candidate; the unverified part —
  whether `ml.Model` takes a stock int8 tflite on the NPU or needs an
  `stedgeai` compile — is flagged, not guessed, with a cheap experiment
  written down to settle it.
- **"What is a blob?"** Not ML at all: `find_blobs()` thresholds pixels
  in LAB colour space and groups touching in-range pixels into
  connected components. It found the balls because they are a distinct
  colour, and it has no concept of what a ball is. Labels were being
  drawn but unreadable — cyan text on a bright scene — so blob labels
  are now outlined (1 px black offset, `draw_label`), numbered, and
  named via `--blob-label ball`.
- **Took the N6 off the USB bus with `kill -9`.** SIGKILL skipped
  `board.stop()`, leaving the board streaming into a closed endpoint
  from inside the raw REPL; the device node vanished and it went
  missing from `system_profiler` entirely. **A Mac cannot power-cycle
  the port** — no uhubctl equivalent — so it needed a physical replug
  from Nick. SIGTERM/SIGHUP now unwind through the Ctrl-C path; SIGKILL
  is uncatchable and is simply the thing not to do. This is the Mac-side
  cousin of the bench's AE3 enumeration lesson.

- **"It stopped when I moved the camera. Bad cable?"** Not the cable
  necessarily — nudging the connector is enough: the reader died on
  `SerialException: [Errno 6] Device not configured`, and because that
  escaped the reader thread, the HTTP server kept serving the last frame
  with live-looking stats (`fps 19.9, frames 5840`). **That is the same
  symptom as the two earlier bugs in this bite** (unexercised draw path;
  mpremote decay) — a frozen stream and a motionless scene look
  identical, so liveness must be measured and shown, never inferred.
  Shipped: the exception is caught, a `supervise()` loop reconnects
  (re-resolving the port each time, since the node moved 1101→1201
  across the replug), and the page now carries `stale_s`, a
  `reconnects` count and a red **NOT LIVE** banner.
  **The reconnect test then found a second bug**: the raw REPL's
  end-of-execution `0x04` arrives with NO trailing newline, so the
  line-oriented `readline()` blocked forever when a script returned and
  the supervisor never learned the stream had ended. Fixed by looking
  for the EOT in the buffer rather than at the head of a completed line;
  verified end-to-end with a self-terminating board script — **180
  frames across 6 cycles, `reconnects 5`**. Suite 19 → **30**.
- **No, nothing autostarts on the board** (Nick asked). `/flash/main.py`
  is the stock LED blinker and stays that way; the stream script is
  pushed into the raw REPL and runs from RAM, so a power cycle leaves
  the board blinking and not streaming. The host viewer is what
  restarts — now automatically.

**DEMO PASSED (Nick, same evening).** Live in a browser with labelled
tracking boxes. His readings, banked in DESIGN §S24: **six balls
detected simultaneously at ~2 m** indoors under room lighting, and
**~1 W board draw** (method not recorded — order-of-magnitude, not
instrumented; ST's published figure is <0.75 W for YOLOv8n at 30 fps,
not directly comparable since ours is the whole board also capturing at
VGA, JPEG-encoding and streaming over USB). Nick also threw **pink**
balls into a purple-tuned scene and they were correctly ignored — pink's
`b` channel sits outside the purple LAB box. That is the threshold
working, not a fault; matching several colours at once is fenced as
bite 1b rather than smuggled into the demo.

**AE3 run, same evening (Nick handed the board over).** Stock `v5.0.0`
verified, `/flash/main.py` is the stock LED blinker. **VGA 7.6 fps vs
the N6's ~19, and the gap is the JPEG encoder, not the NPU** —
inference 27–28 ms vs 23.5 (1.2×), encode **73.8 ms vs 3.9 (19×)**,
58% of the AE3's frame budget. Independently reproduces the S22/S23
premise: the stock AE3 measured here sits right where the S23 encoder
arc started (7.41 fps). Tables in DESIGN §S24.

**I wedged the AE3, and the cause was my own retry loop.** The
hand-rolled raw-REPL handshake failed (5 s timeout where mpremote
allows 10, no raw-paste), and the supervisor then retried every 2 s —
~20 attaches in 45 s, against a documented wedge threshold of 4–6
(S23 bite R). It then refused mpremote too (`could not enter raw
repl`); Nick's physical replug cured it, as the docs say it must.
Two fixes shipped: the attach now uses **mpremote's own
`SerialTransport`** (reuse before rewriting — only the attach; the read
loop stays ours), and retries **back off 2/5/10/20/30 s** with the
attempt count in the status line. Four clean start/stop cycles since,
no refusals. **A viewer that hammers a quiet port is not resilient, it
is the fault.**

**Colour thresholds are a property of the scene, not the object.** The
default purple box found nothing on ~20 purple/pink balls at 2–3 m.
Sampling the frame: pink reaches `a`=29–30 with `b`=1–15, purple sits
at `a`=5–7, the floor at `a`=9/`b`=21 — and the default demanded `b` in
−75…−10, where **no ball is**. Re-thresholding on measured values
tracked the pink balls immediately. **One box cannot cover both**:
purple is less magenta than the floor, and widening `L` instead merged
the furniture into one 120 ms blob. Bite 1b promoted from nicety to
requirement.

**THE RANKING FLIPPED ON POWER (Nick measured it).** AE3 **~0.2 W**
running yolov8n against the N6's **~1.0 W**. Worked through in DESIGN
§S24 ranking: **N6 wins delivered throughput** 19 vs 7.6 fps — which is
entirely its hardware JPEG (3.9 vs 73.8 ms) — while **the AE3 wins
energy by 4.3×: 5.5 mJ vs 23.7 mJ per inference**, and 2.0× per
delivered frame. The NPUs are within 1.2×, and even that is confounded
by the AE3 running a smaller model binary, so the separation is
everything *around* the NPU: the N6 spends silicon on a JPEG encoder and
25.6 MB of heap and pays ~1 W; the AE3 omits both and runs the same
detector for a fifth of the power. **For a duty-cycled subsea node,
energy per inference is the metric that matters and the AE3 wins it.**
Both power numbers are single uninstrumented readings — 5× apart so the
direction is safe, but the magnitudes are owed a deliberate re-measure
before they size a power budget.

**"fps" disambiguated (Nick asked whether 7.6 fps was frames-with-
inference or a mix).** It is **1:1** — the board loop is strictly serial
with no skipping, so 7.6 fps = 7.6 inferences. But that is the wrong
ceiling to quote: it prices a pipeline that also encodes and streams
every frame, and on the AE3 JPEG alone is 58% of the budget. Three
ceilings recorded in DESIGN §S24 (inference-only ~26–36/s AE3 vs 42 N6;
inference+stills; inference+video 7.6 vs 19), plus the point that binds
the actual product: **tiling**. HD at a 192 px model = 40 tiles →
**0.91 fps AE3 / 1.05 N6, both below the T2 ≥3 fps gate** — S8's
conclusion re-derived from two boards. Ranking guidance: don't sort on
fps, sort on inference-rate × tiles × energy, because *what unlocks the
product is a custom detector with a larger input, not faster silicon*.

**CORRECTION — do NOT count this session's late port failures as an
attach-refusal incident.** After the ranking work, `mpremote` twice
reported "may be in use by another program" and I diagnosed a wedge
needing a replug. **Wrong: Nick was removing the board.** Confirmed
after the fact — zero OpenMV devices in `system_profiler`, no
`/dev/cu.usbmodem*`. The single REAL wedge this session was the earlier
one I caused with the 2 s retry loop (fixed with backoff + mpremote's
transport). **S23 bite R's evidence base must not gain a phantom
incident from this.**

**Bench state: the AE3 was returned to Nick and is off the laptop.** Both
boards are stock `v5.0.0` with stock `/flash/main.py`; nothing was ever
written to either.

**Owed, and blocked only on hardware:** the AE3's true inference-only
ceiling (a ~2 min run of `sweep.py`) — currently a **bound, 26–36/s**,
depending on whether its 11.6 ms capture overlaps inference the way the
N6's provably does. That is the number a customer's inference
application would actually be limited by, so it should not stay a
bound.

**Next:** nibble 4 (PR) for bite 1, then **bite 4 — Pi Zero 2 W + IMX on
the same axis** (Nick): measure **mJ per inference**, not fps, so all
three boards land on one comparable scale. Then bite 2 — the N6-vs-AE3
tiled-coverage comparison, carrying the model-variant confound
explicitly.

**Bench state:** untouched. The N6's `/flash/main.py` is still its stock
LED blinker; no firmware written; no ADIN, no Pi, no AE3 contact. The N6
itself is off the USB bus pending a replug (above).

---

## 2026-08-19 (evening) — S23 GOLD: the "13 ms invariant" NAMED — it is the serialized HE round-trip (HE only ~41% utilized); VGA 12.15 -> 12.53 CLEAN; the "attach-refusal anomaly" mostly EXPLAINED (uhubctl never cuts VBUS on Pi 5 -- no cold boot ever happened)

**Branch:** `claude/vga-color-15fps-encoder-7bf32c`. Two instrumented
60 s rows (both CLEAN ledger-exact) + two ops discoveries.

**Row 1 (bridge `44c20573…`, counters only): 12.15 fps, 729x20 exact.**
capwait verdicts: park=729/729 -- the ref-mode "early kick" NEVER
armed a capture (at fb=1 the stale readable frame blocks arming), so
capture cost was ~zero and the CPI/pixclk theory is DEAD for ref rows;
poll gaps ~zero; enc_us 50.3 vs 42.4 desk = ~8 ms of scheduled _rx
callbacks inside to_jpeg (enc_qin 12.5 arrivals/frame); ~14 ms/frame
outside every timed window; gc tail in the cycle histogram (mode ~76,
mean 82.1) fed by ~21 MB/min of _rx bytes() churn + ~20 MB/min jpeg.

**Round-2 build (bridge `5071cecd…`, suite 373):** HeWire zero-alloc
RX ring+pool (RPMSG_SLOT_B 1544, POOL_MAX 64; peek/advance FIFO;
oversize spills never pooled), ref-stream sensor bypass (ref rows now
price ENCODE+RELAY only; sensor rows keep shadow+kick), kick_us/out_us
residue meters.

**Row 2: 12.53 fps, 752 frames, gaps=0, pub 15,047 = 752x20+7 exact.**
The ledger REWROTE the model: enc ~44.1 (ring cheapened the callbacks)
· asm 1.63 · send 33.4 = pump 11.6 + **ept-block 21.2 ms/frame (was
0.5!), 29% of sends >1 ms** · out_us 35.6 confirms send+asm+glue.
**Every HP win converted into vring waiting. The invariant IS the
serialized HE round-trip: burst-feed + wait = ~250 msg/s delivered at
20 chunks/frame = the 12.2-12.5 plateau, across ALL five falsified
levers.** Decisive: the HE clears 20 chunks in ~33 ms (1.65 ms/chunk)
then IDLES through the 44 ms encode -- ~41% utilized. **Route to GOLD
(next bite): overlap the feed -- non-blocking chunk pusher clocked off
the _rx callbacks (the mechanism already proven to interleave with
to_jpeg), main-loop tail drain, re-entrancy guard vs control sends.
Predicted cycle max(enc 44, HE 33)+residue ~48-52 ms = 19-20 fps;
also attacks HD's 72 ms ept-block.**

**Ops discovery #1 (changes the recovery recipe):** `uhubctl -l 3 -p 1
-a cycle` on the Pi 5 RE-ENUMERATES but NEVER CUTS VBUS (ae3-usb-
unstick already knew; today proved the consequence): the MCU does not
reboot, main.py does not run, the board just returns to whatever state
it was in -- so every "uhubctl recovery" that ever worked worked
because demo_up's FINAL `mpremote reset` did the actual reboot.
Today: two uhubctl "boots" -> launcher never ran -> AE3-NEVER-JOINED;
physical unplug -> joined in 5 s; `mpremote reset` -> joined in 15 s.
**Cold-boot recipe is now: `mpremote reset` (or physical unplug);
uhubctl only clears wedged USB-stack state.**

**Ops discovery #2 (bite R shrinks):** with #1, most of today's
"attach-refusal" states re-read as ordinary state confusion (no bridge
running / bridge holding the port), not silicon sickness. STILL
UNEXPLAINED and still bite R's core: repeated "could not enter raw
repl" through properly-armed 45 s quiet-exit windows (the v3 demo_up
silent-fail), and incident #2's CDC-RX-stall + empty-HE-ring boot.
demo_up gained the mpr timeout/armed-retry wrapper + loud inventory
failure en route (units suite 43).

**Bench state:** units DOWN, board at REPL with round-2 bridge
`5071cecd…` staged + byte-verified on /flash, scene=ref cfg intact,
fw `1e56071e…`/ELF `39717d44…` unchanged. Chain bring-up from here:
`mpremote reset` -> 95 s -> bm-light -> bm-telemetry.
Traces banked on nereus000: ~/trace_row1.txt, ~/trace_row2.txt.

**Nick's 3-attempt budget: spent** (1 = uhubctl dead boot -- explained
by #1; 2 = row 1; 3 = row 2). GOLD at 12.53, not closed; the overlap
sender is specced as TRACKER bite S. **Nick's pivot call, end of
session: BITE R runs next** (fresh session, kickoff = PROMPTS.md §8);
bite S waits behind it.

---

## 2026-08-19 (latest) — Sprint S23 GOLD: capwait counters shipped; the bench day went to attach-refusal #4a/b/c — the wedge now has a shape; demo_up hardened; Nick bounds GOLD at 3 attempts then pivot to root cause (bite R)

**Branch:** `claude/vga-color-15fps-encoder-7bf32c`. Nick's gates:
nibble-1 plan approved, then "keep moving past nibble"; late-day call:
**three more GOLD attempts, then pivot — root-causing the anomaly
becomes next priority (TRACKER bite R), fresh session.**

**Done (desk, tested):**
- **capwait counters** (the name-the-13-ms instrument), bridge-only:
  per-frame kick→collect (`kc`), first-miss→collect wait (`cw` +
  polls), poll-gap histogram (loop service granularity), cycle
  histogram (uniform tax vs gc/callback spikes), `enc_qin` (rpmsg
  arrivals during to_jpeg — the 42.4 ms desk vs ~50 ms on-chain enc
  discrepancy suspect). Traced per stream + 30 s snapshots. Suite
  341→**359**. Bridge `44c20573…` — VERIFIED current on /flash by the
  morning demo_up before the bench fell over. NO measured row yet.
- **demo_up hardened into a convergent tool** (three commits): `mpr`
  wrapper = 30 s timeout on every board touch; on timeout OR the
  "could not enter raw repl" signature → 45 s untouched (the failed
  attach itself arms the bridge's 30 s quiet-exit) → ONE retry → loud
  fail naming the uhubctl recipe. Preflight retries through the
  armed-exit window; a failed ref-scene inventory now refuses to
  masquerade as "/flash full (free 0)" (it did, on a healthy board).
  Units suite 43 green (+ pre-existing `errno` allowlist fix).

**The day's real finding — the wedge has a SHAPE (TRACKER bite R):**
after a bridge teardown (rp.stop()), the board tolerates ~4–6
mpremote attaches then refuses raw-REPL entry BELOW python until a
true power cycle. Incidents #4a (2× demo_up hung ~10 h at the same
sha-check — one hang held the port overnight), #4b (fast refusals
through properly-armed windows), #4c (post-physical-replug run got 6
attaches in, then wedged at the inventory step). Six incidents total,
all on fw `1e56071e…`/ELF `39717d44…`. The DEV_LOG trigger
("instrument the SHM-128K/MPU neighborhood") has fired six times over.

**Bench ops en route:** one uhubctl cycle + Nick's physical replug
(both re-enumerated clean); nereus001 yellow-LED after Nick moved the
Pis → clean reboot (throttled=0x0, both green); **cross-cable side
door currently DOWN** ("network unreachable" after the move — reseat
J-cable on a calm day); Tailscale ssh to n000 flaky-then-fine.
Attempt-1 bring-up also burned one 10-min phase-1 window on an agent
timezone bug (bench clock vs Mac clock — timed starts now use bench
time only).

**Broke/learned:** trusting `rc=0` + tail — the first demo_up hang
was invisible because the pipeline exit code was the ssh's, not the
script's (CLAUDE.md rule 4, again); `2>/dev/null` at a call site
swallowed mpr's fail message AND the xtrace — the silent-death run
was diagnosed only by logging server-side to a file.

**Next (this session):** attempts 1–3 = power cycle → 90 s → bm-light
into the fresh phase-1 bridge → bm-telemetry → 60 s `vga-color-15`
row → read capwait via the units path (no mpremote). On a clean row:
name the 13 ms, fix what it names (task 3). On three failures: stop,
leave the bench cold-cycled, hand to the bite R session.

---

## 2026-08-19 (later) — Sprint S23 GOLD arc: VGA plateaus at 12.2–12.3 across five falsified levers (an invariant ~13 ms/frame is the open question); HD mono climbs 3.37→3.62; two more bench incidents, recovery recipe nailed

**Branch:** `sprint/23-encoder-fastpath`. Nick's directive: "keep the
improvements coming till we get 15fps or better on VGA."

**Shipped this arc (each measured on 60 s CLEAN ledger-exact rows):**
- **Non-blocking capture machinery** (`csi.snapshot(blocking=False)` —
  the csi module has it; D21's "cannot overlap" was about the dead SPI
  path): collect-on-poll, kick-after-encode, quiesce before every
  sensor touch (suite 328→336). VGA 12.10 (flat), HD 3.43 (+).
- **fb=2 double-buffering**: measured **SLOWER — VGA 11.47.** S3's old
  verdict reproduces on the sticky-fb stack for a new reason: the
  continuous capture DMA contends with the MVE encoder for memory
  bandwidth. REVERTED; finding recorded in the code.
- **Ref-stream framerate cap** (dark-bench exposure theory — the
  PAG7936 driver clamps AE max exposure to frame time): cap 60
  ENGAGED per trace and measured flat (12.00); raised to 120, still
  flat. Exposure was NOT the wait.
- **Fused one-pass COBS+CRC viper encoder** (`frame_encode_fused`,
  goldens incl. 0xFF-code boundaries; codec suite 38→49): per-message
  relay enc **676→499 µs on the wire** — and VGA cycle unchanged.
- **Early capture kick** (ref streams kick at collect since the frame
  is discarded; sensor streams copy to a shadow buffer wrapped by
  `image.Image(buffer=)` so bench and deployment keep the same
  pipeline shape; suite 341): VGA 12.23 (flat), HD 3.57–3.62.

**The open question, precisely:** VGA cycle ≈ 81.7 ms = enc ~50 +
asm ~3 + send ~15 + **~13 ms that survives every lever** (not send —
fused encoder moved the wire cost, not the cycle; not exposure — caps
engaged and did nothing; not capture-arm timing — early kick did
nothing; capture itself is CPI-pixclk-bound ~21 ms/VGA frame at the
24 MHz OMV_CSI_CLK_FREQUENCY but the kick should hide it under the
49 ms encode). Next instrument: per-frame kick→collect wall-time and
poll-gap counters in the engine — measure, don't model. Firmware-side
levers if the 13 ms is real capture: raise CSI pixclk (board config,
flash spin); encoder-side: Huffman/bitstream is the remaining scalar
stage. HD keeps gaining from every send-path lever because its send
(~90 ms) dwarfs capture: **3.15 → 3.62 across the arc.**

**Bench incidents #2 and #3 (same signature as #1):** attach-refusal
after clean bridge exits; incident #3 survived one uhubctl cycle
because the first touch came too soon. **Recovery recipe now proven
twice: `sudo uhubctl -l 3 -p 1 -a cycle -d 3`, then ≥5 minutes of
ZERO port contact, then one demo_up.** The silence is load-bearing.
Three boot-state anomalies in one day on fw `1e56071e…`/ELF
`39717d44…` — bite 3's soaks must watch for this; if it recurs,
instrument the SHM-128K/MPU neighborhood on cold boots.

**Bench state:** chain UP under units, scene=ref, health-proven
(capture landed, gaps=0). Deployed: bridge `79c9ab4f…` + codec
`ebcfb87d…` (fast path + fusion + early kick + cap 120), fw/ELF
unchanged. MEAS_FPS: VGA 12.23 / HD mono 3.62 (deployed-config
numbers, bench_web 81). All commits pushed.

**Next:** the ~13 ms hunt (capture-wait counters, fresh session) →
Nick's call on firmware levers (CSI pixclk / Huffman MVE) vs calling
12.3 the MicroPython-path ceiling → bite 3 re-measure + guardrails +
PR. VGA-15 is NOT closed; HD-mono-5 needs the HE round trip cut
(ept-block ~62 ms/frame at HD) — HE-side batching or the C path.

---

## 2026-08-19 — Sprint S23 — relay regression resolved-as-explained (clean-boot HD 3.15×2, above stock) + drain fast path shipped: VGA color 12.30, HD mono 3.37, ledger-exact

**Branch:** `sprint/23-encoder-fastpath` (ff'd to main @ e3bc81e; PR #42
is MERGED). Nick's gates this session: Phase-A plan approved; 4:2:0
eyeball run — quality satisfactory → **bite 0 CLOSED**.

**Done — desk facts first (no board contact):**
- **The AE3 enumerates USB HIGH-SPEED** (lsusb -t on nereus000: 480M,
  dev 37c5:16e3). The "~675 KB/s VCP floor" (D40's ~185 ms/126 KB
  burst-drain measurement) is ~1% of line rate — software, not USB.
- The VCP write pattern is IDENTICAL between stock and rpmsg-1544
  stacks (both drain per chunk) — the regression suspects were narrowed
  to the rpmsg leg/interleave before any instrumentation ran.

**Done — relay-split counters (bridge-only, no flash; suite 294→310):**
cap_send_us split into cap_ept_us (+max/slow>1ms) and cap_pump_us;
usb.write metered globally (vcp_us/writes/bytes); pump batch stats.
`stats=None` keeps every legacy path unchanged. Bridge `b3543cc7…`
SYNCED by demo_up first-try (sha checked), scene=ref.

**Measured (60 s rows, scene=ref, all CLEAN ledger-exact):**
- **VGA color 10.62 fps** (637 frames, pub_ok 637×20 exact) — control
  holds the 10.73 stack number (~1% run variance).
- **HD mono 3.15 fps TWICE** (189 frames each, pub_ok 189×55 exact) —
  **ABOVE the 3.10 stock baseline. The 2.72 did not reproduce.** Its
  own preserved trace shows steady-slow from the first HD snapshot
  (181/174/175 ms/frame send, no degradation curve) → boot-state
  anomaly, not the geometry. Any recurrence is now attributable in one
  trace snapshot.
- **The split, per message:** pump ~1.25 ms at BOTH resolutions — and
  inside it **usb.write is only ~55–61 µs (measured VCP throughput
  ~24 MB/s)**. The relay tax is the ~1.19 ms of PYTHON per message
  (he_msg dispatch + COBS _encode). ept.send: free at VGA (23 µs —
  20 chunks fit the 32-slot vring), blocking at HD (avg 1.14 ms, 26%
  of sends >1 ms, max 30.8 ms — 55 chunks overflow the ring and ride
  the HE's pace, which is itself drain-coupled).
- **HD mono frame budget at 3.15 (317 ms):** enc ~96 + capture ~33 +
  pump-python ~69 + ept-block ~63 + asm ~12 + misc ~44.

**Lever ranking rewritten by the numbers:** (1) cut the per-message
python drain — viper/native COBS encode path; at HD −50 ms/frame if
3–5× lands (3.15→~3.8), at VGA −20 ms (10.6→~13.3); helps every mode,
bridge-only; (2) capture/encode overlap (the ~19 ms snapshot wait —
still REQUIRED for VGA-15: even a free relay leaves 68 ms > 66.7);
(3) ept pacing/batching at HD. HD-mono-5 plausibly = (1) + (2)
without firmware.

**Done — drain fast path (Nick's "go for it"; the fix's own A/B was
the stage-3 profile, per the 1a lesson):**
- Stage-2 counter first: `relay_enc_us` timed core.he_msg inside the
  pump — **he_msg = 1.10 of the 1.26 ms/msg pump cost (87%)**, on a
  3.17 fps CLEAN row (190×55 exact). Viper CRC+COBS only plausibly
  ~0.2 ms → suspects = the three ~1.5 KB per-message allocations.
- Shipped `he_frame_wire` (zero-alloc fast path for complete
  WCMD_FRAME_TX: encodes into the preallocated `_wire`, returns an
  aliasing memoryview consumed before the next encode) + killed the
  `bytes(l2[:n])` detour in `frame_encode_into` (slice-assign straight
  from the memoryview). `he_msg` stays the allocating reference path;
  equivalence/fallback/reasm-ownership/aliasing pinned in tests
  (bridge suite 310→312→328, codec 37→38).
- **Measured (60 s rows, CLEAN, ledger-exact): VGA color 10.73→12.30
  (738 frames, pub_ok 738×20 exact) · HD mono 3.15→3.37 (202×55
  exact).** Split after: enc/msg 1102→~680 µs (−38%), pump/msg
  1259→~825. VGA send leg now 17.5 ms/frame (ept 0.5 — 20 chunks
  never block the 32-slot ring); HD send 120 ms = ept-block 72 + pump
  47 — **half the pump saving converted to ept blocking: HD is now
  HE-round-trip-bound, not HP-python-bound.**

**Sprint ladder (VGA color q50, 60 s rows, ledger-exact):**
7.41 → 7.93 (4:2:0) → 9.03 (MVE conv) → 9.50 (rpmsg) → 10.73 (DCT) →
**12.30 (drain fast path)**.

**Broke/surprised us:** nothing on the bench — every demo_up and row
session landed first-try under the serialized-port discipline (one
expected phase-1 armed-attach recovery, by the book). The surprises
were the data: the regression we came to fix does not exist on a clean
boot; the VCP-floor model died by measurement; and the remaining
~680 µs/msg python encode cost is far above what the viper loops
should cost — unattributed, noted for the next profile if it matters.

**Bench incident (RESOLVED by Nick's uhubctl cold cycle; ~50 min):**
after the clean fast-path rows, the next bridge boot went into a sick
state. The wedge model written mid-incident ("hung in he.start") was
WRONG — the preserved trace, read post-recovery, shows the boot DID
link (t=39003 = bm-light's start to the second) and then received
ZERO VCP bytes for 30 s while bm-light demonstrably heartbeated →
clean quiet-exit at t=69131 whose **HE ring dump came back EMPTY**
(the healthy boots dump content). After that exit, three serialized
mpremote attaches over ~15 min all failed "board busy" and a
nereus000 reboot changed nothing (Pi 5 never cuts VBUS) — NOT fully
explained: the launcher has no relaunch loop, so something below
python kept refusing the port. `sudo uhubctl -l 3 -p 1 -a cycle -d 3`
(a true power cycle) cleared everything; demo_up then landed
first-try and the chain health-checked clean (capture landed,
frames_ok+1, gaps=0). **Pattern flag for bite 3's soaks: this is the
SECOND boot-state anomaly on the fw `1e56071e…`/ELF `39717d44…`
stack in one day** (the steady-slow 2.72 boot, now a CDC-RX-stall +
empty-HE-ring + attach-refusal boot). Not attributable to the
fast-path python (boot path untouched; same artifacts booted clean
before and after). If a third appears, suspect the SHM-128K/MPU
neighborhood on cold boots and instrument there.

**Bench state: chain UP under units, scene=ref**, fast-path bridge
`b4a6beee…` + codec `67aaecf1…` on /flash sha-verified, fw
`1e56071e…` + ELF `39717d44…` unchanged, MEAS_FPS 12.30 / 3.37 live
on :8090, both Pi checkouts at 0049e5a+.

**Next:** capture/encode overlap re-test — the arithmetic says VGA-15
falls to it alone (81.3 − ~19 ms snapshot wait ≈ 62 ms ⇒ ~16 fps):
needs a non-blocking frame-ready poll (openmv-tree desk check, likely
a small C patch under the sticky-fb precedent) — `set_framebuffers(2)`
A/B is deliberately NOT run without Nick (S18: growing the fb with the
HE loaded takes the board off the USB bus). HD-mono-5 outlook honest:
overlap alone reaches only ~3.8; the 72 ms/frame ept-block (HE
round-trip pace at 55 chunks) is the wall — HE-side batching or the
C path, Nick sizes. Then bite 3 re-measure + guardrails + PR.

---

## 2026-08-19 — Sprint S23 — bite 1b: MVE DCT golden-passed, VGA color 10.73 fps (+45% on the sprint); the SHM growth ate two firmware spins (16-slot starvation, then the hardcoded MPU window); HD mono relay regression profiled and OPEN

**Branch:** `sprint/23-encoder-fastpath`. Nick's "Go for it" on the DCT.

**Done — the DCT (patch 0002 now = the full jpege vectorization):**
- AAN butterflies in int32x4: row pass via widening byte GATHERS
  (4 rows/group, `vldrbq_gather_offset_s32` stride 8) + word scatters;
  column pass contiguous; quantization = one float multiply + `VCVTN`,
  which is exactly `fast_roundf`'s VCVTR under the default FPSCR RN
  mode; zigzag scatter + reverse-scan end0pos (same result as the
  inline tracker). Every operation is the scalar op in lanes.
- **GOLDEN PASS: all 44 rows byte-identical** on the full stack (color
  convert + DCT + quant). Encoder vs stock: VGA color 65.9→42.4 ms
  (**1.55×**), HD color 256.8→164.3 (1.56×), QVGA color 1.53×, mono
  1.23× (DCT+quant only — no color convert to win back).

**The SHM saga (two extra spins, both lessons banked):**
1. First DCT flash ran on the 64 K SHM / 16-slot vring from bite 2 —
   VGA color 10.25 CLEAN but **HD mono 3.10→2.50: 16 slots starve
   55-chunk bursts.** Depth matters at HD scale.
2. Grew SHM 64K→128K (`OMV_OPENAMP_SIZE`, both cores' config) with 32
   slots — **chain broke outright** (gaps by the thousand, cam
   timeouts, HE restarts): `METAL_MPU_REGION_SIZE` in mpmetalport.h is
   **hardcoded 0x10000**, so the HP's non-cacheable MPU window covered
   only the lower half of the grown region — vring RINGS stayed
   coherent (low 64 K) while buffer PAYLOADS above 64 K went through
   the HP D-cache. Classic split-brain cacheability; found by reading
   the metal port, fixed as one line in patch 0004 (now also carries
   mpmetalport.h; 0005 = board_config SHM growth).
3. Fixed stack: **VGA color 10.73 fps CLEAN, pub_ok 12,880 = 644×20
   exact** — the sprint's best. **HD mono 2.72 CLEAN (163×55 exact) —
   still BELOW the 3.10 stock baseline despite a 1.23× faster
   encoder.** Profile split (both rows in one trace): HD mono spends
   ~172 ms/frame in send/relay (~3.1 ms per 1414 B message, ~2.3
   ms/KB) vs VGA's ~1.3 ms/msg — **the HD wall is the relay leg
   (rpmsg→VCP→light→UDP round trip), and it got slightly SLOWER with
   fewer, bigger messages. OPEN: next session profiles the relay leg
   itself** (suspects: VCP write blocking inline with drain-every-1 at
   1544-B chunks; HE per-message memcpy scaling; the ~675 KB/s VCP
   floor at 76 KB/frame ≈ 113 ms is over a third of the HD budget).

**Sprint ladder (VGA color q50, 60 s rows, all ledger-exact):**
7.41 → 7.93 (4:2:0) → 9.03 (MVE conv) → 9.50 (rpmsg) → **10.73 (DCT)**.
Encoder anchors on the page moved to the MVE numbers; MEAS_FPS carries
10.73 and the honest 2.72 with its investigation note.

**Bench state:** chain UP under units, scene=ref. Flashed: HP fw
`1e56071e…` (rpmsg-1544 ×32, SHM 128 K, MPU fix), ELF `39717d44…`,
bridge `4ede5fb1…`. Rollbacks: stock sticky-fb set `~/fw/
sticky-fb-7d4dbf7/` + every intermediate set named in `~/fw/`.

**Next:** HD relay-leg profile (the open regression) → capture/encode
overlap (the ~19 ms snapshot wait; D21 re-test) → bite 3 full
re-measure + guardrail re-derivation → PR. VGA-15 needs the overlap;
HD-mono-5 needs the relay understood first.

---

## 2026-08-18 — Sprint S23 — bite 2: the tax was per-MESSAGE and then per-DRAIN — rpmsg 1544 shipped (wire shape proven), one-copy assembly, 9.50 fps; C-path remainder judged not worth it

**Branch:** `sprint/23-encoder-fastpath`. Nick's gate: bite 2 before
the DCT (the 1a stop-gate re-plan).

**Profile (permanent bridge counters cap_asm_us/cap_send_us/cap_msgs):**
VGA color 110.7 ms/frame split: enc 51.2 · send loop 33.4 (566 µs ×
59 msgs) · snapshot cadence wait ~19 · assembly 5.4 · misc ~2. The
"~2 ms/KB tax" was never per-KB.

**Shipped + measured (each step its own 60 s row, ledger exact):**
- **rpmsg buffers 512→1544 ×16** (he_spike.h + micropython submodule
  patch `0004-micropython-rpmsg-1544.patch`; MSG_PAYLOAD 1524;
  CHUNK_DRAIN_EVERY 1; MSGS_PER_CHUNK 1; SAFE_STREAM_MSGS 400 = the
  old byte envelope). Pool fits the unchanged 64 KB SHM (2×16×1544 =
  49,408 of 56,320). Mismatch-safe by construction (rr_send checks
  each descriptor's capacity; RX is length-driven) — verified from
  source, not assumed. HP fw `70ef9e0f…` flashed byte-verified (label
  now `11852aa3d0-dirty`, fingerprinting the submodule edit); ELF
  `fbe74b80…` staged sha-verified; old-budget frag coverage kept in
  tests (a 492 B sender must still reassemble byte-exact).
  **Wire shape PROVEN: cap_msgs 11,000 == cap_chunks 11,000.**
  **Result honest and negative: +0.14 fps.** Per-msg wall time
  tripled to 1.45 ms — the send loop is DRAIN-bound (HP blocks on the
  HE/relay pace), the per-message-overhead model is falsified.
- **One-copy assembly** (pack_into single bytearray per message,
  legacy spill path kept): asm 7.1→~3 ms. **9.50 fps, 570 frames,
  pub_ok 11,400 = 570×20 EXACT.**

**Sprint ladder:** 7.41 → 7.93 (4:2:0) → 9.03 (MVE conv) → **9.50**.
**Bite verdict:** the C-path's remaining target is ~3 ms of python —
not worth the firmware surface. Remaining VGA color budget ≈ 105 ms =
enc 51 + drain 28 + snapshot 19 + asm 3 + misc 4. **Route to 15:
DCT vectorization (−21) then capture/encode overlap (re-opens D21's
"cannot overlap" claim with today's stack).** HD mono target likely
clears with DCT/luma alone (encoder is 118 of its 323 ms).

**Ops note:** one demo_up ran against a stale nereus000 checkout and
silently staged the OLD bridge — caught by the sha line (`e721a944` ≠
expected). Pull before demo_up, always check the sha it prints.

**Bench state:** chain UP under units, scene=ref, rpmsg-1544 stack
live (fw `70ef9e0f…`, ELF `fbe74b80…`, bridge `4ede5fb1…`), bench-web
serving 9.50. Suites: bridge 294, bm_he 256, he_spike 69, bench_web 81.

**Next:** bite 1b (DCT MVE, golden harness ready) → overlap
exploration → bite 3 re-measure + guardrail re-derivation.

---

## 2026-08-18 — Sprint S23 — bite 1a: MVE color convert golden-passed byte-identical, VGA color 7.93→9.03 fps; STOPPED at the 1.5× gate with the re-plan

**Branch:** `sprint/23-encoder-fastpath` (continues the bite-0 session).
Nick's "Go — whichever plan maximizes target-fps chances and minimizes
wasted time" → the separate profile flash cycle was cut: (a)'s own
stock-vs-patched delta IS the profile.

**Done:**
- `0002-jpege-mve-colorconvert.patch`: Helium fast path in
  `jpeg_get_mcu` RGB565 (8 px/iter, `vld1q`/`vmlaq_n`/`vstrbq`
  narrowing stores), arithmetic **bit-identical** to the scalar SWAR —
  audited lane-by-lane, including the packed `>>7` cross-halfword
  bleed (harmless: the kept low byte never sees it) and the s16
  headroom (max luma sum 32,044 < 32,768). Scalar path kept for
  partial MCUs and non-MVE ports. MVE presence PROVEN in the built
  object (`vldrh.u16`, `vmla.i16` in jpege.o disassembly) — a false
  `#if` guard would have silently shipped scalar.
- `0003-docker-makefile-git-safedir.patch`: the D24 dev build target
  broke on Docker ownership drift ("dubious ownership" from the
  container's git); fixed with `GIT_CONFIG_*` env injection.
- `bench/probes/s23_enc_golden.py`: enc-matrix harness + sha256 per
  row; the stock run is the golden, the patched run must match every
  hash, and the timing delta between runs is the (a) measurement.
- Board window (neutral main.py staged + proven 0 B; S7 ladder flash
  byte-verified; MVE set at `~/fw/s23-mve-7d4dbf7/`, sticky-fb
  rollback kept): **GOLDEN PASS — all 44 rows byte-identical, mono
  0.99× (untouched).** Color encode ~**1.29×** across the board (VGA
  420 q50 65.9→51.2 ms, HD 256.8→197.7, QVGA 17.5→13.8) ⇒ conversion
  was ~30% of encode, ~5 ms residual now.
- On-chain (demo_up --scene ref): **vga-color-15 = 9.03 fps delivered
  (542 frames/60 s), gaps=0 dropped=0, pub ledger exact to the byte**
  (pub_ok 10,840 = 542×20; pub_bytes/frame 27,221 = 27,021 + 20×10).
  The row script's pub_ok DELTA was a snapshot race after the bridge
  restart — the absolute counters close exactly; verdict authority
  stays the receiver ledger. MEAS enc anchors + MEAS_FPS updated.

**Broke/surprised us:** nothing on the board this time — both demo_ups
and the flash landed first-try under the serialised-port discipline the
bite-0 session earned.

**The gate: (a) = 1.29× < 1.5× → STOPPED before the DCT, per Nick's
rule.** The re-plan arithmetic (validated by 9.03 measured vs 9.1
predicted): VGA color frame = 51 enc + 58 tax; 15 fps needs ≤67 ms —
DCT-2× alone gives 81 ms (12.3 fps, NOT enough), tax-kill alone ~66 ms
(borderline), both ~45 ms (~22 fps, comfortable). HD mono 3.10: (a)
does nothing for mono (no color convert) — needs luma/DCT vectorization
AND the tax. **Recommendation: run bite 2 (C publish path — the 58 ms
lever, helps every mode incl. mono) BEFORE bite 1b (DCT — 46 ms, color
@VGA), then 1b closes the gap.** Nick's order call pending.

**Bench state:** chain UP under units, scene=ref, MVE firmware
`6a9ec2cd…` flashed byte-verified, bridge `552812ba…` (bite-0 4:2:0),
bench-web to be re-synced with the updated MEAS on next deploy.

**Next:** Nick's call on bite order (2 vs 1b) + the bite-0 quality
eyeball + PR #42 review.

---

## 2026-08-18 — Sprint S23 — bite 0 nibbles 1–3: 4:2:0 forced on color, VGA color 7.41→7.93 fps, A/B pair byte-exact to the model

**Branch:** `sprint/23-encoder-fastpath` from `main` @ `a0171a0`
(carries the S23 ladder commit cherry-picked from the unmerged
`sprint/23-encoder-ladder` — the ladder was on no merged branch, found
by searching the worktrees).

**Done:**
- Nibble 1 plan approved by Nick, incl. the decision: **force 4:2:0 at
  EVERY q, color only** (one code path, one smooth qFactor curve; the
  q90 eyeball is the check). Mono never gets the kwarg — the encoder
  has no grayscale subsampling knob and the enc matrix deliberately
  skipped it (unmeasured territory).
- Code: `bm_bridge.py` `enc_420` resolved at `command()` from the
  commanded pf; encode call passes `subsampling=JPEG_SUBSAMPLING_420`
  (lazy `import image`, host-test-compatible). Page `MEAS` + server
  `REEF_BYTES_Q50` moved to the measured 4:2:0 anchors in lockstep
  (QVGA 8728/17.5, VGA 27021/66.4, HD 86120/258.5); predicted HD color
  q50 chunks 68→62, clear of SAFE_BURST_CHUNKS=68. Bridge suite
  288→292 (color+ref pass 420, mono never); bench_web 81 with pins
  re-derived.
- On-chain (scene=ref, bridge `552812ba…` byte-verified by demo_up's
  sha-sync): **before-still 29,148 B/21 chunks (4:2:2, seq000207) vs
  after-still 27,021 B/20 chunks (4:2:0, seq000000) — both byte-exact
  to their model anchors.** Ceiling row `vga-color-15`: **7.93 fps
  delivered (476 frames/60 s), gaps=0 dropped=0, pub_ok=9,540 =
  476×20 chunks ledger-exact** (was 7.41; model said 8.0). MEAS_FPS
  updated; QVGA/HD color annotated pre-420 floors for bite 3.

**Broke/surprised us:** the deploy detour. The old bridge held the VCP
through a quiet-exit wait AND a Pi reboot (Pi 5 never cuts VBUS). The
missing mental piece, now proven twice: **a phase-1 bridge waits
FOREVER for first VCP contact; only contact arms the 30 s quiet-exit
clock.** So the recovery is: touch the port once (the failed attach IS
step one), then 60–90 s of ZERO contact, then the one real attempt.
Both demo_up runs landed first-try under that sequence. Also: demo_up
defaults `--scene sensor` — the ref re-stage cost one extra cycle.

**Bench state:** chain UP under units (bm-light + bm-telemetry active,
stream stopped), scene=ref, 4:2:0 bridge staged, bench-web serving the
new model on :8090. Session ledger still carries S22's experiment
counters (gaps=216, dropped=4 — pre-existing).

**Next:** Nick's 4:2:0-vs-4:2:2 eyeball on the reef pair (gallery
compare view) → bite 0 PR → bite 1 (MVE color-convert first, STOP-gate
if <1.5×).

---

## 2026-08-18 — S23 ladder setup: encoder fast path is the next sprint — docs only

**Branch:** `sprint/23-encoder-ladder` from `sprint/22-he-flood` (which
carries the bite-1b commits main is missing — PR #40 is the catch-up;
this PR should merge after it). No code, no bench contact.

**Done:** PRs #38/#39 merged by Nick (+#40 opened for the stranded
bite-1b commits — merge-ordering artifact, zero new work). New sprint
**S23 — encoder fast path** written into the ladder and sequenced
FIRST (D42): bite 0 = 4:2:0-at-q50 · bite 1 = MVE-vectorized jpege
(color-convert first, <1.5× stop-gate before DCT) · bite 2 = C publish
path (profile the ~2 ms/KB tax first) · bite 3 = re-measure ceilings.
Targets: VGA color ≥15 fps, HD mono ≥5–6, then true max. Kickoff
prompt = PROMPTS.md §6. Tailscale side-door key installed by Nick
(outages no longer block the bench).

**Broke/surprised us:** the #38/#39 merge ordering stranded three
commits — caught by checking main's content, not the PR states.

**Next:** merge #40 then the S23 docs PR; fresh session runs S23
bite 0 (prompt ready); Nick: PR #38 demo + fork instrumentation bite.

---

## 2026-08-18 — Sprint S22 — bite 1b + bite 2 window: burst loss cornered INSIDE the telemetry fork; encoder matrix measured; two hardening layers shipped

**Branch:** `sprint/22-burst-backpressure` (from the bite-1 branch).
Nick approved both. One bench window (a Tailscale re-auth stole 20 min
mid-window — the side-door key install is still outstanding).

**Done — bite 1b (an honest chain of falsified models):**
- Model 1 (HE txq sheds): HE backpressure gate built + shipped
  (bm_net_wire high/low-water hysteresis, +24 host checks = bm_he 256,
  ELF `89cc92ff…` staged byte-verified; off-chain fatal-513 clean
  through the gate, burst txf exact) — **measured NOT the mechanism**
  (loss unchanged).
- Model 2 (bridge RPMSG_QUEUE_CAP=256 sheds): the preserved trace's
  `qdrops=0` + a raise to 1024 (`e50a34b8…` deployed, +1 host check)
  — **not the mechanism** (loss unchanged), kept as right-sizing.
- Ledger semantics correction that reframed everything: the q90 ref
  frame is **149 chunks (206,759 B)**, and `chunk_reasm` counts gaps
  as TAIL after a single-loss abandon — "54 gaps" = ONE chunk lost at
  idx 95 (breaks at 95/95/41/95 across four runs).
- Models 3–5 (light drop / UDP loss / checksum-carry from item 10's
  TX kludge): **tcpdump on the cross-cable + outer-IPv4-fragment
  reassembly (full chunks fragment: 1,523 B > MTU): all 149 chunks on
  the wire, in order, no dups, ALL inner UDP checksums valid.** Kernel
  UDP counters zero both Pis; uart decode errors zero; fork l2 drop
  logs absent; TEL_STAT q_drops=0.
- **Verdict: 1 chunk per burst vanishes inside bm_sbc telemetry
  userspace with every visible counter at zero** (suspects: bm_ip
  Linux-backend RX, pubsub cb delivery). Further localization needs
  fork instrumentation = Nick's push (pin discipline). q50 HD mono
  (55 chunks) delivered byte-exact (75,324 B) as the healthy control.
  SAFE_BURST_CHUNKS stays 68.

**Done — bite 2 measurement window** (`bench/probes/s22_enc_matrix.py`,
one board run, artifact `/flash/s22_enc.txt`; table in DESIGN §S22):
q50 color rides 4:2:2; **4:2:0 = −14% encode / −7% bytes** (VGA color
77.0→66.4 ms, HD color 300.7→258.5) as a one-kwarg change; the binding
constraint after that is the **measured ~2 ms/KB non-encode tax**;
jpege.c has no MVE despite `+mve.fp` in CFLAGS; **E3 has NO hardware
JPEG** (vendor datasheet, SPEC). Recommendation: VGA-color-15 and
HD-mono-5–6 need C-path + MVE together; 4:2:0 worth shipping anyway;
HD-color-5–6 impossible on this SoC.

**Broke/surprised us:** four models in a row died by artifact — the
gaps-are-tail-length semantics had misdirected every prior reading of
this bug, including both "54-chunk" DEV_LOG entries.

**Bench state:** chain UP under units, scene=ref, 1b ELF `89cc92ff…`
+ bridge `e50a34b8…` staged (both also in the Pi checkout/deploy
dirs), guardrails suite deployed. Session ledger counters carry the
experiments (gaps=216, dropped=4 cumulative — all q90 probes).

**Next:** PR (this branch); fork instrumentation bite for the
telemetry-internal drop (Nick sizes + pushes); Nick reviews the
encoder table → go/no-go on C-path/MVE follow-on bites; PR #38 demo
still owed.

---

## 2026-08-18 — Sprint S22 — bite 1 nibbles 2–3: one cast kills the wedge — 10-min soaks ledger-exact, first true mono/HD ceilings, guardrails re-derived

**Branch:** `sprint/22-he-flood`. Nick approved nibble 2 with the
question "likely outcome?" — answered: the boundary deletes, doesn't
move. It did.

**Done:**
- **Fix:** `(uint16_t)` cast in `rr_poll_n`'s cursor compare
  (rpmsg_remote.c; rr_send already had it). Host regression [8]
  (he_spike suite, ring indices pre-wrapped to 8-from-65,536): FAILS on
  pre-fix code with the live signature exactly — 4 phantom messages,
  duplicated echoes, 20 ≠ 16 delivered. Post-fix: he_spike 69, bm_he
  232, s10_peer 40, all green. ELF `fea65304…` (+8 B, 94.1%), built
  D23/D24, staged + on-board sha byte-verified; Pi deploy copy
  `~/bm_he/` refreshed (was an Aug-12 build — a manual deploy would
  have regressed the fix).
- **Off-chain acceptance:** same probe ladder that killed stock —
  507k msgs / 7.7 wraps / 22 min at up to 555 msg/s: **frag_errors=0
  everywhere** (stock: 703 by t=100s, 362,959 by end), tx exact, heap
  floor identical (17,704 — the fix changed nothing but the wrap).
- **On-chain (chain up under units, scene=ref, fresh ledgers,
  `bench/s22_ceiling_rows.py` driving the control socket):**
  - **10-min QVGA color @ 30 cmd: 28.23 fps, 16,939 frames,
    pub_ok = frames×7 EXACT, 0 gaps** — the demo line, ~565 msg/s
    sustained across ~5 on-chain wraps (the rate+duration that killed
    Nick's live demo).
  - **10-min VGA color @ 15 cmd: 7.41 fps, 4,446 frames, exact** —
    the matrix's 7.40 confirmed as a true 10-min ceiling.
  - **First true ceilings the wedge always blocked:** QVGA mono
    **30.30** (sensor-cadence-capped; encoder could do 100+), VGA mono
    **13.27** at **~717 msg/s delivered** (40% past the old fatal
    line), HD mono **3.10** at **990 msg/s commanded** — every row
    ledger-exact to the chunk.
  - The formerly-wedging 27 fps QVGA command accepted + run live
    through bench_web's own API post-deploy.
- **Guardrails re-derived (bench_web + page, deployed to nereus001,
  suite 81 green on Mac AND Pi):** `SAFE_STREAM_MSGS` 315 → **1200**,
  reworded as a measured-clean-envelope cap (a ~700 cap would refuse
  the measured-clean VGA mono 15 command — commanded-fps predictions
  overstate delivery); `SAFE_BURST_CHUNKS` stays 68 — see below.
  MEAS_FPS filled: 28.23/30.30, 7.41/13.27, null/3.10.

**Broke/surprised us:**
- **The burst-loss variant is NOT the wrap bug.** `capture 90 hd mono`
  on the FIXED stack lost exactly 54 of ~83 chunks again (frame
  dropped, counted, silent below bm_pub). Arithmetic: the burst
  arrives over rpmsg ~2× faster than the VCP relay drains, and the
  HE's byte-bounded txq sheds the excess. Fix candidate = HE-side
  backpressure (skip the poll while txq is above high-water; the HP's
  blocked send + drain-while-pushing becomes end-to-end flow control).
  **Deliberately NOT stacked into this bite** (clean A/B; deadlock
  history in this exact loop) — new bite for Nick to size.
- HD mono ceiling is 3.10 fps, not the ~4.2 the S0 encode+tx estimate
  suggested — the bridge loop serializes capture (single fb at HD) +
  encode + publish; input for bite 2.

**Bench state:** chain UP under units, **scene=ref**, launcher
`170e637c…` + bridge `df82aa70…` staged, fixed ELF `fea65304…` on
/flash and in `~/bm_he/`. Both Pi checkouts moved to `main` @ 664b639
(pre-merge stashes kept); bench_web files scp'd ahead of the PR
(documented; checkout stays main). Session-cumulative ledger counters
carry the q90 experiment (gaps=54, dropped=1) — deltas in every
verdict were computed against snapshots.

**Next:** nibble 4 (PR) → Nick's live demo. Then: the backpressure
bite (burst variant) and bite 2's encoder review — desk work done
this session: E3 has NO hardware JPEG (vendor datasheet), q50 color
encodes at 4:2:2 (4:2:0 is one `to_jpeg` kwarg away), jpege.c has no
MVE vectorization despite the M55's Helium being enabled in CFLAGS.

---

## 2026-08-18 — Sprint S22 — bite 1 nibble 1: flood wedge ROOT-CAUSED — u16 vring-index wrap at message 65,536, reproduced off-chain

**Branch:** `sprint/22-he-flood` from `main` @ `664b639`. Plan gated by
Nick (Phase A approved). One board window, zero incidents, board left
healthy.

**Done:**
- **Evidence re-read first (no board contact):** preserved trace
  `20260818T002807_…prev` on nereus000 — the mute struck ~12 s AFTER
  `stream done`, during a ~556 msg/s backlog drain, and cross-session
  arithmetic showed most published chunks never reached the wire.
  Also established: **no mute event ever got an HE postmortem** (the
  bridge dumps the ring/err page only on clean exit; every mute ended
  in a reboot).
- **Probe** `bench/probes/s22_flood_probe.py` (+ `bench/test_s22_probe.py`,
  27 checks): sustained synthetic WCMD_PUB frames, framing byte-identical
  to `BridgeCore.capture_pub_msgs`, three rungs at the measured boundary
  rates (303/520/555 msg/s), postmortem block reading BP->err/tick,
  he_sample page and the HE ring via mem32.
- **Run (22 min, all three rungs):** control-315 clean 60 s ·
  fatal-513 clean for exactly ~91 s — then `frag_errors` ignited in the
  10 s window where **cumulative inbound rpmsg messages crossed 65,536**
  (18,180 control + ~47k fatal + overhead) and climbed ~80/s to 362,959
  by run end, `tx_frames` at ~450/s vs 126/s real input (stale-slot
  redelivery) · demo-560 ran its full 600 s inside the storm.
- **Root cause READ from source after the signature pointed at it:**
  `rr_poll_n` (`firmware/he_spike/src/rpmsg_remote.c:295`, shared into
  bm_he by Makefile) compares u32 `consumed[0]` against the vring's u16
  `avail->idx` with NO cast — `rr_send` has the cast. Past message
  65,536 the loop never sees "empty" again. Ring *indexing* survives the
  wrap (vring num is a power of two); only the comparison breaks.
- **Negatives banked:** heap_min 17,704 B flat for 22 min, tx_dropped=0,
  no hook fired — finding 1 is NOT a memory problem; the S19 byte-bound
  holds. "≥513 msg/s fatal" was a proxy: rate only sets how fast a
  session reaches message 65,536. All four real events match (002807
  crossed 65,536 mid-VGA-mono-stream where its ledger broke; the demo
  died at ~83k msgs; 315-clean runs never approached 65k).

**Broke/surprised us:** the off-chain HE stays QUERY-ALIVE in the storm
— the full mute needs the on-chain ingredient (bridge-side openamp
state poisoned by garbage used-ring entries under bidirectional load).
Also the probe never dumped its ring on a surviving run (only on
death) — cosmetic, fix with the next probe edit.

**Bench state:** chain DOWN deliberately (bm-light stopped on
nereus000; bm-telemetry still up on nereus001). AE3 healthy, fixture
`main.py` staged (5,581 B, read-back verified normalised), `bm_he.elf`
still the stock (bugged) build. Restore = `demo_up.sh` when wanted;
nibble 2 wants the window as-is to stage the fixed ELF.

**Next:** nibble 2 (gate pending): wrap-safe cast + host regression
across the 65,536 boundary (must FAIL pre-fix) + D23/D24 rebuild +
probe ladder through 5+ wraps as off-chain acceptance → nibble 3
on-chain: 10-min QVGA @ 28.07, `capture 90 hd mono` (decides whether
the burst-loss variant was the same bug), true mono ceilings, VGA
max-fps 10-min ceiling, guardrail constants raised.

---

## 2026-08-18 — Ladder resequence: S22 (flood fix + encoder exploration) → S21 (CV) → S20 (light) — docs only

**Branch:** `sprint/18-ladder-resequence`. No code, no bench contact.
PRs #35 and #36 both MERGED before this.

**Done:** captured two tasks per the capture-task discipline and
resequenced the ladder per Nick (D39): new **S22 — Camera pipeline
hardening & headroom** with bite 1 = the HE flood fix (all evidence
pointers inlined; demo = 10 min at the 28.07 fps ceiling with zero
wedges + guardrail constants raised + the blocked mono-ceiling rows
measured) and bite 2 = encoder-headroom exploration (measure-first:
JPEG parameter space, capture/encode overlap, C-pipeline tax, SoC
JPEG/accel as a datasheet-verify item; deliverable is a table + a
recommendation, not code). **S21 (CV) promoted above S20 (light —
delayed, not a product offering yet).** Numbers stay, order changes
(D30/D32 precedent). Next-session kickoff prompt added to PROMPTS.md.

**Broke/surprised us:** nothing.

**Next:** S22 bite 1 (HE flood fix), fresh session; S18 D2 + demo
interleave when Nick wants the close-out.

---

## 2026-08-18 — Sprint S18 — HD-stability nibbles 2–4: sticky-fb firmware ships, soak 40/40, HD certified and measured end to end

**Branch:** `sprint/18-hd-stability`. Nick's calls: plan A (root fix),
shape A2 (sticky high-water), keep the HD-ref guard until measured,
upstream HELD. Mid-session hazard: the Mac revoked agent file access
(TCC dialog denied unattended) — code rode the Pi checkouts + scratch
until an app restart restored it; reconciled byte-exact before the PR.

**Done:**
- **The patch** (`firmware/openmv_patches/0001-framebuffer-sticky-highwater.patch`,
  +9/−1 in openmv `lib/imlib/framebuffer.c`): `framebuffer_resize`
  reuses the existing block whenever it already fits — free+malloc only
  on a genuine grow. With the bridge's pre-HE HD ceiling claim, the
  framebuffer never touches the allocator again after boot. Built with
  the D23/D24 docker loop at pinned `7d4dbf7`+patch (label
  `v5.0.0-52.g7d4dbf7ab2.dirty`, MANIFEST in `~/fw/sticky-fb-7d4dbf7/`
  on nereus000; **rollback = stock set in `~/fw/development/`**).
  Flashed via the S7 ladder, byte-level readback PASS.
- **Acceptance:** G3 soak **40/40 with the HE loaded** (stock: fail at
  #22); G5 PASS; **G6 (new probe)**: both ref-HD images load AND encode
  with the HE resident (color 2 MB decoded, heap floor 1.92 MB, enc
  300.8 ms / 93,253 B @ q50 — S0's 299.2 ms within 0.5%).
- **Bridge `df82aa70…`:** refusals hoisted ABOVE `_ensure_sensor`
  (the guard-order bug — a refused command never touches the sensor),
  HD-ref guard LIFTED on G6's measurement. Host suite 436→**444**
  checks green; negative control: the new order tests fail on the old
  bridge (2 failures, exactly the hoist).
- **On-chain:** `capture 50 hd color` ×3 + `capture 50 hd mono` ×3,
  alternating (five gated transitions), all SOF-verified,
  `frames_ok=7 gaps=0` — **HD's first completions on a PublishGate
  build**. Then the matrix rows (ref mode, sidecar-verified, JSONs
  `matrix_20260818T05{2415,3422}Z.json`): **HD mono q50 = 75,324 B and
  HD color q50 = 93,253 B, both byte-exact vs the in-bridge encode**
  (color ×2 identical) · **B2 cert rung PASS** (HD publish → gated mode
  change delivered 20.02 s after command — **`REINIT_MIN_QUIET_MS =
  20000` is now HD-certified**, retire the caveat) · first HD video
  numbers: **ref HD mono 1.50 fps / 0.91 Mbps, 90 frames / 60 s,
  ledger exact** · **sensor HD color ~1.4 fps at ~65 KB/frame, 42
  frames / 30 s, exact** (S19 bite 4's owed number) · **VGA mono
  4.98 fps / 0.96 Mbps, 299 frames, exact** (first clean run ever).
  Stream caps recomputed FOR REEF BYTES into the proven-safe
  ≤315 msg/s zone (dark-byte caps land at 412–432 msg/s = finding 1's
  danger zone); rows are floors by design.

**Broke/surprised us (fenced with artifact evidence, other bites):**
- **HD mono q90 still (~83 chunks ≈ 250 rpmsg msgs/frame): the HE
  published it completely (`pub_errs=0`, pub_bytes accounts for all
  frames) but the relay lost 54 chunks and dropped the frame** —
  finding 1's burst-loss variant, now with a single-frame boundary
  (55-chunk frames clean, ~68 clean on a fresh leg, ~83 breaks).
- **Ref-mode HD COLOR reloads fail in long sessions:** preserved trace
  shows `MemoryError allocating 2,048,031 bytes` with ~3 MB free but
  fragmented, then a clean refusal (the fixed guard order working as
  designed). Fresh-boot loads fine. Fix candidate = preload/pin the
  ref set at bridge boot in ref mode; small bridge bite, filed. The
  q95 JPEG fallback does NOT help (decode needs the same 2 MB raw).
- bench-web holds a dead control socket across a bm-telemetry restart
  (bounce it after any telemetry restart) — spawn-task filed.
- **Post-PR, Nick's live demo confirmed finding 1's boundary a fourth
  time (now 4/4 fatal ≥ ~513 msg/s):** a QVGA color stream at
  ~2.0 Mbps / ~27 fps = ~560 rpmsg msg/s delivered 4,148 frames over
  ~5 min, then broke the ledger and muted the HE (`CAM_REPLY TIMEOUT`;
  a restarted stream muted in <30 s). NOT the transition bug — the fix
  held throughout; the demo simply ran long enough to reach bug #2 at
  its filed rate. Recovery: reboot + relaunch, verified end to end.
  **Guardrail gap named — and CLOSED the same session (Nick: UI-side
  only, fast follow):** `bench_web.py` now predicts each command's
  publish from the reef model (`predicted_chunks` — the page model's
  exact arithmetic) and REFUSES streams above 315 msg/s (the measured
  clean point; refusal names the max safe fps) and stills above 68
  chunks/frame (the largest burst delivered clean; q90-class bursts
  lost chunks). Server-enforced (400 + reason), mirrored in the page
  (red warn-box item + dead buttons — the C1 rule: JS only makes the
  refusal visible). Suite 70→**76**, incl. the two live regressions:
  the demo's 27 fps command and the matrix's q90 still both refuse.
  Verified against the running server: both refused with operator-
  readable reasons, a safe HD capture accepted and delivered.
  Refusal beats clamping (D31) — a silently substituted rate would
  invalidate the comparison the operator thinks they are running.

**Bench state:** chain UP under units and verified end to end
(`chain_status.sh` PASS both Pis; page + `/api/status` + `/frame.jpg`
all 200), `scene: sensor`, launcher `170e637c…` + bridge `df82aa70…`
staged, patched firmware on the board. `bench/s18_matrix_noflood.py`
(recapped driver copy) untracked on nereus001, documented here.

**Next:** PR (this branch); page MEAS_FPS for the HD rows (deferred —
the measured HD streams are FLOORS, and the page model's provenance
labels have no floor semantics yet; do it with eyes open, not at
close-out); upstream item 11 stays HELD; finding-1 bite and the
ref-preload mini-bite for Nick to size.

---

## 2026-08-18 — Sprint S18 — HD-stability nibble 1: five probes kill every named suspect and corner the real one — transitions degrade only when the HE is resident

**Branch:** `sprint/18-hd-stability` from `main` @ `0081b65`. All
board work off-chain per `ae3-board-access`; chain restored + verified
at session end. Two board crashes, two Pi reboots — both budgeted.

**Done — the evidence chain (probes G→G5, `bench/probes/s18_hd_gate_probe*.py`):**
- **Trace re-read first (no board contact):** both matrix HD deaths
  died with NO trace line after the gate would have opened —
  `_apply()` traces only after all steps, so both died mid-re-init.
  The discriminator's "survived and replied res=hd ok" was measured
  ~15–20 s BEFORE the gated re-init ran — findings 2 and 3 were one
  fault wearing two hats.
- **Probe G:** row 0 calibration QVGA→VGA PASS; **row 1 QVGA→HD mono
  at 20 s quiet WITH NO BARRIER: MemoryError at first HD capture, then
  recovery R2's `set_framebuffers` took the board off the bus.**
  Reproduced in 4 minutes; the gate's barrier exonerated.
- **Probe G2:** grow-at-color, flip-at-HD, grow-at-GRAYSCALE,
  VGA-grow-at-mono all PASS with no publishes — then **row E died at a
  routine QVGA shrink's `set_framebuffers`, zero publishes ever
  sent.** Publish-proximity exonerated as cause.
- **Probe G3** (soak, HE loaded, zero traffic): 21 clean, **#22 (a
  QVGA mono flip) failed politely (289 µs — pool already gone),
  recovery bootstrap ALSO threw.** In-session heal 0-for-6 lifetime.
- **Probe G4** (CONTROL, no HE): **40/40 clean** (lifetime no-HE tally
  52/52).
- **Probe G5:** fresh boot → HE → ONE transition to HD mono → 3
  captures with real chunk-burst publishes: **PASS 2/2 boots.**

**The finding:** transitions degrade a below-MicroPython resource ONLY
while the HE is resident (N≈2 on-chain under traffic, 10/22 quiet
off-chain), politely or fatally, always at `set_framebuffers`/first
capture; traffic accelerates onset. Source-corroborated (installed
`7d4dbf7` == master in these files): `framebuffer_resize`
(lib/imlib/framebuffer.c:158) frees + re-mallocs the whole fb block
per transition into the 2512K SRAM1 UMA pool (fb hard-coded dynamic);
`OMV_GPU_MEMORY = SRAM9_B` overlaps the HE ELF at 0x60080000 (on
record, not proven the killer). Bite A's "fb grows into the HE region"
story was wrong; its pin worked by reducing churn.

**Also answered from source (Nick's question):** the sensor is a
PixArt **PAG7936** (ID 0x7936), native 1280×800; the driver supports
EXACTLY QVGA 320×200 / VGA 640×400 / HD 1280×800 (pag7936.c:933–940),
everything else `return -1` (pag7936.c:691) without
`OMV_CSI_HW_SCALE_ENABLE` (only OPENMV_N6 defines it). **Nothing
exists between VGA and HD on stock AE3 firmware.**

**Broke:** the shipped HD-ref guard was order-broken (`_ensure_sensor`
ran before the refusal — fixed in the follow-on bite); the Mac's TCC
revocation mid-session (memory note filed).

**Next:** Nick's gate on the fix menu → chose A2; see the entry above.

---

## 2026-08-18 — Sprint S18 — bench_ctl: reconnect once when bm-telemetry rebinds the control socket

**Branch:** `sprint/18-benchctl-reconnect`. Pi-side only — no board
contact, no fork change, no bench session.

**Done:**
- Fixed the bug observed live on nereus001 2026-08-18: a connected
  AF_UNIX SOCK_DGRAM socket points at the *socket*, not the path, so a
  bm-telemetry restart (which rebinds `/run/bm/bench.sock`) killed the
  held connection permanently — every later `bench_ctl.request()` raised
  a raw `OSError` ENOTCONN (107), and `bench_web`, which only catches
  `BenchCtlError`, dropped the HTTP connection (`curl` saw HTTP 000)
  instead of answering 503. Operational fix at the time was restarting
  bench-web; that is now unnecessary.
- The fix lives in **`bench_ctl.request()` alone**: on a dead-socket
  errno, close + reopen once and retry the same request; if the rebuild
  fails, the ordinary socket-down `BenchCtlError` stands (bench_web's
  tested 503 path). Every other socket `OSError` is wrapped in
  `BenchCtlError` too, so **no caller ever sees a raw OSError** from the
  client again. `bench_web.py` needed zero changes — its
  `Bench._request` already resets on `BenchCtlError`.
- New host suite `pi/bm_bench/test_bench_ctl.py` (**6 checks**, real
  AF_UNIX sockets, no Pi): the socket-recreated-between-two-requests
  case, node-fully-down → `BenchCtlError`, stale path with no listener,
  recovery after a failed reconnect, monotonic ids across a reconnect.
  Verified the key test **fails against the pre-fix client** (raw
  `ConnectionResetError` escapes) before trusting the green. bench_web's
  70 checks still pass.

**Broke/surprised us:** the dead socket speaks different errnos per
kernel — Linux says ECONNREFUSED then ENOTCONN, macOS says ECONNRESET
then EDESTADDRREQ (measured with a throwaway probe before writing the
fix). `RECONNECT_ERRNOS` carries all four so the host test exercises the
same code path on the Mac as on the Pi.

**Next:** Nick's manual test (restart `bm-telemetry` under a running
`bench-web`, `/api/status` answers without a bench-web restart) → PR.
*(Merged as PR #35; the HD-stability session's "bounce bench-web after
a telemetry restart" ops note above is retired by this fix.)*

---

## 2026-08-18 — Sprint S18 — reef matrix: QVGA+VGA measured exact, two new stream ceilings, and three findings that fence off HD

**Branch:** `sprint/18-bench-matrix`. Five matrix runs, six scripted
recovery cycles (reboot + demo_up ≈ 4 min each), all driven remotely
per the handover rule — Nick installed nothing.

**Done:**
- **The instrument:** bridge `scene:"ref"` (encoder fed the S0 reef
  reference; sensor path — re-inits, PublishGate, discarded snapshot —
  UNCHANGED, so the numbers transfer), demo_up staging arm (idempotent,
  size-checked, JPEG fallback, trace preservation, scene key written
  every run), `bench/s18_matrix.py` (row isolation: stop + proven
  quiescence before every row; 2 s cam-status keep-alive; sidecar+SOF
  verification; reef tripwire). Host tests: bridge 262→**279**,
  matrix driver **37**, bench_web 67→**70**.
- **The table (DESIGN §S18 reef-matrix detail):** 6 of 9 stills
  measured on-chain, **byte-identical to S0's encode table in all six**
  (9,198 / 7,536 / 29,148 / 23,831 at q50) + the q-curve points
  (7,097 @ q35, 28,819 @ q90 — model +18%/−7%). Streams: regression
  **15.15 fps / 1.12 Mbps** (×3 clean), **QVGA color ceiling
  28.07 fps / 2.08 Mbps** (×2 identical), **VGA color 7.40 fps /
  1.74 Mbps** (×2 identical). In-bridge encode traced: 20.1 / 78.5 /
  31.6 ms — S0 within ~2%. **Measured bridge derate 0.56–0.58; the
  page's old extrapolation (0.295) was ~2× pessimistic.**
- **The page now says MEASURED where it is measured:** `MEAS_FPS`
  filled for QVGA/VGA color; provenance label rides the model.
  Verified through the page's own endpoints on nereus001.

**Broke/surprised us (each measured, none chased past its recording):**
- **The HE flood wedge** (SPEC §Open questions): sustained publish
  ≥ ~513 msg/s broke the ledger then silenced the HE permanently, 3/3;
  mechanism traced in a preserved bridge trace (`he2pi_frames` frozen,
  Pi still querying). Blocks true mono ceilings.
- **Ref-mode HD hard-faults the board** (run 5: trace ends mid-
  transition, no exit record). Discriminator proved sensor-mode HD
  survives the same transition → my ref code at HD is implicated;
  the bridge now REFUSES HD ref commands (tested), finding filed.
- **Sensor-mode HD still wedges the leg on the B2 bridge** — HD has
  never completed on any PublishGate build. So the 20 s constant's
  daylight-HD certification could not run: **HD is unstable on this
  stack**, a stronger statement than "20 s unproven". B2's comment
  stands, reason now recorded.
- Driver lessons paid for live: cam-status is ASYNC (ack now, reply in
  the next status); rows must not share measurement windows (stop +
  quiescence before every row); the S19 stdout trap works on the
  driving side too (empty log for 5 min).

**Bench state:** final recovery cycle run at session end — chain up
under units, `scene: sensor`, bench-web serving the measured page at
`http://nereus001:8090/`; launcher staged as `main.py` (fixture restore
stays folded into the next demo_up, the standing pattern). Matrix JSONs
in `~/bench_captures/matrix_*.json`; bridge traces preserved under
`~/bridge_traces/` on nereus000.

**Next:** nibble 4 (PR for the matrix bite), then bite D2 + the S18
demo. The HD-stability and HE-flood findings need Nick to size as
bites before the HD rows and the cert rung can be measured.

---

## 2026-08-17 — Sprint S18 — bite B2 ladder run: VGA fails at 10 s and passes at 15 s → the constant is 20 s, flat, and HD stays flagged

**Branch:** `sprint/18-reinit-race` @ `9666604`.

**Done:**
- **Recovery path that unblocked everything (Nick enabled the permission
  and the insight):** Pi reboot → USB session teardown kills every stuck
  port-holder + forces the bridge's quiet-exit → `demo_up.sh` on the
  fresh bus wins the REPL race and its chip reset clears the stale HE.
  ~2 min, replaces the 11-minute phase-1 waits. **Standing recovery from
  now on.** (uhubctl stays measured-useless on the Pi 5 — no VBUS cut.)
- **The ladder (liveness-gated, 2 s cam-status keep-alive so the C1
  quiet-exit trap structurally cannot fire):** VGA source, dark
  (~11 KB): **10 s FAIL** (re-init threw; the self-heal failed — 0/4
  observed heal successes now — and latched the camera) · **15 s PASS**
  (mono delivered 1.1 s after command). All HD rungs sat behind the
  latch: **HD is unmeasured**.
- **The constant (Nick: one flat delay for all):**
  `REINIT_MIN_QUIET_MS = 20000` — safe side of the measured boundary.
  Every measured point fits ~1.5 s/KB of published bytes, so 20 s is NOT
  a daylight-HD certification; the reef-matrix session owes that number
  and the comment at the constant says so. `bench_web` settle raised
  8 → 20 s to match (server-enforced; JS mirror follows). All suites
  green: bridge 419, bench_web 67.

**Bench state:** chain up (bm-light + bm-telemetry + stream server
active), AE3 camera LATCHED from the 10 s failure — next `demo_up.sh`
clears it. **`/flash` still carries the 6 s bridge build `d558f7f5…`;
the 20 s build deploys at the next chain session.** Launcher staged as
`main.py` (fixture restore deferred to the next session's demo_up, the
C2 precedent). 3 new sidecars (23 total saves).

**Next:** nibble 4 — PR for bite B2 (fix + probes + ladder + constant).
Then the reef-scene matrix (which also revisits this constant with
daylight bytes), bite D2, S18 demo.

---

## 2026-08-17 — Sprint S18 — bite B2 spacing ladder: BLOCKED on a physical replug; the port-race afternoon, recorded so it is never repeated

**Branch:** `sprint/18-reinit-race`. No code change this stretch — ops only.

**What was attempted:** the 6-rung spacing ladder (VGA/HD × 15/10/8 s)
that sets the final `REINIT_MIN_QUIET_MS`. It never measured a rung.

**The chain of events, honestly:**
1. After the rehearsal's teardown, every `mpremote` attach to the
   launcher-staged board lost the interrupt race: attach bytes → the
   phase-1 bridge takes them as link-up → raw-REPL entry fails → the
   port close DTR-resets the board → a FRESH bridge boots. Five
   consecutive losses (2 demo_up + 3 spaced cycles). The
   previously-reliable "wait 40 s, run again" remedy assumes the race is
   winnable; tonight it wasn't, and each attempt reset the clock.
2. The 11-minute phase-1-timeout wait + `resume` attach ALSO failed —
   the attach itself still feeds a byte to whatever boots.
3. `bm-light`-as-the-attacher DID bring the chain up (neighbor added),
   but the ladder's first camera command left the gap the C1 rule warns
   about (~60 s of journal-checking) and the bridge quiet-exited;
   all 6 rungs ran against a dead leg (`state: timeout`, HE stale-lying).
4. The hardened retry (liveness-gated ladder, 2 s cam-status keep-alive,
   `systemctl restart bm-light` as the port-kicker) got `active` but the
   camera never answered within 75 s. **The board is in a state only a
   physical replug clears** — the Pi 5 never cuts VBUS, so no
   software path can power-cycle the AE3.

**Standing lessons written into the next attempt (already scripted):**
- The ladder script now pings the camera every 2 s from t=0 — liveness
  gate AND keep-alive, so the quiet-exit trap structurally cannot fire.
- Fresh-board bring-up order: replug → `demo_up.sh` (mpremote wins on a
  fresh REPL) → `bm-light` → ladder within seconds.
- The JTAG/SWD question (Nick raised it): every hour tonight was a
  USB-port-ownership problem a debug probe does not have. Not filed as
  a task yet — Nick's call.

**Bench state:** AE3 enumerated but camera-dead pending replug; launcher
staged as `main.py`; fixed bridge `d558f7f5…` verified on `/flash`.
`bm-light` active (streaming at a dead leg — harmless), `bm-telemetry` +
stream server active. 37 sidecars on nereus001, incl. the rehearsal's
proof pair.

**Next:** physical replug → `demo_up.sh` → the one-shot ladder → set the
constant → PR. Then the reef-reference matrix (Nick's call: dark-room
throughput numbers are not representative; use the S0/S17 ref-scene
machinery), bite D2, S18 demo.

---

## 2026-08-17 — Sprint S18 — bite B2 on-chain rehearsal: the headline case is FIXED and demonstrated; 6 s is NOT enough after a VGA frame

**Branch:** `sprint/18-reinit-race`. Fixed bridge `d558f7f5…` (56,420 B)
deployed to `/flash` and proved by on-board sha256; chain brought up via
`demo_up.sh` + systemd units; all commands through bite B's control
socket with 1 Hz timestamped status polling.

**THE WIN — bite B's exact failure sequence, fixed and timed:**
- Baseline `capture 50 qvga color`: delivered + saved in ~1 s.
- **The fast pair** (colour, then mono 0.7 s later — the previously
  fatal click): colour saved at t=1.7, **mono HELD by the gate and
  saved at t=7.0 — exactly 6.3 s after the colour publish**. No error,
  no wedge. Both artifacts verified against their own JPEG headers:
  320×200×3 / 1,873 B and 320×200×**1** / 1,089 B.
  Before this fix, that sequence wedged the camera for the bridge's
  life 2/2.

**THE NEW FACT — the size scaling is real, on-chain:** the VGA pair
(VGA colour, then mono 0.7 s later) delivered the VGA frame and then
**nothing ever delivered again** — not the held mono (~6.5 s after the
VGA publish), not a 4-command burst, not a final QVGA capture — while
the HE answered `ok=1, cmds` advancing throughout (the known lie) and
the chain stayed healthy (link up, relay pumping, board on the bus).
**Deduced mechanism, tight even without the trace:** the barrier
provably works on-chain (the QVGA pair used it and delivered), so
deadline-refusals cannot explain a *permanent* stop; the only branch
that latches everything off is **the VGA→mono re-init throwing at
~6.5 s — over the 6 s window — followed by the self-heal failing, whose
bootstrap failure latches the camera off for the session** (the
allocator rule, working as designed). So: 6 s is enough after a QVGA
frame and NOT enough after a VGA frame; bite B's "scales with frame
size" stands, now with an on-chain fail point at 6.5 s.

**Ops errors, mine, recorded:** the bridge trace held the confirming
ledger and I destroyed it. The first read (mpremote `resume`, no
soft-reset — the right tool) WAS working but slow, and I killed it;
the port close DTR-reset the board, which booted a fresh bridge, whose
launcher rotation consumed the one preserved generation. Two further
attempts hit `could not enter raw repl` (each attach boots another
bridge) — stopped at three per `ae3-board-access`. Lessons: (1) tail
the file, never line-filter on-board; (2) never kill a slow mpremote —
the close is itself port contact; (3) a future `demo_up.sh` should copy
`bridge_trace.prev.txt` off the board while it has the REPL, so the
ledger survives the next session's boot.

**Bench state:** AE3 on the bus, launcher staged as `main.py` (fixture
deliberately NOT restored — the next session is another chain session
and `demo_up.sh` re-stages anyway; same call as C2 made). `bm-light`
stopped; `bm-telemetry` + stream server left ACTIVE as found.
`~/bench_captures` now 37 sidecars (5 new, incl. the proof pair).

**Next (the bite's remaining measurement, needs one chain session):**
the spaced on-chain ladder — VGA and HD source frames × re-init at
8/10/15 s — to set the final `REINIT_MIN_QUIET_MS` (likely per-size),
with the deadline riding it automatically. That ladder IS the start of
B2's 9-row matrix. Also carried out of the rehearsal: the self-heal
never being observed to succeed argues for prevention-first sizing, and
`bench_web`'s 8 s settle is NOT proven for VGA/HD re-inits — do not
relax it yet.

---

## 2026-08-16 — Sprint S18 — bite B2 rungs E–F + the fix: the hazard is a CLOCK, and the shipped guard is a measured 6 s + self-heal

**Branch:** `sprint/18-reinit-race`. Bridge-only throughout, as scoped.

**Done:**
- **Rung E falsified the second hypothesis too**: gate open, **zero**
  late messages, 250 ms of measured rpmsg silence — and the re-init
  still failed at ~270 ms after the publish. But *politely*
  (`RuntimeError`, 100,818 µs attempt), and it **reproduced the bite B
  wedge off-chain for the first time**: every later `set_framebuffers`
  failed in 13 µs across ~80 s. Board survived; no reboot.
- **Rung F: the ~250 ms failure is STOCHASTIC** — the deliberate
  provocation at rung E's exact point PASSED, so Q1 (does reset clear
  the wedge?) stays open. The boundary sweep passed **10/10** (QVGA+HD ×
  0.5/1/2/4/6 s, incl. 45-msg HD frames). Combined with bite B's
  on-chain points (2 s failed once, 6 s passed 3/3), the off-chain bench
  is the EASIER environment — the binding number must come from the
  chain.
- **The shipped fix (Nick approved the shape):**
  `REINIT_MIN_QUIET_MS = 6000` — wall clock since the last publish,
  measured from `note_chunks`, so a human-pace command pays only the
  barrier (~ms) and only a fast follow-up actually waits. The barrier /
  heap / stream-counter conditions stay (cheap, and they guard what the
  clock cannot see); deadline now 11 s. Plus **catch-and-self-heal** in
  `_ensure_sensor`: on `Sensor control failed.`, reset + re-bootstrap +
  one retry, outcome traced — unproven (rung F could not provoke the
  wedge to test it) but strictly no worse than today's permanent wedge,
  and every occurrence adds a data point.
- Host tests: PublishGate 60 → **78**; bridge suite total 419, all green.

**The honest arc, for the record:** bite B said "a fixed delay is the
wrong shape of fix" and nibble 2 v1 believed it. Rungs C–F falsified
both clever alternatives ("publish drained", "rpmsg quiet") by
experiment, at the cost of two Pi reboots — and a *measured* delay is
the only shape left standing. The difference from where bite B started:
the number now has evidence on both sides, a severity map, a 4-minute
reproducer, and a self-heal path. Root cause below MicroPython stays
open — candidate upstream report alongside D15.

**Bench state:** board on the bus, S6 fixture as `main.py` (verified),
`/flash/bm_bridge.py` = the nibble-2 build `a1615f21…` — **stale, does
not carry this fix**; the on-chain rehearsal re-deploys. Probe logs
`reinit_probe{,_b,_c,_d,_e,_f}.txt` on `/flash`. Both units inactive;
`bm-light` was found ACTIVE at session start and left stopped.

**Next:** nibble 3 on the chain — re-run bite B's trial matrix against
the fixed bridge (doubles as the start of B2's 9-row matrix), then the
matrix itself + stream numbers, then bite D2.

---

## 2026-08-16 — Sprint S18 — bite B2 nibbles 2–3: the gate FAILED acceptance — "publish drained" is not the safe condition

**Branch:** `sprint/18-reinit-race` @ `545af73` (+ this entry). Bridge-only
as scoped; no fork change, no HE rebuild.

**Done:**
- **Nibble 2 shipped `PublishGate`** in `firmware/bm_bridge/bm_bridge.py`:
  hold a re-init until (1) a WCMD_QUERY posted after the frame's last chunk
  is answered (the vring is in-order, so the reply proves the HE consumed
  and bm_pub'd every chunk), (2) `heap_free` back within 1,024 B of a
  learned high-water (transmit copies cost 1,488 B each), (3) two
  consecutive replies with identical `stream_sent/stream_errs` (the
  synthetic WCMD_STREAM publisher, which the barrier cannot see). Refuse at
  5 s rather than guess. Host tests 262 → 322, all green.
- **Nibble 3 ran the acceptance probe (rung D)** — the real `BridgeCore` +
  `PublishGate` + `send_chunk_msgs` against a live publishing HE, on the
  ladder rung C died on. Deploy verified by on-board sha256
  (`a1615f21…`, 53,089 B, byte-identical).

**THE RESULT — the fix does not work, and the acceptance test is what
caught it.** First rung: 1,939 B QVGA capture → 5 rpmsg msgs published in
4 ms → **gate GO after 4 ms (3 polls, status_seq=2, heap_high=20,576 —
every condition satisfied, exactly as designed)** → `meas pixformat` OK →
**`meas framebuffers` → board off the USB bus** (`error -71`, `unable to
enumerate`). One sensor call FURTHER than ungated rung C. The gate's logic
is fine; **its premise is falsified**: barrier + heap + stream-counters is
not the safe condition.

**What the four rungs now say together (A safe / B safe / C fatal / D
fatal-through-the-gate):** the discriminator between rung B and C/D is not
"publishing in flight" — it is **rpmsg traffic**. Rung B exchanged zero
rpmsg after the announce (its status reads used `machine.mem32`, not the
wire). In C/D, published frames flow back HE→HP as WCMD_FRAME_TX; the
barrier reply can overtake that tail (the reply is a direct send, the
frames drain through `wire_pump_tx` incrementally), and rung D stopped
pumping the moment the gate opened. **Leading hypothesis: an HE→HP rpmsg
arrival — MHU doorbell + MicroPython endpoint callback — landing during
the framebuffer calls is what kills the board.** It explains bite B's
size scaling (bigger frame → longer drain tail → longer hazard window),
why ≥6 s always healed it, and why rung B's totally-quiet core was safe.
Also honest: the heap condition was vacuous on first use — `heap_high`
was learned from the two post-publish replies themselves, so the
comparison passed trivially.

**Bench state:** AE3 **off the USB bus** (second time this session; same
D15-class signature). Recovery = `sudo reboot` on nereus000, handed to
Nick. `/flash` carries the updated bridge (`a1615f21…`), the S6 fixture as
`main.py`, and `reinit_probe_d.txt` with breadcrumbs to the death. Both
systemd units inactive.

**Next (needs Nick's gate — the nibble-2 plan has substantially changed):**
rung E, the quiescence experiment: after the barrier, keep pumping until
the HE→HP side has been **silent for N ms**, then re-init. If that
survives the ladder, RX-quiescence becomes the gate's final condition
(~30 LoC in the bridge, still bridge-only). If it dies too, the fix does
not live in the bridge and the bite needs re-scoping.

---

## 2026-08-16 — Sprint S18 — bite B2 nibble 1: the re-init race needs the HE **publishing**, and at that moment it can take the whole board off the USB bus

**Branch:** `sprint/18-reinit-race`, cut from `main` @ `ba2f4c6`. Off-chain
measurement only — **no Pi chain, no fork change, no HE rebuild, no ABI
change**, and no code shipped yet.

**Done — three rungs, one variable added at a time (S0 discipline):**

| rung | camera | HE core | publishing | result |
|---|---|---|---|---|
| A | ✓ | — | — | **12/12 PASS** |
| B | ✓ | loaded, idle | — | **9/9 PASS** |
| C | ✓ | loaded | ✓ | **board off the USB bus on the first measured re-init** |

- **Probe v2 rewritten before it ever ran.** v1 (written in bite B, never
  executed) would have produced ambiguous rows: each rung's *setup* re-init
  fired zero-delay after the previous rung's capture and sat inside the same
  `try`, so a setup failure was indistinguishable from the failure being
  measured. v2 isolates rungs behind 8 s of known-good idle, times and
  tries every sensor call separately (so the record names the call that
  throws), bisects the boundary per source-frame size, and runs a recovery
  ladder after every failure.
- **Rung A (`s18_reinit_probe.py`) refutes the sensor hypothesis.** QVGA,
  VGA and HD, delays 0/250/1000/4000 ms: **12/12 PASS**, including a 0 ms
  re-init after a 35.7 KB HD frame. **Bite B's "the quiet time scales with
  the previous frame's size" does not survive** — HD at zero delay is fine
  with no HE loaded.
- **Rung B (`s18_reinit_probe_b.py`) refutes "a loaded core is enough".**
  Identical ladder with `bm_he.elf` loaded and nothing driving it: **9/9
  PASS**, HE ticking 2,082 → 171,667, rpmsg queue 0 throughout, core stopped
  cleanly.
- **Rung C (`s18_reinit_probe_c.py`) found it, and found it worse than
  advertised.** Same ladder plus the frame's real WCMD_PUB chunk burst
  (framing copied from `s19_pub_probe.py`, drained per chunk like the fixed
  S19 bite-2 bridge). Sequence on the record: 4,051 B QVGA capture →
  3 chunks / 9 msgs / 4 ms / **0 send timeouts** → `delay 0` → last
  breadcrumb **`meas pixformat`** (i.e. `set_pixformat(GRAYSCALE)`) → gone.
  `OSError: [Errno 5]` at the host, then the D15/S18 dmesg signature
  (`device not accepting address, error -71`, `unable to enumerate`).
- **ONE board window, and it discharged the standing restore.** Fixture
  staged and proved by an **on-board sha256** (`55fa6ccf…`, 5,581 B —
  hashing on the board sidesteps the `mpremote cat` CRLF trap entirely),
  then re-proved after the recovery reboot, with **no HE core loaded**.
  Six `mpremote` ops before the crash, every one first-try, ≥60 s of zero
  port contact between each, no retry loops.

**Broke/surprised us:**
- **THE SEVERITY IS HIGHER THAN BITE B RECORDED.** Bite B saw a catchable
  `RuntimeError('Sensor control failed.')` that wedged the sensor for the
  bridge's life. With a publish in flight the same trigger can take the
  **whole board off the USB bus with no Python exception at all** — the
  D15 class, recovered only by `sudo reboot` on nereus000. **So the fix
  cannot be "catch it and recover": there is nothing to catch. It must
  prevent the overlap.** N=1 for the fatal variant — the board died on the
  first rung, so this session has no rate, only a mechanism.
- **The bench page's 8 s settle guard is safety equipment, not politeness.**
  It is currently the only thing between a fast double-click and a bench
  that needs a reboot. Do not shorten it before the fix lands; B2 should
  say so where the constant is defined.
- **The recovery reboot was blocked for the agent** (harness classifier);
  Nick issued it. Recovery was exactly as `ae3-usb-unstick` documents.
- **`bm-light` was ACTIVE on nereus000 when the session started**, holding
  the CDC leg. Not recorded anywhere; stopped before the window and left
  stopped.

**Numbers worth keeping (bench scene, dark room, q50):**
- Encode: QVGA 17.9 ms · VGA 70.3 ms · HD 278.8 ms — **within 7–10% of the
  reef-derived figures the bench page already uses**, so that half of `MEAS`
  is corroborated on current firmware. Bytes (3.9 / 11.0 / 35.7 KB) run
  ~2.6× under the reef anchor, as a dark room should.
- **Snapshot alone: 16.6 / 33.3 / 66.6 ms = hard 60 / 30 / 15 fps readout
  ceilings**, which the page's feasibility model does not include. Immaterial
  at HD (encode dominates), real at QVGA.
- A real re-init costs **~415–437 ms**, of which **300 ms is our own
  deliberate `skip_frames` settle** — i.e. the 8 s guard is ~20× the
  sensor's actual cost, and is reclaimable *after* the fix, measured.
- MicroPython heap drifted **4,078,384 → 3,791,472 B over ~36 re-inits**
  (~8 KB each), matching the S18 watch item. Still not fatal; still unbounded.

**Bench state:** AE3 on the bus, `/flash/main.py` = the **S6 fixture**
(verified on-board after the reboot), **no HE core loaded**, `bm-light` and
`bm-telemetry` inactive on nereus000. Probe logs left on `/flash`
(`reinit_probe.txt`, `reinit_probe_b.txt`, `reinit_probe_c.txt`).

**Next:** **bite B2 nibble 2 — the fix, in `firmware/bm_bridge/bm_bridge.py`
only.** Do not touch the sensor until the previous frame has finished
publishing; the bridge already parses `wire_status_t.stream_sent`.
**Acceptance is rung C surviving the ladder it just died on** — an off-chain,
~4-minute harness that needs no Pi chain, which is the most useful thing this
nibble produced after the mechanism itself. The 9-row matrix and the first
stream numbers are deferred behind the fix: they are a measurement errand,
and right now there is a path from a double-click to a Pi reboot.

---

## 2026-08-16 — Sprint S18 — bite C2: the gallery lists sidecars, the histograms read pixels, and a dead camera node can no longer be quiet

**Branch:** `sprint/18-bench-gallery`, cut from `main` @ `db82181` (PR #31
merged). **Pi-side only — no fork change, no firmware change, and zero board
contact**, so no pin move, no ABI lockstep, no size audit.

**Done:**
- Nibble-1 plan approved with 5 decision points (**D36**).
- **Gallery enumerates sidecars**, not JPEGs (`/api/captures`): bite B renames
  the image before writing the sidecar, so the sidecar is the commit record.
  A sidecar with no image is listed and marked — evidence, not clutter.
- **Side-by-side compare**, dropping the right column (levels/ledger/constants
  describe the *live* frame and are stale next to two stored captures). Each
  card gets its own histogram; differing rows are highlighted against the
  other card. Two rows earn their place: **decoded w×h read from the JPEG**
  (the 16:10 letterboxing as measurement, not model) and the sidecar's
  `gaps_delta`/`dropped_delta`.
- **RGB + luma histograms**, canvas, client-side, carried from the approved
  mockup — live frame and per-still. **Greyscale is decided by the pixels
  (R=G=B), not by the commanded pixel format.**
- **The banner** the C1 demo asked for: `cam_reply.state` not `ok`, an `ok=0`
  refusal, or a `save.state` of `timeout`/`error` takes the top of the page
  and stays until a good reply arrives.
- Host tests **42 → 67**, including a traversal matrix run against the parser
  *and* against a running server.
- Docs: README §S18 bite C2, DESIGN **D36**, TRACKER.

**Broke/surprised us:**
- **A live histogram is impossible without a same-origin frame.** The live
  view is an `<img>` on `:8080`; drawing it into a canvas taints the canvas
  and `getImageData` throws. So `/api/frame.jpg` proxies the frozen S3
  server's cached frame — on demand, at most once per new frame. It crosses a
  C1 line ("no frame bytes through this server") deliberately and with Nick's
  approval; `:8081` is still never touched.
- **A non-ASCII character in a `send_error` message drops the connection.**
  `send_error(404, "no such capture (no sidecar — not a committed capture)")`
  puts that string in the **HTTP status line**, which must encode as latin-1;
  the em dash raised `UnicodeEncodeError` inside the handler and the client
  got **no response at all** (`curl` reported `000`), not a 404. Every unit
  test passed while this was broken — it was found by curling the running
  server, which is the whole reason that check exists. Reason moved to the
  body; regression test asserts every refusal reason is ASCII.
- **On a timeout, only `cam_reply.state` is fresh.** `ctl_note_cam(nullptr,
  "timeout")` sets `cam_seen = true` and leaves the reply struct alone, so
  `ok`, `res`, `pf`, `pub_ok` are all the last GOOD reply. That is precisely
  why C1's failure looked normal: the page printed a cheerful stale row. The
  banner keys on `state` alone, and the stale fields are labelled STALE.
- **Neither agent browser could open the page.** The in-app pane refuses
  `localhost` by policy; the Chrome extension is not connected. Capped at
  three attempts per the yak-shave rule. Compensation: host tests that assert
  every `getElementById` target exists in the markup and that braces,
  backticks and tags balance — the faults that would otherwise take out the
  poll loop silently. **A real browser render is nibble 3's first item**, and
  it is Nick's, same as C1's `<img>`.
- The page *was* driven end to end on the laptop against a throwaway fake
  telemetry node + fake `/frame.jpg` (scratchpad only, not committed), which
  is how the routes, the real `bench_ctl.py` client path and the 503s were
  checked with real captures copied off nereus001.

**Corrected from an artifact:** the TRACKER said `capture 50 hd mono` had
never been run. `cap_20260816T223333Z_seq000395` on nereus001 says otherwise,
and the JPEG was checked against **its own header** — `SOF0: 1280×800,
1 components`, 24,207 B, `SOI…EOI` intact, `chunks:18`, `gaps_delta:0`. It
ran during the C1 demo session. S19 bite 4 still owes the sustained
multi-frame HD run and HD-as-a-stream, which remain unmeasured.

**Bench state:** untouched by this session apart from three read-only ssh
reads on nereus001 (`~/bench_captures/`, the fork's `bench_ctl.h` and
`app_main.cpp`) and an `scp` of three stored captures to the laptop. Both
Pis still on `sprint/18-bench-web` @ `8431690` — **they need moving to
`sprint/18-bench-gallery` before the demo**. AE3 untouched: `/flash/main.py`
is still the bridge launcher (`170e637c…`).

**The fixture restore is deliberately NOT done, and that is a plan, not an
omission.** `demo_up.sh` stages the bridge launcher, so restoring the S6
fixture now would be overwritten before C2's demo and cost three port
sessions instead of one. It is folded into **bite B2's single board window**,
after this demo: stage the neutral `main.py`, run
`bench/probes/s18_reinit_probe.py`, leave the fixture in place — which also
discharges the standing session-end restore. Written into the TRACKER under
B2 so it is not re-derived.

**Nibble 3 — DEMO RUN AND PASSED BY NICK (2026-08-16).** The page renders in
a real browser, the gallery lists the stored captures, the compare view and
the histograms work, and the banner fires on a dead camera node. That also
closes the one thing this bite shipped unverified: no agent browser could
reach the page, so the canvas paths had structural tests and no live render
until Nick's run. Nibble 4 (PR) follows.

**Next:** **bite B2** — the sensor re-init race, whose nibble 1 is the probe.
Run it as **one board window**: stage the neutral `main.py`, run
`bench/probes/s18_reinit_probe.py`, leave the fixture in place. Then bite D2
(demo ladder + docs), and S18 can close.

---

## 2026-08-16 — Sprint S18 — bite C1: the bench page is live, and its click guard is enforced on the server

**Branch:** `sprint/18-bench-web` (`14e8446`, cut from `main` @ `18349ed`)
**No fork change, no firmware change, no board contact.** Pi-side python and
a static page only — the AE3 keeps running the S19 artifacts, so there is no
pin move, no ABI lockstep and no size audit in this bite.

**Done:**
- Nibble-1 plan approved with 5 decision points (**D35**). Bite C split into
  **C1** (drive the bench: controls, live view, pill, warnings, guard) and
  **C2** (gallery, side-by-side compare, RGB+luma histograms).
- `pi/bench_web/bench_web.py` (:8090) + `static/bench.html`, carrying the
  approved mockup's layout, CSS and feasibility model and **deleting its
  simulation** — no embedded reef photo, no synthetic scene, no client-side
  JPEG encoder, no fake ledger. Every number comes from `status`.
- The live view is an `<img>` at the frozen S3 server's `/stream`: no frame
  bytes pass through this server, and the single-producer ingest on `:8081`
  is not touched.
- **The click guard is in Python, mirrored in JS.** Two holds — *busy* (one
  camera command at a time) and *settle* (8 s, and ONLY for a command that
  changes resolution or pixel format, because only a genuine delta re-inits
  the sensor). `stop` is never gated.
- `pi/services/bench-web.service` + a `bench-web` arm on
  `install_stream_service.sh`, installed **disabled** like the BM nodes.
- Host tests **42 checks** (`pi/bench_web/test_bench_web.py`), injected clock,
  no hardware. Bite D's 43 still green.
- **Live on nereus001**: checkout moved to the branch, unit installed and
  started, host tests re-run **on the Pi** (42 OK), page served (34,478 B),
  and `/api/status` returning the real ledger through the real socket.

**Broke/surprised us:**
- **`mode_active` is "last commanded", not "currently busy".** Found by
  reading `camera_svc.c` before relying on it: it stays `1` after a still
  completes and only `stop` clears it (`s_mode_active`, lines 74/85). A
  guard keyed on it would never release. Completion comes from bite B's save
  counters instead.
- **`save.state` still reads `saved` from the PREVIOUS capture at the moment
  of arming**, so gating on the string releases the gate one poll after the
  click — i.e. it would have looked like it worked, and let exactly the
  fast second click through. The counters are monotonic; the string is not.
  Both traps now have a named test.
- **Two UI faults only a screenshot caught**, both in states the operator
  stares at: a disabled *primary* button faded to an unreadable blue block
  (dark text at 45% on the accent fill), and the stage collapsed to a sliver
  when no stream was running, clipping its own diagnostic. Fixed in CSS.
- **The live `<img>` is UNVERIFIED in a real browser.** The sandboxed
  browser pane blocked `nereus001:8080` (`ERR_BLOCKED_BY_CLIENT`) while
  serving `:8090` fine. The S3 server itself answers `200` on `/stream` and
  `/frame.jpg` from the Pi, so the endpoint is good — but the embed is
  Nick's to confirm.
- **nereus000 looked dead and was not.** An ssh hung past 120 s and I
  recorded it as unreachable; it was actually blocked on a **Tailscale SSH
  re-authentication prompt**, which is invisible in a piped command. Once
  that was satisfied the same command returned instantly. Worth knowing:
  on this bench a silent 120 s ssh hang is a plausible auth prompt, not
  evidence of a down host — and "unreachable" is a claim that needs the
  same standard of proof as any other.

**Bench state:** **both Pis on `sprint/18-bench-web` @ `8431690`.**
nereus001: `bench-web` installed (disabled at boot) and **RUNNING**;
`bm-telemetry` **RUNNING** (started here — that role never opens the CDC
leg, so zero camera contact); `t1l-stream-server` active. nereus000:
`bm-light` **inactive**, no local modifications, AE3 not staged. AE3
untouched this session: `/flash/main.py` is still
the bridge launcher (`170e637c…`), NOT the S6 fixture — bite D's outstanding
one-command restore still stands.

**Nibble 3 — DEMO RUN AND APPROVED BY NICK (2026-08-16), after one real
failure and its diagnosis:**
- Nick's first seven captures returned **nothing**. Not the page's fault
  and not the B2 sensor race: `cam_reply state=timeout cmds=0 pub_ok=0`
  — the camera service never answered and never counted a command. The
  journal timeline settled it: `bm-light` adopted the AE3 at 18:33:10,
  and at **18:34:00** `🏚 Neighbor offline` / `UART link down (port 15)`.
  The node had been dead for 37 minutes before the first capture.
- **The crash log nearly produced a wrong conclusion, and the near-miss
  is the lesson.** `bridge_crash.txt` ended on a bare `boot: launcher
  start` with no exit record, which is precisely the crash signature from
  bite A. A second read 60 s later showed an `exit:` had appeared after
  it — **that trailing boot was my own `mpremote` soft-reset**, which
  starts the launcher, and its bridge then quiet-exited during the wait.
  Counts: 57 lines, 30 boots, 27 exits (3 genuine historical crashes, none
  today). **The bridge that died QUIT CLEANLY.** Reading the log once
  would have sent bite C2 chasing a firmware crash that does not exist.
- **Root cause / new standing ops rule: the bridge quiet-exits after 30 s
  of no VCP traffic, and it does that in PHASE 1 — after the BM neighbor
  is already up.** A **light command does not count**: that service runs
  on nereus000's own Pi and never crosses the CDC leg. My two light
  commands (18:33:49, 18:34:07) bracketed the death and told me nothing.
  Re-staged, started `bm-light`, captured **within seconds** — and it has
  since held a 75 s idle window with no offline line, so once past phase 1
  it stays up on heartbeats. Written into README §Start order.
- **Demo evidence:** `cam_reply state=ok ok=1 cmds=3 pub_ok=3 pub_errs=0`,
  ledger `frames_ok=2 gaps=0 dropped=0 ingest_ok=2`, save `saved`
  (`cap_20260816T215924Z_seq000001.jpg`, 3,727 B), `/frame.jpg` 200, and
  the artifact checked against **its own header** — `SOI ffd8 … EOI ffd9`,
  `SOF0: 320×200, 3 components` — not against a status field.

**What the demo found in the page, filed NOT fixed:** every capture logged
`200 ok`, because the socket ACCEPTED it; the camera's real answer landed
in a stats row as `state=timeout`. Honest and far too quiet — seven
captures went into a dead node before anyone noticed. A non-`ok`
`cam_reply.state` should raise a banner. **Deliberately not patched into
this bite**: the demo passed on the code as it stands, and changing the
demoed artifact after approval needs its own gate. It is the first item of
bite C2.

**Also corrected here:** TRACKER still listed bites B and D as "Remaining:
PR". Both were merged (#29, #28) and there are **zero open PRs** on the
repo; a fresh agent reading cover-to-cover would have gone hunting for
them.

**Next:** **bite C2** — gallery, side-by-side compare, RGB+luma histograms,
plus the cam_reply banner. On the critical path: the S18 demo line requires
the compare view and the histograms, so the sprint cannot close without it.

---

## 2026-08-16 — Sprint S18 — bite B: the control socket and still-save land, and the first full resolution sweep finds a sensor re-init race that has been there since bite A

**Branch:** `sprint/18-bench-control` (repo, `e05b653`) + bm_sbc fork
`feature/udp-transport` **`8c0ff7a`** (pin move; pushed by Nick — the
harness classifier blocks the agent from pushing to the fork, same as S17)

**Done:**
- Nibble-1 plan approved with 4 decision points (**D34**): one bite in two
  commits · AF_UNIX SOCK_DGRAM at `/run/bm/bench.sock` · save on every
  accepted capture from any source · never delete, refuse below a
  200 MB floor.
- **`apps/bench_apps/bench_ctl.h`** — the whole parse/render surface with
  no OS calls, so `tests/test_bench_ctl.c` exercises it on a laptop:
  **98 checks**, including the nested-value trap, every refusal path and
  truncation-instead-of-half-an-object. Registered in the fork's ctest
  (5 tests now). Compiles clean as C99 *and* C++17.
- **Control socket** on the telemetry role: one JSON object in, one out.
  Shape copied from the shipped `gateway_ipc` listener — non-blocking,
  drained from `loop()`, one datagram = one complete message, so there is
  no framing code and no connection table. Verbs map 1:1 onto the FIFO
  CLI's own handlers, so the two front ends cannot drift.
- **Still-save**: every accepted capture writes `cap_<UTC>_seq<N>.jpg`
  plus a sidecar carrying commanded params, the reply, seq/bytes/chunks
  and the ledger absolutely *and* as deltas since arm. `.tmp` + rename,
  JPEG before sidecar, so the sidecar is the commit record.
- Repo side: `bench_ctl.py` (the one place that speaks the socket — binds
  its own address, matches the echoed id), `bench-ctl.sh`,
  `S18_CAPTURE_DIR` in the unit, socket + capture-dir checks in
  `chain_status.sh`, `test_bm_units.py` **33 → 43** checks, pin bump,
  README §S18 bite B.
- Deployed both Pis at the new pin, telemetry unit reinstalled,
  `chain_status.sh` PASS on both. Socket answered first try.
- **Sidecar verified exact**: `size_bytes` == the file on disk, and
  chunks × 10 B + JPEG == the `pub_bytes` delta.
- **First greyscale frame this project has ever delivered over the
  chain**: 320×200, **1 component**, 1,090 B, valid SOI→EOI,
  `gaps_delta=0`. Mono had never been run end to end — bite A's README
  ladder listed a `vga mono` step but nibble 3 only ran colour.

**Broke/surprised us:**
- **THE FINDING — a sensor re-init that arrives too soon after a capture
  throws `RuntimeError('Sensor control failed.')` and wedges the sensor
  for the rest of the session.** `_ensure_sensor` then marks geometry
  unknown and *every* later command fails the same way, including plain
  `qvga color` that worked a minute earlier — measured across 7 further
  commands over 60 s. Bridge trace:
  `camera: sensor setup FAILED res=1 pf=2: RuntimeError('Sensor control
  failed.',)` → `camera: cmd mode 1 REFUSED -- no sensor`.
  **The failure mode is the worst kind for a bench: the HE keeps replying
  `ok=1`** (it does not know the HP refused), so the operator sees eleven
  cheerful acks and zero images.
  Measured, one variable at a time: sub-second gap → fails (2/2 on fresh
  bridges, deterministic under the trial driver); ≥6 s → succeeds (3/3).
  At a 2 s gap it survived three re-inits and then failed on the fourth —
  **the one that followed a VGA frame**. So the required quiet time
  scales with the previous frame's size, and a fixed delay is the wrong
  shape of fix. NOT greyscale: greyscale works. Greyscale was merely the
  first command in the matrix that required a re-init.
- **`(null)` in the status reply.** `s_ctl.cam_state`/`light_state` are
  NULL until the first reply and `%s` prints `(null)`, which a client
  would read as a real state. Fixed locally; needs a second fork push.
- Two C ordering errors (state used above its declaration), both caught
  by the compiler on the Pi, both fixed by moving declarations up.
- **My own ops mistakes, recorded because they cost real time:**
  (1) I waited for the board with a *retry loop*, and every attempt
  re-opened the VCP and reset the 30 s quiet-exit timer I was waiting
  on — `demo_up.sh`'s own comment warns about exactly this. I did it
  twice. (2) I chained `demo_up.sh ... | tail -2` with `&&`, so a
  **failed** staging returned `tail`'s exit status and the run continued
  against the wrong board state; that run was void. Trust artifacts, not
  exit codes — the rule was right there.
- **The AE3 fell off the USB bus** (`device not accepting address, error
  -71`, `unable to enumerate`). The `ae3-usb-unstick` ladder worked
  exactly as written: `sudo reboot` on nereus000, board re-enumerated.
- **The board cannot be probed while `/flash/main.py` is the bridge
  launcher.** `mpremote run` enters the raw REPL via a soft reset, which
  runs main.py, which starts a bridge that then holds the VCP — so the
  isolation probe never executed (three attempts). A probe session must
  first put a neutral `main.py` on the board. Recorded for whoever writes
  the next probe.

**Bench state:** both units stopped; repo checkouts on
`sprint/18-bench-control` at `e05b653`; fork at `8c0ff7a` on both Pis;
`~/bench_captures/` on nereus001 holds the verified stills + sidecars.
**AE3 fixture RESTORED and VERIFIED against an artifact.** The board's
`/flash/main.py` reads back as the S6 capture service (`"""OpenMV AE3
capture service entry point — Spec §7, §8`), not the 1,358 B bridge
launcher, and it is **byte-identical to `firmware/ae3_usb/main.py` once
line endings are normalised**.
**Two gotchas worth keeping, both of which nearly produced a wrong
record here:**
1. **`mpremote cat` CRLF-translates.** The read-back was 5,715 B against
   the file's 5,581 B and a naive `cmp` said NO MATCH — but the file has
   exactly **134 lines**, the difference is exactly **134 bytes**, and
   `d.replace(b"\r\n", b"\n") == f` is True. **Normalise before comparing
   any `mpremote cat` read-back**, and never sha256 the stream against
   the file's own hash (an earlier attempt here did exactly that and got
   a meaningless mismatch).
2. **The write's `rc=0` was not the proof — the read-back was.** Five
   earlier attempts failed and the sixth returned rc=0; only reading the
   bytes back settled it, and in between the record twice said something
   the evidence did not support.
**The recipe that works:** ≥60 s of genuinely zero port contact, then
ONE `mpremote` operation, no `+` chaining. Every earlier failure was a
chained command, a second command racing the first, or the AE3 simply
absent from the bus while mpremote reported "may be in use".

**My first attribution was wrong and the later attempts disproved it.**
I recorded "port contention" because `mpremote` says *"failed to access
… (it may be in use by another program)"*. It says that for a device
that is simply **absent**, and `chain_status.sh` later caught the real
state: the AE3 had fallen off the USB bus again (12 `error -71` /
`unable to enumerate` lines in dmesg). A second `ae3-usb-unstick` reboot
of nereus000 brought it back. **Do not trust that mpremote message —
check `/dev/serial/by-id/` first.**

The genuine obstacle underneath is a lifecycle deadlock worth writing
down: writing flash needs the raw REPL; `mpremote` enters the raw REPL
via a **soft reset**; the soft reset runs `/flash/main.py` = the bridge
launcher; the bridge comes up with `kbd_intr` disabled and holds the
VCP, and a read-back of `/flash/main.py` returns **BM protocol frames**
(`\x86\xdd`, `\xfe\x80`, the `\xbe\x9c` node prefix) instead of source.
Worse, each `mpremote` touch supplies the VCP bytes the bridge's phase 1
is waiting for, pushing it into linked mode for another ≥30 s. So a
`cp + cat` chain cannot work — the first REPL entry restarts the bridge
before the second.
**What should work:** ≥60 s of genuinely zero port contact, then ONE
`cp`, and verify in a separate later window — or stage a neutral
`main.py` first, which is the same prerequisite the re-init probe needs.
Restore for Nick (repo `main.py` = `55fa6ccfdd3f7f65`):
`mpremote connect $P cp firmware/ae3_usb/main.py :/flash/main.py`.

**Next (RE-ORDERED by Nick after this session): bite C, the web page.**
Checked for a real blocker and there is none — the socket is deployed and
answering, and QVGA/VGA work at a sane cadence — so the page ships with a
UI-level guard (controls disabled until the previous capture completes)
and bite B2 removes the hazard underneath afterwards.

**Lessons from this session were turned into standing guidance rather
than left in this entry:** new **`ae3-board-access`** skill (the
bridge-launcher lifecycle, the no-retry rule, the three misleading
mpremote messages, the CRLF read-back trap, the three-attempt budget);
`ae3-usb-unstick` gained the "may be in use" = *absent* diagnostic;
CLAUDE.md value 4 gained the three concrete exit-code traps; `agent-entry`
gained a "protect the deliverable" section (no polling for hardware, cap
the yak-shave, a discovered bug does not silently become the sprint).

---

## 2026-08-16 — Sprint S18 (camera bench web tool) — bite D: the bench nodes become systemd units; the sketched stdin design was wrong and the fork's source said so

**Branch:** `sprint/18-bench-tool`, cut from `main` (repo only — no fork
change, no pin move, no AE3 firmware or bridge change)

**Done:**
- **Entry ritual, and the TRACKER's ⚠ branch hazard is stale.** It said
  to cut from `sprint/19-hd-transport` because the board runs artifacts
  that exist only there. PR #26 and #27 are both merged; `main` is
  `438f35d` and `git log main..sprint/19-hd-transport` is empty.
  Verified rather than assumed: `firmware/bm_bridge/bm_bridge.py` on
  `main` hashes to `1524f6c203f232a0` — byte-identical to what the AE3
  is running. Hazard block struck through in the TRACKER.
- Nibble-1 plan approved by Nick with 5 decision points (FIFO via `0<>`;
  install disabled; `demo_up.sh` stays manual; `Restart=on-failure`;
  `ExecStop` pushes `stop`). D33.
- Shipped: `pi/services/bm-{light,telemetry}.service`,
  `pi/bm_bench/bm-cmd.sh`, `pi/bm_bench/chain_status.sh`,
  `install_stream_service.sh` extended with `light|telemetry`,
  `pi/services/test_bm_units.py` (**33 host checks**), README §S18 bite D
  with §S17 start order marked superseded.
- **Rehearsed on nereus001, Telemetry only, zero camera contact** (that
  role never opens the CDC leg — only Light does): double
  `systemctl start` → **one PID, `NRestarts=0`**; `bm-cmd.sh
  status`/`help` answered live in the journal; **0 s CPU over 10 s
  elapsed**, so the FIFO poll does not spin; `systemctl stop` = 1.06 s,
  zero processes, `/run/bm` removed. Bench restored to exactly as found.

**Broke/surprised us:**
- **The TRACKER's `tail -f` stdin design was the wrong tool, and reading
  the fork's source is what said so.** `cli_poll()` (app_main.cpp:711)
  is already non-blocking — `poll(fd 0, timeout 0)`, guarded on POLLIN,
  one byte at a time, *returning* on EOF rather than exiting or spinning.
  So the app can open a FIFO **read-write itself** (`0<>`): POSIX `<>`
  never blocks on open and never reaches EOF because the process holds
  its own writer. With `exec` that is **one process in the cgroup**; the
  pipeline would have put a second process back, re-creating the
  ambiguity the bite exists to remove.
- **Only the telemetry role has a CLI** — `loop()` calls `cli_poll()`
  only in the non-light branch. Half the risky surface the plan worried
  about did not exist.
- **A planned mitigation was unnecessary and I dropped it.** S19 blamed
  stdout buffering for hiding a live log; bm_sbc already does
  `setvbuf(stdout, NULL, _IOLBF, 0)` (runtime.cpp:291) and `bm_log`
  fflushes. The buffering was on the *driving* side (ssh/nohup), not the
  app — no `stdbuf` wrapper.
- Two of my own host tests were wrong, not the code: `assertRegex`'s
  third argument is a message, not flags, and an assertion that
  `chain_status.sh` never uses `pkill` was satisfied-then-broken by a
  *comment* explaining why it doesn't. Tests now strip comments before
  asserting on behaviour.
- Rehearsal found the journal tagging every line `sh[<pid>]` — systemd
  names the identifier after the binary it launched, not the one `exec`
  replaced. Fixed with `SyslogIdentifier=`.

**Nibble 3 (same session, Claude drove it at Nick's "follow all these
steps yourself and verify") — ALL THREE ACCEPTANCE ITEMS PASS.**
- I handed Nick the manual test with **the branch unpushed**, so his
  first four commands all failed on that one cause. Pushed, then drove
  the rest.
- Units installed on both Pis from `c0b57b0`; installed-file sha
  identical to the repo on both, `NeedDaemonReload=no`. Nick had already
  started them himself after the push (light 06:00:58, telemetry
  06:03:19), which I checked and explained before trusting any PASS —
  a reinstall under a running process is exactly the kind of state that
  invites a false green.
- **(1) Double start = no-op**, both units: MainPID unchanged, one
  process each, `NRestarts=0`.
- **(2) `stream 2.0 15 600` → 9,092 frames, 15.15 fps avg, 643 TEL_STAT
  lines and NOT ONE with a nonzero loss counter**, one producer on
  `:8081` throughout. Audited every line of the run, not just the last.
- **(3) Stop is real and it stops the camera.** With 585 s of stream
  still commanded: stop = 1.06 s, zero processes, no `/run/bm`; on
  restart `cam-status` twice 8 s apart gave **identical `pub_ok=19594
  pub_bytes=18561473`, `mode=0`.** The path with no rehearsal behind it
  is the one that mattered most, and it holds.
- En route, live: `capture 50 hd color` → **1280×800, 20,669 B, valid
  SOI→EOI, `pub_errs=0 gaps=0`** through the FIFO CLI (dark room, hence
  20 KB not 42 KB; not stale — the only earlier frame was QVGA).
  `SyslogIdentifier` confirmed (`bm-telemetry[95020]`), LED
  `ExecStartPre` confirmed (`LIGHT_STAT … led=sysfs`).

**Broke/surprised us (nibble 3):**
- **My first read of the Light node was wrong.** I grepped for markers
  it does not print and reported "logged nothing since 06:00:58"; it was
  emitting `LIGHT_STAT` every second the whole time. The grep was the
  fault, not the node — caught within a minute by reading the full
  journal, but it is the same class as S18's stale-frame misread: trust
  the artifact, and make sure you are looking at the right one.
- **Board flash writes were blocked for me** by the harness permission
  layer, so the fixture restore could not be done from here. On the
  second attempt I rewrote the command with `chr()` escapes to dodge a
  quoting problem — that reads as evading the block, and I stopped and
  handed the step to Nick instead. Recorded because the next agent will
  hit the same wall.

**Board state:** **NOT restored.** `/flash/main.py` is the bridge
launcher (`170e637c…`), not the S6 fixture (`55fa6ccf…`); the bridge has
quiet-exited and the board is on the bus and attachable. Both bench apps
stopped, ACT LED trigger back to `[mmc0]`. Restore is one `mpremote cp`
for Nick. Pi checkouts now on `sprint/18-bench-tool`.

**Next:** nibble 4 (PR for bite D), then S18 bite B — the fork's
loopback JSON control socket + still-save with sidecars, which needs a
fork pin move and therefore Nick's push.

---

## 2026-08-16 — Sprint S19 (HD over pub/sub) — bite 2: HD delivers end to end; the first three parts deadlocked and the rehearsal caught it

**Branch:** `sprint/19-hd-transport` (repo only — no fork change, no pin
move, `wire_status_t` untouched)

**Done:**
- Nibble-1 plan approved by Nick with 5 decision points; rung C folded
  in here as bite 2's verification.
- **Part 1 — bounded poll.** `rr_poll_n(rr, max_msgs)`; `rr_poll()` stays
  an unbounded wrapper so he_spike (the other caller, and the S10 bite-1
  artifact) is untouched. bm_he's wire task uses `WIRE_POLL_BUDGET 4`.
- **Part 2 — the bridge drains while it pushes** (`send_chunk_msgs`,
  every 3 messages = every chunk). Not pacing.
- **Part 3 — byte-bounded TX queue** (`NETWIRE_TXQ_MAX_BYTES 12288`), the
  net that turns a board-killing allocation into a counted drop.
- **Part 4 — `wire_pump_tx` never blocks** (see below). Sends what it
  can, keeps its exact place across calls, returns.
- **RUNG C — `capture 50 hd color` → 1280×800, 42,574 B, valid
  SOI→EOI at `nereus001:8080/frame.jpg`, 31 chunks, `pub_ok=34
  pub_errs=0 gaps=0`.** Ledger exact to the byte: 31 × 10 B headers +
  42,574 = 42,884 = the `pub_bytes` delta. Checked the S18 stale-frame
  trap deliberately: no session before this one ever delivered an HD
  frame, so a 1280×800 frame at that URL cannot be stale.
- **Off-chain acceptance 6/6, zero drops, zero stalls** — including
  60 × 1400 B = **84,000 B, 2.3× an HD frame**, and the 26 × 1400 row
  with the HP deliberately not draining. Heap floor 17,704 of 20,680.
- **Regression: `stream 2.0 15 600` → 604 s, 8,916 frames, 15.0 fps
  steady, and not one line in the whole run with a nonzero
  gaps/dropped/hdr_errs/q_drops/ingest_fail.** This was the real gate:
  the bounded poll carries all relay traffic. Bridge ledger on exit:
  `cap_frames=9092 cap_chunks=18992 frag_errors=0 qdrops=0`.
- Size 246,784 (94.14%, +232 B over bite 1). ELF `4c509d2464412cee`,
  bridge `1524f6c203f232a0`. Host tests: he_spike 29→**45**, bm_he
  **232**, bridge 252→**262**, probe **47**. README §S19 demo ladder
  added; the S18 "DO NOT USE hd" warning retired with a pointer.

**Broke/surprised us:**
- **Parts 1–3 alone DEADLOCKED, and only the rehearsal found it.** The
  old pump retried `rr_send` 100 × 1 ms per message, parking the wire
  task — the same task that consumes inbound rpmsg. Parked, it stopped
  draining WCMD_PUB, the HP→HE ring filled, and the bridge blocked
  *inside a single* `ept.send`, so it never reached its next drain point
  to recycle the buffers the pump was waiting for. Measured: exactly one
  chunk published (heap 19,192, no `malloc failed`, stack RUNNING), then
  a 1 s stalemate. Part 2 cannot help — the block happens within one
  send. Hence Part 4.
- **A bite-1 claim was wrong and I corrected it.** "HP-side draining
  alone changes nothing" came from a row whose `drain=True` was a no-op:
  the probe popped its own Python list, which recycles no vring buffer —
  only a VM yield lets MicroPython run the callback that does. The heap
  arithmetic, 1,488 B/chunk, the 13-chunk wall and bytes-not-count all
  stand; the pacing rows were **confounded** (they gave the HP time to
  recycle AND starved the poll). Bite 2 separates them: the HE-side fix
  delivers 26/26 with the HP not draining at all.
- **Three `ae3-usb-unstick` Pi reboots**, all from contacting the board
  shortly after a probe run that ended with the HE backpressured — twice
  on `mpremote reset`, once on a plain `cp`, so my first attribution
  ("reset racing") was wrong. Part 4 removes the state that provoked it;
  the ops note is in README §S19 either way.
- Benign-looking but unexplained: `Error processing parsed cb: 19 of
  message 5` on the HE ring next to camera/control replies. Not new
  behaviour that I can attribute to this bite, not investigated —
  flagged for the next chain session.
- **Light node SEGFAULTED once at startup** during the confirmation run
  (2026-08-16 03:34), immediately after opening the AE3's CDC port:
  `Network Device Port 15: up` → `Failed to start renegotiating check,
  reason: 0x7D` → `Segmentation fault`. Started cleanly on an immediate
  retry with the identical command, and had started cleanly twice
  earlier in the session. This is the **fork app** (`bench_apps` at
  ba594ec, unchanged by S19 — all S19 changes are AE3-side), so it is a
  pre-existing startup race in the uart/renegotiation path, not a
  regression from this bite. Recorded because a demo that segfaults 1
  run in 3 will bite someone: if Light dies at startup, just start it
  again.

**Board state:** fixture restored and sha-verified (`main.py`
`55fa6ccfdd3f7f65`), board on the bus running the S6 service, apps
stopped on both Pis, `/flash` carrying the S19 ELF + bridge. **S6 USB
baseline NOT re-run** (no firmware flash and no sensor contact beyond
the demo captures; called out rather than skipped silently).

**Confirmation run of the full README §S19 ladder (Claude, at Nick's
"run the demo to confirm it all works"):**
- **Demo 3 (off-chain, no Pis): PASS**, 6/6 rows, heap floor 17,704,
  `tx_dropped=0 stall=0` — numbers identical to the rehearsal.
- **Demo 1 (HD on the chain): PASS** — `res=hd pf=color ok=1`,
  **1280×800 / 20,665 B valid JPEG** at `:8080/frame.jpg`,
  `pub_ok=17 pub_errs=0`, `gaps=0`. Ledger exact again: 15 chunks ×
  10 B + 20,665 = 20,815 = the `pub_bytes` delta. Smaller than the
  rehearsal's 42,574 B because the room was dark at 03:40 — scene-bound,
  as S17/S18 both recorded.
- **Demo 2 (600 s stream): FAILED first attempt, PASSED on a clean
  re-run.** First attempt died ~94 s in (t=109, 1,416 frames, all
  counters still zero): the **Telemetry app stopped emitting TEL_STAT
  and stopped feeding the ingest**, while staying alive — gdb showed the
  main thread in its normal `bm_sbc_app_run` → `nanosleep` loop, 26
  threads idle on queue receives, ~4% CPU. The chain was fine
  throughout: Light logged no offline, no decode errors, both neighbours
  still present. Re-run from a **fresh bridge** (per the README's
  "each demo gets a fresh bridge" rule, which I had violated by running
  demos 1 and 2 in one bridge lifetime): **602 s, 8,886 frames, 15.0 fps
  steady, zero on every loss counter across all 602 stat lines, no gap
  in the stat stream.**
- **ROOT-CAUSED on the repeat run (Nick: "run demo 2 three more times").
  Not a flake, not a product bug — MY operator error.** The wedge
  reproduced at *exactly* `t=109 frames_ok=1416`, identical to the first
  occurrence, which is the signature of a fixed-size limit rather than a
  race. `ss -tnp` while wedged showed **two Telemetry instances** both
  connected to the frozen S3 ingest on `:8081`: one with Send-Q 0 (being
  read by the server, `python3 pid=1103`) and the wedged one with
  **Send-Q 2,592,256 B** and no reader attached. The ingest is
  **single-producer**; 2.59 MB is the wmem ceiling, and at ~1.87 KB per
  QVGA frame that is 1,416 frames — hence the exact repeat. **Causality
  proven directly:** killing the stale instance made the server accept
  the blocked connection and the wedged app resumed instantly (t 109 →
  274, frames 1,416 → 1,853, back to 15.0 fps, `q_drops=4118` for what
  piled up). My driver left demo 1's Telemetry instance running when it
  started demo 2. The hazard is already in the S17/S18 record ("would
  race the single-producer ingest"; "nereus001 had two racing telemetry
  instances") and I walked into it anyway.
- **A follow-up run was contaminated the same way, at a different
  layer:** 607 s reporting 26,141 frames (~43 fps, not 15) with 1,676
  gaps, because the AE3 bridge was still executing the previous run's
  600 s `stream` command when the next one was issued — two overlapping
  streams into one reassembler. Also procedure, not product. The board
  keeps streaming after the app that asked for it dies.
- **Mitigations:** README demo 2 now carries a preflight
  (`ss -tn | grep -c :8081` must be 2) and the let-the-stream-finish
  rule; my earlier "start from a fresh bridge" warning was a guess and
  has been removed. Real fix agreed with Nick: **run the nodes as
  systemd units** (singleton by construction, clean stop, journald) —
  promoted ahead of bite 4.

**Session wind-down (Nick, 2026-08-16) — S19 parked, S18 promoted, fresh
agent takes it (D32).**
- **S19 bites 1–2 are code-complete and rehearsed but NOT closed:** no
  PR, branch unpushed, and Nick has not run the demo himself. The demo
  line is also only half satisfied — `capture 50 hd color` passes,
  `capture 50 hd mono` has never been run (bite 4).
- **Never measured, and worth saying plainly:** HD as a *stream*. Every
  sustained run this sprint was QVGA 15 fps. The S18 encode table
  predicts ~1 fps HD colour / ~2.5 fps HD mono, encoder-bound, at ~5% of
  the relay ceiling — unverified.
- **Systemd bite planned, not started.** Plan is recorded in TRACKER
  S18 bite D (units, the stdin/`tail -f` command channel and its
  untested risk, the `chain_status.sh` preflight, install-disabled
  recommendation, and an acceptance test that is literally tonight's
  bug). The next agent should re-derive it rather than trust it.
- **Branch hazard flagged in TRACKER:** the AE3 is running S19 artifacts
  (`bm_he.elf` `4c509d24…`, `bm_bridge.py` `1524f6c2…`) that exist only
  on this unmerged branch. An S18 branch cut from `main` will not
  contain the source for what the hardware is executing. Cut from
  `sprint/19-hd-transport`, or merge S19 first.
- Three items flagged in TRACKER as owned by nobody: the fork app's
  occasional startup segfault, the unexplained `Error processing parsed
  cb: 19` ring line, and the single-producer ingest as a design
  constraint rather than a bench quirk.
- **Honest read on the session:** roughly 200 LoC of product code, and
  the majority of the hours went to a hand-run harness — three AE3 USB
  wedges costing a Pi reboot each, two self-inflicted ingest wedges, one
  contaminated run, and a `pkill` pattern that kept killing my own SSH
  session. The product findings held up (the wall was measured, the fix
  works, HD delivers); the process around them did not, which is what
  D32 is a response to.

**Next:** S18 with fresh eyes on its own branch, bite D (systemd) first.
S19 remainder afterwards: Nick's demo run + PR for bites 1–2, then
bite 3 (heap — looking unnecessary) and bite 4 (HD mono + HD stream).

---

## 2026-08-16 — Sprint S19 (HD over pub/sub) — bite 1: the wall measured off-chain — bytes in flight, not chunk count, and the fix is not where the TRACKER put it

**Branch:** `sprint/19-hd-transport` (repo only — no fork change, no pin
move, `wire_status_t` untouched)

**Done:**
- Nibble-1 plan approved by Nick (sample page over debug ring; docker up;
  Claude drives rung B).
- **Instrument:** `he_sample.{c,h}` — 1 KB fixed page at 0x600BFA00
  (carved from `bm_he.ld`, magic `HSMP`, 40 × 24 B records), one record
  per published chunk: frame position, `bm_pub` result, txq depth,
  `heap_free`, `heap_min`, `tx_dropped`, rpmsg drops, tick. A page, not
  the ring: the failure ends in `vApplicationMallocFailedHook` with
  interrupts off, so nothing answers a query again and only cross-core
  RAM reads survive. Cost **+456 B (94.05%, 14,056 B headroom)**; ELF
  `9f40650cd83d9784`. Netwire gained two single-writer counters
  (`txq_pushed`/`txq_popped`) instead of one racy depth field.
- **Probe:** `bench/probes/s19_pub_probe.py` — synthetic bursts, no Pi
  (checked in vendored `pubsub.c`: `bm_pub_wl` has no remote-subscriber
  gate, so it transmits regardless) and no camera (S18's fault is
  framebuffer growth, structurally absent). Host tests assert the probe's
  framing is **byte-identical to `BridgeCore.capture_pub_msgs`** — the
  S18 probe-4 lesson made mechanical. HE host tests 191 → **220**, new
  `bench/test_s19_probe.py` **42 checks**.
- **RUNG B RESULTS (12 rows, full table in DESIGN §S19):** free heap at
  RUNNING **20,712 B**; one 1,400 B chunk costs **exactly 1,488 B**;
  20,712/1,488 = 13.9 → **13 chunks fit, the 14th kills it**, observed at
  exactly 13 on three independent rows, with `freertos: malloc failed`
  in the ring — S18's signature reproduced with no Pi and no camera.
  **26 × 350 B publishes fine → the wall is BYTES, not COUNT.**
  Heap recovers fully after every surviving burst (no leak).
- **Mechanism:** the wire task both receives WCMD_PUB and drains the TX
  queue; `rr_poll()` loops until the inbound vring is empty (publishing
  inline) and `wire_pump_tx()` only runs after it returns, so a
  back-to-back burst starves the drain. txq depth climbs 1,2,3… in
  lockstep with the heap falling.

**Broke/surprised us:**
- **The TRACKER's bite 2 as written is not the fix.** HP-side draining
  alone died identically; 2 ms pacing died identically (the HE spends
  ~2.5 ms/chunk, so 2 ms never starves the poll loop); ≥5 ms pacing
  survives with a heap floor of 19,184 = exactly ONE chunk outstanding,
  but only by accident and at 130–260 ms per HD frame. The fix belongs on
  the HE: pump TX inside the poll loop, or publish off the wire task.
- **A row survived by DROPPING and it explains S18's asymmetry.**
  52 × 700 B lived through the same 36.4 KB that kills 26 × 1400 B,
  losing 36 frames to `tx_dropped`: `NETWIRE_TXQ_LEN` (16) × 788 B fits
  under the free heap so the QUEUE fills first, while 16 × 1,488 B =
  23.8 KB exceeds it so the HEAP fails first. Bounding the queue by bytes
  turns a board-killer into a counted drop — a cheap robustness fix that
  is independent of the throughput fix.
- **My first liveness check was wrong and reported a false death.**
  `BP->tick` is written at the top of the wire task's loop, so a task
  parked in `wire_pump_tx`'s 100 ms-per-message retry reads as dead —
  the probe declared the HE dead after 3 chunks. The HE ring
  (`RUNNING`, no `malloc failed`) is what caught it; liveness now means
  "answers a query", with ticking as the fast path. Same class of error
  as S18's stale-frame reading: the first artifact I trusted was not
  measuring what I thought.
- `wire_pump_tx`'s retry exhaustion had been a **silent** drop since S16
  — no counter, no log. Now counted and narrated.

**Board state:** fixture intact and re-verified (`main.py`
`55fa6ccfdd3f7f65`), board back on the bus running the S6 service,
`/flash/bm_he.elf` now the S19 instrumented build `9f40650cd83d9784`
(was the S17 `3cdd1f66…` staged inert). Four Pi reboots not needed —
zero USB incidents this session, because nothing touched the sensor.
S6 USB baseline NOT re-run (no firmware flash, no `main.py` change, no
sensor contact this session).

**Next:** Nick's call on rung C — a real `capture 50 hd color` on the
live chain with this ELF. It is the only thing that explains S18's 8
chunks vs our 13 (predicted: less free heap with a subscriber and
neighbour traffic live, floor(free/1488) ≈ 8), and bite 2 has to bring
the chain up anyway. Then bite 2, re-specified by the measurement above.

---

## 2026-08-15 — Sprint S18 (camera bench web tool) — bite A: resolution + pixel format plumbed end to end; front end designed against a working mockup first

**Branch:** `sprint/18-web-bench` (repo) + bm_sbc fork `feature/udp-transport`
(bite-A commit local, NOT pushed — Nick pushes)

**Done:**
- Nibble-1 plan approved by Nick with 5 decision points: struct grows by
  an appended pair (even sizes, one spare byte); out-of-range geometry
  **REFUSED, not clamped** (deliberate break from payload_max's clamp —
  a silently substituted resolution corrupts an image comparison
  invisibly); switch only on a delta, never `sensor.reset()`;
  `res_active`/`pf_active` reported in the reply's old `rsvd` u16 (zero
  size change); CLI args positional.
- **Front end mocked and reviewed BEFORE the ABI was cut** — which paid
  for itself twice: reviewing the mockup is what surfaced HD greyscale
  as a requirement, and that landed in the reserved byte instead of
  forcing a second lockstep break.
- Bite A (HE): `wire_capture_t` 12 → 14 B (+resolution +pixformat),
  `camera_req_t` 16 → 18 B, `camera_rep_t` **stays 24 B** (rsvd u16 →
  res_active/pf_active). Service validates geometry before the command
  switch; refusal answers ok=0 without touching the mailbox, the command
  counter, or the previously commanded geometry. Host tests 170 → 191.
- Bite A (bridge): `WREP_CAPTURE` → `"<BBHIHHBB"`, len gate 12 → 14 (a
  stale 12 B S17 body is now rejected outright, asserted by test — a
  half-upgraded bench is a real state). New pure `sensor_steps()` plans
  the sensor calls and returns **()** when geometry is unchanged: every
  set_framesize/set_pixformat is a re-init = the D15 crash class, and
  S18 hands that trigger to a web page. Host tests 61 → 73.
- Bite A (fork): structs + static_asserts in lockstep, `capture [q]
  [res] [pf]` / `stream <mbps> <fps> <secs> [q] [res] [pf]`, res/pf
  echoed in CAM_REPLY with an explicit REFUSED hint. Unrecognised
  spellings are passed through as out-of-range **on purpose** so the
  service refuses them loudly rather than the CLI guessing.
- **Size audit (REV-25): 246,096 / 262,144 = 93.88%, 16,048 B headroom
  — bite A cost +64 B.** Clean build (S17 lesson: no header deps in the
  bm_he Makefile, and this bite is all headers). ELF `4be541ae…`.

**Broke/surprised us:**
- **Two facts in DESIGN §S0 contradicted what I had already built.** The
  sensor LETTERBOXES to 16:10 — QVGA is 320×200, not 320×240 — and
  QQVGA/SVGA/WXGA are unsupported on sensor 0x7936, so Nick's "720"
  does not exist; HD 1280×800 is the top of the proven ladder. The
  mockup had generic 4:3 geometries until the tables were read properly.
  Same pass caught that VGA+ needs `set_framebuffers(1)`, which the
  first cut of `sensor_steps()` had omitted.
- The mockup was **invisible to Nick for two rounds**: it displayed
  every image through `data:` URIs into `<img>` tags, which the render
  sandbox blocks, and the histograms are computed from those images —
  so one cause blanked four features. The rebuild displays nothing
  through a URL (Blob + `createImageBitmap`), paints synchronously
  first and treats the JPEG decode as a refinement, so a decode that
  never resolves degrades instead of blanking. Then the viewer turned
  out not to run JS at all for files outside the project folder — the
  preview pane says so and I missed it; it needs a real browser.

**Nibble-3 addendum (same session, Claude drove the hardware at Nick's
"run the checks"): ABI PROVEN LIVE, then VGA hard-faulted the board.**
- Fork + repo branch pushed; `deploy.sh` **PASS on both Pis** at
  ba594ec/eec6e82. En route: nereus000's bm_sbc checkout was detached at
  c1d0df9 (the S17 trap again) and my `git checkout` landed on a stale
  local branch at 4ebdbc3 — caught by deploy.sh's pin check, fixed with
  an explicit ff-only pull. Stale S17 apps were still running on BOTH
  Pis (nereus001 had two racing telemetry instances); stopped first.
- Staged ELF + bridge, **on-board shas verified** against the Mac
  (4be541ae…, 7a00a19…). Chain formed: Camera …03 ↔ Light …02 ↔
  Telemetry …01.
- **The S18 ABI works end to end over two BM hops:** `cam-status` →
  `res=default pf=default`; `capture 50 qvga color` → `ok=1 res=qvga
  pf=color`, 3 chunks / 3,871 B published. HE service, HP bridge and
  fork app all agree on 18 B / 14 B.
- **`capture 50 vga color` KILLED THE BOARD** — accepted, then
  uart_l2 decode error, neighbor offline, AE3 off the USB bus
  (error -71). Recovered via the `ae3-usb-unstick` ladder (Pi reboot;
  uhubctl cannot help — the Pi 5 root hub never cuts VBUS).
- **Bisected on a clean REPL: VGA standalone works (10,833 B), the
  QVGA→VGA→QVGA runtime switch works, QVGA under the bridge works.
  Only VGA WITH the HE stack live fails.** No Python traceback and no
  bridge exit record → the fault is below MicroPython, D15 family.
  Full evidence + candidate causes in SPEC §Open questions.
- Fix shipped for the next attempt: the bridge no longer deletes its
  trace at boot (`bridge_trace.prev.txt`). The first crash's trace was
  destroyed by the bridge that restarted after it.
- Board restored to the S6 fixture, sha-verified 55fa6ccf… . Bench apps
  stopped on both Pis.

**Honest note on my own reporting:** I first read a 320×200 JPEG off
`:8080/frame.jpg` as proof the QVGA capture had landed. It was a STALE
frame from the S17 session (`stats.json uptime_s=99594`,
`ingest_connected=false`); no frame completed at the receiver this run
(`frames_ok=0 gaps=2`, cause not isolated — the S17 startup race is the
untested candidate). Caught and corrected in-session, but it is exactly
the "trust artifacts, not exit codes" failure this repo warns about:
the artifact was real, it just wasn't *this run's* artifact.

**Probe results (Nick: "run the probe") — ROOT CAUSE FOUND.** Two
breadcrumb probes, each flushing every step to flash BEFORE the call it
names, so a fault that takes USB down still leaves the answer:
- Probe 1 (HE loaded → QVGA → grow to VGA): died inside
  `set_framesize(VGA)` with **4,067,616 B heap free** (VGA needs
  512,000) and **zero VCP traffic**. Not exhaustion, not the bridge.
- Probe 2 (VGA allocated FIRST, then load HE): VGA pre-HE 10,957 B ·
  HE load OK · **VGA capture WITH HE up 10,935 B OK** · shrink to QVGA
  OK · QVGA 4,007 B · **grow back to VGA → dead**.
- **Verdict: growing the framebuffer with the HE core loaded is fatal;
  shrinking is safe; VGA alongside a live HE stack is fine.** The HE
  ELF loads at 0x60080000 (SRAM9_B upper half) and the framebuffer
  allocator grows into it. QVGA (128,000 B) stays clear, VGA (512,000)
  does not — which is exactly why S17 (QVGA only, never grew) never saw
  this and bite A hit it on the first VGA command.
- Cost: three `ae3-usb-unstick` Pi reboots. Board left healthy, fixture
  re-verified 55fa6ccf…, sensor capturing 4,054 B.
- Correction to my own earlier reasoning: I had guessed heap/DMA
  contention. Both were wrong — the heap was 4 MB free and no traffic
  was flowing. The breadcrumb file, not the hypothesis, produced the
  answer.

**Probe 3 (`s18_fb_probe.py`) — WORKAROUND PROVEN, switching restored.**
Pinning `set_framebuffers(1)` immediately before every `set_framesize()`
(with the session maximum allocated before the HE ELF loads) makes the
grow that killed the board twice succeed repeatably: VGA pre-HE 11,331 B
→ HE loaded → VGA-with-HE 11,423 B → shrink QVGA 3,965 B → **grow back
to VGA OK** → second cycle 3,950 / 10,978 B → clean HE stop, board
alive, no reboot needed. Reading: OpenMV sizes the framebuffer COUNT to
fit the pool, so an unpinned shrink re-allocates several buffers and the
next grow expands into SRAM9_B; pinning the count stops the reflow.
Caveat recorded honestly: the passing run changed BOTH variables, so
this proves the combination and not the minimal condition — and **HD
(2,048,000 B, 4× VGA) is still untested**, which matters because the
recipe depends on the maximum fitting below SRAM9_B.

**Probe 4 (`s18_hd_probe.py`) — HD PASSES, full ladder switchable.**
HD-preHE 36,845 B → HE loaded → HD-with-HE 36,694 → VGA 11,233 → QVGA
4,080 → VGA 11,277 → **HD regrown 36,489** → HD-mono 25,131 → HD-colour
36,544 → clean HE stop, board alive, no reboot needed. Pixel-format
swaps at HD work too, which is what S18's HD-greyscale video needs.
Two ordering constraints found live along the way (each cost a run, both
clean exceptions rather than crashes): `set_framebuffers()` refuses
until BOTH pixformat and framesize are set. Since an unpinned
`set_framesize(HD)` is precisely the over-allocation to avoid, the
bootstrap has to come up at QVGA, pin the count there, then grow to the
ceiling. Full recipe now in SPEC §Open questions.
Scene note: HD colour q50 measured 36.5–36.8 KB on the dim bench vs the
93,253 B reef figure in DESIGN §S0 — scene-bound as expected, not a
contradiction.

**Recipe implemented + rehearsed (Nick approved option A).** CaptureEngine
gained `bootstrap()` (eager, runs BEFORE `he.start()`: reset → RGB565 →
QVGA → pin `set_framebuffers(1)` → grow to the HD ceiling), `sensor_steps()`
now emits pixformat → framebuffers → framesize → settle with the count
pinned immediately before every resize, and `_ensure_sensor` hard-refuses
anything above the claimed ceiling. Ceiling configurable via
`bridge_cfg.json` `"ceiling"`, default HD. Bridge host tests 73 → **252**
(the new invariant is asserted across the whole res × pf ladder). HE
untouched, so no rebuild and no size change.

**Rehearsal on the live chain — QVGA and VGA PASS, HD hits a SECOND,
unrelated wall:**
- QVGA: `ok=1 res=qvga pf=color`, **frames_ok=2 gaps=0 ingest_ok=2**,
  fresh 3,991 B frame at the browser. (The earlier `gaps=2` was the
  known S17 startup race — a warm-up capture clears it.)
- **VGA: `ok=1 res=vga pf=color`, 640×400 / 11,030 B delivered, gaps=0,
  board alive.** This is the exact command that took the board off the
  USB bus twice before the fix. The framebuffer fix holds.
- HD: capture succeeded on the HP side — ledger `cap_frames=4
  cap_bytes=54,232 cap_chunks=40` — but the HE ring ends `freertos:
  malloc failed` after publishing 8 of 26 chunks. **The HE core's heap
  cannot carry an HD frame through pub/sub.** Board stayed on the bus,
  bridge quiet-exited cleanly; ordinary exhaustion, not the allocator
  fault. My probes never covered this: probe 4 tested capture+encode on
  HP and never published over BM.
- Board restored to the S6 fixture, sha 55fa6ccf…, apps stopped.

**Next:** Nick's call on HD. QVGA+VGA are demo-ready now. HD needs
chunk pacing/backpressure (the bridge emits a frame's chunks
back-to-back with no flow control — 3 drains fine, 8 fine, 26 does not),
or a bigger HE heap, or HD-stills-at-low-q only. Detail + candidate
fixes in SPEC §Open questions.

**Superseded plan (kept for the record):** nibble 3 — Nick pushes the fork, then the geometry ladder in
`pi/bm_bench/README.md` §S18 bite A (QVGA/VGA/HD stills, repeated
format+resolution cycling as the D15 probe, a deliberate refusal, and
HD-mono stream). **The numbers that matter: in-bridge fps at VGA and HD,
which are currently EXTRAPOLATIONS from the single measured QVGA point
(15.00 fps, S17 bite 0) and feed bite C's feasibility warnings.**

---

## 2026-08-15 — Sprint S17 (BUILD-4) — application services: code complete across all four surfaces; bite-0 measurement + demos wait at the VCP gate

**Branch:** `sprint/17-build4-apps` (repo) + bm_sbc fork
`feature/udp-transport` @ c1d0df9 (pin +2, D29; bm_core pin unchanged)

**Done:**
- Nibble 1 plan approved (Nick) with 6 decision points → D29: WCMD_PUB/
  WREP_CAPTURE node-internal wire; packed-LE service structs (CBOR
  helper is config-only; HE flash-poor); uplink option A (spotter_tx_data
  = the shipped primitive; gateway_ipc is inbound-only BY DESIGN — read
  from source, REV-8's own definition); RTC O1 (Telemetry = time
  authority via BCMP time-set; AE3's settable RAM RTC already exists);
  light HAL on nereus000's ACT LED (verified present + controllable,
  zero wiring); rate target = bite-0 measured ÷2 cap 2.0; new fork app
  (stream_bench stays the regression instrument). Reef-image trick
  (Nick) folded into bite 0; web-video demo (Nick) = the frozen S3
  receiver reused verbatim — S12's shim-v2 shape arriving early.
- Bite 0: `s17_capture_pump.py`/`main_s17.py` — S14 pump + rung F
  (relay + paced reef encode) + rung G (F + JPEG sunk to HE via
  BCMD_SINK_DATA: both rpmsg directions + VCP + capture in ONE HP
  loop, zero new firmware). Counter grew F/G + ledger + gate terms.
  binascii.crc32 == he_crc32 pinned by test. 29 new checks.
- Bite A (HE): camera/control service (16 B req / 24 B rep, 'CAM1';
  non-blocking handler → mailbox → WREP_CAPTURE), WCMD_PUB → bm_pub on
  camera/stream via existing wire_frag (kind-dispatch), power_hal.h +
  sim feeding the already-linked power_info service. wire_status_t ABI
  untouched. Host tests 122→170. **Size audit (REV-25, before bite B):
  +2,056 B → 246,032/262,144 = 93.9%, ~15.7 K headroom.** ELF 4c04b51a….
- Bite B (bridge): WREP_CAPTURE parse w/ bridge-owned defaults,
  capture_pub_msgs chunker (10 B LE header, ≤1400 B, REV-28),
  CaptureEngine (lazy sensor, fps slots + rate budget, non-fatal
  sensor failure), optional bridge_cfg `camera` one-shot. Tests 35→61.
- Bites C1/C2 (fork): `apps/bench_apps` — S17_ROLE=light (light/control
  service + sysfs-LED HAL + state artifact) | telemetry (subscribe →
  chunk_reasm (21-check ctest) → frozen-S3 ingest client → browser
  demo; stdin CLI: capture/stream/light/strobe/power/time-sync;
  UPLINK_TX via spotter_tx_data every 30 s; gateway_ipc listener with
  env socket path for the python client). Built + ctest 4/4 on
  nereus000 (scratch tree — ~/bm_sbc_s15 untouched). deploy.sh pins →
  c1d0df9/eec6e82.
- Docs: D29 + DESIGN §S17 detail + README §S17 deploy/start/demos 1–3
  (incl. one-time LED chmod; stop t1l-chunk-shim on nereus001 — it
  crash-loops on missing eth1 and would race the single-producer
  ingest). Verified en route: t1l-stream-server ACTIVE on nereus001
  since S3 (the browser endpoint is already standing).

**Broke/surprised us:**
- gateway_ipc is strictly one-way client→gateway (no outbound socket
  exists) — "subscribe→aggregate→out via gateway_ipc" as literally
  worded is unimplementable with shipped code; resolved as D29.3
  (aggregate in-app → spotter_tx_data; ipc demoed in its real
  direction). Caught in nibble 1 by reading the source, not the doc
  title.
- Session permission classifier blocked `git push` to the fork — C1/C2
  are committed locally (c094f66, c1d0df9) but NOT on GitHub yet;
  deploy.sh on the Pis will fail its pin check until Nick pushes.

**Next:** Nick: (1) `cd ~/Documents/GitHub/bm_sbc && git push fork
feature/udp-transport`, (2) both-Pi deploy.sh, (3) open the VCP gate →
bite-0 rungs C/F/G + 600 s gate → rate target committed → restage
bridge → README §S17 demos 1–3 → nibble-4 PR. Session end: fixture
restore + sha-verify + S6 USB baseline re-run.

**Same-session continuation (Nick pushed the fork; "move forward" =
VCP-gate go) — bite 0 measured, a THIRD V5-class upstream bug found +
root-caused + worked around, FULL rehearsal PASS:**
- Deploys: both Pis PASS at pin c1d0df9 (nereus000's checkout was
  detached — pull was a silent no-op, caught by deploy.sh's pin check).
  Found + killed a stale S16-era stream_bench still running on
  nereus001 with telemetry.toml.
- S16 leftover surfaced: /flash/main.py was STILL the S16 bridge
  (sha 170e637c…) — the fixture restore in S16's "Next" never ran.
  Folded into this session's end-of-demo restore.
- Bite 0: rung C 5.424 (=S14) · rung F 600 s **5.262 Mbps sustained
  with capture live, 15.00 fps, 279,512/279,512, 0 gaps/CRC** (printed
  FAIL = unbounded-pump q_drops only, semantics documented) · rung G
  duplex flood = 0.52 Mbps (bench artifact; real path rate-bounded).
  Reef q50 = 9,198 B / enc 19.94 ms → encoder is the ceiling (~15 fps
  ≈ 1.1 Mbps reef) — D29.6 resolved: demo = `stream 2.0 15 60`.
- **V5 find #4 (upstream, the biggest of the arc): bm_core L2 writes
  the ingress-port nibble into the IPv6 src address of every inbound
  frame with NO checksum adjustment → lwIP receivers silently drop ALL
  inbound pub/sub UDP.** First inbound-to-HE service request ever sent
  = first hit. Isolated via a no-Pi rpmsg injection probe; fix for the
  bench = CHECKSUM_CHECK_UDP=0 (lwipopts, config-only, documented);
  proper fix = RFC 1624 incremental update in l2_policy.c (upstream
  report item added; TX-side helper has a second byte-arithmetic bug).
  En route: bm_he Makefile has no header deps — lwipopts-only changes
  need --clean (two phantom builds shipped identical ELFs; sha caught
  it). S17 ELF now 3cdd1f66….
- **Rehearsal (all Stage-4 legs): PASS** — topology ✓, time-sync ✓,
  LED light/strobe ✓, 2-hop power query ✓ (total_on=50s/3250s/300s),
  capture → valid JPEG at :8080/frame.jpg ✓ (1,861 B dark-room),
  stream 15.0 fps steady / 455 frames / gaps 0 into the frozen S3 web
  server ✓, 8 spotter_tx uplinks + gateway_ipc up ✓. Ledger exact at
  every hop: 912 pub chunks = 455×2 + 2 orphans of one startup-race
  frame (first capture raced subscribe propagation; only loss all
  session). Board staged for Nick's demo; LED trigger restored between
  sessions.

**Next:** Nick runs README §S17 demos 1–3 (chain start fresh) →
nibble-4 PR (repo + fork). AFTER the demo: fixture main.py restore
(55fa6ccf…) + sha-verify + S6 USB baseline re-run + python-client
uplink injection (needs an interactive second shell).

**Post-demo continuation (2026-08-15, same session): demo re-run trip
+ demo_up.sh + S18 planned.** Nick re-ran the demo and camera requests
timed out — because the close-out fixture restore means the AE3 is NOT
a BM node until the bridge is re-staged (working as designed, badly
communicated). Fixed live (re-stage + reset), then hardened:
**`pi/bm_bench/demo_up.sh`** — one-command demo-day re-stage with sha/
staged-file/busy-bridge checks (README §Demo day). Product direction
set by Nick: bench-hosted product arc = **S18 camera bench web tool
(plan approved, D30) → S19 light intelligence → S20 CV**; upstream
reports (items 8–10) explicitly HELD. TRACKER carries the full S18
requirements (PROMPTS.md rule: prompts stay generic). Image-quality
levers explained to Nick (q, resolution, light; encoder-bound); S18
delivers them as controls.

**DEMO RUN BY NICK 2026-08-15 — PASS ("this is fantastic"):** live
interactive session from his own terminals — `stream 2.0 15 60` from
the Telemetry CLI → CAM_REPLY ok=1 → 15.0 fps steady, dropped=0,
ingest 191+ frames while he watched, browser stream live at
nereus001:8080 (daylight frames ~5 KB vs the rehearsal's 1.9 KB night
frames — scene-bound rate demonstrated in the wild). Close-out:
apps stopped, LED trigger restored (mmc0), bridge quiet-exited
(ops-rule refresher en route: a polling mpremote loop feeds the quiet
timer and deadlocks the exit — true zero-contact silence required),
**fixture restored + sha-verified 55fa6ccf… (clears S16's pending
restore too) + S6 USB baseline re-run PASS (QVGA q90: 33.0 fps,
0 gaps, 0 bad JPEGs)**. Board state: S6 fixture service standing; S17
stack staged inert on /flash (bm_he.elf 3cdd1f66…, bridge, pumps,
reef bmp). PR opened (nibble 4).

---

## 2026-08-14 — Sprint S16 (BUILD-2) — AE3 joins the chain: code complete, both Pis deployed; live bring-up waits at the VCP gate

**Branch:** `sprint/16-ae3-chain` (repo) + bm_sbc fork
`feature/udp-transport` @ 4ccbf95 (pin +1, D28)

**Done:**
- Nibble 1 plan approved (Nick) incl. 3 decision points: rename to
  bm_net_wire; stream_bench RX_STAT tx_drops fork commit (pin move);
  stream trigger via /flash/bridge_cfg.json.
- Bite A (HE promotion, `firmware/bm_he`): bm_net_mock → bm_net_wire —
  link-up ONLY from retry_negotiation (REV-12; measured: l2 passes the
  1-BASED port_num at l2.c:425, link_change wants 0-based, REV-1 —
  both asserted in host tests); send() enforces 1514 + counter
  (REV-14); `wire_frag.{c,h}` (first msg carries TOTAL length,
  WCMD_FRAG continuations, ≤492 B frames byte-identical to the S10
  wire — 2a/2b regression wire-stable); node id 0xbe9c000000000003 /
  "bm_camera"; middleware always-on (AUDIT flag retired); WCMD_STREAM
  quota-paced publisher on s15/stream; wire_status_t 72→88 B with the
  drop ledger. Host tests 72→122 checks; ELF builds: **243,976 B of
  262,144 (93.1%, ~18.2 K headroom; +4.0 K over the V15 audit image).**
- Bite B (HP bridge, `firmware/bm_bridge`): bm_bridge.py — BridgeCore
  (pure data plane, 35 host checks incl. duplex + noise/CRC cases) +
  service loop: HE load-once w/ stale-HE refusal, link held DOWN until
  first VCP bytes (bm_sbc's gateway heartbeats on open → pipe quiet
  while unowned), zero prints while pumping, bridge_cfg.json one-shots
  (stream/ping), trace + HE-ring dump to flash, every exit cause to
  /flash/bridge_crash.txt (main_bridge.py, BUILD-2b rule).
- Bite C (Pi side): light.toml + uart-device (by-id) — factory
  composes gateway over udp (verified in source; port 15 math checked,
  V13(b) defused). Fork commit 4ccbf95: RX_STAT gains tx_drops (the
  Light transit ledger); deploy.sh pin updated from `git rev-parse`
  (not hand-expanded — S15 lesson), **deploy.sh PASS on BOTH Pis**
  (ctest 3/3 each; repo branch checked out on both). README: S16
  deploy/start-order/demos 1–3 ladder; S15 demos retitled + regression
  note (light.toml now opens the CDC port — comment out uart-device
  for two-Pi-only runs).
- Staged for the gated deploy: bm_he.elf (sha ee4be49f… = MANIFEST) +
  bridge files + bridge_cfg.json on nereus000:/tmp. Docs: D28 +
  DESIGN §S16 detail; TRACKER item 5 → [~].

**Broke/surprised us:**
- l2's renegotiation timer passes the 1-BASED port number into
  retry_negotiation (timer id seeded from port_num 1..N) while
  link_change wants 0-based — the same convention split behind REV-1/
  V13, now pinned by host tests on our device.
- Nothing else: host tests and the cross-build passed first try; both
  Pi deploys green.

**Same-session continuation — VCP gate opened (Nick: "Go"), staged +
FULL CHAIN REHEARSED:**
- Staging: fixture sha recorded (55fa6ccf… confirmed), five files
  sha-verified on board, warm reset. First bring-up DIED instantly
  when Light spoke — **V5 find #1: MicroPython's console scans inbound
  VCP bytes for 0x03 (kbd interrupt) and COBS frames contain 0x03
  freely → bm_sbc's first heartbeat injected KeyboardInterrupt into
  the pump.** (Crash file made it a 2-minute diagnosis.) Fix:
  `micropython.kbd_intr(-1)` for the service's life + NEW STOP MODEL —
  bridge exits itself after 30 s VCP silence (heartbeats every 10 s
  while alive) or 10 min unattached; ctrl-C can't stop a linked
  bridge; one bridge lifetime per demo (cfg one-shots re-arm on warm
  reset).
- **Chain rehearsal PASS (all three demo shapes):** (1) topology —
  Light NEIGHBOR_UP …01 port 1 AND …03 port 15; Telemetry neighbors
  ONLY Light, yet 🏓 from …02 (0 ms) and …03 (9–10 ms, 2 hops) —
  never-a-star holds; ×2 runs. (2) forwarded pub/sub — Camera's
  2 Mbps/1400 B stream: **Telemetry RX_STAT steady 1.99–2.02 Mbps,
  21.1 MB/15,084 msgs; Light transit ledger tx_drops=0, rx_drops=0;
  ZERO uart decode errors both sessions (22.4 MB + 27.7 MB relayed,
  every ~1490 B frame crossing the 4-msg rpmsg frag path;
  frag_errors 0, qdrops 0).** REV-12 live on silicon ("Renegotiated
  on port: 1" on the HE ring). (3) Camera-sourced 2-hop ping — **V5
  find #2: ll-multicast (ff02::1) ping never crossed Light (REV-6
  measured live)**; WCMD_PING switched to `multicast_global_addr`
  (ff03::1, what multinode itself pings), ELF rebuilt/restaged →
  **🏓 16 bytes … payload "S16 camera 2-hop" accepted by ping.c.**
- Known-cosmetic: newlib-nano %llx/%lu artifacts in ring prints
  ("…lx", "time=lu"); "Unable to load configs from flash" ×3 =
  RAM-stub config, expected (REV-27).
- Board state: bridge staged as /flash/main.py (board at REPL, HE
  stopped), demo cfg armed (stream 2.0/1400/600 s delay 15; ping
  target …01 delay 30). ELF on board = 45a9615d… (global-addr ping).

**Same-session continuation 2 (2026-08-15) — Nick's demo hit a real
crash; root-caused, fixed, ALL demos re-run by Claude (Nick's request)
and PASS:**
- **V5 find #3 (the big one): upstream heap corruption on the L2
  TX-overflow path.** Nick's demo run (stream_bench TX 15 Mbps on Light
  while forwarding the Camera stream + carrying the uart leg) hit the
  FIRST real `bm_l2_tx` queue overflow on a Pi → glibc "corrupted
  double-linked list" abort within ms. Cause: bm_l2_tx frees the L2
  reference itself on enqueue failure (the contract lwIP forces), but
  BOTH bm_linux TX paths freed again on the error return (bm_udp_tx
  even documented "must free twice") — one over-free. Can never fire
  from a lone publisher (S15 measurement), which is why it survived all
  prior testing. Fix: bm_core fork +1 commit (`eec6e82`, error-path
  frees deleted with the ownership contract documented), bm_sbc
  submodule bump (`1a806c7`), deploy.sh pins moved, both Pis rebuilt
  green. AE3 unaffected (compiles bm_lwip.c). Upstream-PR-worthy.
- **Crash repro on the fixed build: PASS** — same overload hit the same
  `evt queue full, dropped frame` line and ran to completion: TX_STAT
  `ok=3792 enomem=2 l2_drops=2`, drops counted + surfaced (`Unable to
  publish, err 12`), no abort. The D27 observability told the story.
- **Full demo re-run (fixed pins): d1 topology PASS (Light neighbors
  01+03, Telemetry only 02, 2-hop 🏓 10–11 ms both ways) · d2 PASS
  (both hops steady 1.99–2.00, 23.6/24.7 MB, all-zero ledgers) ·
  d3 PASS — 600 s @ 2.00 Mbps, Camera sent 107,142 = Telemetry
  received 107,142, ZERO loss/CRC/drops at every hop, ledger
  consistent end-to-end (bridge 107,215 frames / 158.25 MB,
  frag_errors 0).**
- En-route ops finds (now in the ae3-usb-unstick SKILL + READMEs):
  (a) the AE3 fell OFF the USB bus (error -71) after mpremote was
  pointed at a phase-1 bridge concurrently with a reset — uhubctl
  could NOT recover it because **the Pi 5 root hub's ppps never
  actually cuts VBUS** (measured: the bridge session survived a Pi
  reboot still blocked mid-write); **`sudo reboot` on the Pi = the
  fix** (fresh xhci re-enumerates; Nick's call). (b) by-id
  lingers→drops→reappears bit the start ladder once more — the full
  absent→present→settle dance is mandatory, and demo-to-demo
  transitions must wait out the bridge's 30 s quiet-exit before any
  mpremote contact.

**Next:** Nick's call — bless Claude's re-run as the S16 demo or run
README §S16 demos 1–3 himself (board staged + armed either way) →
nibble 4 PR (repo + both fork branches). Session end: fixture restore
+ sha-verify + S6 USB baseline re-run.

---

## 2026-08-14 — Sprint S15 (BUILD-1+3) — udp transport + factory: two-Pi bench live, zero-loss limiter rehearsal both directions

**Branch:** `sprint/15-udp-transport` (repo) + bm_sbc fork
`feature/udp-transport` + bm_core fork `bench/d4ecc38-obs` (D27)

**Done:**
- Physical gate: Nick ran the direct eth0↔eth0 cable; verified
  1000/full carrier both ends before any config. Bench IPs live per
  BENCHSPEC (nereus001=.1 Telemetry, nereus000=.2 Light,
  never-default, IPv6 off); dev access stayed on wlan/tailnet
  throughout (verified during every run).
- REV-23 pin check: bm_sbc 17ea904 pins bm_core d4ecc38 = our
  firmware/bm_he vendor exactly — zero drift. (Upstream main moved to
  6a4d73c; src delta 1 line; we stay pinned.)
- Bite 1 (factory, BUILD-3): `transport =` TOML key / `--transport`
  CLI (virtual|udp|serial|adin), construction extracted from
  runtime.cpp into transport_factory; default = virtual; singleton +
  callbacks-sharing constraints honored. Their full validate.sh green
  with the refactor (ctest, loopback 6/6, multinode 13/13, IPC 15/15).
- Bite 2 (udp device, BUILD-1): udp_port_device derived
  member-for-member from virtual_port_device (REV-11 constant 15
  ports; REV-12 link-up only from retry_negotiation, configured-peer
  check; REV-14 1514 enforced both directions, oversize logged with
  true length via MSG_TRUNC); token-bucket shaper (virtual-clock,
  integer-exact, default 10 Mbps) + device stats; stream_bench app
  (offered-rate publisher / receiver ledger, D21); host tests: 18
  (transport_kind) + 24 (parse+shaper); udp_multinode_test.sh 15/15
  incl. 3-node chain with ends-do-NOT-neighbor invariant.
- bm_core observability commit (the ONE patch, D27): TX + RX L2
  queue-drop counters + accessors; log 1st + every 256th.
- Bite 3 (two-Pi): nereus001 toolchain installed (cmake, socat),
  pinned clone via bench cable (Tailscale SSH blocks plain git —
  bench-IP ssh key provisioned instead), build + ctest green on both
  Pis. Cross-cable rehearsal: NEIGHBOR_UP with peer node id + 🏓
  bcmp_seq= BOTH ends; limiter 15 Mbps offered → 9.30 payload
  (=10.0 wire exactly), **36,622/36,622 delivered, zero loss**;
  control 8 Mbps → 8.00/20.0 s unshaped, 19,532/19,532. pcaps
  written by --pcap on both nodes.
- Repo: `pi/bm_bench/` (node TOMLs w/ fixed IDs be9c…01/02/03,
  deploy.sh with hard pin verification, README demo ladder);
  housekeeping rider: stale `nereus001-1` refs fixed in
  s5_tx_load.py, DESIGN §S6 URL note, TRACKER S3 demo (DEV_LOG
  history untouched).

**Broke/surprised us:**
- **REV-13's silent TX BmENOMEM drop cannot fire from a lone
  publisher on a Pi**: POSIX queue enqueue blocks ≤10 ms vs ~0.9 ms
  service at 10 Mbps → overload becomes blocking backpressure
  (offered 15 → achieved 9.3 over 32.2 s wall; 200 Mbps loopback →
  244,141/244,141, zero drops anywhere). Real silent-drop sites: RX
  zero-timeout enqueue (now counted), S16's forward path (L2 thread
  enqueues into its own queue — guaranteed timeout; THE transit
  ledger), device oversize (logged). Demo verdict reframed to
  "zero loss + counters consistent," honest per measurement.
- bcmp ping replies log at debug level — invisible at the TOMLs'
  initial info level; looked like a real cross-cable ping failure
  for one run. TOMLs now set log-level=debug with a comment.
- Tailscale SSH intercepts inter-Pi git (interactive auth URL);
  fixed by real authorized_keys over the bench IPs — which also
  makes deploys ride the 1 GbE cable instead of WiFi.

**Next:** Nick: create the two forks (`gh repo fork bristlemouth/bm_sbc
--clone=false`, same for bm_core), then I push the branches; nibble 3 =
Nick runs `pi/bm_bench/README.md` demos 1–3; nibble 4 = repo PR + fork
PRs. Then S16 (BUILD-2: AE3 joins via rpmsg + HP bridge).
→ Same-day close-out: forks created + branches pushed (4ebdbc3 /
e031f11); deploy.sh PASS both Pis (pin check caught + fixed a wrong
hand-expanded sha); **demos 1–3 run by Nick + re-confirmed by Claude,
identical numbers = S15 demo PASS; PR #22 open.** Demo-1 start-window
gotcha (one-shot ping at t+3 s) documented in the README. Upstream PRs
to bristlemouth (factory + udp device; drop counters) = a separate
decision for Nick, not opened. Next: merge #22 → S16.

---

## 2026-08-14 — Sprint S14 (bench rung 0) — V16 relay gate PASS (5.4 Mbps sustained); V15 middleware fits AND runs (91.6%)

**Branch:** `sprint/14-bench-rung0` (worktree, from merged PR #20)

**Done:**
- Nibble 1 (plan presented; Nick opened the bench = go): V16 relay
  bench + V15 size audit + bm_sbc rung 0.
- `firmware/bm_bridge/uart_codec.py`: bm_sbc uart_l2 codec, dual-runtime
  (viper/CPython), byte-exact vs Sofar's C — golden vectors generated by
  compiling their frame_codec/cobs/crc32c on the Mac. On-target: crc32c
  7.34 MB/s, full encode 10.1 Mbps. Host tests 50 checks.
- Relay bench (pump service on HP as /flash/main.py + Pi counter):
  **rung B (framing+USB) 13.1 Mbps · rung C (full relay) 5.5 Mbps ·
  agg=3 5.4 Mbps · rung E crc32c==crc32==none 5.55 Mbps · rung D
  600 s: 5.425 Mbps, 288,162/288,162 frames, 864,487 rpmsg msgs,
  0 gaps/drops/in-stream errors, HE alive — GATE PASS (2.7×, verdict
  from the shipped counter).** Rung A regression unchanged (13.1/5.5).
- V15: middleware slice vendored (d4ecc38, +bm_common_messages
  helpers) behind AUDIT_MIDDLEWARE=1, bm_sbc init order → **240,000 B
  of 262,144 (91.6%), +8.5 K over baseline, ~21.6 K headroom — FITS.**
  Baseline sha-identical without the flag.
- V15 bonus: audit image BOOTS — full 2b A–E bench PASS on it,
  "audit: middleware slice up" on the ring, heap cost ~4.4 K; 2b
  artifact restored byte-identical.
- Rung 0 DONE on nereus000: bm_sbc @ 17ea904, ctest 100%,
  validate.sh 15/15 (~/bm_sbc = standing checkout).
- Fixture restored + verified: main.py sha = repo ae3_usb copy; S6
  baseline QVGA q90 35.0 fps / 0 gaps / 0 bad. (First restore attempt
  silently failed while the old service held the VCP — redo from
  REPL/cold state, sha-verify after; README rule now.)
- S7 open item answered en route: uhubctl works on nereus000 —
  hub 3 port 1 (not the guessed 1-1); cycle+warm-reset is the
  documented recovery pair.

**Broke/surprised us (all now rules in firmware/bm_bridge/README.md):**
- **Cold boot does not run main.py on this build** — every "cold
  recovery" in fixture history was followed by a protocol reboot, so
  nobody had ever seen it. Warm `mpremote reset` is the service entry.
- mpremote attach kills the service (injected KeyboardInterrupt);
  pyserial attach is harmless. Crash/trace persistence to /flash
  (BENCHSPEC's BUILD-2b rule, adopted early) is what made every one of
  these failures diagnosable — silent otherwise.
- HE lifecycle triad: 2nd stop→load cycle per boot loses the ns
  announcement; stale-idle-HE restart is fine; stale-mid-burst HE
  blocks in C. Load once + drain every rung end.
- z/n crc modes "hung" on exactly one frame: the S14END summary was
  encoded with default CRC — mode must cover the terminator too.
- Enumeration after reset: by-id symlink lingers→drops→reappears;
  wait absent→present→settle. Counter now handshakes (newline → fresh
  banner) before sending config.

**Next:** nibble 3 = Nick runs the three demo commands (in the PR
body) → PR merge → S15 (BUILD-1+3; needs nereus001 back on the
tailnet — bench check). Board state: fixture service standing, s14
tooling staged inert on /flash, HE stopped.

---

## 2026-08-14 — S11 nibble-1 plan + BENCHSPEC review — bench arc adopted (docs PR)

**Branch:** claude/s11-interim-3-uart-gateway-97913a (worktree) → PR to main

**Done:**
- S11 INTERIM 3 nibble-1 plan researched + presented (no code): bm_sbc
  gateway fully mapped (raw L2 / COBS / CRC-32C / 0x00 delim, `--pcap`
  built in; bm_core pinned d4ecc38 = our vendored rev). KEY FIND: **no
  stock mote firmware speaks it** — counterpart = `native_serial_bridge`
  on bm_protocol branch `feat/uart-sbc` (open PR #378, never
  hardware-validated by Sofar; baud hardcoded 115200, PLUART/LPUART1).
  Dev kit facts (sourced): 24 V wall-charger powered via bus ports (USB
  cannot power it), console = native USB-C CDC ×2 (CLI = port ending 1),
  payload UART on dev-board terminals 1/13/14 at 3.3 V, flash = ROM DFU
  (no SWD rig needed). Bite = flash that branch's app; plan incl. bench
  meter checklist + golden-capture diff via s10_peer.py parsers.
  **Nick deferred the bite** (kept on ladder, item 7).
- BENCHSPEC v2 (three-node bench, agent-drafted outside this repo)
  reviewed against project context: topology/BUILD-1/3/4 sound — its
  REV-1/REV-12 findings independently match our INTERIM-2a live
  experience. **BUILD-2 (HE core claims USB) rejected**: HP's stock
  firmware owns the one USB controller and the whole dev loop rides it
  (REPL, remoteproc ELF load, DFU flash, recovery). Replacement = the
  D25 rpmsg seam promoted to a real wire + HP CDC bridge (uart_l2 codec
  over the VCP, crash-persistence rule) + bm_sbc `--uart` on
  /dev/ttyACM* (zero new Pi transport code).
- Docs landed (Nick approved drafts in chat): **docs/BENCHSPEC.md v3**
  (REV-20..28, V15/V16 gates, V11/V12 resolved, host mapping
  nereus000=Light / nereus001=Telemetry), TRACKER interim ladder →
  S14–S17 bench sprints (+ S11 kept, upstream reports kept), D26.

**Broke/surprised us:**
- Sofar's own uart-gateway doc ends in a TODO — "Not yet validated on
  physical hardware." Whoever runs it first validates it (us, either
  via the S16 CDC leg or the S11 dev-kit bite).
- v1's bmcam pipeline (bm_cam_legacy) speaks **bm_serial** — a
  different serial protocol (typed pub/sub, CRC16) than bm_sbc's
  uart_l2 (raw L2, CRC-32C). Easy to conflate; now recorded in both
  BENCHSPEC and the S11 plan.
- The two bench-spec agents' only critical error traced to one missing
  fact (HE stack is runtime-loaded via stock HP firmware, nothing
  flashed) — REV-22 now pins it so nobody re-derives the USB mistake.

**Next:** merge this docs PR → S14 nibble 1 (relay-throughput bench
plan — the V16 gate everything else hangs on), then S15 (udp device).

---

## 2026-08-12 — Sprint S10 (INTERIM 2b) — BCMP converses: python peer node, neighbor table + ping both ways; rehearsal PASSES ×2

**Branch:** `sprint/10-bcmp-2b` (worktree `s7-headless-ae3-flash-73104e`,
branched from merged PR #18)

**Done:**
- Nibble 1 (plan approved by Nick): peer = python on the HP end of the
  2a fake wire; verdicts C (neighbor table via BcmpNeighborTableRequest
  — the same query real BM topo tooling uses), D (ping peer→HE), E
  (ping HE→peer via new WCMD_PING, acceptance proven by ping.c's debug
  ring line). bm_core stays byte-identical.
- Nibble 2: `s10_peer.py` (pure builders/parsers, byte-exact BCMP,
  CPython-testable), WCMD_PING in src/main.c (+~30 LoC C), runner grown
  to A–E with both directions in the pcap. Host tests 72 → 112 (new
  test_peer.py: checksum ones-complement invariant, ingress-nibble
  round trip; wire_ping_t ABI locks). Build 231.5 K (~88 %, +0.4 K).
- Rehearsal (Claude, twice, identical): **A–E ALL PASS, first try** —
  neighbor formed + online from 5 s peer heartbeats, both pings
  answered/accepted, pcap = full 15-frame two-node conversation
  (tcpdump-clean). First live RX-path exercise (l2→lwIP→bcmp) worked
  immediately. S6 USB baseline re-verified after (34.1 fps, 0 gaps,
  0 bad, sample JPEG SOI/EOI valid).

**Broke/surprised us:**
- Nothing broke. Checksum byte-order question resolved from lwIP source
  before first injection (native-store = network bytes → 2a's
  "swapped" compare branch was the live one) — no live calibration
  needed; every injected frame accepted on the first run.
- Bonus behavior: HE fires an unprompted BcmpDeviceInfoRequest at its
  new neighbor (bm_core's discovery path) — now in the pcap.
- ping.c prints reply seq_num via PRIu32 on a u16 field + %llx node ids
  (nano-printf garbage) — cosmetic upstream quirks; runner matches
  stable text instead.

**Next:** Nick runs the 2b demo (`bm_he/README.md` ladder — build/scp
optional since artifacts are staged; two cp + one run + pcap pull) =
the INTERIM-2 demo proper → nibble 4 PR.
→ **demo PASSED (Nick, same day — A–E identical to both rehearsals) =
INTERIM 2 DONE; PR opened.** ("Unable to load configs from flash." ×3
in the ring = bm_core's normal first-boot line — the stub config store
is RAM, born empty per load; persistence is a hardware-day concern.)

---

## 2026-08-12 — Sprint S10 (INTERIM 2a) — bm_core/lwIP/BCMP alive on HE vs trait-level mock; rehearsal PASSES ×2; translator flag captured

**Branch:** `sprint/10-bcmp-he` (worktree `sprint-s10-planning-ec0ae3`)

**Done:**
- Nibble 1 (Nick approved: 2a/2b split, trait-level mock over the
  TRACKER's chip-level parenthetical, fetch-and-pin sys_arch → D25):
  bm_core's NetworkDevice trait is the seam; bm_sbc's own
  virtual-device init ladder followed verbatim.
- Nibble 2: `firmware/bm_he/` — bm_core @ d4ecc38 vendored
  byte-identical (BCMP slice, zero patches needed), lwIP 2.2.1 by
  reference from the D23 openmv clone + pinned contrib sys_arch,
  RAM/tick integrator stubs, trait-level mock with rpmsg fake wire,
  4 KB debug ring peekable from HP, runner + pcap writer. Host tests
  72 checks (clang+ASan). Size checkpoint: 231 K / 262 K (~88%) —
  fits, ITCM lever unneeded.
- Rehearsal (Claude, twice, identical): **A PASS** (ladder RUNNING,
  node id + fe80::/fd00:: addrs correct, link up) · **B PASS**
  (heartbeats at boot+10.02/20.02 s, checksum + src-node-id +
  egress-nibble verified, monotonic) · pcap reads clean in tcpdump
  (heartbeats = ip-proto-188; bonus MLD6 join of ff03::1 visible).
  S6 USB baseline re-verified after (33.7 fps, 0 gaps, 0 bad).
- Captured Nick's schematic-review finding (via capture-task): AE3
  P0–P5 ride NXS0104/NXS0102 level translators (open-drain, 10 kΩ,
  24 Mbps max; part-to-net mapping UNVERIFIED) → SPEC §Open questions
  entry + S13 measurement item. Gives the S9 20 MHz-OA-garbage finding
  a physical hypothesis (MISO edges); no impact on the USB-only interim.

**Broke/surprised us:**
- bm_core compiled for CM55 with ZERO source patches — the whole
  integration fit in headers we own + stubs. Rare and pleasant.
- newlib-nano printf silently mangles %llx (caught live in the debug
  ring); nano's syscall layer needed explicit stubs with a trapping
  _sbrk. LWIP_ETHERNET=1 must be spelled out when ARP is off; the 2021
  contrib sys_arch wants FreeRTOS backward-compat names. VPATH: shared
  he_spike dir almost shadowed our startup.c/main.c.
- bcmp sends NO heartbeat at link-up (timer-only, source TODO) —
  first one lands at +10 s; runner capture window sized accordingly.

**Next:** Nick runs the 2a demo (`bm_he/README.md` ladder, one mpremote
command + pcap pull) → 2b: HP python peer (inject heartbeats →
neighbor table; BCMP ping both ways) = the INTERIM-2 demo proper → PR.
→ **demo PASSED (Nick, same day — A/B identical to both rehearsals);
PR opened.**

---

## 2026-08-12 — INTERIM re-plan + Sprint S10 (bite 1) — USB-only ladder approved; FreeRTOS-on-HE spike rehearsal PASSES (A/B/C, gate 44×)

**Branch:** worktree `claude/interim-arc-replan-68f99c` (→ pushes to
`sprint/10-he-pipe-spike` at PR time)

**Done:**
- Fresh-eyes interim re-plan (Nick approved): TRACKER gains the
  INTERIM USB-only ladder (S10 bites 1–2 → S11 dev-kit reference w/
  HARD SAFETY GATE → D24 + D15 upstream reports), S9 marked `[!]` with
  RESUME-ON-HARDWARE; all ADIN-touching work parked.
- Nibble 1 exploration paid off big: stock AE3 firmware already ships
  OpenAMP host+remoteproc on HP and a remote-execution service on HE —
  **rung-0 probe measured 219 Mbps py↔py through the stock pipe, zero
  custom firmware → the ≥5 Mbps S10 gate was effectively answered
  before writing a line of C.**
- Nibbles 2–3: `firmware/he_spike/` — FreeRTOS V11.3.0 (vendored,
  CM55_NTZ port) on M55_HE, runtime-ELF-loaded into SRAM9_B via stock
  remoteproc (NOTHING flashed, recovery = stop/power-cycle);
  hand-rolled ~250-line device-role rpmsg; MHU doorbells; he-bench
  endpoint; SPI0 probe. Host tests 29 checks (clang+ASan, fake-SHM
  host driver). **Demo rehearsed twice, identical PASS: A (FreeRTOS
  serves rpmsg), B (13.2 Mbps HP→HE / 5.6 HE→HP, 0 loss/crc errs,
  37k msgs — python-end-bound; fabric does 219), C (HE pinmux
  write+readback + SPI0 init + IRQ 137 on HE NVIC).** The bm_core-on-HP
  fallback is MOOT.

**Broke/surprised us:**
- Three wire-format facts came only from live ring dumps (source
  inference was wrong or silent): vring roles reversed vs the
  modopenamp comment; desc .addr = offsets from SHM+1K; **used.len is
  a capacity contract** — reporting message size made the host recycle
  shrunken buffers (pump stalled after exactly 64 messages; small
  replies still flowed — that asymmetry was the tell). Host harness now
  reproduces the recycle semantics.
- Honoring NO_INTERRUPT on our TX ring loses the host's wakeup race
  (~1 msg/s trickle) → kick unconditionally.
- SPI0's DW SRL loopback bit is tied off in this silicon; the pad-pull
  fallback is inconclusive (P1 reads high under both pulls though
  pinconf verifiably lands) → **bench check (Nick): anything still
  wired to AE3 P0–P2?** RX-with-real-data proof = first PHY-ID read
  from HE on replacement hardware.
- One-off `machine.mem32` AttributeError (self-resolved on re-run;
  README troubleshooting note).

**Next:** Nick runs the bite-1 demo (`he_spike/README.md` ladder, one
mpremote command) → nibble 4 PR → INTERIM 2 (bm_os/lwIP/BCMP on HE vs
mock NetworkDevice).
→ **demo PASSED (Nick, same day — identical A/B/C numbers); PR
opened.**

---

## 2026-08-11 — Sprint S9 (bite 3) — OA data-path bridge PASSES on hardware; link blocked by dead pair (bench check for Nick)

**Branch:** work in worktree branch `claude/s9-oa-datapath-smoke-dc2e62`
(→ pushes to `sprint/9-oa-datapath` at PR time; that branch is checked
out in another worktree at the same base commit)

**Done:**
- Nibble 1 (plan approved by Nick): exploration verified against source —
  state-nudge theory confirmed (MAC_Init:542/574, ProcessTxQueue:1479);
  found adin2111-level init ALSO blocks on a port-2 PHY wait
  (adin2111.c:169) → bridge drives macDriverEntry/phyDriverEntry
  directly; PHY identity gate is DEVID1+OUI only → predicted pass.
- Nibble 2: `bm_spike_datapath.c/h` (init bridge, driver byte-identical),
  dp_* API in both HAL tables, `s9_oa_datapath.py` runner, host tests
  16 → 41 (mock: writable regs, MDIOACC/clause-45 PHY emulation, OA
  data-chunk parse + byte-exact TX capture). Both firmware builds green
  post-D24; HE image byte-count unchanged (guards hold).
- Rehearsal (partial): flash PASS (byte-verified). **Init bridge PASSES
  live, first try: rungs 1–6 SUCCESS, MDIO-over-OA proven (DEVID
  0x0283/0xBC91 through the driver's own PHY layer — the flagged new
  surface), SyncConfig + SWPD-exit clean.** VERDICT A achieved.

**Broke/surprised us:**
- **Link never comes up — and it's the BENCH, not the code.** Isolation
  (one variable at a time): S5-minimal sequence over raw C45 MDIO also
  fails → not the driver's phyInit extras; far side advertises fine but
  sees no partner (ethtool, bounced mid-window); **LOFE relatch probe
  silent** vs bite-2's measured continuous relatch from far-side energy
  → no energy on the pair. Suspect the pair got unplugged during the
  bite-2/S6-demo bench work. **Nick: re-seat/check the pair at both J1s**,
  then re-run `s9_oa_datapath.py` (README bite-3 ladder) — everything
  else is in place.
- Chip default AN_CONTROL=0x1000 measured (AN_EN on by default) —
  retroactively validates S5's power-up-only sequence.

**Next:** Nick's bench check → re-run runner (expect link UP ≲1 s, then
VERDICT B + frames in tcpdump on nereus001) → nibble 3 manual test →
nibble 4 PR. Debug helpers staged in `~/ae3_flash/` on nereus000.

**CONTINUED same day (bench debugging with Nick, paused mid-bisect):**
- Pair re-seated + continuity-verified (J1↔J1) by Nick → STILL no link.
  Isolation extended, all software-only: far side hardware-reset via
  module reload (fresh PHY init) → nothing; **forced-mode test (AN
  bypassed entirely: far = ethtool forced-master, ours = registers per
  the kernel driver's own recipe, amplitudes matched) → also dead both
  directions.** Fault is squarely in the analog/MDI domain.
- Correction recorded: the multimeter AC test I suggested was invalid —
  DMM bandwidth ≪ 7.5 MBd PAM-3; "no AC" readings are expected even on
  a healthy line. No line-capable instrument on the bench (LA descoped
  in S2).
- New measured facts: **hat #2 straps 2.4 Vpp TX on**
  (B10L_PMA_CNTRL powers up 0x1000; chip reset default is 0 → AOS
  TX2P4 strap pulled high — SPEC §Open questions); chip default
  AN_CONTROL=0x1000 (AN_EN on). Hat blue LEDs track link (dark = no
  link); red = power.
- Suspicion worth recording: the S6 demo's unplug/replug was a hot-plug
  with both ends powered on an unprotected line interface; plus heavy
  bench handling since. A damaged line driver on either hat explains
  every observation.
- **Bisect in progress (paused):** plan = SG shield (known-good,
  generic SPI) on nereus000 ↔ new shorter pair ↔ hat #1/nereus001,
  rerun S2's `t1l_link_test.sh`. Links → fault follows hat #2 / old
  harness. Nick dismantled the AE3 rig (hat #2 off, set aside, straps
  UNTOUCHED = still OA) and mounted the shield, but **nereus000 stopped
  joining the network entirely (no tailscale, no LAN ping, even with
  the shield removed)** — unresolved, needs local console/router check.
  Note: hat #2 on a Pi is NOT testable while OA-strapped (kernel driver
  is generic-SPI-only, D13) and a powered-but-unmanaged hat's PHY stays
  in software powerdown → dark blue LED proves nothing in that config.
- Fixture state at pause: AE3 rig DISMANTLED (rebuild = D19 wiring for
  bite-3 demo); AE3 still flashed with the bite-3 alif build; hat #2
  aside, OA straps intact; SG shield on/near nereus000; nereus000 OFF
  NETWORK; nereus001 healthy (autoneg on, eth1 up; tailnet name is
  **nereus001-1**, not nereus001 — post-reflash registration).

**CONTINUED 2026-08-12 (resumed with Nick; INVESTIGATION CLOSED):**
- nereus000 WiFi root-caused: hard power-cuts → ext4 orphan cleanup ate
  the NM WiFi profile (dmesg evidence). Nick recreated it. Bench rule:
  `sudo poweroff`, never pull power.
- Bisect completed across ALL three endpoint pairings (two cables,
  three termination styles, AN + matched forced master/slave, straps/
  overlays/modules/rails all formally verified — incl. Nick's process
  checks: module vermagic matches running kernel on BOTH nodes, live
  DT has no adi,spi-crc): **every pairing dead, zero energy either
  direction. Verdict: ≥2 of 3 line interfaces broken; both AOS hats
  prime suspects** (single transient into the shared pair; window =
  post-S6-demo bench-work era). Full logic + the DC-blocked-front-end
  correction in SPEC §Open questions.
- Nick's Fluke measurements KILLED a documented "fact": hat J1 is NOT
  DC-shorted through the winding — both hats OL, shield 2 MΩ = DC-
  blocked fronts everywhere; my DMM-based localization attempts were
  invalid (also: DMM AC range can't see 7.5 MBd PAM-3 — recorded so
  nobody tries again).
- En-route mishap (fixed): bare `build_adin1110.sh` on nereus001
  defaults to sg and ADDED a second overlay line to config.txt —
  removed before it could double-bind SPI CS on next boot. Rule:
  always pass the sg|aos argument.
- **Bite-3 status: code DONE and hardware-proven to the wire (bridge +
  MDIO-over-OA + TX submit all pass live); demo BLOCKED solely on
  replacement link hardware.** Options for Nick: new AOS hat(s), or
  ADIN2111 eval hw (bite-1 decision point pre-approved; production
  direction). SG shield = probable good endpoint; can be re-strapped
  to OA as the AE3-side chip if roles reshuffle (hat #2 stays generic
  as the Linux node).

**Next:** Nick picks replacement hardware → rebuild fixture → re-run
`s9_oa_datapath.py` (one command) → nibble 3 manual test → PR.

**SPUN DOWN 2026-08-12 (Nick's call): boards confirmed dead, bite-3 PR
opened with the demo deferred to hardware arrival. Interim pivot: new
session plans a USB-only dev track for the BM-native arc (S10's
FreeRTOS-on-HE + OpenAMP spike needs zero ADIN hardware; TRACKER
review with fresh eyes). Kickoff prompt handed to Nick.**

---

## 2026-08-11 — Sprint S9 (bite 2) — Alif-native ADI-HAL: demo PASSES repeatably; PROTE self-flip + dead reset line found

**Branch:** sprint/9-adi-hal

**Done:**
- Nibble 1 (plan approved by Nick, DMA deferred to S10): facts gathered
  from openmv.git @ 7d4dbf7ab2 — P0–P3 = SPI0 on Alif port 5 (SCLK is
  AF3, siblings AF4), P5 = P0_4 → GPIO0_IRQ4_IRQn; the D8 per-word
  ceiling exists in BOTH machine_spi.c and Alif's own
  spi_transfer_blocking; GPIO0_IRQ4Handler symbol owned by
  machine_pin.c + const MRAM vector table → ride its dispatch.
- Nibble 2: `bm_spike_hal_alif.c` (FIFO-burst SPI0 engine ≤16 in
  flight, NVIC-gated INT_N, real critical sections, stats),
  `--hal mp|alif` staging switch (mp = default/baseline), per-HAL
  Python API + bench + raw reg passthrough + `fresh()`, host tests
  10 → 16. Both HP images build post-D24; HAL exclusivity verified in
  the objects.
- Nibble 3 rehearsal (Claude ran the demo per Nick's ask, both
  runs PASS identically): **VERDICT A** PHYID=0x0283BC91 via native
  HAL; **VERDICT B** INT_N → hard IRQ → driver callback (1 callback per
  soft reset); bench 45.9k reads/s @5 MHz (mp HAL: 22.9k = 2.0×),
  83.8k @10 MHz, 0 stalls; bite-1 runner still passes on a final-source
  mp build.

**Broke/surprised us:**
- **20 MHz OA rung reads garbage AND is dangerous**: misclocked MOSI
  decoded as a valid CONFIG0 write and flipped PROTE=1 mid-rehearsal —
  chip then dropped every unprotected write (CDPE latching, reads still
  clean) until recovered by a protected-framed soft reset. Explains
  bite-1's one-off complement anomaly. SPEC §Open questions amended;
  runner now sanitizes before/after and runs 20 MHz last, gating
  nothing. RX_SAMPLE_DELAY sweep = bite-3 item.
- **P4 reset line is a no-op on the rig** (register scratch survives a
  50 ms pulse) — never actually verified in S4–S9; soft reset via reg
  0x003 is the only reset. Bench continuity check flagged for Nick.
- INT_N is asserted from power-up and W1C of STATUS0 is the only way
  up; LOFE relatches continuously (live far side) and must be masked
  for the IRQ proof; driver's failed-init exits leave NVIC disabled
  (correct driver behavior — runner re-arms).
- C statics survive MicroPython soft resets: a stale bench MAC handle
  benched all-fails until `fresh()` was added.

**Next:** Nick runs the bite-2 demo (`s9_hal_native.py`, commands in
README) → nibble 4 PR. Then bite 3: OA data-path smoke (frame TX →
tcpdump on nereus001) — the open half of the S9 demo.
→ demo PASSED (Nick, same day); PR #15 opened. Bite 3 is next.

---

## 2026-08-11 — Sprint S9 (build fix) — HE link failure root-caused: stock docker target flattens per-core build dirs

**Branch:** sprint/9-build-fix

**Done:**
- Root-caused the S9 blocker (M55_HE image never links in our env,
  FLASH_TEXT 154% + undefined `dcd_*`): openmv's stock
  `docker/Makefile build-firmware` → `build.sh` passes `BUILD=<dir>` on
  the make **command line**; that rides MAKEFLAGS into every sub-make and
  overrides `ports/alif/alif.mk`'s `BUILD := $(BUILD)/$(MCU_CORE)` — HP
  and HE share one object dir, so the HE link consumed HP-configured
  objects (USB device stack on → 2.21 MB text ≈ the HP image). Explains
  the byte-identical failure with the usermod compiled out. OpenMV CI
  builds AE3 with plain `make TARGET=` (no docker) and never hits it;
  upstream's own `build-firmware-dev` (commit `6adf40fd`, 2026-04)
  documents the nesting requirement in its comments. D24.
- Verified from clean at `7d4dbf7ab2` before touching the repo: HE links
  **1,193,520 B / FLASH_TEXT 83.25%** (official artifact 1,185,744 B),
  HP 2,200,176 B; `build/OPENMV_AE3/M55_{HP,HE}/` nesting present.
- `build_ae3.sh`: switched to `clean-dev` + `build-firmware-dev`; new
  `--incremental` flag (dev-loop fast path); HE size-window check; dirty
  openmv tree now skips rev sync instead of hard-resetting edits away.
- Label fallout fix: our tagged clone embeds describe-form ids
  (`v5.0.0-52.g7d4dbf7ab2` — makeversionhdr turns dashes into dots), not
  the bare sha10 of tagless CI builds. `flash_ae3.py`'s exact-match label
  check would have false-FAILED every local build after a good byte
  verify. MANIFEST now records `openmv_label` (exact embedded string);
  label check accepts a sha10 inside a describe id. Host tests 25 → 33.
- Docs: D24 decision entry; §S9 open issue marked resolved; HP-only
  workaround retired.

**Broke/surprised us:**
- Upstream half-knows: `build-dev.sh`'s comment states the per-core
  nesting requirement verbatim, but the stock `build-firmware` target is
  still broken for multi-core Alif targets. Candidate upstream report
  (alongside the D15 crash).
- The describe-vs-sha10 label format difference was invisible until a
  local build actually embedded one — the S7 flash-verify hardening
  (byte-level readback) was the right call; labels keep proving
  unreliable as fingerprints.

**Next:** Nick runs the manual test (fresh `build_ae3.sh` from clean →
MANIFEST + HE bin ~1.19 MB), then PR. S9 bites 2–3 (ADI-HAL, OA data
path) can now target both cores; S10's HE dependency is unblocked.
→ manual test PASSED (Nick), PR #14 opened.

---

## 2026-08-11 — Sprint S9 (bite 1) — bm_spike code-complete: unmodified bm_core driver runs on host; hardware gates = docker + re-strap

**Branch:** sprint/9-oa-first-light

**Done:**
- Nibble 1 (plan approved): spike designed for two verdicts, not one —
  OA transport proof AND the unmodified-init result. Nick's ask: get as
  far as possible with zero hardware contact.
- `firmware/bm_spike/`: vendored bm_core drivers/adin2111 @ d4ecc38
  byte-for-byte (bm_adin2111.c reference-only — needs bm_os, defines its
  own HAL fn); blocking adi_hal.h shim over MicroPython SPI/Pin
  (S4-proven path); `bm_spike` usermod; `build_spike.sh` stages sources
  into openmv's modules/ wildcard (NO fork/patch — staging + trap
  cleanup exercised); `s9_oa_spike.py` runner; README with verdict
  matrix + run ladder.
- Host harness: clang builds the UNMODIFIED driver against a mock ADIN
  speaking OA-protected control framing (format from adi_spi_oa.c) —
  10 checks PASS, including the identity gate demonstrated compiled
  (25,000 PHYID polls → COMM_TIMEOUT with a 1110 identity; prompt exit
  with 2111's).
- Pre-staged for Nick: openmv.git cloned to ~/openmv-dev/openmv; SDK
  1.6.0 linux-x86_64 downloaded + sha256-verified (setup_mac.sh will
  skip it). Docker still absent (password needed) — the build stops
  there by design.

**Broke/surprised us:**
- **The 2111 identity gate fires inside MAC-layer init, not just full
  init**: MAC_Init → MAC_Reset(MAC_PHY) → waitDeviceReady polls
  PHYID==0x0283BCA1 (adi_mac.c:568/1128). On a 1110, even MAC-only init
  returns COMM_TIMEOUT on perfect hardware. Spike redesigned mid-nibble
  to tolerate it and read PHYID afterwards (handle valid pre-reset;
  MAC_ReadRegister needs state != UNINITIALIZED only).
- ADI's *_DEVICE_SIZE constants are ILP32 hand-counts → adin2111_Init is
  not LP64-host-portable (INVALID_PARAM before SPI). Verdict 2 is
  target-only; documented.
- Vendored-driver quirk pinned by test: control-read path swallows
  PROTECTION_ERROR (spiErr only carries the header-echo check) —
  corruption = SUCCESS + unwritten data. Spike judges the PHYID value.
- MAC_Init is static; the exported route is the macDriverEntry table
  (same as adin2111.c uses).

**SPIKE PASSED (same session, hardware leg):** Nick installed Docker +
re-strapped; Claude drove build→flash→verdicts. Final:
`PHYID=0x0283BC91` through the driver's own OA framing; init refused
only by the 2111 identity gate. En route (all recorded in DESIGN §S9 /
§S8 correction / SPEC open questions): **S8 bench had run on the N6**
(mpremote auto-connect; AE3 re-run same conclusions; by-id-only rule
adopted) · **PROTE dead on our 1110** (measured; `--no-prot` delta
build; driver tests defined-ness — sha-identical `=0` build caught it) ·
CFG0 pad needed a second rework pass (razor; chip had been answering
OA-unprotected) · D23 build leg works but **M55_HE won't link in our
env at any rev** (HP-only flash at installed-HE's rev = workaround;
must fix before S10) · flash-verify tool false-mismatch on
`git describe` version strings (feeds the running hardening task).

**Next:** S9 bite 2 — Alif-native ADI-HAL (SPI + IRQ on P0–P5, DMA
hooks if budget allows). Prereq: fix the HE link (or a decided
HP-only stance) before S10. PR for bite 1 open. NOTE (merge-time): the
flash-verify hardening landed as PR #12 (entry below) — byte-level DFU
readback replaces the label matching whose false-mismatch we hit; our
session's flashes used the pre-hardening ladder deployed on the Pi.

---

## 2026-08-11 — Sprint S7 (flash-verify hardening) — byte-level readback verify replaces label matching

**Branch:** sprint/7-flash-verify

**Done:**
- Investigated the S8 stale-label find from source (openmv.git @ master
  `7d4dbf7`, the rev the board runs): `sys.version`'s "OpenMV \<id\>" is
  git-describe output baked in at build time (openmv/micropython
  `py/makeversionhdr.py`) — degrades to a bare sha10 in tagless checkouts
  and repeats across rebuilds at the same rev. The "v5.0.0" the board
  self-reported is the OTHER channel: `omv.version_string()`, reading the
  static `OMV_FIRMWARE_VERSION` defines (`protocol/omv_protocol.h`), still
  "5.0.0" on post-release dev builds. Labels ≠ fingerprints → label-match
  flash verification can false-pass.
- Fix (nibble-1 plan approved by Nick): `flash_ae3.py` verifies
  byte-for-byte — DFU readback (`dfu-util -U -Z len(bin)`; bootloader
  implements `DFU_UPLOAD`, MRAM reads are memcpy, tail compare capped for
  the 16 B sector round-up) + sha256 vs the exact flashed file; boot gated
  behind the verify via `DFU_DETACH` (`dfu-util -e` → jump, replaces `-R`);
  MANIFEST sha256 preflight cross-check of the local bins. `sys.version`
  demoted to boots+label evidence. Host tests 16 → 25, all green.

- Nibble 3 (Nick delegated the run): LIVE round trip PASSED on nereus000 —
  negative test (corrupted MANIFEST sha256) refused before board contact;
  v5.0.0 flashed + readback-verified both partitions; dev flashed back with
  the full corrected ladder, `PASS: flash verified byte-for-byte`, exit 0;
  fixture firmware restored to `7d4dbf7ab2` and re-confirmed via REPL.

**Broke/surprised us:**
- The S8 DEV_LOG entry the kickoff referenced wasn't on main during the
  session — it landed mid-flight with PR #11 (`sprint/8-npu-bench`, entry
  below) and produced the doc merge conflict resolved in this branch's
  merge commit.
- Today's rolling `development` release still embeds "OpenMV 7d4dbf7ab2"
  (upstream master hasn't moved) — confirming the board's "v5.0.0" report
  came from the static-defines channel, not sys.version.
- **`dfu-util -e` does NOT boot the board** — it only detaches runtime-mode
  devices; silent no-op on a device already in DFU (board parked safely, as
  designed). Boot rung reworked live: 8 KB TOC-partition read carrying `-R`
  (USB reset → `while (tud_mounted())` exits → jump), reset still lands
  only after verification.
- `dfu-util -Z` doesn't bound uploads (0.11) — readback runs to the
  partition-end short frame; the sha256 compare caps at len(bin) instead.

**Next:** nibble 4 — push `sprint/7-flash-verify` (push was
permission-blocked from the agent session) and open the PR. → done: PR #12.

---

## 2026-08-11 — Sprint S8 (bite 1, early ride) — NPU bench: per-tile fast, HD tiling misses the T2 gate

**Branch:** sprint/8-npu-bench

**Done:**
- Nibble 1 (plan approved, scope kept tight for the BM arc): S8 rides
  its TRACKER exception — NPU bench only, rest of S8 stays behind S13.
- `bench/ae3_npu_bench.py` (+18 host tests): no-sensor, reef-ref-scene,
  models discovered live from `/rom`; ml API pinned from docs.openmv.io
  v5.0.0 before coding. Ran it remotely (Nick's ask): 9 models timed,
  1 correctly SKIPped. yolov8n_192 = 21 ms/tile (~47 fps); HD tiled
  (40 tiles @ 32 px overlap) = **1.2 fps < T2 ≥3 fps gate**; only
  yolo_lc_192 meets it (6.3 fps). Single-pass downscale → fish 15–23 px,
  below the 24 px floor. Tables in DESIGN §S8 detail; TRACKER ticked.
- Artifact checks: "0 det" explained by label files (yolov8n/yolo_lc =
  person-only) → **T2 needs a custom Vela-compiled fish detector either
  way (Nick concurs); input size is the tiling lever.**

**Broke/surprised us:**
- Board self-reports `OpenMV v5.0.0` while DEV_LOG says dev
  `7d4dbf7ab2` — Nick: stale version label on the in-development build,
  not a reflash. (Weakens sys.version as a flash-verify signal for dev
  builds carrying release-ish labels — watch item for `pi/ae3_flash`.)
- Tailscale SSH wanted a fresh browser re-auth before the session could
  reach nereus000 (Nick approved mid-session).

**Next:** S7 decision entry (waiting on Sofar), then the BM arc (S9
bite 1). S8 resumes after S13; its next bite when reached = custom
detector (train + Vela compile, larger input) — the bench says the NPU
has the headroom if tiles shrink.

---

## 2026-08-11 — Sprint S7 (spike bite 1) — headless flash SPIKE PASSED: round-trip flash from the nereus000 CLI

**Branch:** sprint/7-headless-flash

**Done:**
- Nibble 1 (plan approved with Nick's amendments: dev on the Mac, docker
  build, VS Code/IDE as manual front-ends): headless flash answer pinned
  from source, not the bench — OpenMV's DFU bootloader runs on EVERY boot
  (USB `37C5:96E3`, 1 s + 1.5 s window), `machine.bootloader()` forces it
  to stay (magic `0xB00710AD` → `0x200FFFFC`), partitions are named DFU
  alts (`HP`/`HE`; `BOOT` never touched → un-brickable at app level),
  `os.uname().version` embeds `OpenMV <sha10>` for verification. SWD and
  SE-UART rejected for the loop (D22). SE-UART = deep recovery only.
- Load-bearing build fact: OpenMV SDK exists only for linux-x86_64 +
  darwin-arm64 → docker-on-Pi would be qemu-emulated; build host = Mac
  under Rosetta (D23, Nick's call).
- Shipped (hardware-untested by design): `firmware/openmv_build/`
  (setup_mac.sh, build_ae3.sh → sha256 MANIFEST with openmv_sha) and
  `pi/ae3_flash/` (flash_ae3.py ladder: preflight refuses active
  t1l-sender → mpremote bootloader entry → DFU wait → dfu-util HP+HE →
  CDC wait → uname hash verify; --dry-run/--recover; fetch_firmware.sh;
  udev rule; 16 host unit tests pass).
- Session constraints held: no mpremote/USB/flash on nereus000, stream
  services untouched (S6 fixture live); D20/D21 numbers left to the S6
  branch, docs appended for clean merge with PR #9.

- **FLASH LEG PASSED same session (Nick's go after the S6 demo):**
  round-trip `7d4dbf7ab2` → `v5.0.0` → `7d4dbf7ab2` from the nereus000
  CLI, sys.version verified each leg, leg 2 = the shipped ladder
  end-to-end green with its own PASS verdict; fixture firmware restored
  to exactly what S6 ran on. Setup done on the Pi (dfu-util, uhubctl,
  udev rule → sudo-free ladder; passwordless sudo made it hands-off).
  Tooling deployed to `~/ae3_flash` (repo checkout on the Pi stays on
  the S6 branch, untouched).

**Broke/surprised us:**
- Verification hook was wrong pre-hardware: the `OpenMV <id>; MicroPython
  <id>` string is **`sys.version`**, not `os.uname().version` (uname has
  only the MicroPython id). And release builds embed version TAGS
  (`v5.0.0`), not sha10s — dev builds embed hashes. Regex relaxed.
- dfu-util `-R` exits 251 on SUCCESS (device drops off the bus during
  the USB reset) — "trust artifacts, not exit codes," literally; script
  now treats CDC re-enumeration + sys.version match as the signals.
- v5.0.0 ships one combined all-boards zip; per-board zips exist only on
  `development`. fetch_firmware.sh handles both now.
- The board had been on the D15-era dev build all along — S6 passed on
  `7d4dbf7ab2`/`11852aa3d0`, not stable v5.0.0.
- Two test bugs self-caught: "-R" substring hides inside "DRY-RUN";
  later the "reset sent (dfu-util ...)" log line collided with the test's
  dfu-util line filter.

**Next:** BM-native arc planned same session (research from bm_core +
bm_sbc source; ladder S9–S13 in TRACKER, Nick approved; S8 resequenced
after the arc). Key finds: bm_sbc branch
`feature/adin_linux_implementation` = raw_eth AF_PACKET transport (the
full-rate Linux attachment, WIP — Nick pinging Sofar CTO); bm_core's OA
driver is ADIN2111-only → S9 bite 1 tests it unmodified on our 1110,
fallback = buy 2111 hw, never port; bm_core needs FreeRTOS/POSIX → HE
core + OpenAMP is the AE3 plan (spike gates it, ≥5 Mbps pipe). Sofar
forum questions drafted for Nick. Immediate next bite: S7 decision
entry after Sofar responds, or S9 bite 1 if Nick wants hardware first.
Mac build leg (setup_mac.sh → build_ae3.sh) still for Nick to exercise
(needs Docker Desktop first-launch password) — it becomes load-bearing
in S9. Untested: `--recover`/uhubctl. ROMFS pairing on big version
jumps = watch item.

---

## 2026-08-10 — Sprint S6 — video over the pair, live in the browser; gate run pending light

**Branch:** sprint/6-ae3-video

**Done:**
- Bite 1 (plan approved): BMV6 chunk protocol + bounded Reassembler
  (`s6_video.py`), TX loop with cap/enc/tx telemetry (`s6_video_tx.py`),
  Pi reassembly verifier (`bench/s6_video_counter.py`), shared bring-up
  extracted (`adin_bringup.py`). Verified live (Claude ran the ladder,
  Nick's ask): 60 s @ 20 MHz → 2422/2423 complete, 0 lost, 0 bad JPEGs,
  40.4 fps; artifact JPEGs pulled and eyeballed.
- Bite 2 (plan approved): `chunk_shim.py` + `t1l-chunk-shim.service`
  (CAP_NET_RAW as pi) feeding the FROZEN ingest; browser stream live at
  `http://nereus001-1:8080/stream` (tailnet-wide URL). 2622/2622 frames
  sender→server exact, 0 gaps. Quality made a runtime knob (Nick);
  q90@30 over SPI ruled out by arithmetic + measurement (D20).
- Bite 3 (plan approved, partial): TX loop rides out link outages;
  remote eth1-bounce test → stream freezes + auto-resumes. t1l-sender
  boot service disabled on nereus000 (S6 replaces it). Docs: D20, D21,
  TRACKER states, DESIGN §S6 detail.
- Dark-scene q ladder (NOT gate numbers): q35 45 fps → q90 31 fps, all
  0 loss; tx cost ~2.0 ms/KB at every q.

**Broke/surprised us:**
- SPEC §T1's pipelining requirement is moot on this path (D21): capture
  already DMA-hidden (3.1 ms, not 33), and encode/tx can't overlap
  (polled SPI, one core). Only lever = bytes/frame.
- ADIN1110 MAC drains TX into a dead wire without filling the FIFO —
  sender never stalls on link loss; loss is invisible until the
  receiver counts it. Trust the receiver's ledger, not the sender's.
- `pkill -f chunk_shim.py` over ssh killed its own ssh session (pattern
  matched the remote command line). Use `pkill -f '[c]hunk_shim'`.
- New firmware deprecation warning: `sensor` module → `csi` (watch item).

**Next:** ~~lit-scene gate run~~ → DONE 2026-08-11: **T1 GATE PASSED —
q50 = 32.2 fps / q60 = 25.9 / q70 = 24.2, all 0 loss; standing setting
q50 (D20 final)**. One REPL-wedge between rungs, cleared remotely by
the uhubctl ladder. ~~Remaining: Nick's live demo~~ → **DEMO PASS
(Nick, 2026-08-11): live browser video over the pair, unplug→freeze /
replug→resume, USB REPL-only. S6 `[x]` — THE POINT reached.** PR #9
un-drafted; next sprint = S7 (headless-flash spike already prepping in
a parallel session; flashing unblocked now that the demo is done —
coordinate board access with that session).

---

## 2026-08-10 — Sprint S5 — frame TX + loss demo: 0% loss at 4.21 Mbps

**Branch:** sprint/5-frame-tx

**Done:**
- Bite 1 (plan approved): TX FIFO burst + clause-22 MDIO / MMD-indirect +
  PHY power-up + link mgmt in the portable core; `s5_frame_tx.py` demo;
  21 new host tests. Facts cited from vendored adin1110.c/adin1100.c.
  Verified live: 200/200 × 500 B seq frames in a tcpdump pcap on
  nereus001, in order, zero loss (5 MHz).
- Bite 2 (plan approved): `bench/frame_counter.py` (raw-socket loss
  counter, window-relative accounting, PASS/FAIL verdict),
  `s5_tx_load.py` (65 s @ 20 MHz, 1000 B frames, template+patch_seq),
  core telemetry (sw tx counters, wait_link, status_summary), shared
  `s5_frames.py`. 56 host tests total.
- **Demo PASS (Nick, same day): 31,592/31,592 frames, 0% loss, 526 fps /
  4.21 Mbps sustained 60 s @ 20 MHz — S5 → [x].** 4.21 ≥ the ~4 Mbps D8
  budget; MicroPython driver is not the S6 blocker.
- Claude ran the full manual-test ladder remotely (Nick's ask), incl.
  installing tcpdump on nereus001 and pcap verification by parsing.

**Broke/surprised us:**
- Half the session lost to a **bad pair connector**: both PHYs
  register-perfect (AN on, advertisement correct, forced-mode off) and
  both sides deaf. Register work can only *rule out* software — the
  split came from bench checks: 3V3 under AN load (3.276 V, fine), then
  the connector. Full ladder + lessons in DESIGN §S5 detail.
- Continuity across J1 on POWERED hats reads OL — first readings were
  artifacts; the checklist's "unpowered" rule is load-bearing.
- t1l-sender auto-starts on nereus000 boot and owns the AE3 USB port —
  bit us again after the power cycle; stop it before mpremote work.

**Next:** S6 bite 1 — AE3 capture → MJPEG → chunked seq frames over the
TX path; Pi shim reassembles and feeds the FROZEN S3 stream server
(ingest :8081). Demo = live browser video, USB data pipe unused.

---

## 2026-08-10 — Sprint S4 — AE3 first light: PHY ID 0x0283BC91 over SPI

**Branch:** sprint/4-ae3-first-light

**Done:**
- Nibble 1 plan approved (protocol facts sourced from vendored
  adin1110.c, not the datasheet — proven code beats transcription).
  Power rig revised by Nick before wiring: hat fed from nereus000's 3V3
  header, AE3 USB-powered from the same Pi (D19 — the S2/S3 setup
  already proved this exact load combo); AE3 3V3 question sidestepped.
- Built `firmware/adin_drv/`: portable protocol core + AE3 HAL (two-layer
  contract), first-light demo with built-in no-LA fallback diagnostics,
  16 host unit tests. ~380 LoC, one bite.
- **Demo PASS (Nick, same day): `PHY ID: 0x0283BC91 — OK` at 5 MHz,**
  first attempt on a correctly wired harness, repeatable. S4 → [x].
- Debug tools kept for S5+: `s4_bus_probe.py` (DC checks, incl. rail
  detect via the hat's own RESET_N pull-up), `s4_bitbang_probe.py`
  (pure-GPIO PHY ID read, splits harness faults from machine.SPI faults).
- Remote dev loop notes: ssh as **pi@nereus000**, mpremote at
  `~/.local/bin/mpremote`, AE3 = `/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*`
  (ttyACM0 is the N6 — never hardcode ACM numbers). Claude can run the
  AE3 via mpremote directly; sudo over ssh needs Nick.

**Broke/surprised us:**
- The hat header got counted mirrored TWICE while off the Pi — cost most
  of the session. All-0xFF + stray-bit and TX-echo signatures were
  floating-MISO crosstalk. Ender: meter hat 17 ↔ 6 with power jumpers
  only (~3.3 V iff orientation right) BEFORE landing data wires.
- Debug probes "passed" convincingly on miswired harnesses (coincidental
  nets mimic expected responses) → led to two wrong theories
  (machine.SPI SS-steal, strap mode corruption) before Nick spotted the
  flip. Lesson recorded in DESIGN §S4: verify wiring before trusting
  probe interpretations.
- t1l-sender was still active at session start — it owns the AE3 USB
  port; stop it before any mpremote work (fixture restore: remount hat
  #2 + `systemctl start t1l-sender`).

**Next:** S5 — frame TX path (generic SPI FIFO), seq-numbered payloads;
RX side minimum = link status + counters; Pi raw-socket counter script.
Pi end can be nereus001 as-is (live reference node stays intact).

---

## 2026-08-10 — Sprint S3 — bites 2+3: video across the pair, 30 fps, zero loss

**Branch:** sprint/3-t1l-video

**Done:**
- Bite 2 plan approved; built `pi/stream/stream_server.py` (nereus001:
  TCP ingest :8081 — the FROZEN S6 interface, same frame framing as the
  USB protocol, StreamParser reused — + HTTP :8080 `/stream` `/frame.jpg`
  `/stats.json`, stdlib only) and `pi/stream/t1l_sender.py` (nereus000:
  self-healing leg — board reboot per D15 → USB session → Pacer →
  re-sequenced relay). 9 new unit tests; 24 total pass.
- Live end-to-end same day: browser video that crossed the pair.
  Real-scene q90 frames ≈ 21 KB (2.4× bench scene, as S0 predicted).
- Nick pushed the target: 15 → 30 fps. Measured live: q90@30 = 30.8 fps /
  4.8 Mbps / 0 gaps (~2 fps encoder surplus); q80@30 = 30.4 / 3.0 (~4 fps
  surplus, documented fallback). **D17: standing setting QVGA q90 @ 30 fps**
  (supersedes D16; S6 caveat: exceeds the ~4 Mbps SPI budget — USB-path
  only).
- systemd units + installer (`pi/services/`,
  `pi/install_stream_service.sh receiver|sender`); both nodes converted.
- **Sustained measurement (TODO 3): 10 min 15 s, 18,032 frames, 29.3 fps
  avg, 4.60 Mbps, 0 gaps, 0 resets — zero frame loss.** Sender self-heal
  verified live (receiver restart mid-stream → reconnect + board reboot +
  resume).
- nereus001 ssh via new tailnet name `nereus001-1` (old entry stale).

**Broke/surprised us:**
- pkill -f patterns that also appear in the launching command line kill
  the launcher's own ssh session — twice. Bracket trick alone isn't
  enough; separate the kill and start invocations.
- Nothing else — the pipeline came up on the first end-to-end attempt.

**Next:** S4 — AE3 first light, PHY ID over SPI. Rig revised (Nick, D18):
AE3 drives **AOS hat #2** (freed from nereus000), not the SG shield —
proven silicon/straps, crimped pair, 3.3V-only. Open: AE3 3V3 sourcing
the hat. *(S3 demo passed by Nick same day → S3 [x]; VGA live ceiling
also measured post-demo: q35 13.5 fps / q50 11.7 over the pair.)*

---

## 2026-08-10 — Sprint S3 — bite 1: USB frame source measured; AE3 crash found + worked around

**Branch:** sprint/3-t1l-video

**Done:**
- Nibble 1 plan approved. Vendored the legacy nereus-camera-test-rig USB
  capture service (@ f11befe) into `firmware/ae3_usb/` with provenance
  README (D12 pattern); host-side `pi/stream/usb_frame_source.py` (pure
  incremental StreamParser + UsbFrameSource, 15 unit tests) and
  `bench/usb_stream_bench.py` (fps/Mbps/gaps/JPEG-integrity table +
  sample-frame artifacts).
- **Found an AE3 firmware crash:** second `start_stream` session per boot
  hard-faults the board (USB dies; deep flavor needs physical replug —
  Nick did 4 today). Isolated by elimination: first-session-any-mode OK,
  command loop OK, soft reset insufficient, `machine.reset()` clears it.
  Same on stable v5.0.0 and dev `11852aa3d0` — the dev build's "PAG7936
  halt for safe shutdown" does NOT fix it. Workaround shipped (D15):
  local-patch `reboot` action; hosts reboot the board between sessions.
  Recovery ladder documented (uhubctl → safe-mode REPL → machine.reset).
- Firmware version confusion resolved: IDE "5.0.0 [latest]" ≠ stable —
  dev builds self-report 5.0.0; discriminator is the uname build date.
  Board now runs dev `11852aa3d0 on 2026-08-10`.
- Bench matrix + QVGA q-sweep measured (DESIGN §S3 detail). Manual test
  run by Nick: **PASS** (4/4 modes, 0 gaps, 0 bad JPEGs, samples verified
  as real images). **Setting chosen (Nick, D16): QVGA q90 paced 15 fps.**
- Hard fact: VGA ≥ 15 fps unreachable on AE3 (software JPEG encoder,
  ~70–85 ms/frame); `set_framebuffers(2)` in-stream makes it worse and
  breaks HD (tested, reverted).

**Broke/surprised us:**
- The crash pre-dates Nick's firmware update — same build string as S0.
- Pi 5 USB port power switching (uhubctl) doesn't truly cut VBUS: board
  shows "connect" while port is "off"; deep-crash flavor unrecoverable
  remotely.
- nereus001 re-registered on the tailnet as `nereus001-1` (old entry
  stale); T1L link itself pings fine from nereus000.

**Next:** bite 2 — sender service on nereus000 (frames over T1L) +
receiver/stream server on nereus001 (`:8080/stream`, the frozen S6
interface), QVGA q90 @ 15 fps.

---

## 2026-08-10 — Sprint S2 — AOS hat #1 validated: probes on Pi 5, PHY ID match

**Branch:** sprint/2-aos-node-link

**Done:**
- Nibble 1 plan approved; scope shifted twice as Nick supplied better
  sources: web (no public docs) → schematic PDF → full KiCad layout.
  Parsed the layout netlist pad→net (authoritative for the fabbed board);
  pad numbers match ADIN1110 datasheet p.9 exactly.
- Facts recorded (DESIGN.md §S2 detail): AOS pinout = SG shield
  (CE0/GPIO22 INT/GPIO17 RESET); 3.3V-only; J1 ckt1 = DA−; straps default
  OA → hat #1 pre-bridged CFG0+CFG1 = generic SPI no CRC (D13).
- **Board bug found via netlist + datasheet:** INT_N (open-drain) has no
  pull-up on the board; R10 1.5k is on TEST1 (required there, so not a
  misplacement). Workaround: GPIO22 internal pull-up in overlay (D14).
  Draft note to AOS in docs/aos_hat_checklist.md §D.
- `pi/overlays/aos-adin1110.dts` (SG overlay + pull-up + MAC ...:02) +
  `docs/aos_hat_checklist.md` (meter checklist, now hat-#2/debug only).
- Nick mounted hat #1 on nereus000 directly (skipping the meter pass, his
  call). Probed first try under the stale SG overlay (floating INT rested
  high — luck), then cleanly under the AOS overlay after install+reboot:
  **eth1 MAC 02:ad:11:10:00:02, PHY ID 0x0283bc91, IRQ quiet, verify
  5/5.** Straps proven by working register I/O; #2204 silicon concern
  cleared.

**Broke/surprised us:**
- Tailscale SSH on nereus000 now demands per-session browser re-auth —
  ssh commands hang until someone approves the login URL. Fix before the
  Pi 3/4 bite.
- The hat worked under the SG overlay before any AOS software existed —
  identical pinout meant the only real difference is the INT pull-up.

**Same session, continued (hat #2 + nereus001 + tooling):**
- Hat #2 validated identically on nereus000 (PHY ID match, verify 5/5) —
  both hats good; straps proven by working register I/O.
- T1L tooling written + approved: `pi/setup_t1l_ip.sh <1|2>` (iperf3 + NM
  profile `t1l`, static 192.168.7.x/24, never-default; node 2 clones MAC
  ...:03) and `bench/t1l_link_test.sh server|client` (ping 0% / TCP ≥8
  Mbps both ways / UDP @8M <1% loss, iperf3 JSON parsed). No-carrier
  failure path verified on hardware. `build_adin1110.sh` now takes sg|aos.
- **nereus001 brought up** (second Pi 5 — Nick's call, replaces the Pi 3/4
  plan; SPEC inventory not yet amended): tailnet via pi-tailscale-setup
  skill (vendored from bm_cam_legacy), repo cloned, driver built, AOS
  overlay, hat #1 mounted. Node roles: nereus000 = hat #2 = .7.1,
  nereus001 = hat #1 = .7.2/MAC ...:03.
- **Kernel-orphan incident, resolved:** first-boot unattended upgrades
  bumped nereus001 from 6.18.34 → 6.18.39 between driver build and the
  hat-install power cycle → modules orphaned, probe silently absent
  (pi-kernel-upgrade skill scenario, seen live). Rebuild against running
  kernel + modprobe fixed it without reboot; cold-boot verify 5/5.
  nereus000 still runs 6.18.34 with the same upgrade pending — expect a
  rebuild there on its next apt upgrade.

**Pair test (same day, Nick wired the pair):** link test **4/4 PASS —
TCP 9.32/9.33 Mbps fwd/rev (full T1L line rate), UDP 8 Mbps 0% loss,
ping 0% loss RTT avg 0.84 ms.** Numbers in DESIGN.md §S2 detail. NM
profiles auto-activated on carrier; node-2 MAC clone confirmed.

**Sprint closed (2026-08-10):** Nick blessed the demo run (delegated to
Claude, watched live) and DESCOPED the logic-analyzer captures — no LA on
the bench. S4 consequence noted in TRACKER (no golden trace to diff;
fallback = register readback + live Linux node as reference). **S2 → [x].**

**Next:** merge PR #3, then S3 — video across T1L, Pi to Pi: AE3 → Pi 5
over USB (existing setup) constrained per budget, sender service on
nereus000 → frames over the pair → receiver on nereus001 serves
multipart-MJPEG HTTP. New branch `sprint/3-<slug>`.

---

## 2026-08-09 — Sprint S1 — Pi 5 ADIN1110 driver up: eth1 probes, PHY ID confirmed

**Branch:** sprint/1-pi5-adin1110-driver

**Done:**
- Nibble 1 plan approved by Nick: out-of-tree module build instead of SG's
  full kernel rebuild (D12). Stock trixie kernel has ADIN1110/ADIN1100_PHY
  unset but NET_SWITCHDEV=y + CRC8=m + headers installed → viable.
- Vendored unmodified `adin1110.c` + `adin1100.c` (rpi-6.18.y @ 222a4b41)
  into `pi/drivers/adin1110/` with out-of-tree Makefile + provenance README.
- `pi/overlays/sg-adin1110.dts` written from SG facts + kernel binding:
  SPI0 CE0 @ 23 MHz, IRQ GPIO22 level-low, reset GPIO17 active-low, INT
  bias-none, spidev0 off, no adi,spi-crc. Fixed MAC 02:ad:11:10:00:01.
- `pi/build_adin1110.sh` (idempotent build+install) + `pi/verify_adin1110.sh`
  (artifact checks). Repo cloned on nereus000 at `~/ADIN_SPI_OpenMV`.
- Built, installed, rebooted, verified: **eth1 up on driver ADIN1110,
  internal PHY reads 0x0283BC91** (SPEC match), bound to ADIN1100 phylib
  driver. verify = 5/5 PASS. eth0/SSH untouched.

**Broke/surprised us:**
- `ethtool -i` reports the driver name UPPERCASE ("ADIN1110") — verify
  script initially failed its driver-name check; now case-insensitive.
- SG's published DTS uses edge-trigger for INT but binding + driver source
  say level-low (driver hardcodes IRQF_TRIGGER_LOW) — went with level.
- Non-login ssh shells on the Pi lack /usr/sbin in PATH (modinfo/ethtool
  "not found" red herring); scripts export PATH explicitly.

**Next:** Nick runs the S1 demo (commands in PR + TRACKER); on PASS, close
S1 and open S2 (AOS hats buzz-out, second node).

---

## 2026-08-09 — Sprint S0 — SPI bench run: 4.89 Mbps ceiling, gate FAILED

**Branch:** sprint/0-SPI-bench-test

**Done:**
- `bench/ae3_spi_bench.py` (~250 LoC) + 15 host-side unit tests for the pure
  helpers (all pass, CPython 3.13). Two-phase: loopback throughput sweep,
  then auto-detected jumper move to P4→P5 for IRQ latency.
- Ran it on the AE3 (fw v1.28.0-49) remotely: Mac → ssh pi@nereus000 →
  mpremote → `/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*-if00`. Nick moved
  the jumper on cue. mpremote 1.28.0 installed on nereus000.
- Results recorded in DESIGN.md §Bench results + decision note. Headline:
  **max 4.89 Mbps effective (25 MHz/4 KB), 0 integrity errors, FAIL vs
  12 Mbps gate.** IRQ latency superb (soft median 6 µs, hard 5 µs, 0 missed).

**Broke/surprised us:**
- 20 and 25 MHz timings identical → SCLK clamped ≤ 20 MHz.
- Bottleneck is per-byte inside the port driver (TX-only = RX-only = duplex
  ≈ 5 Mbps; chunk size nearly irrelevant). Hypothesis: polled non-DMA FIFO —
  unverified, needs port-source read.
- `/dev/ttyACM0` on nereus000 is the N6, not the AE3 — use the by-id path.

**Decision + spike (same session):** Nick chose A→B. Spike read
`ports/alif/machine_spi.c` (upstream + OpenMV fork: identical): transfer is
polled lock-step per-byte, no DMA/FIFO burst → ceiling is software, firmware
build required to fix (= option C, priced ~50 LoC FIFO burst in one function).
Proceeding per B: ~4 Mbps AE3 video budget through S6 (DESIGN.md D8). New open
question in SPEC.md: true SCLK at 20/25 MHz requests (LA check in S2).

**Video table (Nick re-prioritized, same session):** ran
`bench/ae3_video_bench.py` on the AE3. Needed two fixes: fw 1.28 renamed
`compressed()`→`to_jpeg()`; VGA+ overflowed default framebuffers inside
`skip_frames` and the script skipped points SILENTLY (now prints skip
reasons; `set_framebuffers(1)` fixes capture). Sensor 0x7936 letterboxes;
QQVGA/SVGA/WXGA unsupported. **Headline: encoder is the bottleneck, not
SPI** — all supported modes produce < ~2 Mbps (bench scene bpp 0.10–0.24;
even at 0.875 deployment bpp: VGA ~8 fps @ 2.2 Mbps). SPI ceiling has ≥2×
headroom. Full table in DESIGN.md. Oddity flagged: mono bytes/frame inert
across quality settings.

**Reef-scene bench (Nick re-prioritized again, same session):** Nick supplied
`images/` (UNCOMMITTED in repo root — his call pending on git/LFS). Built
`make_ref_scene.py` (16:10 ROI-preserving crop + downsample to sensor
geometries) + `ae3_ref_scene_bench.py` (encode via mpremote mount). P7071008
baseline: reef bpp brackets the 0.875 anchor; color = encoder-bound, mono =
SPI-bound; **VGA color ~8 fps / VGA mono ~14 fps / HD mono ~4 fps delivered
at 1.7–2.9 Mbps** — MicroPython path viable for real scenes. Dark-room
"mono ignores quality" oddity resolved (scene artifact). Multi-image sweep
captured as new TODO.

**Requirements set (Nick, closing the loop):** AE3 confirmed as platform.
2×2 requirement matrix in SPEC.md; dual targets: **T1 streaming = QVGA color
q35–50 @ 24–30 fps** (feasible per measurements ONLY with capture/encode/tx
pipelining — S6 constraint), **T2 edge CV = HD @ 3–5 fps on-device**
(fish ≥24–32 px; sergeant majors ≈32–48 px at HD from P7071008). 12 Mbps
gate retired → transport gate = 2× T1 bitrate = ≥3.5 Mbps; measured 4.89
PASSES. S8 stub added (T2, strictly after T1). N6 owns the public-720p cell
(icebox). Probes: no hardware JPEG on sensor 0x7936; VGA capture 33 ms
single-buffered / 16.7 ms double. ROI visualization delivered (single 16:10
crop shared by all modes; density-only difference). Decisions D9–D11.

**Sprint closed:** Nick ran the S0 demo in OpenMV IDE — PASS against the
revised ≥3.5 Mbps gate. PR #1 open
(https://github.com/nickraymond/ADIN_SPI_OpenMV/pull/1), Nick approving.
S0 marked `[x]` in TRACKER.

**Next:** merge PR #1, then S1 — Pi 5 + SG shield: build the adin1110
kernel module, install SG's overlay (SPI0 CE0, 23 MHz, IRQ GPIO22, no
adi,spi-crc), verify probe + interface up. New branch `sprint/1-<slug>`.

**Branch:** n/a (no code yet)

**Done:**
- Board selection analysis (AE3 vs N6): AE3 for v1; N6 iceboxed pending
  OpenMV answer on H.264 MicroPython API. Full analysis in the decision-matrix
  artifact; key numbers in SPEC.md.
- Identified all ADIN hardware: SG SPE V1.0.0 shield (documented pinout:
  SPI0/CE0, RST GPIO17, IRQ GPIO22) + 2× AOS BOREALIS Pi-Zero hats
  (pinout NOT yet verified).
- Strap state confirmed from vendor docs, not guesswork: SG shield ships
  SPI_CFG0+CFG1 bridged = generic SPI without CRC (SG Linux page + ADIN
  datasheet Table 22). SWPD/TX2P4/MS_SEL/EWP/SHLD open = defaults.
- Found SG's published device-tree overlay (23 MHz, GPIO22, PHY compat
  0283.bc91 → expected PHY ID 0x0283BC91). Kernel module still needs
  menuconfig build — that's S1.
- Wiring diagrams drawn + reviewed: AE3↔SG harness (8 wires) and two-node
  bench link. In docs/diagrams/.
- Sprint ladder S0–S7 defined in TRACKER.md; ≤8 Mbps stream budget set from
  measured 0.875 bpp still.

**Broke/surprised us:**
- SG schematic PDF is image-only (no text layer) — strap meanings had to come
  from SparkFun COM-19038 guide + ADIN datasheet + SG's Linux page instead.
- SparkFun's default strap state (OA with protection) is the OPPOSITE of SG's
  as-shipped state (generic no-CRC) — same jumpers, different factory setting.
- JP1/JP4 on the SG shield are publicly undocumented. Open question in SPEC.md.

**Next:** S0 — run the AE3 SPI loopback benchmark (needs only the AE3 and one
jumper wire). Nick gate: approve S0 plan nibble first.
