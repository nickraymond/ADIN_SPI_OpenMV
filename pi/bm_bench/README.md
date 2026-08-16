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

> **Superseded by §S18 bite D (2026-08-16): the nodes are systemd units
> now.** Kept because it documents the roles, env vars and ordering the
> units encode. Start with `systemctl start`, not these command lines —
> hand-running them alongside a unit is the S19 wedge.

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

## Demo day, one command

The AE3 always ends a session restored to the S6 fixture, so every demo
day starts by re-staging the bridge:

```bash
ssh pi@nereus000 '~/ADIN_SPI_OpenMV/pi/bm_bench/demo_up.sh'
```

Prints READY when the bridge is booted and waiting; then start Light,
then Telemetry (commands in the script header / §S17 start order).

---

## S18 bite A — capture geometry (QVGA + VGA)

Adds resolution + pixel format to the camera service. Geometry is
**measured, not chosen** (DESIGN §S0, sensor 0x7936): the sensor
letterboxes to 16:10, so QVGA is **320×200**, VGA **640×400**, HD
**1280×800**; QQVGA/SVGA/WXGA are unsupported (there is no 720 mode)
and nothing above HD has been tested.

> ### ~~⚠ DO NOT USE `hd`~~ — fixed by S19 bite 2 (2026-08-16)
> HD used to exhaust the HE core's FreeRTOS heap partway through the
> chunk burst (`freertos: malloc failed` after 8 of 26 chunks). It now
> delivers: `capture 50 hd color` → 1280×800, ~42 KB, `gaps=0`. Needs
> the S19 ELF (`4c509d24…`) and bridge (`1524f6c2…`) staged — with the
> S18 build the warning above still applies. See §S19 below.

CLI (Telemetry stdin): `capture [q] [res] [pf]` ·
`stream <mbps> <fps> <secs> [q] [res] [pf]`, where `res` = `qvga|vga`
and `pf` = `color|mono`. Omit either for the bridge default (QVGA
colour). CAM_REPLY echoes `res=` / `pf=`.

**Preconditions:** fork pushed at `ba594ec`, `deploy.sh` PASS on both
Pis, `bm_he.elf` = `4be541ae…` and `bm_bridge.py` = `200412b3…` on
/flash, `demo_up.sh` READY, Light then Telemetry started (§S17 start
order).

Host tests first — no hardware, no docker:

```bash
firmware/bm_he/host_test/run_tests.sh
```

```bash
python3 firmware/bm_bridge/host_test/test_bridge_core.py
```

Then, at the Telemetry prompt, in order:

1. `capture 50 qvga color` — **warm-up, expect it to be dropped.** The
   first capture after startup races the receiver's subscribe
   (`gaps=1`); this is the known S17 startup race, not a fault.
2. `capture 50 qvga color` → `CAM_REPLY ok=1 res=qvga pf=color`, a valid
   320×200 JPEG at `http://nereus001:8080/frame.jpg`, `gaps=0`.
3. `capture 50 vga color` → 640×400, ~11 KB. **This is the command that
   took the board off the USB bus twice before the ceiling fix**; it
   should now be unremarkable.
4. **Switch probe** — alternate `capture 50 qvga color` and
   `capture 50 vga mono` 3–4 times. Every switch is a sensor re-init
   (the D15 class); the bridge re-inits only on a genuine delta and
   pins the framebuffer count before every resize.
5. `capture 50 720p` → **expect `ok=0` + `CAM_REPLY REFUSED`**. Refusing
   an unknown geometry rather than quietly substituting QVGA is the
   point (D31): a silently wrong resolution corrupts a comparison.
6. `stream 2.0 10 30 50 vga color` → note the delivered fps.
7. `cam-status` → `res=`/`pf=` still report the last commanded pair.

**Record the delivered fps from 6.** In-bridge fps at VGA is currently
EXTRAPOLATED from the one measured point (QVGA colour reef held
15.00 fps, S17 bite 0), and it feeds bite C's feasibility warnings.

# S19 — HD stills over pub/sub

HD capture always worked; **publishing** it did not. The HE core's wire
task both receives chunk messages and drains the TX queue, and an
unbounded `rr_poll` let a back-to-back burst starve its own drain —
every published chunk's 1,488 B frame copy stayed on the 64 KB FreeRTOS
heap (20,712 B free at RUNNING) until the burst ended, so the 14th chunk
could not allocate. Bite 1 measured that; bite 2 fixed it. Full story:
DESIGN §S19.

