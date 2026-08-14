# bm_bench — the three-node Bristlemouth bench (BENCHSPEC Stages 1–3)

Real `bm_core` nodes on three machines: the two Pis over the direct
eth0↔eth0 cable (`udp_port_device`, S15/BUILD-1+3) and the AE3 behind
Light's USB (rpmsg netdev + HP bridge + `--uart` gateway, S16/BUILD-2).
Dev access rides wlan0/tailnet throughout; the bench subnet is
`10.42.0.0/24`, no gateway, no DHCP.

```
Telemetry (nereus001) ──UDP/eth0── Light (nereus000) ──USB CDC── Camera (AE3 HE)
   head of chain                     pass-through                     leaf
```

| Node | Host | Bench IP | Node ID |
|---|---|---|---|
| Telemetry | nereus001 | 10.42.0.1 | `0xbe9c000000000001` |
| Light | nereus000 | 10.42.0.2 | `0xbe9c000000000002` |
| Camera | AE3 (HE core) via Light's USB | — | `0xbe9c000000000003` |

Node IDs fixed 2026-08-14 (Nick); never reuse. **The chain exists only
in the peer tables: Camera and Telemetry must NEVER appear in each
other's neighbor lists** — traffic between them transits Light (the
single most important invariant, BENCHSPEC §2).

## Code + pins (REV-23 discipline — track carefully)

Everything builds from **forks with pinned revs**; upstream is never
tracked live:

- `bm_sbc` fork branch **`feature/udp-transport`** = upstream `17ea904`
  + 3 commits (transport factory; udp_port_device + stream_bench + tests;
  S16: `tx_drops` in RX_STAT — the pass-through transit ledger).
- `bm_core` fork branch **`bench/d4ecc38-obs`** = upstream `d4ecc38`
  (the rev vendored in `firmware/bm_he` AND pinned by bm_sbc 17ea904 —
  verified identical, no drift) **+ exactly one observability commit**:
  TX/RX L2-queue drop counters + accessors, zero behavior change.
  **All bench nodes remain protocol-identical to d4ecc38**; the AE3's
  vendored copy stays byte-identical. If the pin ever moves, it moves on
  every node in the same change (BENCHSPEC §2 Version pinning).

Exact shas live in `deploy.sh` (`BM_SBC_PIN` / `BM_CORE_PIN`) — the deploy
fails loudly on drift. Standing checkouts: `~/bm_sbc_s15` on both Pis
(`~/bm_sbc` on nereus000 stays a pristine upstream clone).

## Deploy / verify

On each Pi:

```bash
~/ADIN_SPI_OpenMV/pi/bm_bench/deploy.sh
```

Prints `PASS` + binary path + both verified revs, or fails.

> **S15 regression note:** `light.toml` now carries `uart-device` (the
> S16 Camera leg), so the Light node opens the AE3's CDC port at start.
> To re-run the two-Pi demos without the AE3 bridge running, comment out
> the `uart-device` line first.

## S15 demo 1 — neighbors + BCMP ping across the cable

Terminal A (nereus000 / Light):

```bash
~/bm_sbc_s15/build/all/bm_sbc_multinode --init ~/bm_bench/light.toml
```

Terminal B (nereus001 / Telemetry):

```bash
~/bm_sbc_s15/build/all/bm_sbc_multinode --init ~/bm_bench/telemetry.toml
```

**Start both within ~10 s of each other** — each node sends exactly ONE
multicast ping, 3 s after its own start, so a node started before its
peer never sees a reply. If one end is missing its 🏓 line, leave the
other running and just restart the quiet one.

Expect within ~15 s, on BOTH ends (Ctrl-C to stop):

- `NEIGHBOR_UP node=be9c000000000001` on Light /
  `NEIGHBOR_UP node=be9c000000000002` on Telemetry
- a `🏓 … bcmp_seq=0 …` reply line on each end (logged at debug level —
  the TOMLs set `log-level = "debug"` for exactly this)
- `PUBSUB_RX` from the remote node id
- pcap artifacts at `/tmp/bm_bench_light.pcap` / `…telemetry.pcap`

## S15 demo 2 — rate limiter measured (receiver-side ledger, D21)

Receiver first (nereus001):

```bash
S15_ROLE=rx ~/bm_sbc_s15/build/all/bm_sbc_stream_bench --init ~/bm_bench/telemetry.toml
```

Sender (nereus000), 15 Mbit/s offered through the 10 Mbit/s shaper:

```bash
S15_ROLE=tx S15_MBPS=15 S15_SECONDS=20 ~/bm_sbc_s15/build/all/bm_sbc_stream_bench --init ~/bm_bench/light.toml
```

