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

## Route B — headless `dfu-util` — **PARTIALLY UNVERIFIED, read the caveat**

The N6's bootloader exposes named DFU partitions at `37C5:9206`:
`BOOTLOADER`(0), `FIRMWARE`(1), `FILESYSTEM`(2), `ROMFS0`(3). This repo has
**proven alt 3** (ROMFS0) end to end with read-back verification
(`ml/README.md` §"N6 RESOLVED 2026-08-20"). Writing **alt 1 (FIRMWARE) has
never been done on this bench**, and whether the bootloader wants the raw
`firmware.bin` there or a wrapped image is **not verified** — do not assume
it from the ROMFS success.

Settle that question before using this route, not during. Route A is
documented by OpenMV and needs no assumption; Route B is worth proving only
because a headless path is what field rigs need.

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

**Alt 0 is never written.** That is what keeps this recoverable: the DFU
window survives a bad application write, so the board can always be
re-entered and rewritten.

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

Flash the release `firmware.bin` recorded in step 2 above by the same route.
ROMFS0 is untouched by either direction, so the models do not need
redeploying.