## S19 deploy (AE3 only — no fork change, no pin move)

Build on the Mac, then from the repo checkout:

```bash
scp firmware/bm_he/build/bm_he.elf firmware/bm_bridge/bm_bridge.py pi@nereus000:/tmp/
```

The off-chain probe (demo 3) lives on the Pi, not on the board, and
**must not live in `/tmp`** — this bench reboots the Pi to recover the
AE3's USB (`ae3-usb-unstick`), which wipes it. Put it beside the TOMLs:

```bash
scp bench/probes/s19_pub_probe.py pi@nereus000:~/bm_bench/
```

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; mpremote connect $P cp /tmp/bm_he.elf :/flash/bm_he.elf + cp /tmp/bm_bridge.py :/flash/bm_bridge.py'
```

Expected on-board shas: `bm_he.elf` `4c509d2464412cee`, `bm_bridge.py`
`1524f6c203f232a0`. Then `demo_up.sh` and the §S17 start order.

## S19 demo 1 — HD colour, the whole path (THE demo)

At the Telemetry prompt, in order:

1. `capture 50 qvga color` — warm-up; the first capture after startup
   races the receiver's subscribe (known S17 startup race).
2. `capture 50 hd color` → `CAM_REPLY ok=1 res=hd pf=color`.
3. Open `http://nereus001:8080/frame.jpg` → **a 1280×800 JPEG that
   opens**, ~42 KB on a lit scene.
4. `cam-status` → `pub_errs=0`, and `pub_bytes` should advance by
   `chunks × 10 + jpeg_bytes` exactly (31 × 10 + 42,574 = 42,884 in the
   rehearsal — the ledger is exact, not approximate).

`TEL_STAT` must show `gaps=0 dropped=0 hdr_errs=0`.

## S19 demo 2 — the regression that matters

The bounded poll carries **all** relay traffic, not just camera chunks:

```
stream 2.0 15 600
```

Expect 15.0 fps steady for 600 s with `gaps=0 dropped=0 q_drops=0`,
matching the S17 demoed number. Measured 2026-08-16: 602 s, 8,886
frames, zero on every loss counter across all 602 stat lines.

> ### ⚠ Preflight: exactly ONE producer on the ingest
> The frozen S3 stream server is **single-producer**. Two Telemetry
> instances both connect to `:8081`; the server reads one and never
> reads the other, so the loser's socket buffer fills at 2,592,256 B —
> about 1,416 frames — and the app wedges. Deterministic: it froze at
> exactly `t=109 frames_ok=1416` on both occasions, and killing the
> stale instance unwedged the live one instantly (t 109 → 274). The app
> stays alive and the chain stays up, which makes it look like a product
> fault. It is not.
>
> Before every run, expect exactly two lines (one producer, two socket
> ends):
>
> ```bash
> ssh pi@nereus001 'ss -tn | grep -c :8081'
> ```
>
> Also let the previous `stream` command finish, or cycle the bridge:
> the AE3 keeps streaming its 600 s command even after the Telemetry app
> that asked for it dies, and a second overlapping stream shows up as
> impossible frame counts and gaps (measured: 26,141 frames / 1,676 gaps
> in 607 s — two streams interleaved, not a transport fault).
>
> Both hazards disappear once the nodes run as systemd units.

## S19 demo 3 — off-chain acceptance (no Pis, no camera, ~90 s)