Expected:

- RX_STAT steady ≈ **9.3 Mbps payload** (= 10.0 Mbps on the wire; the gap
  is BM/IPv6/UDP framing overhead on 1024 B payloads)
- TX_DONE `offered_mbps=15.00 achieved_mbps≈9.3 wall_s≈32` — offered rate
  above the limit shows up as **wall-clock stretch (backpressure), not
  drops**: bm_pub blocks when the L2 TX queue is full (10 ms enqueue
  timeout ≫ per-frame service time), so a lone publisher is throttled
- final RX `total_msgs` **equals** TX `pub_ok` — zero loss at the BM layer
- all drop counters 0 and printed each second (`rx_drops=` in RX_STAT,
  `enomem`/`l2_drops` in TX_STAT) — the observability plumbing the S16
  forwarding ledger will use

Control run (under the limit — no shaping, no stretch):

```bash
S15_ROLE=tx S15_MBPS=8 S15_SECONDS=20 ~/bm_sbc_s15/build/all/bm_sbc_stream_bench --init ~/bm_bench/light.toml
```

Expect RX steady ≈ 8.0 Mbps payload (≈8.6 Mbps on the wire, under the
limit → unshaped), `achieved_mbps=8.00 wall_s=20.0`, zero drops.
(Rehearsed 2026-08-14: 19,532/19,532 delivered.)

## S15 demo 3 — dev access intact

During any run, on either Pi:

```bash
ip route | head -1
```

Default route stays `via 192.168.86.1 dev wlan0`; the SSH session you are
typing in is itself the liveness proof.

---

# S16 — the AE3 joins the chain (BENCHSPEC Stages 2–3)

The Camera node is bm_core compiled for the HE core (`firmware/bm_he`,
runtime-loaded, nothing flashed), attached through the HP bridge
(`firmware/bm_bridge/bm_bridge.py`) speaking `uart_l2` over the VCP.
The Light Pi runs the stock `--uart` gateway on the same binary —
`light.toml`'s `uart-device` key is the entire Pi-side change.

**AE3 ops rules are absolute** (`firmware/bm_bridge/README.md`): by-id
mpremote only; cold boot does NOT run main.py — service entry is a warm
reset; mpremote attach kills the bridge; recovery pair =
`sudo uhubctl -l 3 -p 1 -a cycle -d 3` + warm reset.

## S16 deploy (bridge + HE image onto the AE3)

Build the HE image on the Mac (`firmware/bm_he/build_bm_he.sh`), then
from the repo checkout on the Mac:

```bash
scp firmware/bm_he/build/bm_he.elf firmware/bm_bridge/{bm_bridge.py,main_bridge.py,uart_codec.py} pi@nereus000:/tmp/
```

Stage on the board (nereus000; board at REPL — cold-cycle first if a
previous service holds the VCP):

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  mpremote connect $P cp /tmp/bm_he.elf :/flash/bm_he.elf + cp /tmp/bm_bridge.py :/flash/bm_bridge.py + cp /tmp/uart_codec.py :/flash/uart_codec.py + cp /tmp/main_bridge.py :/flash/main.py'
```

Arm the demo triggers (stream + Camera-sourced ping; delays count from
link-up):

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  printf "{\"stream\": {\"mbps\": 2.0, \"payload\": 1400, \"secs\": 600, \"delay\": 15}, \"ping\": {\"target\": \"0xbe9c000000000001\", \"delay\": 5}}" > /tmp/bridge_cfg.json && \
  mpremote connect $P cp /tmp/bridge_cfg.json :/flash/bridge_cfg.json'
```

## S16 start order (every session)

1. **Bridge:** `mpremote connect $P reset` → wait for the by-id symlink
   to settle (absent→present→3 s). The bridge loads the HE, then waits —
   link stays down until the Pi speaks.
2. **Light** (nereus000): start its bm_sbc node (commands below). Its
   gateway heartbeats immediately → bridge sees bytes → link-up → the
   trigger clocks start.
3. **Telemetry** (nereus001): start within ~10 s of Light (the one-shot
   ping window, S15 demo-1 gotcha).

To stop: Ctrl-C the Pi nodes; stop the bridge by attaching mpremote
(the injected KeyboardInterrupt IS the stop signal; the bridge persists
its ledger, stops the HE, and drops to REPL).

## S16 demo 1 — chain topology (never a star)

Arm `bridge_cfg.json` with ping only (or leave stream armed — harmless
noise), then start in order; on the Pis:

```bash
~/bm_sbc_s15/build/all/bm_sbc_multinode --init ~/bm_bench/light.toml      # nereus000
```

