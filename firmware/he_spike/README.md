# he_spike — S10 bite 1: FreeRTOS on M55_HE + OpenAMP pipe

INTERIM ladder item 1 (USB-only; no ADIN hardware, no wiring, nothing
flashed). Proves the BM-native arc's platform assumption: the AE3's
second core (M55_HE) can run FreeRTOS — the OS bm_core needs — and the
HP↔HE OpenAMP pipe carries video-rate data.

## Verdicts

| # | Claim | Evidence |
|---|---|---|
| A | HE runs our FreeRTOS app | rpmsg NS announce + PING answered; status page stage=RUNNING |
| B | Pipe ≥ 5 Mbps (TRACKER gate) | HP→HE sink window (HE-counted, seq+CRC), HE→HP pump |
| C | HE can own SPI0 + its IRQ | pinmux claim + DW internal-loopback + SPI0 IRQ on the HE NVIC |

Pre-measured context: the stock python↔python pipe already does
**219 Mbps** (HE→HP, 2026-08-12 rung-0 probe, board as-fixtured), so B
verifies plumbing + integrity through OUR stack rather than discovering
the ceiling.

## How it works

- The stock HP firmware stays untouched: its `openamp` module is the
  host. `s10_pipe_bench.py` (run via mpremote) loads `he_spike.elf` onto
  the HE core with `openamp.RemoteProc` — runtime load into SRAM9_B's
  upper half (0x60080000, 256 KB; provably untouched by the flashed HP
  image, whose `.gpu_memory` ends exactly there — D24 build maps).
  Recovery from any HE misbehavior = `rp.stop()` or power cycle.
- `src/rpmsg_remote.c` is a ~250-line device-role rpmsg implementation
  against the host's fixed SHM layout (rsc @ 0x60000000, vrings @
  +0x1400/+0x400, 2×64×512 B) — reimplemented instead of porting
  open-amp+libmetal because the layout is pinned by the host build and
  the glue would outweigh the protocol. Wire formats cited per-struct.
- Doorbells ride the same MHU words micropython's port uses
  (`mhu.c`; HP→HE RX 0x40080000/IRQ 41, HE→HP TX 0x40090000).
- The FreeRTOS kernel is vendored at `vendor/freertos/`
  (V11.3.0 @ 9b777ae5c5, GCC/ARM_CM55_NTZ/non_secure port — see
  PROVENANCE.txt).
- A status page at 0x600BFF00 (magic 'HESP') is peekable from HP via
  `machine.mem32` even when rpmsg is down — first stop for any debugging.

## Build (Mac, D23 docker env)

```bash
firmware/he_spike/build_he_spike.sh          # → build/he_spike.{elf,bin} + MANIFEST
```

Host tests (no docker, no hardware):

```bash
firmware/he_spike/host_test/run_tests.sh     # clang + ASan/UBSan
```

## Run ladder (from nereus000; by-id ONLY — two OpenMV boards live there)

```bash
AE3=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00
# 1. ship artifacts (from the Mac):  see build script's final scp line
# 2. copy the ELF to the board's filesystem (once per build):
mpremote connect $AE3 cp ~/he_spike/he_spike.elf :/flash/he_spike.elf
# 3. run the bench:
mpremote connect $AE3 run ~/he_spike/s10_pipe_bench.py
```

Expected output ends with `S10 bite 1 verdict : PASS (A:... B:... C:...)`.

## Restore / fixture safety

- Nothing is flashed. The HE core is stopped by the runner; a
  `machine.reset()` or power cycle returns the board to its stock state
  (stock HE image in MRAM is untouched; it only runs when something
  starts it).
- S6 USB baseline regression: run one `bench/usb_stream_bench.py`
  session (single session — D15 crash class) after the bench.
- If the runner dies mid-run: `mpremote connect $AE3 soft-reset`, then
  peek `machine.mem32[0x600BFF00]` — 0x48455350 means the HE app is
  still resident; a fresh runner invocation re-loads it anyway.

## Known limits / levers (documented, not gates)

- HE runs uncached out of SRAM9_B; caches are a perf lever if a future
  bite needs it (MPU already carves the SHM non-cacheable).
- `CTRLR0` bit 13 = DW SRL loopback is inferred from the DW SSI layout
  (Alif's spi.h names bits 12 and 14, leaves 13 unnamed); the loopback
  test is self-verifying either way.
- EWIC (deep-sleep wake) is out of spike scope — no sleep states here.
