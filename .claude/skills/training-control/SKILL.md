---
name: training-control
description: Start, monitor, or watch ANY Mac-side training run (YOLOX, RF-DETR, future trainers) through the ONE sanctioned browser cockpit — train_ctl and its subclass pattern. Use BEFORE launching a training run, when asked to "bring up" training progress/mAP/epoch UI, or when a run is already going and needs supervision. Never build an ad-hoc monitor page — that mistake has already been made and corrected (2026-08-25).
---

# Training control — one cockpit, every run

OWNER: **Nick**. The rule this skill exists to enforce, learned live
2026-08-25: Nick asked for "the UI we use" and got a bespoke read-only
page instead of the controller he meant. **The controller is
`ml/yolox_urchin/train_ctl.py`.** Every training run gets IT (or its
subclass), with the Start/Pause/Stop buttons, the progress bar, the
thermal warning, and the CPU/GPU time-series. A page without the
buttons is not the UI.

## What the cockpit gives (all of it, every time)

- **Start / Pause / Stop buttons.** Pause = SIGSTOP (instant, GPU
  freed, nothing lost — resume continues mid-instruction). Stop =
  SIGTERM. For train.py runs stop checkpoints within one iteration;
  for rfdetr the checkpoint is epoch-end only — the subclass rewrites
  the hint text to say so. Never lie in the hint.
- **Progress bar + epoch/pace rows** parsed from the run's own
  artifacts (train.py: `loss.log`; Lightning trainers: `metrics.csv`).
- **System panel**: CPU % + GPU % time-series (psutil + `ioreg`
  IOAccelerator sampling, 10 s cadence, 2 h ring) with **red vertical
  lines wherever `pmset -g therm` reported a thermal warning** — the
  "is my laptop cooking" view. Real °C needs root; the pmset flag is
  the signal that matters.
- **Loss + rung-A mAP50 panels** (`plot.png`), the mAP history over
  time with the comparison bar drawn in.
- **Adoption**: a controller started AFTER the run finds it by pgrep
  and controls it — a detached run is never a reason to build a
  read-only page.
- **Night schedule** row (launchctl, train.py runs only).

## Using it

YOLOX/train.py run (config in the terminal, page is the switch):

```bash
~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train_ctl.py \
    [--score-every N] -- --arch yolox-tiny --epochs 160 ... --run-name <run>
```

RF-DETR gate (the subclass adapter — same page, rfdetr specifics):

```bash
~/nereus_ml/venvs/rfdetr/bin/python ml/rfdetr_gate/gate_ctl.py --port 8894
```

- **One controller instance per run, one port per instance.** 8898 is
  the canonical train.py port; take the next free (8894…) for a
  parallel run. `Errno 48` means an older controller is still serving
  — check WHOSE run it is before killing it (a finished run's page may
  still be someone's open tab).
- Ctrl-C stops the PAGE only; the training child runs in its own
  session and is stopped only by its Stop button.
- The controller's venv needs `psutil` and `matplotlib` (the plots and
  the system sampler import them lazily).

## Adapting to a NEW trainer (the subclass pattern)

Copy `ml/rfdetr_gate/gate_ctl.py`'s shape — subclass `train_ctl.Ctl`
and override ONLY: `start()` (spawn command + env), `_adopt()` (pgrep
pattern), `_cfg()` (model/init/corpus rows), `status()` (progress from
the trainer's native artifact), `plot_png()` (loss + score history).
Reuse the page (string-replace the title and any hint whose semantics
differ), the handler, the sampler, and the signal primitives untouched.
Do not fork the HTML; do not write a new server.

## Traps

- `setsid` does not exist on macOS — detach with
  `nohup ... & disown`, or spawn via the controller (start_new_session).
- A controller launched into a taken port dies AFTER printing nothing —
  always curl `api/status` and check the `run` field names YOUR run.
- Lightning's metrics.csv flushes lazily (rows can lag minutes at slow
  step rates); "measuring…" pace is buffering, not a hang — confirm
  liveness via the GPU % series instead.