```bash
~/bm_sbc_s15/build/all/bm_sbc_multinode --init ~/bm_bench/telemetry.toml  # nereus001
```

Expect within ~20 s:

- **Light**: `NEIGHBOR_UP node=be9c000000000001` (port 1, UDP) AND
  `NEIGHBOR_UP node=be9c000000000003` (port 15, the gateway UART leg) —
  the two-port pass-through.
- **Telemetry**: `NEIGHBOR_UP node=be9c000000000002` and **nothing
  else** — Camera must never appear (heartbeats are link-local and
  never forwarded; the chain invariant holds physically).
- **Telemetry's 🏓 lines**: its t+3 s one-shot ping goes to `ff03::1`
  (which L2 *does* forward), so replies arrive from BOTH
  `be9c…02` (1 hop) and `be9c…03` (2 hops — request forwarded through
  Light, reply returned via BCMP re-transmit). Label honestly: the
  request path exercises L2 forwarding, the reply path BCMP re-tx
  (REV-6/V10).
- The Camera-sourced ping (bridge_cfg `ping`, target `…01`) lands as a
  🏓 acceptance line on the HE debug ring — read it after stopping the
  bridge in `/flash/bridge_trace.txt` (ring dump at exit).

## S16 demo 2 — forwarded pub/sub (Camera → Telemetry through Light)

Pub/sub rides `ff03::1` multicast — **this is the L2-forwarding traffic
class** (REV-6). Subscribe at BOTH Pi nodes (rx is the default role):

```bash
S15_ROLE=rx ~/bm_sbc_s15/build/all/bm_sbc_stream_bench --init ~/bm_bench/light.toml      # nereus000
```

```bash
S15_ROLE=rx ~/bm_sbc_s15/build/all/bm_sbc_stream_bench --init ~/bm_bench/telemetry.toml  # nereus001
```

With the stream armed (2 Mbps / 1400 B), after link-up + delay the
Camera publishes on `s15/stream`. Expect:

- **Telemetry RX_STAT ≈ 2.0 Mbps** — every payload crossed BOTH hops
  (CDC then UDP), forwarded by Light's L2. This is the Stage-2
  acceptance traffic.
- **Light RX_STAT ≈ 2.0 Mbps** — the same flood, counted mid-chain.
- `tx_drops=` on Light = the forward-path transit ledger (expect 0 at
  2 Mbps; the S15 finding says this is where silent drops would live).

## S16 demo 3 — sustained rate + the drop ledger at every hop (Stage 3)

Same setup as demo 2 with the full 600 s stream
(`"secs": 600` — already armed above). Let it run to completion, then
collect the ledger:

| Hop | Where | Counter |
|---|---|---|
| HE stack → rpmsg | `/flash/bridge_trace.txt` ring dump | `stream: done sent=N errs=…` + WREP status (`tx_dropped`, `tx_oversize`) |
| rpmsg → bridge | same trace, `stats` lines | `qdrops`, `frag_errors` |
| bridge → Light | Light's stdout | `uart_l2: decode error` count (**zero CRC failures** = none logged) |
| Light forward path | Light RX_STAT | `tx_drops=` (transit ledger) + `rx_drops=` |
| Light → Telemetry | Telemetry RX_STAT | `rx_drops=` + steady `mbps=` |

Pass = Telemetry RX_STAT ≈ 2.0 Mbps windows for ≥10 min, zero decode
errors on Light, and a consistent ledger (Camera `stream_sent` ≈
Telemetry `total_msgs` + every counted drop). Margin context: the S14
relay measured 5.4 Mbps sustained through this exact path shape — 2.7×
the gate.

Afterwards stop the bridge (mpremote attach) and read
`/flash/bridge_trace.txt` + `/flash/bridge_crash.txt` for the final
ledger; V5 (Sofar's serial path first-ever hardware run) says surprises
land there.

## Where drops actually happen (S15 finding, for S16)

REV-13's silent `BmENOMEM` drop **cannot fire from a single publisher on a
Pi**: the POSIX queue's 10 ms enqueue timeout converts overload into
blocking backpressure (measured: 200 Mbps offered on loopback →
244,141/244,141 delivered). The silent-drop sites are:

1. **RX path** (`bm_l2_rx`): zero-timeout enqueue — counted by the new
   `bm_l2_get_rx_queue_drops()`.
2. **Forward path** (S16 Light node): the L2 thread enqueues into the
   queue it drains — guaranteed timeout under forwarding load — counted
   by `bm_l2_get_tx_queue_drops()`. This is the S16 transit-loss ledger.
3. **Device level**: oversize frames (>1514, REV-14) — dropped with a
   logged length + ingress port (proven by `udp_multinode_test.sh` u3).
