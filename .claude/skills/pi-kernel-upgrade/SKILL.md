---
name: pi-kernel-upgrade
description: Rebuild the out-of-tree ADIN1110 driver after a kernel upgrade on the Pi. Use BEFORE running `sudo apt upgrade` / `full-upgrade` on nereus000 (or any Pi carrying the ADIN shield), and whenever eth1 has vanished after a reboot — the adin1110/adin1100 modules are version-locked to the running kernel and silently disappear when apt installs a new one.
---

# Pi kernel upgrade — ADIN1110 driver rebuild

OWNER: **Nick**. Background: DESIGN.md D12 — the ADIN1110 driver is an
**out-of-tree module build** (`pi/drivers/adin1110/`), installed under
`/lib/modules/<exact-kernel-version>/updates/`. A new kernel package gets a
new version directory that does NOT contain our modules, so after the next
reboot `eth1` is simply **gone** — no error, no dmesg line, just silence.
This is expected, not a regression. Do not start debugging the overlay,
wiring, or driver source.

## If you are about to run `sudo apt upgrade` on the Pi

1. Check whether a kernel is in the batch:
   ```bash
   apt list --upgradable 2>/dev/null | grep -E "linux-image|linux-headers|raspi-firmware" || echo "no kernel packages — driver unaffected"
   ```
2. No kernel packages → proceed normally, nothing else to do.
   (`sudo apt update` alone never affects the driver — it only refreshes
   package lists.)
3. Kernel packages present → after the upgrade **and reboot**, rebuild:
   ```bash
   cd ~/ADIN_SPI_OpenMV/pi && ./build_adin1110.sh && sudo reboot
   ```
4. After the second reboot, verify artifacts (never trust exit codes):
   ```bash
   cd ~/ADIN_SPI_OpenMV/pi && ./verify_adin1110.sh
   ```
   Expect 5/5 PASS: overlay applied, both modules loaded, dmesg probe line,
   `eth1` on driver `ADIN1110` (ethtool reports the name UPPERCASE).

## If eth1 is missing and you didn't just upgrade

Same first suspect — check for a version mismatch before anything else:

```bash
uname -r; ls /lib/modules/$(uname -r)/updates/ 2>/dev/null || echo "no out-of-tree modules for RUNNING kernel — rebuild needed"
```

If the modules directory for the running kernel lacks `adin1110.ko.xz`,
run the rebuild in step 3 above. Only if the modules ARE present for the
running kernel and eth1 is still missing is this a real bring-up problem —
see DESIGN.md §S1 detail for the known-good configuration and the
"PHY ID read: 0" early-silicon failure mode.

## Notes

- `build_adin1110.sh` is idempotent and fails loudly if the headers for the
  running kernel are missing (`sudo apt install linux-headers-rpi-2712`).
- Headers upgrade in lockstep with the kernel on Pi OS, but the build must
  run while the NEW kernel is the running one (script builds against
  `uname -r`) — hence upgrade → reboot → build → reboot.
- If manual re-runs get annoying, DKMS is the standard automation —
  capture it via /capture-task rather than bolting it on mid-session.
