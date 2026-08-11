# bm_spike — S9 bite 1: 1110-vs-2111 verify spike

Runs **bm_core's ADIN2111 OA driver, unmodified,** against our ADIN1110
(AOS hat #2, re-strapped to default = OPEN Alliance with protection) from a
custom OpenMV AE3 firmware. Goal: a decision input, not a driver.

## Provenance

`vendor/adin2111/` = `bristlemouth/bm_core` @ `d4ecc38` (via the
`~/Documents/GitHub/bm_sbc` clone's `lib/bm_core` submodule, bm_sbc @
`6bc9524`), directory `drivers/adin2111/`, **byte-for-byte unmodified**.
`bm_adin2111.c/h` are vendored for reference only — never compiled here
(they need bm_os and define their own `HAL_RegisterCallback`).

## Design (facts from source, cited)

- Driver config is OA **with protection** (`adi_config.h`) = the hats'
  default strap state (D13 bridges removed). No configuration work.
- **The 2111 identity gate fires inside MAC-layer init**: `MAC_Init` →
  `MAC_Reset(MAC_PHY)` → `waitDeviceReady` polls `MAC PHYID ==
  RSTVAL_MAC_PHYID (0x0283BCA1)` 25,000× (`adi_mac.c:568,1128`). Our 1110
  answers `0x0283BC91` → `COMM_TIMEOUT` is the *expected* init result on
  working hardware (~seconds of polling at 5 MHz — be patient).
- The device handle is valid before that reset, and `MAC_ReadRegister`
  only needs state ≠ UNINITIALIZED — so the spike tolerates the init
  result and then reads PHYID through the driver's own OA framing.
- Driver quirk (pinned by host test [4]): on control reads the OA state
  machine drops `oaCtrlCmdReadData`'s PROTECTION_ERROR — corruption shows
  up as SUCCESS + unwritten (0) data. Judge the PHYID **value**, never the
  result code alone.

### Verdict matrix (`bm_spike.verify(spi, cs)` → `(r1, phyid, r2)`)

| Observation | Meaning |
|---|---|
| `phyid == 0x0283BC91` | **OA transport + driver framing PROVEN on 1110.** Only the 2111 identity constants block full init → decision: bm_core route viable; identity delta feeds S13's 2111 notes. |
| `r1 == 0` and `phyid == 0` | Wire/strap problem (or protection garbage — see quirk). Fall back to `s4_bus_probe.py`. |
| `r2 == 0` (full init passes) | Would mean 2111 silicon — not expected on our bench. |

## Files

- `src/bm_spike_verify.c/h` — the two verdicts (portable core; host + target)
- `src/bm_spike_hal_mp.c` — `adi_hal.h` impl over MicroPython SPI/Pin
  (blocking; SPI callback invoked inline; IRQ hooks stubbed — polled spike)
- `src/bm_spike_mod.c` — `bm_spike` usermod (auto-registered via openmv's
  `modules/` wildcard; no openmv fork or patch)
- `host_test/` — clang build of the UNMODIFIED driver + a mock ADIN
  speaking the OA control wire format (`run_host_tests.sh`, 10 checks)
- `build_spike.sh` — stages sources into `<openmv>/modules/`, runs
  `firmware/openmv_build/build_ae3.sh`, un-stages on exit
- `s9_oa_spike.py` — REPL runner (reset pulse + SPI setup + verdict print)

## Run ladder

1. Host tests (no hardware): `host_test/run_host_tests.sh` → `RESULT: PASS`
2. Mac (once): Docker Desktop first launch, then
   `firmware/openmv_build/setup_mac.sh` (SDK may already be pre-staged)
3. Mac build: `firmware/bm_spike/build_spike.sh` → MANIFEST + artifacts
4. **Hardware gate (Nick): re-strap hat #2 to OA — remove both CFG0/CFG1
   solder bridges (D13, reversible). Bench power off during rework.**
5. scp artifacts to nereus000 → flash via `pi/ae3_flash/flash_ae3.py`
6. `mpremote run firmware/bm_spike/s9_oa_spike.py` → verdicts
7. Restore path: reflash S6 firmware (S7 round-trip proven); re-bridge
   straps whenever the generic-SPI baseline is needed again.

## Known limits (deliberate)

- Full `adin2111_Init` is not host-portable (ADI `*_DEVICE_SIZE` constants
  are ILP32-tuned; init returns INVALID_PARAM on LP64) — verdict 2 is
  target-only. Host covers the OA framing + identity-gate demonstration.
- MicroPython-SPI-backed HAL is spike-only; the Alif-native ADI-HAL
  (IRQ + DMA) is S9 bite 2.
- An exception inside `spi.write_readinto` would skip the CS-high in
  `HAL_SpiReadWrite` (no MicroPython try/finally at C level) — reset the
  board before rerunning after a crash.
