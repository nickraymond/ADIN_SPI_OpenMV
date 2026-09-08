# openmv_build — OpenMV firmware dev environment on the Mac

Build OPENMV_AE3 firmware in docker on an Apple Silicon Mac; artifacts ship
to nereus000 and flash headlessly via `pi/ae3_flash/`. This is the build leg
of the S7 remote-dev-loop spike (edit → build → scp → flash → test).

## Why docker on the Mac, not the Pi

The build needs the OpenMV SDK toolchain bundle, which is published only for
`linux-x86_64` and `darwin-arm64` (probed 2026-08-11 — `linux-aarch64` 404s).
Docker on the Pi 5 would mean qemu amd64 emulation on the live fixture host;
the Mac runs the same amd64 container fast under Rosetta. Decision D23.

## One-time setup

```bash
./setup_mac.sh
```

Installs Docker Desktop (brew cask `docker-desktop`) if missing and downloads
the sha-verified linux-x86_64 SDK. Then launch Docker Desktop once and
approve its password prompt. VS Code is already installed; enable the `code`
CLI from its command palette if wanted. For hands-on flashing at the desk,
OpenMV IDE comes from https://openmv.io/pages/download (no brew cask) — the
IDE's `Tools → Run Bootloader` flashes a custom `firmware.bin` over the same
DFU path the headless tool uses.

## Build

```bash
./build_ae3.sh                      # master HEAD, from clean
./build_ae3.sh --rev v5.0.0         # exact release tag
./build_ae3.sh --incremental       # dev loop: skip the clean, rebuild deltas
```

Wraps `openmv.git`'s `docker/Makefile build-firmware-dev` (reuse before
rewriting) with rev pinning, the amd64-platform + linux-SDK plumbing, and
artifact verification (existence, per-core size windows, embedded id) into
`MANIFEST.txt`. First build is the slow one (image + submodules + full tree,
~15 min); prints the scp command for the Pi when done.

**Why `build-firmware-dev` and not the stock `build-firmware`:** the stock
target's `build.sh` passes `BUILD=<dir>` on the make command line, which
rides MAKEFLAGS into every sub-make and overrides `ports/alif/alif.mk`'s
`BUILD := $(BUILD)/$(MCU_CORE)` per-core nesting. Both cores then share one
object dir and the M55_HE image links HP-configured objects — FLASH_TEXT
154%, undefined `dcd_*` (TinyUSB DCD), relocation errors. OpenMV's CI builds
AE3 *without* docker (`tools/ci.sh`) and never hits it; `build-dev.sh`'s own
comments document the nesting requirement. Root-caused 2026-08-11 (DESIGN
decision log); verified fix: HE links at 1,193,520 B vs 1,185,744 B official.

Editing workflow: clone lives at `~/openmv-dev/openmv` — open it in VS Code,
edit, re-run `build_ae3.sh --incremental`. A dirty tree skips the rev sync
(your edits are never hard-reset away) and shows as `-dirty` in the manifest
rev.

## Building for the N6 (`build_n6.sh`)

```bash
./build_n6.sh                     # upstream master
./build_n6.sh --rev v5.0.1        # exact release tag
./build_n6.sh --pr 3247           # an UNMERGED upstream pull request head
./build_n6.sh --pr 3247 --incremental
```

Same docker/Rosetta/SDK plumbing and the same artifacts-not-exit-codes
verification as `build_ae3.sh`, with two deliberate differences.

**It uses a separate tree, `~/openmv-dev/openmv-n6`.** The AE3 clone above is
the C dev loop and carries this repo's in-flight AE3 patches (framebuffer
sticky-highwater, jpege MVE colorconvert). An N6 build must not silently
inherit them and then be reported as "upstream". The script seeds the second
tree by cloning the first when it exists — same filesystem, so git hardlinks
the objects and the history costs ~0 disk.

**`--pr <N>` builds an unmerged upstream PR by number.** That is the point:
hardware H.264 on the N6 is openmv/openmv#3247, which is upstream work rather
than a fork (S31 — `docs/N6_H264_FINDINGS.md`). The PR head is fetched to
`FETCH_HEAD`, not to a local branch, because git refuses to fetch into a
branch that is checked out.

**The `safe.directory` patch (`../openmv_patches/0003`) is required, not
optional.** Without it the container's `git submodule update` dies with
"detected dubious ownership" → `make: Error 128` (re-measured 2026-09-07 by
reverting it). `build_n6.sh` applies it as a real commit on a throwaway
`build/n6-<sha>` branch instead of as a dirty-tree edit, so `git describe`
stays clean and the firmware's embedded version label remains checkable
rather than degrading to `...dirty`. The patch touches `docker/Makefile`
only — no compiled source.

**Verification beyond "exit 0".** All 7 artifacts an official
`firmware_OPENMV_N6.zip` carries must exist and be plausibly sized; the
firmware's self-reported label must match the built rev; and the manifest
records a **feature probe** (`codec.H264Encoder in image: yes|no`), because a
firmware that builds cleanly without the feature in it is the failure mode
this repo keeps meeting.

Flashing is out of scope for this script. See `N6_H264_FLASH.md`.

## bm_core next (placeholder)

Same pattern planned post-S7 decision: Sofar's bm_core in its own container,
side by side, nothing installed on the host beyond docker. Not built yet —
tracked by the S7 decision gate.
