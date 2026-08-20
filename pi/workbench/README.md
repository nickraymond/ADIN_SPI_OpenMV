# The machine-vision workbench (S25)

Boot nereus000, open **http://nereus000:8088/**, pick a test, click
**▶ Start**. The Pi verifies the boards are in that test's known-good
state (repairing what it safely can), runs it, and shows the demo link
when it is LIVE. **■ Stop demo** tears it down cleanly; **⚙ Dev mode**
confirms the ports are free for mpremote/flashing.

- Server: `workbench.py`, unit `pi/services/workbench.service`
  (**enabled at boot** — install once with
  `sudo pi/install_stream_service.sh workbench`).
- Tests: `python3 pi/workbench/test_workbench.py` (no hardware needed).
- Exposure (Nick, 2026-08-20): binds `0.0.0.0`, loud banner, no auth,
  trusted LAN only.

## How to add a test (the release step)

1. **Drop a TOML file in `recipes/`.** That is the whole release: the
   registry re-reads per request, so the card appears on refresh. A file
   that fails validation shows as a red card naming the error — fix it
   until the card turns normal.
2. **Give it a thumbnail.** Capture a representative frame while the
   test runs (e.g. `curl http://nereus000:8090/s/0/frame.jpg`), put it
   in `recipes/thumbs/`, reference it as `thumbnail = "thumbs/x.jpg"`.
3. **Run the test suite.** `test_workbench.py` includes a
   shipped-recipes-load-clean gate, so a broken released recipe fails CI
   on any machine, not just the bench.
4. **Sanity-check the safety posture** (see below) if your recipe
   declares models.

## Recipe reference

```toml
name  = "my-test"            # unique, kebab-case; the API id
title = "Friendly name"      # what the card shows — no sprint jargon
summary = "One or two sentences of what the operator will see."
opens = ":8090"              # where the demo serves once LIVE (optional)
thumbnail = "thumbs/my.jpg"  # optional; lives under recipes/thumbs/
services = []                # systemd units this test needs shown in
                             # preflight (omit if none — most CV tests)

[[boards]]                   # one per board the test owns
label = "AE3"                # display name
by_id = "usb-OpenMV_..."     # name under /dev/serial/by-id — NEVER ttyACM
firmware = "v5.0.0-52...."   # optional: substring of the board's
                             # sys.version; drift REFUSES the run
# [[boards.models]]          # optional: files that must be on the board
# name  = "detector"
# path  = "/flash/m.tflite"  # where the board loads it from
# sha256 = "…64 hex…"        # exact bytes expected
# src   = "ml/artifacts/m.tflite"  # repo artifact to repair from
                             # (copy-route boards only — see below)

[run]
argv = ["python3", "bench/my_test.py", "--bind", "0.0.0.0", "..."]
cwd  = "."                   # relative to the repo root

[health]
http = "http://127.0.0.1:8090/"   # LIVE = this answers 200
```

## What Start actually does

preflight (ports free? boards present?) → **reconcile** (one serialized
`mpremote exec` per board reads `sys.version` + sha256 of declared
models; drift is repaired only via the file-copy route and re-verified
by hash, everything else is reported with the manual step) → spawn the
`[run]` argv in its own process group → poll `[health]` until 200 →
**LIVE**. Stop sends SIGINT, then SIGTERM after a grace period. After
any stop the boards get a **35 s settle window** before the next start
(the AE3 wedges without it — measured).

## The safety posture (do not weaken it in a recipe)

This page is an unauthenticated LAN control surface, so the runner
refuses anything it cannot undo:

- **Never DFU alt 0** (BOOTLOADER). The schema cannot even express a DFU
  target; the N6's ROMFS model deploy stays a deliberate manual act
  (`ml/README.md`) and reconcile only *reports* N6 model drift.
- **Never SIGKILL** (it has taken the N6 off the USB bus); a demo that
  ignores SIGINT+SIGTERM goes to a STUCK banner with the manual command.
- **Never touch a port it does not own**: foreign holders are named,
  never killed; one demo at a time; the settle window is enforced.
- **Firmware drift is never auto-fixed** — reflash via the S7 ladder
  (`pi/ae3_flash/README.md`) or update the recipe.