Proves the wall is gone at bursts far past HD, and needs nothing but the
AE3:

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; printf "{\"phases\": [\"verify\"]}" > /tmp/c.json; mpremote connect $P cp /tmp/c.json :/flash/s19_probe_cfg.json; mpremote connect $P run ~/bm_bench/s19_pub_probe.py'
```

`mpremote run` resolves its path **on the Pi**, not on your Mac — a
repo-relative path only works if you are sitting in a checkout that has
the branch, which the Pi's is not (it tracks whatever the last deploy
left). Hence the absolute path and the scp above.

Expect `VERDICT: SURVIVED all 6 rows`, including 60 × 1400 B = 84,000 B
(2.3× an HD frame), with `txdrop=0 stall=0` and a heap floor around
17,704 B.

> **Ops note (bench-earned, S19):** contacting the board shortly after a
> probe run that ended with the HE backpressured took the AE3 off the
> USB bus three times (`error -71`), each costing a Pi reboot via the
> `ae3-usb-unstick` ladder. Let a run finish and settle before the next
> `mpremote` command. The non-blocking pump removes the backpressured
> state that provoked it, but the habit is cheap.

---

# S18 bite D — the bench nodes run as systemd units

**This replaces the hand-run start order above.** Everything from §S16
onward said "run the binary in a shell and Ctrl-C it"; that is what cost
the S19 session most of its hours. Two Telemetry instances silently wedge
the single-producer S3 ingest, `pkill -f` patterns match the driving SSH
command line, and a leftover `stream` command corrupts the next run. A
systemd unit is a **singleton by construction** — a second
`systemctl start` is a no-op, not a race.

The units are installed **disabled**: they never start at boot. An
enabled `bm-light` would open the AE3's CDC port on every boot and fight
`mpremote`, `demo_up.sh` and firmware flashing — the dev loop wins by
default.

## Install (once per Pi, after a `git pull` on the branch)

```bash
ssh pi@nereus000 'sudo ~/ADIN_SPI_OpenMV/pi/install_stream_service.sh light'
```

```bash
ssh pi@nereus001 'sudo ~/ADIN_SPI_OpenMV/pi/install_stream_service.sh telemetry'
```

Each prints `installed, NOT enabled at boot (state: disabled)`. Re-running
is idempotent, and it re-disables a unit someone enabled by hand.

## Start order (every session)

AE3 staging stays manual and separate — starting a service should never
rewrite board flash:

```bash
ssh pi@nereus000 '~/ADIN_SPI_OpenMV/pi/bm_bench/demo_up.sh'
```

```bash
ssh pi@nereus000 'sudo systemctl start bm-light'
```

```bash
ssh pi@nereus001 'sudo systemctl start bm-telemetry'
```

`bm-light` refuses to start if the AE3 is not on the bus, naming the
`ae3-usb-unstick` ladder, instead of failing later inside bm_sbc's uart
open. It also chmods the ACT LED sysfs for the light HAL, which retires
the manual per-boot `chmod` in §S17 deploy.

> ### ⚠ Command the camera within ~30 s of starting `bm-light`
>
> **The bridge quiet-exits after 30 s with no VCP traffic, and it can do
> that in phase 1 — after the BM neighbor has already come up.** Measured
> 2026-08-16 (S18 bite C1 demo): `bm-light` attached at 18:33:10, the AE3
> was adopted as neighbor `be9c…03`, and at **18:34:00** the log read
> `🏚 Neighbor offline` / `UART link down (port 15)`. `bridge_crash.txt`
> shows that bridge wrote `exit: main() returned cleanly` — it **quit, it
> did not crash**. The captures that followed 37 minutes later all came
> back `cam_reply state=timeout cmds=0`.
>
> **A light command does not count** — the light service runs on
> nereus000's own Pi and never crosses the CDC leg to the board. Only a
> camera command does.
>
> So: start Telemetry, then **capture immediately**, then check the link
> before trusting anything:
>
> ```bash
> ssh pi@nereus000 'journalctl -u bm-light --no-pager -o short-iso | grep -Ei "neighbor|link (up|down)" | tail -3'
> ```
>
> No `Neighbor offline` line after the `Adding new neighbor` = you are
> good; once past phase 1 it stays up on heartbeats (verified over a 75 s
> idle window).

## Commands and output

The operator CLI is a FIFO now, not a terminal:

```bash
ssh pi@nereus001 '~/ADIN_SPI_OpenMV/pi/bm_bench/bm-cmd.sh capture 50 hd color'
```

```bash
ssh pi@nereus001 'journalctl -u bm-telemetry -f'
```

`bm-cmd.sh` refuses to write when the unit is not active — a command
appended to a FIFO nobody reads looks exactly like a command that worked.
Every command from §S16–§S19 works unchanged (`help` lists them).

## Preflight (run on BOTH Pis, before and after a demo)

```bash
ssh pi@nereus000 '~/ADIN_SPI_OpenMV/pi/bm_bench/chain_status.sh'
```

```bash
ssh pi@nereus001 '~/ADIN_SPI_OpenMV/pi/bm_bench/chain_status.sh'
```

Read-only, PASS/FAIL per check, non-zero exit on any FAIL. It checks the
things that actually bit: one bench_apps process and it is the unit's
(found by `/proc/<pid>/exe`, never by command-line pattern), exactly one
producer on `:8081` (0 or 2 socket ends — 4 is the wedge), units not
enabled at boot, AE3 present, stream server up, shim stopped, FIFO
present. **It supersedes the §S19 demo-2 preflight box** — that box
documents the failure this script now checks for you.

## Stop

```bash
ssh pi@nereus001 'sudo systemctl stop bm-telemetry'
```

```bash
ssh pi@nereus000 'sudo systemctl stop bm-light'
```

Stopping Telemetry pushes `stop` to the camera first, so the AE3 is not
left executing a 600 s `stream` into the next run — the other S19
contaminator. `systemctl stop` kills the whole cgroup, so "did it
actually die?" stops being a question. The bridge quiet-exits ~30 s after
the port closes, exactly as before.

## Host tests (no hardware, no Pi)

```bash
python3 pi/services/test_bm_units.py
```

33 checks: the singleton properties, the FIFO contract, and the
cross-file path agreements (FIFO / AE3 by-id / binary) that would
otherwise drift into a bench that starts and then does nothing.

## Acceptance — the bug that caused this bite

1. `sudo systemctl start bm-telemetry` **twice** → `chain_status.sh`
   still reports exactly one process. The second start is a silent no-op;
   there is no error to miss.
2. A full `stream 2.0 15 600` under units → 15.0 fps, zero on every loss
   counter (the run that wedged twice in S19).
3. `sudo systemctl stop bm-telemetry` → zero processes, `/run/bm` gone,
   and the board not left streaming.

**Rehearsed 2026-08-16 (Telemetry only, no camera contact):** double
start → one PID, `NRestarts=0`; `bm-cmd.sh status`/`help` answered live
in the journal; 0 s CPU over 10 s elapsed (the FIFO poll does not spin);
stop took 1.06 s leaving zero processes and no `/run/bm`. Items 2 and 3's
camera half need the chain, i.e. Nick's run.

---

# S18 bite B — control socket + still-save

Two additions to the telemetry role, both Pi-side. **No camera_svc.h
change, no wire change, no bridge or HE firmware change** — the AE3 keeps
running exactly the S19 artifacts.

1. **A loopback control socket** at `/run/bm/bench.sock` (AF_UNIX
   SOCK_DGRAM): one JSON object in, one JSON object out. It is the door
   the S18 web tool (bite C) drives the bench through.
2. **Still-save**: every accepted `capture` writes the frame to
   `~/bench_captures/` with a JSON sidecar carrying the commanded
   parameters, the camera's reply, the frame's seq/bytes/chunks, and the
   receiver ledger — absolutely and as deltas since the capture was armed.

## Deploy (both Pis, after the fork push)

```bash
ssh pi@nereus000 'cd ~/bm_sbc_s15 && git fetch fork -q && git checkout -q feature/udp-transport && git pull -q --ff-only && cd ~/ADIN_SPI_OpenMV && git pull -q && ~/ADIN_SPI_OpenMV/pi/bm_bench/deploy.sh'
```

```bash
ssh pi@nereus001 'cd ~/bm_sbc_s15 && git fetch fork -q && git checkout -q feature/udp-transport && git pull -q --ff-only && cd ~/ADIN_SPI_OpenMV && git pull -q && ~/ADIN_SPI_OpenMV/pi/bm_bench/deploy.sh'
```

Pin for this bite: `8c0ff7a`. `deploy.sh` refuses to build on any other
rev, so a half-deployed bench fails loudly instead of behaving oddly.

The telemetry unit gained `S18_CAPTURE_DIR`, so **reinstall it** before
starting:

```bash
ssh pi@nereus001 'sudo ~/ADIN_SPI_OpenMV/pi/install_stream_service.sh telemetry'
```

## Commands

`bench-ctl.sh` takes the same argument order as the FIFO CLI and prints
the JSON reply here instead of on the journal:

```bash
ssh pi@nereus001 '~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh capture 50 hd color'
```

```bash
ssh pi@nereus001 '~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh status'
```

Raw JSON works too, for anything the shorthand does not cover:

```bash
ssh pi@nereus001 '~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh "{\"cmd\":\"capture\",\"q\":90,\"save\":false}"'
```

**Camera and light commands are asynchronous.** The reply says `accepted`,
not `done`; the camera's own answer lands in the next `status` (under
`cam_reply`) and in the journal as `CAM_REPLY`. `status` is the only verb
that answers with live data — params, last replies, receiver ledger, save
state — and it is what the web tool polls at ~1 Hz.

`bm-cmd.sh` still works unchanged, and **its captures are saved too**: the
save is armed inside the app's command path, not in the socket, so a still
taken by hand is recorded exactly like one taken from the web tool.

## Stills and sidecars

```bash
ssh pi@nereus001 'ls -l ~/bench_captures/ | tail -6'
```

Each capture produces `cap_<UTC>_seq<N>.jpg` and `cap_<UTC>_seq<N>.json`.
The **sidecar is the commit record**: the JPEG is written and renamed
first, so a sidecar can never point at a missing or half-written image.
Bite C's gallery enumerates sidecars for that reason.

`gaps_delta` and `dropped_delta` answer "did THIS still lose anything?" —
zero means the frame crossed the chain intact.

Nothing here ever deletes a capture. Below 200 MB free the save is refused
and counted (`save.errors` in `status`); a capture whose frame never
arrives times out after 8 s and logs `CAP_SAVE TIMEOUT` rather than
staying silent.

## Host tests (no hardware, no Pi)

```bash
python3 pi/services/test_bm_units.py
```

43 checks: the bite D singleton properties plus the bite B path agreements
(socket, capture dir), the client's bind-and-match-id contract, and the
stdlib-only rule.

The fork's own wire-format tests run under ctest (`deploy.sh` runs them):
`bench_ctl` is 98 checks covering the parser's nested-value trap, every
refusal path, and truncation-instead-of-half-an-object.

# S18 bite C1 — the bench page

A control page on the Telemetry Pi that drives the bite-B socket. **Pi-side
only**: no fork change, no `camera_svc.h`, no wire change, no bridge or HE
firmware — the AE3 keeps running exactly the S19 artifacts, so there is no
pin move, no ABI lockstep and no size audit in this bite.

Layout and the feasibility model are carried from the mockup Nick approved
on 2026-08-16 (`docs/mockups/s18_bench_mockup.html`). The mockup's
*simulation* is not: no embedded reef photo, no synthetic scene, no
client-side JPEG encoder, no simulated ledger. Every number on the page
comes from `status`, and the live view is the frozen S3 server's own
`/stream` — this server copies no frame bytes and never touches the
single-producer ingest on `:8081`.

Gallery, side-by-side compare and the RGB+luma histograms are **bite C2**.

## Install and start (nereus001)

```bash
ssh pi@nereus001 'sudo ~/ADIN_SPI_OpenMV/pi/install_stream_service.sh bench-web'
```

```bash
ssh pi@nereus001 'sudo systemctl start bench-web'
```

Then open **`http://nereus001:8090/`**. Installed disabled, like the BM
nodes. It does *not* require `bm-telemetry`: with the socket absent the page
header goes red and every command answers 503 saying which unit to start,
which beats a unit that refuses to boot and leaves you with a browser error.

