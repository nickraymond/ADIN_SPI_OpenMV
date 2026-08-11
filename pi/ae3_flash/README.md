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
printed artifact set — both produce a `MANIFEST.txt` with `openmv_sha:` that
`flash_ae3.py` verifies against automatically.

## Flash

```bash
python3 flash_ae3.py --hp ~/fw/development/firmware_M55_HP.bin \
                     --he ~/fw/development/firmware_M55_HE.bin
```

Ladder: preflight → `machine.bootloader()` via mpremote → wait for DFU
`37c5:96e3` → `dfu-util` HP then HE (reset on last) → wait for CDC → verify
`os.uname().version` contains the manifest's OpenMV sha10 → PASS/FAIL.

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

Each run must end `PASS: board is running OpenMV <sha> (matches expected)`,
with two different hashes.

## Flash-day checks (unverified until first live run)

- dfu-util `-R` reset behavior against this bootloader (forced-DFU exit path);
  fallback: power cycle after download.
- Whether ROMFS partitions need reflashing when jumping firmware versions
  (release zips carry `romfs0.img`; stock scripts may live there).
- uhubctl port power control on the Pi 5's specific hub topology.
- `--recover` timing: poll-detect → dfu-util attach must land inside the
  1.5 s boot window; may need a tight retry loop around the whole rung.
