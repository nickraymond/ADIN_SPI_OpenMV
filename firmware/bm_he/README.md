# bm_he — S10 INTERIM 2a: bm_core boots on the HE core (mock wire)

INTERIM ladder item 2, first bite (USB-only; no ADIN hardware, no wiring,
nothing flashed). The real Bristlemouth stack — bm_os on FreeRTOS, lwIP
2.2.1, BCMP — runs on the AE3's M55_HE core against a **mock
NetworkDevice** whose "wire" is the HP↔HE rpmsg pipe from bite 1
(Nick-approved trait-level mock, 2026-08-12). Scope split:

- **2a (this bite):** the stack boots and *talks* — init ladder green,
  heartbeats emitted on schedule, wire-format verified, pcap captured.
- **2b (next):** the stack *converses* — HP python plays a peer node:
  neighbor discovery, BCMP ping both ways.

## Verdicts (2a)

| # | Claim | Evidence |
|---|---|---|
| A | BM stack up on HE | init ladder RUNNING, err 0; node id + both IPv6 addrs (fe80::/fd00:: + id in bytes 8–15, bm_lwip.c:289) via wire status |
| B | BCMP heartbeats flow | ≥2 heartbeats in 25 s, Ethernet II 0x86DD / IPv6 nh 0xBC / type 0x01, BCMP checksum + src-addr node id verified, boot-µs monotonic; frames land in a pcap |

## How it works

- Same runtime-load scheme as bite 1: the HP runner
  (`s10_bcmp_bench.py`) loads `bm_he.elf` into SRAM9_B's upper half via
  `openamp.RemoteProc` — NOTHING flashed, recovery = `rp.stop()` or
  power cycle. rpmsg/MHU scaffold and the FreeRTOS kernel are shared
  with `../he_spike` by reference (one copy; PROVENANCE grew timers.c +
  stream_buffer.c, same pinned rev).
- **bm_core** is vendored byte-identical @ `d4ecc38` (the S9 rev) under
  `vendor/bm_core/` — the BCMP-and-below slice only (see its
  PROVENANCE.txt). Init order mirrors Sofar's own custom-device
  integration (bm_sbc runtime.cpp): device → config → l2 → timer_cb →
  ip → bcmp → power/link-up.
- **lwIP 2.2.1** comes from the D23/D24 openmv clone
  (`lib/micropython/lib/lwip`) by reference; the FreeRTOS `sys_arch`
  glue is vendored from lwip-contrib (pinned, `vendor/lwip_contrib/`).
  IPv6-only, no TCP/sockets (`src/lwipopts.h`).
- **Mock device** (`src/bm_net_mock.c`) implements the 9-function
  NetworkDevice trait: stack `send` → rpmsg `WCMD_FRAME_TX` to the HP;
  HP `WCMD_FRAME_RX` → l2's receive callback; link driven by
  `WCMD_LINK` / init. Frames > 492 B don't fit one rpmsg buffer and are
  dropped + counted (`tx_oversize`) — BCMP control traffic fits;
  chunking is a 2b/S12 concern.
- **Integrator stubs** (`src/bm_stubs.c`): RAM-backed config partitions,
  RAM-scratch DFU hooks, tick-derived RTC, fixed node id
  `0x424D4845AE30BEEF` (synthetic on purpose; real derivation is a
  hardware-day question).
- Two peekable status pages (`machine.mem32`, work with rpmsg down):
  bite-1's at `0x600BFF00` (magic 'HESP') + bm_he's at `0x600BFE00`
  (magic 'BMHE', stack stage/err + a 4 KB debug ring that carries every
  `bm_debug`/lwIP diag line — the runner dumps it on failure).

## Size checkpoint (256 KB SRAM9_B region)

First green build: text 70.9 K + data 0.1 K + bss 156.4 K = **231 K of
262 K (~88 %)**. Biggest bss items: 64 K FreeRTOS heap (all task stacks
~30 K live there; `heap_free`/`heap_min` reported in the wire status),
16 K lwIP mem, 18 K pbuf pool, 2×13 K config partitions. Levers if 2b
needs room: config-store trim, pbuf pool, heap; the load-to-ITCM lever
stays unproven/unneeded.

## Build (Mac, D23 docker env)

```bash
firmware/bm_he/build_bm_he.sh          # → build/bm_he.{elf,bin} + MANIFEST
```

Host tests (no docker, no hardware — 72 checks, clang + ASan/UBSan):

```bash
firmware/bm_he/host_test/run_tests.sh
```

## Run ladder (from nereus000; by-id ONLY — two OpenMV boards live there)

```bash
AE3=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00
# 1. ship artifacts (from the Mac): see build script's final scp line
# 2. copy the ELF to the board's filesystem (once per build):
mpremote connect $AE3 cp ~/bm_he/bm_he.elf :/flash/bm_he.elf
# 3. run the bench (captures 25 s of wire traffic):
mpremote connect $AE3 run ~/bm_he/s10_bcmp_bench.py
# 4. pull the heartbeat capture for Wireshark:
mpremote connect $AE3 cp :/flash/bm_he_hb.pcap .
```

Expected output ends with `S10 INTERIM 2a verdict : PASS (A:PASS B:PASS)`.

Wireshark: open `bm_he_hb.pcap`; for decoded BCMP fields load Sofar's
dissector (`proto_bcmp.lua` in the bm_core repo root):
`wireshark -X lua_script:proto_bcmp.lua bm_he_hb.pcap`.

## Restore / fixture safety

- Nothing is flashed; stock/fixture firmware untouched. The runner stops
  the HE core at the end; `machine.reset()` or power cycle fully
  restores the board.
- S6 USB baseline regression: one `bench/usb_stream_bench.py` session
  (single session — D15 crash class) after the bench.

## Troubleshooting

- Runner raises "bm-wire never announced": it prints the bm status page
  and the HE debug ring — stage tells you which init rung died; the ring
  has bm_core's own error text.
- `machine.mem32` AttributeError: seen once in bite 1, self-resolved on
  re-run (OpenMV lazy-loader quirk).
- Runner dies mid-run: `mpremote connect $AE3 soft-reset`, re-run.
