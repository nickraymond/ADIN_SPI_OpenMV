# ml/yolox_urchin — stage-1 detector + stage-2 species head (S8 bite E)

Model card + eval ladder: `STAGE1.md`. Pieces: `model.py` (gated
YOLOX-Nano/Tiny builder + MPS patches + export wrapper), `data.py`
(corpus_v1 dataset, band-targeted downscale + box-aware crop + mosaic),
`train.py`, `eval_rung_a.py` (float `.pt` or int8 `--tflite`),
`export.py` (int8 + both board compiles), `stage2_autobox.py`,
`harvest_crops.py`, `train_species.py`, `plot_curves.py`.

## Training control page (`train_ctl.py`)

One-switch browser control — config stays in the terminal; the page is
a light switch, not a cockpit. Start / Pause / Resume / Stop + the
night-schedule toggle, live status from the run's loss.log:

    ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train_ctl.py \
        -- --arch yolox-s --epochs 120 --batch 24 --mosaic 0.75 \
           --run-name stage1_s_labeler --stop-after-hours 8

Open http://localhost:8898/. Start auto-resumes from the run's last.pt
when one exists; Pause/Resume = SIGSTOP/SIGCONT (instant, lossless);
Stop = SIGTERM → the trainer checkpoints within one iteration and exits
(the page shows STOPPING for the ~15 s of checkpoint + teardown). The
schedule button drives the LaunchAgent below and refuses politely if it
is not installed. Integration-tested live 2026-08-23 (all five controls
+ auto-resume + frozen-log artifact check).

## Running training around laptop use (Nick's controls)

**Pause instantly / resume instantly** (nothing lost, no checkpoint):

    pkill -STOP -f stage1_     # freeze: GPU freed, RAM kept
    pkill -CONT -f stage1_     # continue exactly where it stopped

A reboot kills a frozen run — worst case loses work since the last
epoch checkpoint (~5 min); recover with resume (below).

**Bounded sessions** — run 8 h and stop cleanly at an epoch boundary:

    ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train.py \
        --arch yolox-tiny --epochs 120 --batch 32 --mosaic 0.75 \
        --run-name my_run --stop-after-hours 8

    # next night: continue the same schedule (same run-name!)
    ... train.py <same flags> --run-name my_run \
        --resume ~/nereus_ml/runs/stage1_yolox/my_run/last.pt \
        --stop-after-hours 8

Resume restores model, optimizer, EMA, and the LR-schedule position;
epochs append to the same run dir and loss log.

**Automatic nights (23:00 → ~07:00)** — a user LaunchAgent; install is
Nick's hands (writes to ~/Library):

    cat > ~/Library/LaunchAgents/com.nereus.train-night.plist <<'PLIST'
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>Label</key><string>com.nereus.train-night</string>
      <key>ProgramArguments</key><array>
        <string>/bin/bash</string><string>-lc</string>
        <string>caffeinate -is ~/nereus_ml/train_night.sh</string>
      </array>
      <key>StartCalendarInterval</key>
      <dict><key>Hour</key><integer>23</integer>
            <key>Minute</key><integer>0</integer></dict>
      <key>StandardOutPath</key>
      <string>/tmp/train_night.log</string>
      <key>StandardErrorPath</key>
      <string>/tmp/train_night.log</string>
    </dict></plist>
    PLIST
    launchctl load ~/Library/LaunchAgents/com.nereus.train-night.plist

`~/nereus_ml/train_night.sh` holds the train command with
`--stop-after-hours 8` and `--resume` pointing at the run's `last.pt`
(first night: create the run without --resume; the script can test for
the checkpoint's existence). `caffeinate -is` keeps a lid-closed Mac
awake for the duration. Unload with `launchctl unload …` when the run
completes.