## The click guard

Until bite B2 lands, a sensor re-init arriving too soon after a capture
throws `Sensor control failed.` and **wedges the camera until the bridge
restarts** (SPEC §Open questions). The page holds its controls in two
independent ways, and says which one is active:

- **busy** — one camera command at a time. A capture holds until bite B's
  save counter moves (`saved` or `errors`), because `mode_active` in the
  camera reply is *last commanded*, not *currently busy* — it stays 1 after
  a still completes. A frame that never arrives releases at 12 s, past the
  save's own 8 s timeout.
- **settle** — 8 s after a capture, **and only for a command that changes
  resolution or pixel format**, because only a genuine delta re-inits the
  sensor (bite A). QVGA→QVGA colour repeats are never held.

**The server enforces it, the page only shows it** — a reload or a second
tab cannot get past a guard that lives in JavaScript. `Stop` is never gated.

8 s is 2 beyond the only passing measurement (≥6 s succeeded 3/3;
sub-second failed 2/2). The required quiet time **scales with the previous
frame's size and nothing has been measured at HD** — `--settle` is a knob,
and bite B2's matrix is what should replace the constant.

## Predictions are EXTRAPOLATED

The fps/bandwidth model comes from **one** measured point (QVGA colour reef
q50 = 15.00 fps in-bridge, S17 bite 0); everything off that mode is
arithmetic. The page says so on every warning, in the constants panel and in
the footer. Bite B2's 9-row matrix replaces it with measurement.

## Host tests (no hardware, no Pi, no browser)

```bash
python3 pi/bench_web/test_bench_web.py
```

42 checks, most of them on the guard: the stale-`save.state` trap, the
grace release, repeats-not-held, mode-change-held, stop-never-gated, and
that a refused command never reaches the socket.
