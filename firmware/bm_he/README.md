# bm_he — S10 INTERIM 2: bm_core on the HE core (mock wire)

INTERIM ladder item 2 (USB-only; no ADIN hardware, no wiring, nothing
flashed). The real Bristlemouth stack — bm_os on FreeRTOS, lwIP 2.2.1,
BCMP — runs on the AE3's M55_HE core against a **mock NetworkDevice**
whose "wire" is the HP↔HE rpmsg pipe from bite 1 (Nick-approved
trait-level mock, 2026-08-12; rationale D25). Two bites:

- **2a (done, demo PASSED):** the stack boots and *talks* — init ladder
  green, heartbeats emitted on schedule, wire-format verified.
- **2b (this bite):** the stack *converses* — the HP runner plays a full
  **python peer node** (`s10_peer.py`, byte-exact BCMP): the peer's
  heartbeats form a neighbor table on the HE, and BCMP ping is answered
  in **both** directions. This is the INTERIM-2 demo proper.

## Verdicts (2b — A/B are the 2a regression)

| # | Claim | Evidence |
|---|---|---|
| A | BM stack up on HE | init ladder RUNNING, err 0; node id + both IPv6 addrs (fe80::/fd00:: + id in bytes 8–15, bm_lwip.c:289) via wire status |
| B | HE's BCMP heartbeats flow | ≥2 heartbeats in 25 s, 0x86DD/0xBC/type 0x01, checksum + src-addr node id verified, boot-µs monotonic |
| C | Neighbor table forms | peer injects heartbeats every 5 s → `BcmpNeighborTableRequest` (0x08) targeted at the HE returns a reply (0x09) listing the peer node id, port 1, **online** (neighbors.c:32–75) |
| D | Ping peer→HE answered | peer's `BcmpEchoRequest` (0x02) → HE's `BcmpEchoReply` (0x03) with node id + id/seq/payload echoed, checksum good (ping.c:96–113) |
| E | Ping HE→peer answered *and accepted* | `WCMD_PING` → HE emits an echo request on the wire (id = low-16 of its node id) → peer replies → ping.c's acceptance line ("… bytes from …") lands on the HE debug ring (ping.c:127–148) |

Both directions of the whole conversation land in `/flash/bm_he_hb.pcap`.

## How it works

- Same runtime-load scheme as bite 1: the HP runner
  (`s10_bcmp_bench.py`) loads `bm_he.elf` into SRAM9_B's upper half via
  `openamp.RemoteProc` — NOTHING flashed, recovery = `rp.stop()` or
  power cycle. rpmsg/MHU scaffold and the FreeRTOS kernel are shared
  with `../he_spike` by reference.
- **bm_core** is vendored byte-identical @ `d4ecc38` (the S9 rev) under
  `vendor/bm_core/` — the BCMP-and-below slice only (PROVENANCE.txt).
  Still **zero patches** in 2b: the one new firmware surface is a wire
  command (`WCMD_PING`) in our own `src/main.c` that calls
  `bcmp_send_ping_request()` the same way bm_sbc's app threads do.
- **lwIP 2.2.1** by reference from the D23/D24 openmv clone + pinned
  lwip-contrib FreeRTOS `sys_arch` (`vendor/lwip_contrib/`). IPv6-only,
  no TCP/sockets (`src/lwipopts.h`).
- **Mock device** (`src/bm_net_mock.c`): stack `send` → rpmsg
  `WCMD_FRAME_TX` to the HP; HP `WCMD_FRAME_RX` → l2's receive
  callback. Frames > 492 B don't fit one rpmsg buffer and are dropped +
  counted — BCMP control traffic fits; chunking is an S12 concern.
- **Python peer** (`s10_peer.py`): pure builders/parsers (no
  machine/openamp imports — the same file runs under CPython in the
  host tests). Wire format cited line-by-line from the vendored
  sources: 13-B BcmpHeader (messages.h:10), pseudo-header checksum
  stored big-endian on the wire (bm_lwip.c:114 → lwIP inet_chksum
  native-store semantics, confirmed live in 2a), egress nibble in src
  byte 2 covered by the checksum (l2.c:37, packet.c:452–454), MAC =
  00:00 + low-4 of node id (device.c:20). Peer node id
  `0x50454552AE30D00D` ("PEER…"), as synthetic as the HE's.
- 2b exercises the **RX path** (l2 → lwIP → bcmp) live for the first
  time — 2a only proved TX. Failures narrate on the debug ring
  (checksum mismatches included), which the runner dumps.

## Size checkpoint (256 KB SRAM9_B region)

2b build: text 71.3 K + data 0.1 K + bss 156.4 K = **231.5 K of 262 K
(~88 %)** — +0.4 K over 2a. Levers if needed: config-store trim, pbuf
pool, heap; load-to-ITCM stays unproven/unneeded.

## Build (Mac, D23 docker env)

```bash
firmware/bm_he/build_bm_he.sh          # → build/bm_he.{elf,bin} + MANIFEST
```

Host tests (no docker, no hardware — C: mock/stubs/ABI locks under
clang+ASan/UBSan; python: the peer's builders/parsers under CPython):

```bash
firmware/bm_he/host_test/run_tests.sh
```

## Run ladder (from nereus000; by-id ONLY — two OpenMV boards live there)

```bash
AE3=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00
# 1. ship artifacts (from the Mac): see build script's final scp line
# 2. copy the ELF + peer module to the board's filesystem (once per build):
mpremote connect $AE3 cp ~/bm_he/bm_he.elf :/flash/bm_he.elf
mpremote connect $AE3 cp ~/bm_he/s10_peer.py :/flash/s10_peer.py
# 3. run the bench (~40 s: 25 s capture + the conversation phases):
mpremote connect $AE3 run ~/bm_he/s10_bcmp_bench.py
# 4. pull the two-node conversation for Wireshark:
mpremote connect $AE3 cp :/flash/bm_he_hb.pcap .
```

Expected output ends with
`S10 INTERIM 2b verdict : PASS  (A:PASS B:PASS C:PASS D:PASS E:PASS)`.

Wireshark: open `bm_he_hb.pcap`; for decoded BCMP fields load Sofar's
dissector (`proto_bcmp.lua` in the bm_core repo root):
`wireshark -X lua_script:proto_bcmp.lua bm_he_hb.pcap`. Expect both
nodes' heartbeats interleaved plus the neighbor-table and echo
request/reply exchanges.

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
- Runner raises ImportError for `s10_peer`: step 2's second `cp` was
  skipped.
- C/D/E FAIL with "no … in 3 s": check the final debug-ring dump — a
  `Packet checksum mismatch, read X, calculated Y` line means an
  injected frame was rejected (wire-format regression); silence means
  the frame never reached bcmp (l2/lwIP RX path — ring narrates drops).
- `machine.mem32` AttributeError: seen once in bite 1, self-resolved on
  re-run (OpenMV lazy-loader quirk).
- Runner dies mid-run: `mpremote connect $AE3 soft-reset`, re-run.
