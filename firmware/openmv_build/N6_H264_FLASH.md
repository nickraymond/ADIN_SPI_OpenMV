# N6 H.264 firmware — artifact and flash procedure (S31)

**The session that built this did not flash it and could not: the boards were
owned by another session.** This is the written procedure a hardware-owning
session follows, plus what it must check before and after.

Background and the decision this rides on: `docs/N6_H264_FINDINGS.md`.

---

## What was built

```bash
./firmware/openmv_build/build_n6.sh --pr 3247
```

| | |
|---|---|
| Target | `OPENMV_N6` |
| Upstream | [openmv/openmv#3247](https://github.com/openmv/openmv/pull/3247) head `5aa47553` — *unmerged*, milestoned v5.1.0 |
| Base | `v5.0.0-56-g5aa47553` |
| Build harness | `firmware/openmv_patches/0003-docker-makefile-git-safedir.patch`, committed on a throwaway `build/n6-<sha>` branch. **`docker/Makefile` only — no compiled source.** Required: without it the container's `git submodule update` fails "dubious ownership" → `make: Error 128` (re-measured 2026-09-07) |
| SDK | 1.6.0 linux-x86_64, amd64 container under Rosetta |
| Tree | `~/openmv-dev/openmv-n6` — **deliberately not** the AE3 dev clone, which carries this repo's in-flight AE3 patches |

Artifacts land in `~/openmv-dev/openmv-n6/build/OPENMV_N6/bin/` alongside
`MANIFEST.txt` (sha256 of each, plus the label the firmware self-reports).

### The build was verified, not merely exited-zero

| Check | Result |
|---|---|
| All 7 artifacts present and non-empty | yes — the same 7 filenames an official `firmware_OPENMV_N6.zip` carries |
| `firmware.bin` | **2,043,432 B**, sha256 `99fc71af73566fbf5a9f5dcea37f52609ec4c8214bad00ec00e13d6b73770c39` |
| `.text` | 2,035,464 B — `FLASH_TEXT 55.68 %` of the N6's 3584 KB region, so the feature is nowhere near a flash wall |
| Feature probe | `strings firmware.bin \| grep H264Encoder` **hits**, and the ELF carries `MP_QSTR_H264Encoder` and `py_codec.c`. The binding is really in the image |
| Self-reported label | `v5.0.0-57.g9d67f849f2` — matches the manifest's `build_sha` |
| **Reproducible** | re-running the build yields the **same** `build_sha` and the **same** `firmware.bin` sha256. The harness commit's dates are pinned to the upstream commit's so identical inputs give an identical artifact |
| **A/B against the PR's own base** | the base commit `aa5d9f7d` was built the same way: `firmware.bin` **1,985,272 B** vs the PR's **2,043,432 B** = **+58,160 B (+2.93 %)**, against upstream CI's reported **+58,120 B (+2.94 %)**. `FLASH_TEXT` 54.09 % → 55.68 %. The feature's cost in flash is measured here, not inferred |
| **Negative control** | the same probe on the base build correctly reports `codec.H264Encoder in image: no`. A probe that only ever says "yes" proves nothing |

A firmware that builds without the codec in it is exactly the failure mode
this repo keeps meeting, so `build_n6.sh` records the probe result in the
manifest as `codec.H264Encoder in image: yes|no`.

**The probe lied once, and the way it lied is worth carrying.** Written as
`strings … | grep -q`, it reported **no** on an image that provably contained
the string. Under `set -o pipefail`, `grep -q` exits at the first match and
closes the pipe, `strings` dies of SIGPIPE (141), and pipefail hands the
pipeline that failure — so *finding* the feature was reported as *not finding*
it. `grep -c` reads to EOF and cannot SIGPIPE. This is CLAUDE.md's
"a pipeline returns the LAST command's status" trap wearing a different hat:
here the verifier itself was the thing that needed verifying.

---

## Before flashing — read this first

1. **Board ownership.** One owner per board port, ever. Check the workbench
   (`http://nereus000:8088/api/runner`, `/api/preflight`) and stop demos from
   the page, never by killing a process. The N6 is
   `usb-MicroPython_Pyboard_…` (the names are backwards from the guess).
2. **Record what is on the board now**, so it can be put back:
   ```bash
   mpremote connect <n6-by-id> exec 'import os; print(os.uname())'
   ```
   Then download the matching release from
   `https://github.com/openmv/openmv/releases` — that zip is the rollback.
3. **Do NOT flash `romfs0.img`.** The build's ROMFS carries the *vendor's*
   models only. The N6 on this bench has a custom ROMFS0 with
   `nereus_fomo.tflite` in it (S8 bite B2, `ml/build_romfs_n6.sh`), and
   writing this image over it destroys that deployment. Only the FIRMWARE
   partition needs to change.
4. **This is a non-release firmware.** Everything the N6 does today — model
   loading from `/rom`, the S29 discovery/ownership path, the workbench
   recipes — is unproven on it until re-run. Budget for that, and see the
   bench plan in `docs/N6_H264_FINDINGS.md` §Rung 6, whose leg 0 is a
   regression check before any codec work.

---

## Route A — OpenMV IDE (the documented path)

OpenMV's own `docs/firmware.md` names this as the way to load a locally
built image: install OpenMV IDE, then `Tools → Run Bootloader`, and give it
`firmware.bin`. The IDE does not check that the firmware matches the camera
model, so point it at the N6 build, not the AE3 one. A wrong image does not
damage the board — re-run the bootloader with the right `firmware.bin`.

This is the route to use unless a headless flash is needed.

## Route B — headless `dfu-util` — **PROVEN 2026-09-08 (S33), use `n6_flash.py`**

The N6's bootloader exposes named DFU partitions at `37C5:9206`:
`BOOTLOADER`(0), `FIRMWARE`(1), `FILESYSTEM`(2), `ROMFS0`(3). This repo had
**proven alt 3** (ROMFS0) end to end (`ml/README.md` §"N6 RESOLVED
2026-08-20"); **alt 1 (FIRMWARE) is now proven too**, rehearsed stock → stock
on nereus002 *before* any H.264 image went near the board.

**The answer to the open question: the bootloader wants the RAW
`firmware.bin` at alt 1, unwrapped.** The partition read back byte-identical
to the stock image over its first 1,987,840 bytes *before anything was
written*, which proved the read path at zero risk. A download to alt 1 erases
and writes only the pages it touches — after writing, the whole partition was
byte-identical to the pre-write backup.

Do not drive `dfu-util` by hand. Use **`pi/field/n6_flash.py`**, which carries
the three things the rehearsal discovered:

### 1. The partition is not just the firmware

| Offset | Size | What |
|---|---|---|
| `0x00000000` | 1,987,840 B | stock `firmware.bin`, byte for byte |
| `0x001F0000` | 11,984 B | a 12 KB blob present in `openmv.bin`, **not** in the stock `firmware.bin` |
| `0x00380000` | 4 B | trailer |

99.3 % of the tail is erased `0xFF`. A whole-file `sha256` over the 3.6 MB
readback can *never* match a 2.0 MB image, so it would call a perfect flash a
failure. Verification therefore asks two separate questions — did the image
land at offset 0, and is everything past it still what the backup says — and
without a backup it reports the second as **not checked**, never as a pass.

### 2. The partition reads back LONGER than it can be written

An upload of alt 1 returns **3,670,020** bytes; the writable partition is
**3,670,016** (3584 KB = 896 × 4096). The extra four bytes read as `00000000`
and belong to nothing. Handing that file straight back to `dfu-util -D` fails
at 96 % with *"Cannot program memory due to received address that is out of
range"*. `n6_flash.py backup` stores the block-aligned image so the file it
writes is directly restorable, and refuses to trim a *non-zero* tail.

### 3. THE ROLLBACK IS A WHOLE-PARTITION RESTORE, NOT A `firmware.bin` WRITE

The H.264 build's `firmware.bin` is **2,043,600 B** and ends at `0x1F2ED0` —
exactly where the stock 12 KB blob ends — so it covers that region with its
own content. Writing the stock `firmware.bin` (which stops at `0x1E5000`) to
roll back would leave **a stale 12 KB region from the H.264 build** in place.
Restore the whole partition instead.

```bash
# BEFORE flashing anything: take the artefact that gets you home.
python3 pi/field/n6_flash.py backup --out ~/fw/n6_stock_v501_partition.bin
#   nereus002, stock v5.0.1: 3,670,016 B
#   sha256 45d9cd2123f763de5d94fffa4e7a952151f2847f4378809aa17a5bc19739ae09
#   also kept off-rig at ~/fw_backups/ on the Mac -- on the SD card alone it
#   dies with the SD card.

# Flash, byte-verified, with "nothing else was disturbed" actually checked.
python3 pi/field/n6_flash.py write \
    --image ~/fw/n6-h264/firmware.bin \
    --expect-unchanged ~/fw/n6_stock_v501_partition.bin

# ROLL BACK.
python3 pi/field/n6_flash.py write \
    --image ~/fw/n6_stock_v501_partition.bin --whole
```

Both directions verified on hardware; the board boots to `v1.28.0-64` with 19
ROMFS entries, no `codec` module, and **leg 0 VGA q30 at 68.7 fps** against
S30's 68.6 baseline.

**`dfu-util`'s exit code is not evidence.** `-R` returns **251** because
resetting the device is indistinguishable from losing it. The proof the board
came back is that it re-enumerates and answers `os.uname()`.

### The old hand-driven commands, for reference only

```bash
# On the Mac: ship the artifact.
scp ~/openmv-dev/openmv-n6/build/OPENMV_N6/bin/firmware.bin \
    ~/openmv-dev/openmv-n6/build/OPENMV_N6/bin/MANIFEST.txt \
    pi@nereus000:~/fw/n6-h264/

# Enter DFU (the N6's bootloader window is always present at boot).
ssh pi@nereus000 "mpremote connect <n6-by-id> exec 'import machine; machine.bootloader()'"

# UNVERIFIED STEP — see the caveat above.
ssh pi@nereus000 "dfu-util -a 1 -D ~/fw/n6-h264/firmware.bin"

# Any read plus -R boots it.
ssh pi@nereus000 "dfu-util -a 2 -U /tmp/fs.img -R"
```

**Alt 0 is never written, and neither is alt 3.** Alt 0 is what keeps this
recoverable: the DFU window survives a bad application write, so the board can
always be re-entered and rewritten — demonstrated for real when the failed
whole-partition write above left the board sitting in `dfuERROR` and it read
back intact. Alt 3 is ROMFS0, where a rig's custom models live.
`n6_flash.py` refuses both by name.

---

## After flashing — verify the artifact, not the message

"Download done" is not evidence. Three checks, in order:

```bash
# 1. The board is running the image we built.
mpremote connect <n6-by-id> exec 'import os; print(os.uname().version)'
#    expect the manifest's openmv_label: v5.0.0-57.g9d67f849f2

# 2. The binding exists and constructs.
mpremote connect <n6-by-id> exec 'import codec; e = codec.H264Encoder(640, 480, fps=30, bitrate=1000000); print(e); print(len(e.sps_pps()))'
#    expect a JSON-ish repr with width/height/fps/bitrate, and a non-zero SPS/PPS length

# 3. The models survived. ROMFS0 was not written, so this must be unchanged.
mpremote connect <n6-by-id> exec 'import os; print(len(os.listdir("/rom")))'
#    expect 18 entries, nereus_fomo.tflite among them (S8 bite B2)
```

Then run leg 0 of the bench plan — the S30 MJPEG probe — before trusting any
H.264 number. If VGA q30 does not reproduce at 68.6 fps / 13.7 KB, stop.

## Rolling back

**Restore the whole partition** — see Route B §3 above for why flashing the
release `firmware.bin` is NOT sufficient (it would leave the H.264 build's
12 KB region at `0x1F0000` behind):

```bash
python3 pi/field/n6_flash.py write \
    --image ~/fw/n6_stock_v501_partition.bin --whole
```

ROMFS0 is untouched in either direction, so models never need redeploying.
