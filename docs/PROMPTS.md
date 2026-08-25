# PROMPTS.md — Session Kickoff Prompts

*Copy-paste these verbatim; fill only `<N>` and `<slug>`. Never add task
instructions here or in the prompt itself — requirements belong in SPEC.md or
TRACKER.md, where the next session can see them.*

*(No skills installed? Replace the first line of any prompt with: "Follow the
Rules for Agents at the top of docs/TRACKER.md.")*

---

## 1 — New sprint

```
Run /agent-entry. We're starting Sprint S<N>.

Read docs/TRACKER.md cover to cover, skim docs/SPEC.md and docs/DESIGN.md,
and read the top 3 entries of docs/DEV_LOG.md before doing anything else.

Then: create branch sprint/<N>-<slug>, and give me your PLAN for the first
bite of S<N> — nibble 1 only. Throwaway exploration is fine, but change no
files and write no production code until I approve the plan.

Remember: ~300 LoC bites, I run the manual tests (give me copy-pastable
commands), and this sprint is not done until I've run its demo from the
TRACKER and it passes.
```

## 2 — Resume mid-sprint

```
Run /agent-entry. We're mid-Sprint S<N> on branch sprint/<N>-<slug>.
Do the full read ritual, then check the branch diff against the TRACKER
state and the top DEV_LOG entry, tell me exactly where the last session
stopped and which nibble we're in, and wait for my go before continuing.
```

## 3 — Sprint close-out (demo already passed)

```
Sprint S<N> demo passed on my end. Close it out: mark S<N> done in
TRACKER.md, append the DEV_LOG entry, add any DESIGN.md decisions made
this sprint, open the PR with the demo commands in the description,
and show me the diff of all doc changes before committing.
```

## 4 — Capture a task without acting on it

```
Run /capture-task: <one-line description>. Size it, place it (current
sprint / later sprint / icebox), show me the TRACKER diff, and then
return to the current bite — do not start work on it.
```

## 5 — Ready to paste: S22 bite 1 kickoff (written 2026-08-18)

```
Run /agent-entry. Next is S22 bite 1: the HE flood fix — the wire task
goes permanently mute under sustained camera publish, and it is the
last measured bug between the bench and honest ceiling numbers.

Branch sprint/22-he-flood from main.

Start from the evidence, not from scratch:
- SPEC §Open questions (the flood entry, incl. the 2026-08-18 burst
  datum) + TRACKER S22 bite 1 have the measured picture: 315 msg/s
  clean 4/4, 466 marginal 2/3, >=513 fatal 4/4 (one live demo at ~560);
  single-frame bursts: 55 and 68 chunks clean, ~83 lost 54 chunks WHILE
  THE HE PUBLISHED COMPLETELY (pub_errs=0) — the loss is downstream of
  bm_pub.
- Preserved trace ~/bridge_traces/20260818T002807_* on nereus000 shows
  the mute (he2pi_frames frozen, pi2he advancing). Read before board
  contact.
- Suspect territory: the HE netwire TX path under sustained load
  (S19 bite 2's non-blocking pump, firmware/bm_he). The S18 G-probe
  pattern (bench/probes/) is the off-chain reproducer shape: synthetic
  publish at swept rates, no Pi chain.
- The transition bug is FIXED (sticky-fb firmware on the board,
  rollback in ~/fw/development/) — do not re-litigate it; a wedge now
  is finding 1, not B2/B4.

Ops: board access per ae3-board-access; recovery = reboot nereus000 +
demo_up (~2 min, standing permission). Handover rule stands: you
deploy, you bring the chain up, you verify end to end; I get URLs and
results.

Success = sustained QVGA color at the measured 28.07 fps ceiling for
10 min with a ledger-exact run and zero wedges; capture 90 hd mono
(the q90 burst) delivers; the bench UI guardrail constants raised to
the NEW measured boundary (suite updated); the mono-ceiling matrix
rows finding 1 blocked run clean and land in MEAS_FPS.

Nibble 1 = plan first, my gate before code. ~300 LoC bites, short
actionable replies, 10-min status updates.
```

## 6 — Ready to paste: S23 encoder fast path kickoff (written 2026-08-18)

```
Run /agent-entry. We're starting Sprint S23 — the encoder fast path.
Targets: VGA color q50 >= 15 fps and HD mono >= 5-6 fps delivered,
ledger-exact, then push to the hardware's true max per mode.

Branch sprint/23-encoder-fastpath from main.

Start from the evidence, not from scratch:
- TRACKER S23 (bites 0-3, the measured route and the stop-gate) and
  DESIGN D41/D42 + §S22 detail (the enc-matrix table, the ~2 ms/KB
  tax, delivered ~= 1000/(enc + tax + capture)).
- Instruments already built: bench/probes/s22_enc_matrix.py (encoder
  acceptance) and bench/s22_ceiling_rows.py (delivered-fps rows).
- The MVE patch rides the sticky-fb precedent: repo-carried openmv
  patch, D23/D24 Mac docker build, S7 flash ladder, byte-verified,
  stock rollback kept. jpege.c is plain C; +mve.fp is already in the
  port CFLAGS.
- HD color >=5 is OUT OF SCOPE (no hardware codec on the E3 —
  vendor-verified, SPEC).

Bite 0 first (4:2:0 at q50 — one kwarg + tests + one re-measured
ceiling row), then bite 1 (MVE color-convert first; STOP and re-plan
with me if it lands under 1.5x before touching the DCT).

Ops: board access per ae3-board-access; recovery = reboot nereus000 +
demo_up (~2 min, standing permission); Tailscale side-door key is
installed (ssh -J pi@nereus001 pi@10.42.0.2 works if Tailscale
re-auth blocks). S22 leftovers (bite 1b fork instrumentation, PR #38
demo) are mine and do not gate you.

Nibble 1 = plan first, my gate before code. ~300 LoC bites, short
actionable replies, 10-min status updates.
```

## 7 — Ready to paste: S23 GOLD — hunt the invariant ~13 ms to VGA 15+ (written 2026-08-19)

```
Run /agent-entry. Continue Sprint S23 on sprint/23-encoder-fastpath —
GOLD: VGA color q50 >= 15 fps delivered, ledger-exact.

State: VGA is PINNED at 12.2-12.3 across five measured levers (four
falsified — DEV_LOG 2026-08-19 "GOLD arc" entry has the full table).
The VGA cycle is ~81.7 ms = enc ~50 + asm ~3 + send ~15 + an INVARIANT
~13 ms that survived: exposure caps (engaged per trace), framerate
120, the fused COBS+CRC viper (wire cost 676->499 us/msg measured),
and the early capture kick. Do NOT re-try those; the falsifications
are banked.

Bite: NAME the 13 ms by measurement, then kill it.
1. Instrument first (bridge-only, no flash): per-frame kick->collect
   wall time, poll-gap histogram, and a cycle split traced per stream.
   One 60 s VGA row. The counters from the relay bite are the pattern
   (cap_ept/cap_pump/relay_enc splits).
2. Fix what the numbers name. Known suspects, in order: the collect
   not finding the frame ready despite the early kick (CPI one-shot
   semantics); main-loop service granularity (sleep_ms(1) quantization
   x N passes); enc_us hiding scheduled-callback time. Firmware levers
   ONLY if the counters point there: OMV_CSI_CLK_FREQUENCY 24 MHz
   (~21 ms/VGA frame readout — board_config, flash spin, my gate) or
   Huffman/bitstream MVE (the last scalar encoder stage).
3. Acceptance: vga-color-15 row >= 15.0 CLEAN ledger-exact, HD mono
   >= 3.5 held, then a 10-min soak at the new number.

Known-good stack on the bench: bridge 79c9ab4f + codec ebcfb87d, fw
1e56071e, ELF 39717d44. fb=2 is FALSIFIED (slower — DMA/encoder
contention); do not revisit without new evidence.

Ops (matters — three boot-state anomalies in one day): git pull on the
Pi BEFORE demo_up, check the sha it prints. Attach-refusal recovery,
proven twice: sudo uhubctl -l 3 -p 1 -a cycle -d 3 on nereus000, then
>= 5 MINUTES of ZERO port contact (the silence is load-bearing), then
one demo_up. Board access per ae3-board-access, one command then hands
off the port. Bite 3 (full re-measure + guardrails + PR) runs after
GOLD or after my stop call.

Nibble 1 = plan first, my gate before code. Short actionable replies.
```

## 8 — Ready to paste: S23 bite R — the two unexplained board states (written 2026-08-19, Nick's pivot call)

```
Run /agent-entry. Sprint S23 bite R on a fresh branch from main --
root-cause the two REMAINING unexplained AE3 board states. GOLD is
parked at 12.53 CLEAN (bite S, the overlap feed, is specced in the
TRACKER and waits behind this).

Context that is SETTLED -- do not re-derive: uhubctl on the Pi 5
NEVER cuts VBUS (no cold boot; MCU state survives), so most of
2026-08-19's "attach refusals" were state confusion, not sickness.
Cold-boot recipe = mpremote reset or physical unplug. demo_up now
carries the mpr timeout/armed-retry wrapper.

The two states that survive that explanation (= this bite):
1. Repeated "could not enter raw repl" through PROPERLY-ARMED 45 s
   quiet-exit windows (the v3 demo_up silent-fail, ~17:20). The
   armed-exit model says the retry MUST find a REPL; it didn't, twice.
2. Incident #2's boot (DEV_LOG 2026-08-19 "relay regression" entry):
   linked, then ZERO VCP bytes for 30 s while bm-light demonstrably
   heartbeated, HE ring dump EMPTY, then attach refusals a Pi reboot
   could not clear.

Ladder (TRACKER bite R has detail): (1) reproducer -- scripted
mpremote-reset cold boot + repeated bridge lifecycles, count
attaches-to-refusal; distinguish state-confusion refusals (explained)
from true ones (screen each refusal against the state machine before
counting it). (2) instrument boots: MPU/cache region config + SHM
pool state to flash; on refusal, CDC endpoint state vs HE-ring
readability. (3) bisect: same reproducer on pre-SHM-128K firmware
(7d4dbf7+sticky-fb) -- vanishes = patches 0004/0005 cache attributes;
persists = Alif ROM/TinyUSB, mitigate + document.

Stack on the bench: bridge 5071cecd + fw 1e56071e + ELF 39717d44,
scene=ref cfg intact, units DOWN, board at REPL. Board access per
ae3-board-access; one command then hands off. Traces banked:
nereus000 ~/trace_row1.txt ~/trace_row2.txt ~/bridge_traces/.
Cross-cable side door DOWN since the Pi move (reseat = calm-day item).

Nibble 1 = plan first, my gate before code. Short actionable replies.
```

## 9 — Ready to paste: S8 CV — custom detector on AE3 + N6 (written 2026-08-20, after the S24 fold)

```
Run /agent-entry. Sprint S8 on a fresh branch from main -- the CV
sprint, reshaped and now leading the ladder. S23 is PARKED at GOLD
12.53; no VGA speed work until detector numbers exist.

Context that is SETTLED -- do not re-derive (TRACKER S8 carries the
full verified-facts block; read it before touching hardware):
- The stock ROM detectors are ONE class. /rom/yolov8n_192.tflite
  emits output_shape (1, 5, 756) = 4 box coords + 1 class ("person").
  A pink ball is unreachable by ANY configuration change. That is why
  a custom model is mandatory, not a preference.
- N6 = Neural-ART on STM32N657X0. AE3 = 2x Ethos-U55 -> Vela.
  Both boards run OpenMV v5.0.0 / MicroPython v1.28.0-49 and both load
  .tflite through the ml module.
- N6 measured: yolov8n_192 inference 20.7 / 23.7 / 32.2 ms at
  QVGA/VGA/HD; capture+inference end-to-end 47.9 / 41.8 / 30.2 fps;
  capture is DMA-hidden at 0.2 ms so inference is the whole budget.
- Verify firmware with sys.version, NOT os.uname().
- The sensor letterboxes to 16:10: QVGA 320x200, VGA 640x400, HD
  1280x800. OpenMV v5 draw_* takes a TUPLE first arg.
- Nothing autostarts on the N6; /flash/main.py is the stock blinker.
  The device node is NOT stable -- re-resolve the port every attempt.

The ladder (TRACKER S8 has the detail):
A. multi-colour blob thresholds -- the CLASSIC-CV CONTROL the ML
   numbers get compared against. Small, and bite C has no baseline
   without it.
B1. does a stock int8 .tflite run on Neural-ART, or does it need an
    stedgeai compile pass? Settle BY TEST, not by reading: load a
    candidate, print output_shape, time predict against 23.7 ms -- a
    CPU fallback is obvious in the number. NEEDS MY GO: first bite
    that writes to the board and downloads a model.
B2. the from-scratch "pink ball vs purple ball" detector, trained,
    compiled per target, deployed to BOTH boards.
C. end-to-end capture -> detect -> count at 1 m and 2 m, per board and
   per method, with PIXELS-ON-TARGET recorded next to the distance.
D. the board-decision number: HD tiled arithmetic, N6 vs AE3, carrying
   the model-variant confound explicitly.

Acceptance trap to design around from the start: prove the model runs
ON THE NPU, not silently on the CPU. A rejected .tflite still returns
correct answers, just slowly -- "it inferred" is not the artifact.

Hardware (CHANGED 2026-08-20, D44): BOTH boards are on nereus000's USB
-- the Mac holds no board and is the training/toolchain host (Docker,
dataset, model compilation); artifacts reach the boards through the Pi.
Identify boards by the by-id path, and note it is backwards from the
guess: the N6 is usb-MicroPython_Pyboard_Virtual_Comm_Port... and the
AE3 is usb-OpenMV_OpenMV_Camera_0829c14... Board access per
ae3-board-access, one command then hand off; the no-MSC udev rule is
installed there. Reuse bench/n6_stream_{board,host}.py and
bench/ae3_npu_bench.py; do not start a third harness -- and note there
is an off-repo fork of the stream host on the Pi at ~/n6_sidebyside
(multi-board --board NAME=PATH + --bind) that must be reconciled, not
re-forked.

Nibble 1 = plan first, my gate before code. Short actionable replies.
```

## 10 — Ready to paste: S25 — nereus000 as the machine-vision workbench (written 2026-08-20)

```
Run /agent-entry. Sprint S25 on a fresh branch from main -- turn nereus000
into the machine-vision workbench. S8's CV work is PARKED at bite B2 (the
custom two-colour model); this sprint comes first because every future test
needs a way to get the hardware into a known-good state without a human
flashing things by hand.

Goal: boot the Pi, open a page, pick a test, and the Pi puts the OpenMV
boards into that test's known-good state and runs it. Each released test
setup adds a menu entry.

SETTLED -- do not re-derive (TRACKER S25 and DESIGN carry the detail):
- REUSE, do not rewrite. S18 was tombstoned precisely because it shipped
  the substrate this sprint needs: pi/bench_web/bench_web.py + tests,
  pi/services/bench-web.service and five sibling units, and
  pi/bm_bench/{demo_up,bench-ctl,chain_status,deploy}.sh. This sprint is a
  menu and a runner on top of those.
- BOTH boards are on nereus000 (D44). They enumerate BACKWARDS from the
  guess: the N6 is usb-MicroPython_Pyboard_Virtual_Comm_Port... (37c5:1206)
  and the AE3 is usb-OpenMV_OpenMV_Camera_0829c14... (37c5:16e3). Always
  the by-id path; ttyACM<n> is enumeration order and not stable.
- Deploy routes are PROVEN (S8 B1, ml/README.md): AE3 = copy a
  vela-compiled .tflite to /flash. N6 = build a ROMFS image and write
  ROMFS0 over USB DFU alt 3 (no ST-LINK; never write alt 0 BOOTLOADER, and
  a bad ROMFS write stays recoverable). mpremote romfs deploy would DESTROY
  the vendor models -- it reads OpenMV's partition as size 0.
- The first recipe already exists in full: the S8 two-colour ball demo,
  per-board thresholds and per-board pixel floors, in PR #50's demo block.

THE HAZARD THIS SPRINT MUST DESIGN AROUND: port ownership. Two processes on
one board wedges it -- that happened repeatedly on 2026-08-20, and a menu
that can launch anything makes it trivially easy. The single-owner board
lock is a REQUIREMENT of bite 2, not a nicety.

Nibble 1 = plan first, my gate before code. Short actionable replies, and
use the milestone report format in CLAUDE.md.
```

## 11 — Ready to paste: S8 bite B2 — the from-scratch two-colour detector (written 2026-08-20 night)

```
Run /agent-entry. Sprint S8, bite B2, on a fresh branch from main -- the
from-scratch two-colour detector. The whole point: our OWN trained weights
through the ALREADY-PROVEN route (collect -> label -> train -> export ->
compile -> deploy -> measure) on BOTH boards. This is the bite the last
three sprints cleared the road for.

SETTLED -- do not re-derive (S8 B0/B1 in TRACKER, ml/README.md, PR #50):
- The compile+deploy route is PROVEN on both boards. AE3 = vela 5.0.0 ->
  copy .tflite to /flash (our compile ran 1.66 ms vs vendor 1.81). N6 =
  stedgeai 4.0.0 -> ROMFS image -> USB DFU alt 3 (2.75 ms vs 2.76).
  NEVER write DFU alt 0 (BOOTLOADER). NEVER `mpremote romfs deploy` -- it
  reads OpenMV's partition as size 0 and would destroy the vendor models.
  The N6 canNOT load models from /flash (stedgeai binaries want XIP) --
  its deploy is a partition flash, full stop.
- Training host = the Mac (~/nereus_ml venv: python 3.11, torch 2.12.1
  MPS, ultralytics 8.4.124; ml/chain_proof.py re-verifies the chain).
- KNOWN BLOCKER, planned around, not discovered: ultralytics' only
  TFLite path (LiteRT) emits NCHW float32; the boards want NHWC uint8
  (OpenMV source models are (1,192,192,3) uint8, scale 1/255), and
  OpenMV's maintainers report stock Ultralytics INT8 export failing
  stedgeai outright. So bite B2 starts with a SMALL CLASSIFIER or
  FOMO-style detector, NOT YOLO.
- Data collection rig exists: bite A's `--save-frames` +` index.jsonl`
  (its per-class blob boxes are the auto-labels). Ground truth scene:
  11 pink / 10 purple balls; blob baseline converges at 10/7.
- Acceptance trap (bite B's own words): prove the model runs ON THE NPU,
  not silently on the CPU -- the evidence is a measured per-inference
  time consistent with the 1.66/2.75 ms class, never "it inferred".

BENCH RULES (new since S25 -- the workbench owns the boards now):
- Both boards on nereus000, ALWAYS by-id (N6 = "MicroPython Pyboard
  Virtual Comm Port" 37c5:1206; AE3 = "OpenMV Camera" 37c5:16e3 -- yes,
  backwards from the guess).
- Before ANY board contact, check http://nereus000:8088/api/runner and
  /api/preflight. If a demo is LIVE, stop it from the page or
  POST /api/stop -- never kill its process, never open a held port.
  After any stop, the AE3 needs the 35 s settle window the page enforces
  -- do not race it with mpremote.
- ae3-board-access skill BEFORE any mpremote against the AE3; one op per
  invocation, no retry loops, 60 s+ silence between attempts.
- Deliverable hand-off: release the detector as a workbench recipe
  (pi/workbench/recipes/, thumbnail + [health]) so the demo is one click
  -- S25 bite 3's reconciliation can then own its model drift (declare
  models[] with src + sha256).

Nibble 1 = plan first, my gate before code. Short actionable replies, and
use the milestone report format in CLAUDE.md.
```

## 12 — Ready to paste: S26 — urchin dataset access & validation (written 2026-08-21, parallel track)

```
Run /agent-entry. Sprint S26 on a fresh branch from main -- urchin dataset
access & validation. This is a PARALLEL desk track: pure Mac/network work,
ZERO bench hardware, ZERO board contact -- do not touch nereus000 or the
OpenMV boards; the S8 ball sessions own them. Its output gates S8 bite E
(the urchin model).

THE SOURCE DOCUMENT: docs/urchin_datasets.md -- Nick's two-sweep research
from 2026-08-17. Treat EVERY row as a claim until you have verified it;
live counts in it are a date-stamped snapshot. S26 = turn that file into
a verified inventory + a training-corpus plan with real numbers.

SETTLED -- do not re-derive:
- Headline finding (verify, don't re-search): no existing dataset labels
  purple-vs-red urchins with boxes at scale. Urchinbot (CC-BY 4.0, 9,872
  imgs / 44k boxes, temperate reef, pretrained YOLOv5 incl.) is the
  pretraining anchor; iNaturalist/GBIF is the only purple-vs-red signal
  (image-level, needs auto-boxing); DUO's 50k urchin boxes are
  license-unstated -- fence research-only. The file's dead-ends list is
  real work already done: extend it, never re-search it silently.
- The 4-step data strategy at the file's end (backbone / species head /
  domain fine-tune / NOAA yolo11 baseline) is the shape bite 3 fills in
  with verified numbers -- not a thing to reinvent.

RULES OF ENGAGEMENT:
- ACCOUNT SIGNUPS, LOGINS, AND API KEYS ARE NICK'S HANDS, always (same
  rule as sudo): prep the exact steps/URLs, hand them over, wait. Never
  create accounts or enter credentials yourself.
- Datasets land in ~/nereus_ml/datasets/<source>/ on the Mac -- NEVER in
  the repo (worktrees; multi-GB). The repo gets the dossier, manifests
  (sha256 + counts), and small sample crops only if tiny.
- Licenses: capture the ACTUAL license text per source verbatim into the
  dossier -- "the page said CC-BY" is not an artifact. FathomNet is
  per-image licensing: filter and count CC0/CC-BY separately from NC/ND.
- Trust artifacts: a download that "succeeded" but has no images failed.
  Verify counts against claims, open real images, check boxes land on
  urchins before a row is stamped verified.

BITES (TRACKER S26): 1 = access + inventory verification (dossier rows:
verified count / format / license / sample-viewed, dated). 2 = label
quality + usable-volume-after-license-filter + NOAA yolo11 weights
load-and-run on the Mac. 3 = the corpus plan mapped onto the 4-step
strategy, reviewed with Nick, S8 bite E re-scoped against it.

Nibble 1 = plan first, my gate before code/downloads over ~100 MB. Short
actionable replies, milestone report format per CLAUDE.md.
```

## 13 — Ready to paste: S8 bite E — urchin model: compile gate, then train (written 2026-08-22, after the S26 plan was minted)

```
Run /agent-entry. You are executing S8 bite E per the APPROVED corpus
plan: docs/urchin_corpus_plan.md (Nick, 2026-08-22). That document is
the contract — read it FIRST, then docs/urchin_datasets.md
(§Verification + §Bite-2 QA) and ml/urchin_data/ (manifests, convert.py,
eval_rung_a.py). This is Mac-side train-compile-measure work: NO bench
hardware, NO board contact — nereus000 and both OpenMV boards belong to
the S8 bench sessions; on-board deployment happens later through the S25
workbench, coordinated with Nick.

SETTLED — do not re-derive or re-litigate:
- The corpus is FINAL and on disk under ~/nereus_ml/datasets/ with
  labels.jsonl per source (S26 delivered; manifests + sha256 in repo).
  Backbone = Urchinbot 44,268 + DUO 50,156 (UNFENCED, Nick) + RF100
  25,299 boxes, single class "urchin". Species head = GBIF clean subset
  (2,009 purple / 569 red, image-level; ~70-75% out-of-water — filter).
  74-img set = hard-case eval, purple-only. NO dataset work remains.
- Architecture decision (Nick): Apache-2.0 family (YOLOX/NanoDet class),
  GATED on the compile check below. AGPL fallback only if no Apache
  candidate passes both compilers — then documented, not debated.
- Baseline to beat, rung A (983-img official Urchinbot test split, via
  ml/urchin_data/eval_rung_a.py): yolo11n mAP50=0.243 / yolo11x 0.351.
  Ceiling proof on the same data: Urchinbot's published 0.908.
- Pixels-on-target is THE eval axis (distance is not a variable).
  Targets ≥24-32 px min-side; downscale-augment Urchinbot into the
  24-64 px band. labels.jsonl convention: absolute-pixel
  [ci,x0,y0,w,h,pixels]; converters are the species source of truth.

BITE E ORDER (each nibble-gated by Nick):
1. COMPILE GATE FIRST, before any training: 1-2 untrained Apache-2.0
   candidates (192-256 px input) int8-exported and pushed through BOTH
   board compilers — Vela (AE3 Ethos-U55) AND stedgeai (N6 Neural-ART)
   — reusing S8's phase-0 scaffold (ml/compile_model.sh; the S8 session
   confirmed this is an afternoon, not a toolchain bite). Deliverable:
   NPU placement report per candidate. STOP for Nick's pick.
2. corpus_v1: merged training view under ~/nereus_ml/datasets/corpus_v1/
   (symlink/manifest based, NO copies), Urchinbot official splits
   respected (983-img test split NEVER trains).
3. Stage-1 training on the gated architecture; score rung A; iterate
   until decisively above 0.351; report vs baseline and ceiling.
4. Stage-2 auto-box: stage-1 model over GBIF clean images, underwater/
   junk filter (hands, dry, dead tests, specimens, larvae), species from
   folder; rung B = ~150+150 hand-verified crops (Nick reviews in S8's
   label GUI: python3 ml/fomo/label_gui.py ~/nereus_ml/datasets).

RULES: venvs under ~/nereus_ml/venvs/, every training run records
config + git sha + data-manifest hash under ~/nereus_ml/runs/; the repo
gets manifests, model cards, and eval tables — never datasets or
checkpoints. Trust artifacts, not exit codes: a "trained" model is a
scored model. Long/expensive runs (>~30 min GPU/CPU) get my gate first.
Milestone report format per CLAUDE.md.
```

## 14 — Ready to paste: S8 bench window — power rig + deploy both stage-1 candidates + measured numbers (written 2026-08-23, after bite E's Mac-side arc merged)

```
Run /agent-entry. This is an S8 BENCH session on nereus000 — board
contact allowed, THROUGH the S25 workbench discipline (check
http://nereus000:8088/api/runner + /api/preflight before any board
touch; one owner per port; 35 s settle after any stop; by-id paths
ONLY; use the ae3-board-access skill before any mpremote against the
AE3). The Mac may be busy training the labeler — do NOT start Mac GPU
work; artifacts below are already built.

CONTEXT (do not re-derive): S8 bite E's Mac-side arc is MERGED (PR #60).
Two deployment candidates are staged, placement-verified, int8:
  ~/nereus_ml/exports/stage1_v2/       nano  (1.0 MB, vela est 28.1 ms)
  ~/nereus_ml/exports/stage1_tiny_v1/  tiny  (4.97 MB, vela est 41.7 ms)
Each holds ae3/ (vela .tflite for /flash) and n6/ (ROMFS-able binary).
Decision table awaiting the bench numbers: ml/yolox_urchin/STAGE1.md.
Deploy routes are PROVEN (S8 B1 / ml/README.md): AE3 = copy to /flash +
sha read-back; N6 = ROMFS image via USB DFU alt 3 (NEVER alt 0).
Vela estimates have measured 2.7x optimistic before (FOMO 2.05->5.51 ms)
— only measured numbers count (S8 standing rule).

TASKS, in order, each nibble-gated by Nick:
1. POWER RIG FIRST (bite D sub-item, Nick's parts in hand, ~few hours):
   stand up the USB power meter inline on a board's supply, get the Pi
   LOGGING it (identify the meter's interface; a timestamped CSV/JSONL
   logger the workbench can start/stop; write the procedure down as a
   routine). Verifiable: a logged idle-vs-load power trace of one board.
2. Deploy BOTH candidates to BOTH boards by the proven routes; verify
   by bytes (sha read-back / partition read-back), never by rc.
3. Measured per-inference latency: timing loop per model per board,
   NPU-consistency check vs the tables (a CPU fallback or a failed
   tiny SRAM arena is obvious in the number). Also os.statvfs('/flash')
   — closes SPEC's AE3 memory open question.
4. mJ/inference from the power rig during the timing loops — the
   DUO-style energy column, both boards, SAME model (kills the S24-era
   model-binary confound).
5. Fill the decision table in ml/yolox_urchin/STAGE1.md + bite D's
   table; STOP for Nick's nano-vs-tiny pick.
Bank but do not start: the HIL demo (urchin footage on a screen via the
workbench) rides the picked model as bite E's demo bar.

RULES: no firmware flashing beyond the proven model-deploy routes; the
workbench page stops demos, never kill -9 on port holders; boards left
enumerated + ports free at session end; artifacts (traces, tables,
logs) recorded under ~/nereus_ml/ + repo eval tables. Milestone report
format per CLAUDE.md.
```

## 15 — Ready to paste: S8 bite E2 — root-cause the AE3-tiny HIL anomaly (written 2026-08-25, after the first scored matrix)

```
Run /agent-entry. This is an S8 HIL BENCH session on nereus000 — you
OWN the bench and the HIL rig for this session (board contact through
the S25 workbench discipline: check http://nereus000:8088/api/runner +
/api/preflight first; one owner per port; 35 s settle; by-id ONLY;
ae3-board-access skill before any mpremote against the AE3; the
hil-setup skill is the rig runbook). PREREQUISITE: PR #63 merged (or
branch from claude/yolox-decode-nms-sizing-3ca136) — the rig lives
there.

CONTEXT (do not re-derive): the 2026-08-25 HIL matrix
(~/hil_runs/matrix_d70_1/, table in TRACKER header + PR #63) measured
the AE3-tiny anomaly this session exists to explain: AE3 tiny-tiled
recall 0.13 with 3-4x FEWER detections than AE3 nano-tiled (0.39),
while the N6 orders them as bench mAP predicts (tiny 0.40 > nano 0.32)
and AE3-tiny's few detections score HIGHER conf than nano's (p50 0.65
vs 0.56). Same int8 tflite both boards; AE3 runs it from /rom (OSPI
XIP, ROMFS0 image built 2026-08-24); timing NPU-consistent (351.7 ms
~= 6x58.4). It computes fast but under-detects, on one board only.

GOAL (bite E2, TRACKER): find WHY, fix it or measure the explanation.
Success = an AE3 tiny-tiled HIL leg whose recall ordering vs nano
matches the N6's, or a documented mechanism why it cannot.

METHOD — Nick's explicit instruction: WORK IN A LOOP. Test one theory;
when it does not hold, record the verdict and move to the next. The
ladder, cheapest first (renumber freely if evidence reorders it):
T1 desk: conf sweep on recorded rows (det_conf is in rows.jsonl) —
   is tiny recall hiding below the 0.30 threshold?
T2 desk: compile/deploy audit — was tiny built/deployed compatibly
   with the AE3? Diff the artifact chain vs nano and vs the N6 copy:
   export.py int8 config, vela profile (RTSS_HP_SRAM_OSPI for /rom vs
   the /flash config), sha of the tflite INSIDE the AE3 ROMFS image vs
   ~/nereus_ml/exports/stage1_tiny_v1/. Anything tiny-only or
   AE3-only is a suspect. Recompiling + redeploying tiny to the AE3 by
   the PROVEN routes (ml/README.md; ROMFS0 DFU) is IN SCOPE this
   session — verify the artifact, do not assume it.
T3 bench (the discriminator): golden-input diff — one known 256x256
   input through (a) Mac int8 interpreter, (b) N6 /rom tiny, (c) AE3
   /rom tiny; compare raw cells. (c) diverges => AE3 deployment/
   runtime guilty. All agree => upstream (camera/ISP/preproc, T5).
T4 bench: cell-cap/obj_thr — rerun one AE3 tiny-tiled leg with
   cell_cap 256 / obj_thr 0.05 (harness _CFG knobs, no code change).
T5 bench: AE3-camera domain — AE3-captured frames through the Mac
   interpreter; isolates the warmer AE3 ISP from its runtime.
Repeat HIL legs are allowed whenever a theory needs one (the rig is
yours); hil-setup skill section 4 has the harness command; cameras and
screen are positioned and calibrated per-run automatically — DO NOT
move them.

RULES: shielded USB cables only (SPEC — the N6 unshielded-cable
incident); no browser/kiosk on the Pi while boards run (hil-lcd only);
no firmware flashing (model deploys by the proven routes are fine);
boards left enumerated + ports free; one verdict per theory in
DEV_LOG; artifacts under ~/hil_runs/ + ~/nereus_ml/runs/; milestone
report format per CLAUDE.md; STOP at the fix (or the explanation) for
Nick's review before folding anything into the decision table.
```

## 16 — Ready to paste: S8 bite E4 — closed-loop HIL handshake + two-board runs (written 2026-08-25, after the open-loop hazard was measured)

```
Run /agent-entry. This is an S8 HIL BENCH session on nereus000 — you
OWN the bench (workbench discipline: check nereus000:8088/api/runner +
/api/preflight first; one owner per port; 35 s settle; by-id ONLY;
ae3-board-access before any mpremote against the AE3; hil-setup is the
rig runbook). Branch from main after the E2 PR merges.

CONTEXT (do not re-derive): the HIL harness is OPEN-LOOP — the board
free-runs a pre-budgeted frame count while the host steps stills and
discards frames arriving inside a settle window. The hazard is
MEASURED: the 2026-08-25 AE3 HD leg scored frame-1s at 0.33 recall vs
frame-2s at 0.59, because the AE3's ~3.5 s HD frame cycle exceeds the
2.5 s settle — first frames straddle the still change. VGA legs audit
clean (recall-by-frame_in_still deltas <=0.002 — that audit is the
instrument). Also open-loop costs: the surplus-budget drain tail
(minutes of dead screen at HD) and serialized single-board runs.

GOAL (bite E4, TRACKER — Nick's design is the spec): the Pi hosts the
image, TELLS each camera to start inferring, each camera reports DONE
and waits, and only then does the Pi advance. One control byte
host->board on the VCP (board polls stdin between frames). The same
plumbing delivers: per-still handshake (sync errors impossible by
construction; settle/budget-slack deleted), phase barrier, BOTH boards
running simultaneously (still advances when ALL boards report done;
matrix wall time = slower board alone), and early phase exit (drain
tail deleted). Failure containment: a dead board stream drops out of
the barrier, the rest continue solo, score-what-was-collected.

METHOD — TEST-FIRST, Nick's explicit contract: the new communication
method is PROVEN BY TESTS before anything is handed over for a demo.
Nibble 1 plan, then: (a) protocol state machine as its own testable
module; host tests against a FAKE board covering the ugly cases —
board dies mid-phase, garbled/partial lines, one board stalls, host
byte lost (timeout->recover), CRLF translation (the known CDC trap);
(b) board-side stdin poll proven on ONE board with a trivial
echo-probe before the full harness rides it; (c) bench acceptance =
a two-board VGA run vs back-to-back solo runs — per-board scores
within noise, per-phase wall time ~= the slower board alone, ZERO
settle-discards in the log; then one AE3 HD leg whose
recall-by-frame_in_still deltas are <=0.01 (the measured hazard,
gone). Only after (c) passes does Nick get the demo.

RULES: shielded USB cables only; no browser/kiosk on the Pi (hil-lcd
only); no firmware flashing; boards left enumerated + ports free;
artifacts under ~/hil_runs/; milestone reports per CLAUDE.md; the
existing single-board path stays working until the new path's
acceptance passes (never strand the bench).
```
