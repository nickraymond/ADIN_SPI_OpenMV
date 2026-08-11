# ae3_flash — headless AE3 firmware flashing from the Pi (S7 spike)

Flash OpenMV AE3 firmware entirely from the nereus000 CLI: no OpenMV IDE, no
hands on the board. Uses the board's own always-present DFU bootloader — see
the protocol facts in `flash_ae3.py`'s header and DESIGN.md §S7 detail.

**HARD GATE: do not run any of this while the S6 demo fixture is live, or
while `t1l-sender` is active (it owns the AE3 USB port — preflight refuses).**

## One-time setup on the Pi (needs sudo → Nick)

```bash
sudo apt install dfu-util uhubctl
sudo cp ~/ADIN_SPI_OpenMV/pi/ae3_flash/99-openmv-dfu.rules /etc/udev/rules.d/
sudo udevadm control --reload
```

`uhubctl` is only for `--recover`; check `uhubctl` output once to learn the
hub location/port for the AE3 (Pi 5 port power switching is hub-dependent —
verify on the bench, don't assume).

## Get firmware

Either fetch a release:

```bash
./fetch_firmware.sh v5.0.0        # → ~/fw/v5.0.0/
./fetch_firmware.sh development   # rolling dev build
```

or build on the Mac (`firmware/openmv_build/build_ae3.sh`) and scp the
printed artifact set — both produce a `MANIFEST.txt` whose sha256 lines are
checked against the local bins before flashing, and whose `openmv_sha:` is
cross-checked against `sys.version` after boot.

## Flash

```bash
python3 flash_ae3.py --hp ~/fw/development/firmware_M55_HP.bin \
                     --he ~/fw/development/firmware_M55_HE.bin
```

Ladder: preflight (incl. MANIFEST sha256 check of the bins) →
`machine.bootloader()` via mpremote → wait for DFU `37c5:96e3` → `dfu-util`
download HP then HE → **read each partition back (`dfu-util -U`) and
sha256-compare against the flashed file** → boot (8 KB TOC read carrying
`-R`: post-verify USB reset → bootloader jumps to the app) → wait for CDC →
`sys.version` parses, label cross-checked → PASS/FAIL.

The readback compare is the verify — the version strings are labels, not
build fingerprints (see below), so PASS means "the bytes on flash are the
bytes in the file, and the board boots". On a readback mismatch the board is
deliberately left in DFU, un-booted; a power cycle recovers it.

`--dry-run` prints every command without touching anything. `--recover`
power-cycles via uhubctl and catches the 1.5 s boot-time DFU window instead
of asking the app to reboot (for a wedged/bricked app). The `BOOT` partition
is never written, so a bad app flash is always recoverable by power cycle.

## S7 spike demo (round trip, restores the fixture firmware)

```bash
./fetch_firmware.sh development && ./fetch_firmware.sh v5.0.0
python3 flash_ae3.py --hp ~/fw/development/firmware_M55_HP.bin --he ~/fw/development/firmware_M55_HE.bin
python3 flash_ae3.py --hp ~/fw/v5.0.0/firmware_M55_HP.bin --he ~/fw/v5.0.0/firmware_M55_HE.bin
```

Each run must end `PASS: flash verified byte-for-byte; board runs OpenMV
<id> ...`, with two different ids and a `readback verify OK` line per
partition.

## Flash-day results (first live run 2026-08-11 — round trip PASSED)

- ANSWERED: dfu-util `-R` resets the board fine but exits non-zero (251)
  because the device drops off the bus mid-reset — script tolerates it;
  CDC re-enumeration + sys.version match are the success signals.
- ANSWERED: verification reads **`sys.version`** (`os.uname().version`
  carries only the MicroPython id); release builds embed version tags,
  dev builds embed sha10s.
- ANSWERED: ROMFS reflash was NOT needed between `7d4dbf7ab2` and
  `v5.0.0` — still re-check on bigger version jumps.
- STILL OPEN: uhubctl port power on the Pi 5's hub topology, and
  `--recover` window timing (poll-detect → dfu-util attach inside the
  1.5 s boot window; may need a tight retry loop). Untested — the happy
  path never needed it.

## Stale-label find (S8 NPU bench, 2026-08-11) → byte-level verify

The S6 fixture board (running dev `7d4dbf7ab2`) self-reported
"OpenMV v5.0.0" during the S8 bench. Root cause, verified in openmv.git
@ master `7d4dbf7`: there are two version channels and **neither is a
build fingerprint**.

- `sys.version`'s "OpenMV \<id\>" is `git describe --tags --dirty
  --always --abbrev=10` output baked in at build time
  (openmv/micropython `py/makeversionhdr.py`). A tagless CI checkout
  degrades it to a bare sha10; rebuilds at the same rev (different
  submodules/SDK/ROMFS) repeat it exactly.
- `omv.version_string()` (and the IDE's firmware-version field) is the
  static `OMV_FIRMWARE_VERSION` defines in `protocol/omv_protocol.h` —
  stuck at "5.0.0" on every post-release dev build. That's the "v5.0.0"
  the board reported.

So label matching can false-pass across two different dev builds. Fix:
`flash_ae3.py` now verifies by **DFU readback** — the bootloader
implements `DFU_UPLOAD` (`boot/src/common/dfu.c:92`) and MRAM reads are
a plain memcpy (`boot/src/ports/alif/alif_flash.c:42`); compares cover
exactly `len(bin)` bytes because MRAM writes round the tail up to the
16 B sector with buffer residue. Boot is gated behind the verify.

Live-confirmed on the round-trip run (2026-08-11, this ladder):

- Upload-after-download in one DFU session works; readback of HP (3 MB)
  and HE (1.4 MB) partitions is a few seconds each.
- `dfu-util -Z` does NOT bound the transfer (0.11): uploads run to the
  partition-end short frame and `-Z` just triggers an "Unexpected number
  of bytes uploaded" warning — so the script omits it and the sha256
  compare caps at `len(bin)` instead.
- **`dfu-util -e` does NOT boot the board**: `-e` only detaches
  runtime-mode devices and is a silent no-op on a device already in DFU
  (board stayed parked in DFU — by design, recoverable). The working
  boot rung is a tiny read of the 8 KB `TOC` partition carrying `-R`:
  the USB reset unmounts the device, the bootloader's
  `while (tud_mounted())` loop exits, and it jumps to the app — the
  same mechanism the old `-R`-on-download used, now issued only after
  verification passes.
