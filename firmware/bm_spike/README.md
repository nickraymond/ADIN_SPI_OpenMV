# bm_spike — S9: bm_core's OA driver on our silicon

Runs **bm_core's ADIN2111 OA driver, unmodified,** against our ADIN1110
(AOS hat #2, re-strapped to default = OPEN Alliance with protection) from a
custom OpenMV AE3 firmware.

- **Bite 1 (PASSED 2026-08-11):** verify spike over a MicroPython-backed
  HAL — proved OA transport + pinned the 2-item 1110/2111 delta.
- **Bite 2:** same driver, same verdicts, but `adi_hal.h` implemented
  **Alif-native** (`--hal alif`): bare-metal SPI0 FIFO-burst engine +
  real INT_N IRQ delivery. See "Bite 2" sections below.

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

- `src/bm_spike_verify.c/h` — verdicts + bench core (portable; host + target)
- `src/bm_spike_hal_mp.c` — `adi_hal.h` impl over MicroPython SPI/Pin
  (blocking; SPI callback invoked inline; IRQ hooks stubbed — bite-1
  baseline, still the `--hal mp` default)
- `src/bm_spike_hal_alif.c` — bite 2: `adi_hal.h` against the Alif silicon
  (SPI0 FIFO-burst full duplex, ≤16 frames in flight; NVIC-gated INT_N
  IRQ; real critical sections; stats counters). Hardware facts cited in
  the file header, all verified in openmv.git @ the pinned rev.
- `src/bm_spike_mod.c` — `bm_spike` usermod; Python API follows the staged
  HAL (`bm_spike.HAL` == `"mp"` / `"alif"`)
- `host_test/` — clang build of the UNMODIFIED driver + a mock ADIN
  speaking the OA control wire format (`run_host_tests.sh`, 16 checks)
- `build_spike.sh` — stages sources into `<openmv>/modules/`, runs
  `firmware/openmv_build/build_ae3.sh`, un-stages on exit; `--hal mp|alif`
  picks the HAL (exactly one is staged — they define the same symbols)
- `s9_oa_spike.py` — bite-1 runner (needs a `--hal mp` build)
- `s9_hal_native.py` — bite-2 runner (needs `--hal alif`): verify through
  the native engine, 5/10/20 MHz bench ladder, INT_N IRQ proof

### Bite-2 API (`--hal alif` builds)

```python
bm_spike.setup(hz)          # native SPI0 up; returns controller's actual Hz
bm_spike.verify()           # same (r1, phyid, r2) verdicts as bite 1
bm_spike.bench(n)           # (elapsed_us, fails, phyid) for n PHYID reads
bm_spike.irq_trampoline     # pass to Pin("P5").irq(handler=..., hard=True)
bm_spike.stats()            # (xfers, bytes, stalls, irqs) — trust artifacts
bm_spike.stats_clear()
bm_spike.actual_hz()
```

Ownership rule: in an alif-HAL build the native engine owns SPI0 + CS
(P0–P3). Never construct `machine.SPI(0)` alongside it. P4 (reset) and
P5 (IRQ registration) remain `machine.Pin` scaffolding in the runner —
IRQ *delivery* rides `machine_pin.c`'s existing `GPIO0_IRQ4Handler`
dispatch into a hard-mode C trampoline (the vector table is const in
MRAM and that symbol is taken; riding the dispatch avoids any fork).

## Run ladder — AS RUN, SPIKE PASSED 2026-08-11

1. Host tests (no hardware): `host_test/run_host_tests.sh` → `RESULT: PASS`
2. Mac (once): Docker Desktop first launch, then
   `firmware/openmv_build/setup_mac.sh` (SDK may already be pre-staged)
3. Mac build: `build_spike.sh --clean --no-prot --rev 7d4dbf7ab2`
   (**--no-prot is REQUIRED on our 1110** — PROTE won't set, see DESIGN
   §S9; --clean whenever the staged set changes: stale-object trap)
4. Hardware gate (Nick): re-strap hat #2 to OA — remove both CFG0/CFG1
   solder bridges (D13, reversible), bench power off. Verify the pads
   are FULLY cleared (first attempt left CFG0 partially bridged →
   OA-without-protection symptoms).
5. scp the HP bin → flash **HP only** via `pi/ae3_flash/flash_ae3.py
   --hp ... --device /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_...`
   (HE image doesn't link in our env — HP-only at the installed HE's rev
   avoids skew; NEVER bare mpremote on nereus000, two OpenMV boards)
6. `mpremote connect <by-id> run firmware/bm_spike/s9_oa_spike.py` →
   **observed: verdict 1 SUCCESS PHYID=0x0283BC91 (OA proven), verdict 2
   COMM_TIMEOUT (2111 identity gate — expected)**
7. Restore path: reflash stock `7d4dbf7ab2` HP (S7 ladder); re-bridge
   straps whenever the generic-SPI/S6 baseline is needed again.
   Bench debug helpers live in `~/ae3_flash/` on nereus000:
   `s9_raw_probe.py`, `s9_matrix.py`, `s9_regs.py`, `s9_wrtest.py`.

## Run ladder — bite 2 (Alif-native HAL)

1. Host tests (no hardware): `host_test/run_host_tests.sh` → 16 checks PASS
2. Mac build: `./build_spike.sh --clean --no-prot --hal alif`
   (--no-prot still REQUIRED — PROTE is dead on our 1110; --clean because
   the staged set changed vs bite 1)
3. Fixture check (unchanged from bite 1's end state): hat #2 strapped OA,
   S4 harness on P0–P5, board reachable at the by-id path
4. scp the HP bin → flash HP via the S7 ladder (post-D24 the HE image
   builds too; flashing HP-only at the pinned rev remains the
   least-variables option — the installed HE is already that rev)
5. `mpremote connect <by-id> run firmware/bm_spike/s9_hal_native.py` →
   expect: VERDICT A PHYID=0x0283BC91 via native HAL; bench ladder
   5/10/20 MHz with 0 fails / 0 stalls; VERDICT B IRQ callback ≥ 1
   (see the runner's INCONCLUSIVE note — whether an un-inited 1110
   asserts INT_N post-reset is measured here, not assumed)
6. Regression rung: rebuild `--hal mp` (`--clean`), reflash, run
   `s9_oa_spike.py` → bite-1 verdicts unchanged; optionally
   `bm_spike.bench(spi, cs, 2000)` for the HAL-to-HAL speed comparison
7. Restore path: reflash stock dev HP (S7 ladder) when done

## Known limits (deliberate)

- Full `adin2111_Init` is not host-portable (ADI `*_DEVICE_SIZE` constants
  are ILP32-tuned; init returns INVALID_PARAM on LP64) — verdict 2 is
  target-only. Host covers the OA framing + identity-gate demonstration +
  bench plumbing.
- Nothing in the alif HAL is host-portable (volatile silicon registers
  throughout) — its evidence is target-side, by design.
- DMA (`SPI_DMACR` + DMA0 engine) deliberately NOT wired — deferred to
  S10 with Nick's approval 2026-08-11; `useDma` is accepted and ignored.
- IRQ trigger is falling-edge (machine_pin.c exposes edge only); the
  ADIN's INT_N is level-low open-drain. Fine for counting IRQs in this
  bite; bite 3's data path should revisit level-vs-edge (a native
  `gpio_interrupt_set_level_trigger` call can convert it without forking).
- mp-HAL only: an exception inside `spi.write_readinto` would skip the
  CS-high in `HAL_SpiReadWrite` — reset the board before rerunning after
  a crash. (The alif HAL always restores CS; its failure mode is a loud
  ADI_HAL_ERROR + stall counter.)
