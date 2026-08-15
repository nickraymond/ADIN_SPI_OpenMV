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

To stop: Ctrl-C the Pi nodes; the bridge then **stops itself ~30 s
after the VCP goes quiet** (ctrl-C cannot reach it — kbd_intr is
disabled because COBS bytes contain 0x03; found live). It persists its
ledger + HE ring dump, stops the HE, and drops to REPL — wait ~30 s
after stopping Light before attaching mpremote. Each demo gets a fresh
bridge (warm reset re-arms the cfg one-shots).

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

Afterwards stop the Pi nodes, wait ~30 s for the bridge's quiet-exit,
then read `/flash/bridge_trace.txt` + `/flash/bridge_crash.txt` for the
final ledger (the trace ends with the HE debug-ring dump — the stream
publisher's `stream: done` line and any 🏓 acceptance live there).

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

---

## S17 deploy (BUILD-4 apps — the pin move + AE3 restage)

Pin move (D27): bm_sbc `feature/udp-transport` +2 commits (c094f66
bench_apps C1, c1d0df9 CLI/uplink C2); bm_core pin unchanged. On BOTH
Pis after the fork branch is pushed:

```bash
cd ~/bm_sbc_s15 && git pull && git submodule update --init && cd ~/ADIN_SPI_OpenMV && git fetch && git checkout sprint/17-build4-apps && git pull && pi/bm_bench/deploy.sh
```

AE3 staging (VCP GATE — Nick's go; board at REPL, cold-cycle first if a
service holds the VCP). Build `firmware/bm_he/build_bm_he.sh` on the
Mac (S17 ELF sha in its MANIFEST), then:

```bash
scp firmware/bm_he/build/bm_he.elf firmware/bm_bridge/{bm_bridge.py,main_bridge.py,uart_codec.py} pi@nereus000:/tmp/
```

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  mpremote connect $P cp /tmp/bm_he.elf :/flash/bm_he.elf + cp /tmp/bm_bridge.py :/flash/bm_bridge.py + cp /tmp/uart_codec.py :/flash/uart_codec.py + cp /tmp/main_bridge.py :/flash/main.py'
```

For S17 the cfg one-shots stay EMPTY (triggers come from the operator
CLI over BM — that's the point):

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  printf "{}" > /tmp/bridge_cfg.json && mpremote connect $P cp /tmp/bridge_cfg.json :/flash/bridge_cfg.json'
```

One-time per boot on nereus000 (LED HAL permission) — restore
`mmc0` when done:

```bash
ssh pi@nereus000 'sudo chmod a+w /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness'
```

One-time on nereus001: the frozen S3 receiver must own ingest :8081
alone — stop the S6 shim (its eth1 source is gone; it crash-loops):

```bash
ssh pi@nereus001 'sudo systemctl stop t1l-chunk-shim && systemctl is-active t1l-stream-server'
```

## S17 start order

1. **Bridge:** `mpremote connect $P reset` → by-id absent→present→settle.
2. **Light** (nereus000):

```bash
S17_ROLE=light ~/bm_sbc_s15/build/all/bm_sbc_bench_apps --init ~/bm_bench/light.toml
```

3. **Telemetry** (nereus001, within ~10 s):

```bash
S17_ROLE=telemetry BM_SBC_GATEWAY_IPC=/tmp/s17_ipc.sock ~/bm_sbc_s15/build/all/bm_sbc_bench_apps --init ~/bm_bench/telemetry.toml
```

Stop = Ctrl-C the Pi nodes; bridge quiet-exits ~30 s later (S16 stop
model). One bridge lifetime per demo.

## S17 demo 1 — services round trip (light + power + time)

At the Telemetry CLI (type into the running bench_apps; `help` lists
commands):

- `time-sync` → `TIME_SYNC …` (camera inherits this node's clock — O1).
- `light 100` → **the green ACT LED on nereus000 lights**;
  `LIGHT_REPLY … ok=1 level=100`; state artifact:
  `cat /tmp/s17_light_state` on nereus000 changed.
- `strobe 200 200 10` → LED blinks 10×; `LIGHT_REPLY … strobing=1`.
- `power` → `POWER_REPLY total_on=…s remaining_on=…s upcoming_off=300s`
  — a 2-hop service round trip to the AE3's simulated power HAL
  (values are synthetic round numbers, by design).

## S17 demo 2 — capture → stream → browser (THE demo)

- `capture` → `CAM_REPLY … ok=1`; one TEL_STAT frame; then open
  `http://nereus001:8080/frame.jpg` — the still that crossed
  AE3→rpmsg→CDC→Light→UDP→Telemetry.
- `stream 2.0 15 60` (rate cap 2.0 Mbps, 15 fps — the measured encode
  ceiling; 60 s) → **browser: `http://nereus001:8080/stream` over the
  tailnet — live video**. `TEL_STAT fps≈15`; `/stats.json` gaps=0.
  Delivered kBps is SCENE-bound (dim room ≈ 1.9 KB/frame ≈ 0.23 Mbps;
  reef-q50 would be ≈ 1.1 Mbps) — capacity was proven separately
  (bite 0: relay 5.26 Mbps sustained with capture live).
- Ledger at every hop (S16 demo-3 table applies): bridge trace
  (`cap_frames/cap_chunks`, frag_errors), HE camera service counters
  (`cam-status` → pub_ok/pub_errs), Light `tx_drops` (transit), TEL_STAT
  `dropped/gaps/hdr_errs/q_drops`, ingest_ok. Honesty note: a dim scene
  encodes below the commanded rate — the rate CAPACITY claim is bite 0's
  reef number, the browser demo shows whatever the bench sees.

## S17 demo 3 — uplink out via gateway_ipc (shipped surfaces only)

- Passive: every 30 s Telemetry prints `UPLINK_TX … {…ledger json…}` —
  that's `spotter_tx_data()` publishing on `spotter/transmit-data`
  (pcap-visible at both Pis; no mote exists to receive it — stated, not
  hidden).
- Active (the python client, second shell on nereus001):

```bash
cd ~/bm_sbc_s15/clients/python && BM_SBC_GATEWAY_IPC=/tmp/s17_ipc.sock python3 -c "import bm_sbc_gateway as g; g.spotter_tx(b'S17 uplink demo'); g.sensor_data('s17/demo', b'hello from ipc')"
```

  Telemetry logs `IPC RX spotter_tx …` / `IPC RX sensor_data …`; the
  sensor publish (`sensor/<node>/s17/demo`) is pcap-visible crossing to
  Light. Client → gateway → BM network: the shipped uplink door, end to
  end.
