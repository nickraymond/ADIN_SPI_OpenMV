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
