# BENCHSPEC v3 — Three-Node Bristlemouth Bench (No ADIN, No PoDL)

**Status:** v3 — adopted (Nick, 2026-08-14)
**Date:** 2026-08-14
**Owner:** Nick (approver)
**Supersedes:** BENCHSPEC-v2 (revised draft, 2026-08-14); BENCHSPEC v1 (2026-08-14)

**Revision provenance.** v1 was agent-generated. v2 reviewed v1 against the
actual source of `bristlemouth/bm_core` @ `c15e6b1` and `bristlemouth/bm_sbc`
@ `17ea904` (cloned and read 2026-08-14) and fixed two critical technical
errors, one inverted reading, and several scope omissions ([REV-1]–[REV-19]).
**v3 reviews v2 against this repo's project context** (TRACKER/DESIGN/DEV_LOG,
the S10 INTERIM builds, the S7/S11 research) — context the v1/v2 agents did not
have — and adds [REV-20]–[REV-28]. The headline change: BUILD-2 no longer gives
the AE3's HE core the USB controller; the AE3 attaches through the already-built
rpmsg seam plus an HP-side bridge (REV-20..22). If you are an agent working
from this spec: read §10 before "fixing" anything back, and read this repo's
`docs/TRACKER.md` rules before doing anything at all.

---

## 1. Purpose

Stand up a three-node Bristlemouth network on the bench using ordinary Ethernet,
USB, and serial, so application software development proceeds while the ADIN
expander boards (~1 month out) are in fabrication.

The bench must be built so that **when the ADIN + PoDL hardware arrives, only the
network-device layer is replaced.** No application, service, pub/sub, or protocol
code may need rework.

### Goals

- Three real nodes, on three real machines, running the real `bm_core` stack.
- A logical daisy chain (not a star), so L2 forwarding and multi-port behaviour
  are genuinely exercised.
- AE3 participates as a true BM node, running bm_core **as compiled C on the HE
  core**. **[REV-22] Precision (v2's REV-2 understated this):** the HE stack is
  a **runtime-loaded ELF placed in SRAM9_B via stock OpenMV firmware's
  remoteproc, driven from HP MicroPython over USB. Nothing is flashed; HP keeps
  running stock OpenMV firmware.** A standalone custom AE3 firmware is
  explicitly out of scope for this bench.
- Camera video path: **2 Mbps continuous stream** from the AE3, carried over
  USB — via the HP bridge, not an HE-owned USB stack (see §2, REV-20). **[REV-3]**

### Non-goals

- ADIN register access, OPEN Alliance SPI, PHY autonegotiation, PoDL.
- Interoperability with Sofar mote / Spotter firmware. *(The S11 INTERIM 3
  dev-kit bite in TRACKER.md covers that separately — and validates the same
  `uart_l2` gateway wire format this bench rides.)*
- Physical-layer timing fidelity.
- A second CDC interface in custom OpenMV firmware (priced-later option if
  one-VCP time-sharing becomes an ops burden — not assumed by anything here).

> **[REV-3] v1 said:** "Throughput headroom sufficient to develop the video path
> (not baud-limited)" while routing the camera over a serial UART. Contradiction:
> UART 8N1 at the transport's 2 Mbaud maximum carries 1.6 Mbps raw, ~1.4–1.5 Mbps
> after framing + BM headers — below the 2 Mbps stream. The video requirement
> therefore *selects* USB; v3 routes it through the HP bridge (REV-20).

---

## 2. Bench topology

```
 Telemetry (nereus001)  ──Ethernet/UDP──  Light (nereus000)  ──USB CDC (HP bridge)──  Camera (AE3 HE core)
    head of chain                          pass-through                                    leaf
```

| Node | Hardware | Node ID | Link A | Link B | Console/debug |
|---|---|---|---|---|---|
| Telemetry | Pi 5 `nereus001` | `0x…01` | UDP → Light | — | tailnet SSH |
| Light | Pi 5 `nereus000` | `0x…02` | UDP → Telemetry | USB CDC → Camera | tailnet SSH |
| Camera | OpenMV AE3 (HE core) | `0x…03` | rpmsg → HP bridge → USB CDC → Light | — | HE debug ring read from HP; HP VCP time-shared **[REV-21]** |

> **[REV-20] Host mapping (v2 said Light = "Pi 3/4"):** Light = **nereus000** —
> the AE3 is already on its USB, and the VCP ownership rules (by-id mpremote,
> uhubctl recovery, session discipline) are established there. Telemetry =
> **nereus001** — its frozen S3 stream server is userspace and unaffected. The
> Pi 3/4 is not needed. Ethernet hop = **direct eth0↔eth0 cable**, static
> `10.42.0.0/24`, no default gateway; dev access stays on the tailnet/WiFi.
> **Bench check (Nick): confirm both eth0 ports are actually free before S15.**
>
> **[REV-21] AE3 console (v2's REV-4 wired a side-header UART + SWD):** neither.
> Debug = the **existing 4 KB HE debug ring** (address on the bm status page,
> read and auto-dumped from HP — proven in INTERIM 2, carried bm_core's boot
> narrative and caught the %llx bug). No SWD debugger exists on this bench
> (DESIGN D22), and a P4/P5 console would collide with those pins'
> hardware-day roles (ADIN RESET/IRQ, DESIGN D3). The VCP remains available
> for interactive REPL whenever the bridge is not running. v2's REV-4 pin
> facts (UART3 RTS/CTS is 1.8 V B2B-only; side UARTs have no flow control)
> stay recorded for any future raw-UART revival.

**Port-map note [REV-5]:** v1's table said "1 BM port / 2 BM ports / 1 BM port"
per node. In fact **every bm_sbc node reports 15 ports** to `bm_l2_init()` /
`topology_init()`: `vpd_num_ports()` returns the constant 15 regardless of peer
count, and the gateway composite depends on that (see BUILD-1, invariant 2).
The *logical* wiring is 1/2/1; the *reported* port count is 15 on the Pis (the
AE3 netdev reports 1 — see BUILD-2).

**The chain exists only in the peer tables.** Camera and Telemetry must never
appear in each other's peer list. Traffic between them must transit the Light
node. If all three see each other, the bench degenerates to a star and stops
testing forwarding — this is the single most important invariant.

**What "transits" means [REV-6]:** from `l2_policy.c` + `middleware.c`:

- **`ff03::1` global multicast** — flooded to all ports except ingress. This is
  what pub/sub and `bm_service` ride (`pubsub.c` registers on
  `multicast_global_addr`). **This is the traffic class that exercises L2
  forwarding through the Light node.**
- **`ff02::1` link-local neighbour multicast** (heartbeats) — never forwarded,
  by design. Neighbours are per-link.
- **Everything else, including unicast `fd00::<node>`** — L2 does **not** forward
  it. BCMP multi-hop works only via BCMP's own re-transmit path (`bcmp.c:212`).
  Ad-hoc unicast between Telemetry and Camera will silently vanish. This is the
  sharpest reason for anti-duplication rule §6.2.

Physical wiring: **one Ethernet cable, one USB cable** (the AE3's existing one).
An unmanaged switch is optional (packet-capture laptop or a future 4th node).

### Network configuration

- Bench interfaces get static IPs on an isolated subnet, **no default gateway**.
- Suggested: `10.42.0.1` (Telemetry), `10.42.0.2` (Light), UDP port `22000`.
- Bench subnet must not overlap the lab WiFi subnet.
- Dev access (SSH / Tailscale / internet) stays on WiFi/tailnet on both Pis.
- No DHCP, no internet, no live-network dependency.

### Version pinning **[REV-23]**

All three nodes build against **bm_core @ `d4ecc38`** — the rev vendored in
`firmware/bm_he` (compiles byte-identical on CM55, zero patches) and pinned by
bm_sbc main's submodule. v2's review rev `c15e6b1` is NOT the build rev.
Upgrades happen as a deliberate all-nodes bump, never per-node.

---

## 3. Reused from Sofar — do not reimplement

### From `bm_core` (github.com/Bristlemouth/bm_core @ d4ecc38 [REV-23])

| Path | What it gives us |
|---|---|
| `network/network_device.h` | `NetworkDeviceTrait` / `NetworkDeviceCallbacks` / `NetworkDevice`. The swap seam. **Convention warning [REV-1]: `receive()` takes a 1-based port number (1–15); `link_change()` takes a 0-based port index.** |
| `network/l2.c`, `l2_policy.c` | L2 queueing, ingress/egress port encoding, multicast hairpin suppression, forwarding (multicast-only — see §2 [REV-6]). |
| `network/bm_linux.c` | Pure-software Ethernet+IPv6 stack for Linux hosts (replaces lwIP). |
| `common/bm_posix.c` | POSIX OS abstraction (tasks, queues, timers). |
| `bcmp/` | Heartbeat, neighbours, ping, info, time, DFU, resource discovery. |
| `integrations/topology.c` | Topology. **[REV-7] v1 said this lived in `bcmp/` — it doesn't.** |
| `middleware/pubsub.c`, `bm_service.c`, `bm_service_request.c`, `cbor_service_helper.c` | Pub/sub and request/reply services. All application messaging uses these. **[REV-25] On the AE3 this slice must pass the V15 size audit before BUILD-4 commitments.** |
| `middleware/power_info_service.c`, `sys_info_service.c`, `metrics_service.c` | Standard services. Power service is developed now against a simulated backend (`power_info_service_init(stats_cb, arg)` — API shape confirmed). |
| `common/pcap.c` + `proto_bcmp.lua` | Frame capture + Wireshark dissector. |
| `drivers/adin2111/` | Not used on the bench. Drop-in replacement on hardware day. |

### From `bm_sbc` (github.com/Bristlemouth/bm_sbc @ main, bm_core pinned d4ecc38)

| Path | What it gives us |
|---|---|
| `src/net/virtual_port_device.cpp/.h` | Peer-table network device (port N == `peers[N-1]`, max 15), Unix `SOCK_DGRAM`. **Template for the UDP device.** 460 lines, verified. |
| `src/net/gateway_device.cpp/.h` | Composite device: VPD ports + serial link on port 15. Makes the Light node two-transport. Known latent bugs — see V13. |
| `src/net/gateway_ipc.cpp/.h` + `docs/gateway-ipc.md` + `clients/python/bm_sbc_gateway/` | **[REV-8] Shipped uplink channel** for an external process to reach the BM network through a node. v1 omitted this and would have had BUILD-4 reinvent it, violating its own rule §6.2. |
| `src/transports/uart_l2/` | `cobs.[ch]`, `crc32c.[ch]`, `frame_codec.[ch]`, `uart_l2_transport.cpp`. Wire format: `[COBS([len_hi][len_lo][L2 frame][CRC-32C])][0x00]`. The codec is transport-agnostic. **[REV-20] The Pi end of the AE3 link is `uart_l2_transport` pointed at `/dev/ttyACM*` — a CDC tty is still a tty; termios applies; baud is ignored by CDC. Zero new Pi-side transport code.** |
| `src/core/runtime.cpp` | CLI + TOML parsing, device composition, stack bootstrap. Full init order (v1 omitted two steps **[REV-9]**): `bm_l2_init` → `timer_callback_handler_init` → `bm_ip_init` → `bcmp_init` → `topology_init` → `bm_service_init` → **`bm_pubsub_init`** → `bm_middleware_init` → `sys_info_service_init` → `config_cbor_map_service_init`. |
| `src/platform/linux/` | Config partitions, RTC, DFU file wrappers. |
| `apps/multinode` + `scripts/multinode_test.sh`, `scripts/validate.sh` | **[REV-10] Already-shipped CI tests** covering 2-node ping, pub/sub, 3-node chain topology, 15-peer cap. v1's Stage 1 proposed building what these already do. |
| `scripts/gateway_loopback_test.sh` | socat-based two-node serial loopback test. |
| `examples/node*.toml` | Config file format. |
| `--pcap` flag (runtime.cpp + `pcap_file_sink`) | L2 capture both directions at any Pi node — the bench's packet-truth instrument (same mechanism as the S11 golden captures). |

### From this repo (context the v1/v2 agents lacked)

| Asset | What it gives us |
|---|---|
| `firmware/bm_he/` (INTERIM 2, S10) | bm_core @ d4ecc38 running on HE (FreeRTOS + lwIP 2.2.1 + BCMP), **trait-level NetworkDevice with rpmsg as the wire (DESIGN D25 — designed for exactly this promotion)**, debug ring, runner + pcap writer, 112 host-test checks. |
| `firmware/bm_he/s10_peer.py` | Byte-exact BCMP builders/parsers (CPython-testable) — wire-format reference + golden-capture diff tool base. |
| `firmware/he_spike/` (INTERIM 1, S10) | The rpmsg pipe itself: measured 13.2 Mbps HP→HE / 5.6 HE→HP (python-end-bound; fabric 219), recycle semantics host-harnessed. |
| `firmware/ae3_usb/` + `pi/stream/usb_frame_source.py` (S3) | Proven pattern for binary framed streaming over the HP VCP (~2.5 Mbps sustained) + host-side session/reboot discipline (D15). |
| `pi/ae3_flash/` (S7) | The flash/recovery ladder that REV-20 preserves. |

---

## 4. To be built

### BUILD-1 — `udp_port_device` (Pi side)

A `NetworkDevice` that carries L2 frames between hosts over UDP.

Derived from `virtual_port_device.cpp`. The change is mechanical:

| virtual_port_device | udp_port_device |
|---|---|
| `AF_UNIX` / `SOCK_DGRAM` | `AF_INET` / `SOCK_DGRAM` |
| `struct sockaddr_un`, socket path | `struct sockaddr_in`, IP + port |
| peer identified by node-ID-derived path | peer identified by `ip:port` |

Unchanged and **must stay unchanged**:

1. Peer table indexed by `port_num - 1`; the 1-byte egress-port prefix on each
   datagram (sender's egress = receiver's ingress).
2. **`num_ports()` returns the constant 15**, not the configured peer count.
   **[REV-11]** This is load-bearing: `gateway_device_get()` computes the serial
   port as `min(num_ports, 14) + 1`, and `gateway_uart_rx_cb()` hardcodes
   `GATEWAY_UART_PORT` (15). If `num_ports()` returned the real peer count, the
   computed port (2) and the hardcoded constant (15) would diverge and the
   serial link's auto-link-up path would silently die.
3. One bound receive socket plus per-peer send sockets; RX thread with
   `SO_RCVTIMEO`.
4. **Link-up is fired from `retry_negotiation()`, NOT from `enable()`.**
   **[REV-12] v1 said the opposite.** The code explicitly does *not* fire
   link_change in `vpd_enable()` — doing so races the L2 thread's
   renegotiation-timer startup (comment in source; independently hit live in
   this repo's INTERIM 2a bring-up). `vpd_retry_negotiation()` detects the peer
   (for UDP: replace the `access()` socket-path check with a reachability
   heuristic or configured-peer check) and fires `link_change(idx, true)` from
   the 100 ms L2 timer. Preserve **that**.

Additional requirements:

- Optional token-bucket rate limit, default **10 Mbps** (10BASE-T1L emulation).
  **[REV-13]** L2's TX queue is 32 deep with a 10 ms enqueue timeout that
  **frees the buffer on overflow** (`bm_l2_tx`, `l2.c`) — silent drop, not
  backpressure. The rate limiter shapes the wire but will *hide* that failure
  mode, not surface it. Add a drop counter on the `BmENOMEM` path and per-port
  TX-depth logging so overflow is observable. Also note the real hardware
  ceiling is the ADIN's OA-SPI link, below 10 Mbps line rate — treat 10 Mbps
  as optimistic, and validate the 2 Mbps video stream against this limiter
  early.
- Config accepts `ip:port` peers in port-slot order.
- Datagram size ceiling 1 + 1514 bytes; oversize frames dropped **with a log
  line that includes the length and ingress port**. **[REV-14]** MTU decision:
  the serial codec accepts L2 frames to 1522 (`FRAME_CODEC_MAX_L2_SIZE`) but
  VPD/UDP caps at 1514 (`VIRTUAL_PORT_MAX_FRAME_LEN`) and rejects oversize with
  a bare `BmEINVAL`. The Light node bridges both. **Decision: 1514 is the
  network-wide max frame size.** Enforce it at the AE3 sender; the logged drop
  at the Light node is the backstop, not the mechanism.

**Done when:** two Pis discover each other and ping across the Ethernet link,
with the rate limiter measured.

### BUILD-2 — AE3 attachment: rpmsg netdev + HP bridge **[REV-20: replaces v2's HE-owns-USB CDC design]**

> **Why not HE-owned USB (breadcrumb):** v2's REV-3/V12 reasoned that with
> "bm_core as compiled C on the HE core — not stock OpenMV MicroPython" the
> USB controller was assignable to HE. The premise is false on this bench
> (REV-22): **HP runs stock OpenMV MicroPython and owns the single USB 2.0 HS
> controller**, and the entire dev loop rides it — mpremote REPL, the
> remoteproc ELF load of the HE stack itself, the S7 DFU flash ladder, and
> every D15/uhubctl recovery path. Surrendering USB to HE breaks all of it
> and additionally requires a bare-metal Alif USB device stack (a multi-bite
> project). V12 is thereby RESOLVED: HP claims USB, and we keep it that way.

Three pieces, two of which mostly exist:

**(a) HE netdev** — promote `firmware/bm_he`'s trait-level NetworkDevice from
"mock with scripted wire" to "real device with rpmsg wire". Same trait rules
as v2 specified (they were correct and match our INTERIM 2a experience):

| Member | Implementation |
|---|---|
| `send` | frame → rpmsg to HP. Port 0 (flood) and port 1 both mean the single link. Enforce 1514-byte max L2 frame (REV-14). |
| `enable` | Start the link + RX task. **Do NOT fire `link_change` here** (REV-12). |
| `disable` | Stop the link; fire `link_change(0, false)`. |
| `num_ports` | Returns `1`. |
| `enable_port` / `disable_port` | Return `BmOK`, no-op. |
| `retry_negotiation` | **Load-bearing, not a stub [REV-12].** When the bridge link is up: set `*renegotiated = true`, call `callbacks->link_change(0, true)`, return `BmOK`. |
| `port_stats` | Zeroed or unsupported. |
| `handle_interrupt` | No-op. |

- RX path: rpmsg frame → `callbacks->receive(1, frame, len)` (1-based).
- `callbacks->power` → **do not call it.** **[REV-15]** Only `bristlemouth_init()`
  assigns it (not called here) and only `bm_adin2111.c` invokes it, NULL-checked.
- **Link-up: `callbacks->link_change(0, true)` — index 0, not 1.** **[REV-1]**
  `l2.c` masks with `1 << port_idx`; with `num_ports() == 1` a 1-based call
  sets a mask that never intersects `all_ports_mask` and **TX dies silently
  inside L2** — no driver log will show it.

**(b) HP bridge** — a MicroPython script (the successor of the INTERIM-2
runner's fake-wire pump) that moves L2 frames between the HE rpmsg endpoint
and the USB VCP, framed with the `uart_l2` codec (COBS + CRC-32C — implement
the codec in python once, ~80 LoC, tested against `s10_peer.py`-style host
tests and bm_sbc's C vectors). Rules:

- **Crash persistence (from the one-VCP review):** the pump wraps in
  try/except and writes any traceback to `/flash/bridge_crash.txt` before
  exiting — because when bm_sbc holds the VCP, a printed traceback lands in
  its decoder as COBS garbage and the text is lost. Failure sequence: Pi sees
  link death → stop bm_sbc → reattach mpremote → read crash file + debug ring.
- The VCP is time-shared: bridge running = data pipe (REPL unavailable),
  bridge stopped = normal REPL. Same ops rhythm as the S3/S6 capture service.
- Stray text on the wire (boot banner, a print) does not kill the link —
  COBS resyncs at the next `0x00`; bm_sbc counts decode errors.

**(c) Pi side** — bm_sbc's existing `--uart` gateway pointed at the AE3's
CDC device (`/dev/serial/by-id/...` per this repo's by-id rule). **Zero new
transport code.**

Buffer budget: `FRAME_CODEC_MAX_L2_SIZE` = 1522 → worst-case wire =
`COBS_ENCODE_MAX(1528) + 1` = **1536** bytes (v1 said ≈1535 **[REV-16]**).
Budget ~2 × 1.6 KB plus the L2 queue on HE; size rpmsg + VCP buffering for
sustained throughput, not just max-frame.

**Platform scope [REV-17], V11 RESOLVED [REV-27]:** the existing HE build uses
the **FreeRTOS bm_os shim + lwIP 2.2.1 + pinned lwip-contrib sys_arch**
(`firmware/bm_he`, DESIGN §S10). Config partitions and RTC are **RAM stubs,
born empty each load — deliberate**. Flash-backed config and a real
`bm_rtc_*` backend are hardware-day work; BUILD-4's time-sync feature is
gated on choosing an RTC backend (see BUILD-4).

**Done when:** the Light Pi lists the AE3 as a neighbour, ping round-trips, and
a sustained 2 Mbps publish from the AE3 arrives at the Light node without CRC
failures for ≥10 minutes.

### BUILD-3 — Transport factory + config plumbing **[REV-18: refactor, not just keys]**

> v1 framed this as "extend TOML/CLI with UDP peers." The actual work is a
> **refactor of `runtime.cpp`**, whose device construction is hardwired
> (`virtual_port_device_get(&vpc)`, optionally wrapped by
> `gateway_device_get()`). There is no factory or transport enum today.

- Introduce a network-device factory selected by a `transport =` config key
  (`virtual` | `udp` | `serial` | `adin`), composable with the gateway wrapper.
  Same binary runs all transports. No `#ifdef`.
- Constraint: `virtual_port_device.cpp` (`g_vport_state`) and
  `gateway_device.cpp` (`s_gw`) are **module-level singletons** — one device of
  each kind per process. Keep `udp_port_device` the same way, or fix all three;
  don't mix.
- Constraint: `gateway_device_get()` copies the inner device and shares its
  `callbacks` pointer — a composed device must expose its callbacks struct the
  way VPD does (`dev.callbacks = &state.callbacks`).
- Extend TOML/CLI: `udp-peers = ["10.42.0.2:22000", …]` alongside existing
  `peers`, `uart-device`, `uart-baud` keys.
- Node IDs fixed now and never reused.
- Config partitions: three partitions ≥ 4359 bytes each via `--cfg-dir` on Pis;
  **RAM-stubbed on the AE3 for the bench (REV-27)** — flash-backed is
  hardware-day.

### BUILD-4 — Application layer (the actual point of the bench)

Built on `bm_service` / `pubsub` / `cbor_service_helper`, never on ad-hoc
messages. **[REV-6] The enforcement is physical, not just stylistic: L2
forwards only `ff03::1` multicast, so an invented unicast message layer would
not even cross the Light node.**

**[REV-25] Gate: the V15 size audit** (pubsub + bm_service + cbor slice linked
into the HE image, map read against the 262 K region) **passes before any
BUILD-4-on-AE3 commitment.** The HE image is at ~88% today.

- **Light service** — level, strobe, state query.
- **Camera service** — trigger capture, status; image/video data on a pub/sub
  topic (this is the 2 Mbps stream — it transits the Light node via multicast
  flood, which is exactly the forwarding path the bench exists to exercise).
  **[REV-28] Chunked at ≤ ~1400 B payloads** (the S12 framing plan; S6's BMV6
  chunk protocol adapted to pub/sub). Pub/sub is fire-and-forget — the drop
  counters (REV-13) at every hop are the loss ledger, per this repo's
  receiver-side counting philosophy (D21).
  **Note: capture/encode runs on HP MicroPython alongside the bridge pump —
  single core, CPU-bound (D8/D21). The V16 relay gate must be re-checked with
  capture+encode live before this service's rate targets are committed.**
- **Telemetry node** — subscribes, aggregates, forwards to the uplink **via the
  shipped `gateway_ipc` channel and Python client** (`docs/gateway-ipc.md`),
  not a new mechanism. **[REV-8]**
- **Power HAL** — `power_hal.h` with a simulated backend feeding
  `power_info_service_init(stats_cb, arg)`. Real regulator driver swaps in on
  hardware day.
- **Time sync** — `bcmp/time.c` + `bm_rtc_*`, for camera-capture ↔ strobe
  timing. **[REV-27] Gated on an RTC-backend decision for the AE3 (currently a
  RAM stub); Pi nodes have the platform RTC wrappers already.**

---

## 5. Bring-up sequence

| Stage | Action | Acceptance |
|---|---|---|
| 0 | Build; run `scripts/validate.sh` (CI parent: unit tests + multinode + loopback + IPC) **[REV-10]**. **Plus the two v3 gates [REV-25/26]: V16 relay-throughput bench (HE→HP rpmsg + HP→Pi VCP simultaneous relay, ≥2 Mbps sustained 10 min, target 2×) and V15 HE size audit (middleware slice linked, map read).** | All existing tests pass on the Pi. Relay gate met with printed Mbps + verdict. Size table recorded. **Either gate failing re-plans BUILD-2/BUILD-4 before code is written.** |
| 1 | BUILD-1 + BUILD-3, split across two Pis over Ethernet | Neighbour discovery + ping across the real link; rate limiter measured; L2 drop counter observable (REV-13). |
| 2 | BUILD-2, AE3 joins over the HP bridge | Three-node chain. **Pub/sub message** from Camera arrives at Telemetry (exercises L2 forwarding through Light — [REV-6]: a two-hop BCMP ping tests BCMP's re-transmit, *not* L2 forwarding; both are worth running, labeled as what they are). |
| 3 | Sustained-rate test | 2 Mbps publish from AE3 for ≥10 min; zero CRC failures; drop counters at every hop logged. |
| 4 | BUILD-4 application services | End-to-end: capture triggered, stream published, light commanded, uplink via gateway_ipc. |
| 5 | ADIN boards arrive — swap `udp_port_device` / rpmsg netdev for the ADIN driver | Application code unchanged. Pi side: prefer Sofar's `feature/adin_linux_implementation` raw_eth device (REV-24). |

TRACKER.md carries these stages as sprints S14–S17 (rung 0 = S14, stages 1 =
S15, 2–3 = S16, 4 = S17); stage 5 is RESUME-ON-HARDWARE.

---

## 6. Anti-duplication rules

1. Nothing above L2 may know which transport it is on. No `#ifdef` on transport
   in application, service, or pub/sub code.
2. All node-to-node messaging goes through `bm_service` / `pubsub` with
   `bm_common_messages` conventions. **[REV-6] This is physically enforced:
   ad-hoc unicast does not cross the pass-through node.**
3. Board-specific behaviour sits behind a HAL header with a simulated backend
   (power first).
4. The simulated transports are **kept permanently** as the CI and regression
   bench. They are not scaffolding.
5. The uplink is `gateway_ipc`. Do not build a second one. **[REV-8]**
6. **[REV-20]** The AE3 leg reuses the `uart_l2` wire format verbatim over the
   VCP. Do not invent a second framing; do not fork the codec.

---

## 7. Risks and VERIFY items

Carried over where still valid; struck-through items are resolved with the
resolution noted (breadcrumbs for the next agent).

| # | Item | Status / why it matters |
|---|---|---|
| ~~V1~~ | ~~Docs say serial port = peers+1; code says 15~~ | **Resolved [REV-11]:** port 15 — but only because `num_ports()` returns constant 15. The invariant to protect is the constant, not the number. |
| ~~V2~~ | ~~`baud_to_speed()` caps at B230400~~ | **Resolved (v1 had it backwards):** the code supports 1 M / 1.5 M / 2 Mbaud; the *docs* are stale. Moot for the data path — CDC ignores baud. |
| ~~V3~~ | ~~Can the HE core drive USB?~~ | **Superseded → V12, now RESOLVED [REV-20]: HP owns USB and keeps it.** |
| ~~V4~~ | ~~uart_l2 is 8N1 no flow control~~ | **Moot [REV-21]:** UART console dropped entirely; debug = HE ring. Pin facts retained in §2 for any future raw-UART path. |
| V5 | bm_sbc serial path "Not yet validated on physical hardware" (verbatim, `docs/uart-gateway.md`) | Still true. Expect to be first to hit partial-read and timing bugs — now on the CDC byte pipe instead of a tty. (The S11 dev-kit bite, if run, validates the same path on real serial.) |
| V6 | `bm_core` ships **only** `drivers/adin2111`; no ADIN1110 driver | Still true. Mitigated in this repo: S9 proved bm_core's 2111 driver drives our 1110 through an init bridge (2-item delta + bridge, DESIGN §S9); production goes 2111 anyway. |
| V7 | The pass-through node needs two ports; deployment chain order drives which *board* gets the two-port part | Still open, still urgent — boards are ~1 month out; settle before layout. With the 2 Mbps stream, whichever node sits between Camera and Telemetry carries the full stream through its two ports. |
| ~~V8~~ | ~~Three single-port ADIN1110 hats on hand can't form a bm_core three-node chain~~ | **Overtaken by events: both AOS hats are condemned (2026-08-12)** — there is no working T1L hardware at all until PCBAs arrive [REV-24]. |
| V9 | UDP over Ethernet is faster/more reliable than 10BASE-T1L | Still true. Mitigation is BUILD-1's limiter **plus the drop counter [REV-13]**. |
| **V10** | L2 forwards only `ff03::1`; unicast is not forwarded; middleware `routing_cb` forwarding exists but only `bm_mavlink` uses it | Determines what "traffic transits Light" means in every acceptance test. Pub/sub = L2 forwarding; BCMP ping = BCMP re-transmit. |
| ~~V11~~ | ~~Which OS shim + IP stack does the AE3 build use?~~ | **RESOLVED [REV-27]:** FreeRTOS shim + lwIP 2.2.1 + pinned contrib sys_arch; config/RTC are RAM stubs (persistence = hardware-day). `firmware/bm_he`, DESIGN §S10. |
| ~~V12~~ | ~~Does anything in HP firmware claim the USB controller?~~ | **RESOLVED [REV-20]: yes — stock OpenMV HP firmware owns it, and the whole dev loop (REPL, remoteproc ELF load, DFU flash, recovery) rides it. Decision: keep it there; AE3 attaches via rpmsg + HP bridge.** |
| **V13** | Latent bugs in `gateway_device.cpp` reused as-is: (a) `gw_retry_negotiation` / `gw_port_stats` treat their port arg as 0-based but `l2.c` passes 1-based — top VPD port never renegotiates; (b) `gateway_uart_rx_cb` hardcodes `GATEWAY_UART_PORT` | (a) only bites with 14 peers configured — not on this bench, but upstream a fix; (b) is the trap that makes BUILD-1 invariant 2 (constant `num_ports()`) load-bearing. |
| **V14** | ADIN1110 kernel-driver `AF_PACKET` device as a realism upgrade for the Ethernet hop | **[REV-24] Blocked today — no working T1L line hardware (both AOS hats condemned).** On hardware arrival, prefer Sofar's `feature/adin_linux_implementation` raw_eth branch (it IS this device; S7 research) over writing our own. UDP device now. |
| **V15** | **[NEW, REV-25]** HE image is at **231.5 K of 262 K (~88%)**; BUILD-2 additions + BUILD-4's middleware slice may not fit | Size-audit gate in Stage 0. Levers: config store 26 K, pbuf 18 K, 64 K heap tuning, unproven load-to-ITCM. Can invalidate BUILD-4-on-AE3 scope — check before code. |
| **V16** | **[NEW, REV-26]** Combined relay throughput unproven: HE→HP rpmsg (5.6 Mbps measured alone, python-bound) + HP→Pi VCP (~2.5 Mbps measured alone, S3) have never run **simultaneously through one HP python loop** — and BUILD-4 later adds capture+encode on the same single core | Stage-0 gate: ≥2 Mbps sustained 10 min through the full relay (target 2×). Re-check with capture+encode live before BUILD-4 rate commitments. S0 discipline: measure before building. |

---

## 8. References

**Source repositories:**

- `bm_core` — https://github.com/Bristlemouth/bm_core — **build rev `d4ecc38` [REV-23]** (v2 review rev was `c15e6b1`)
  - `network/network_device.h` — the trait; 0-based/1-based split [REV-1]
  - `network/l2.h/.c`, `l2_policy.c` — forwarding policy (V10), TX-drop path (REV-13)
  - `network/bm_linux.c`, `common/bm_posix.c`, `network/network_frames.h`
  - `integrations/topology.c` [REV-7]
- `bm_sbc` — https://github.com/Bristlemouth/bm_sbc @ main (local clone: `~/Documents/GitHub/bm_sbc`)
  - `src/net/virtual_port_device.h/.cpp` — BUILD-1 template; link-up-via-retry design [REV-12]
  - `src/net/gateway_device.h/.cpp` — composite device; known latent bugs (V13)
  - `src/net/gateway_ipc.cpp`, `docs/gateway-ipc.md`, `clients/python/` — the uplink [REV-8]
  - `src/transports/uart_l2/frame_codec.h` — wire format
  - `scripts/validate.sh`, `scripts/multinode_test.sh` [REV-10]
  - `agents.md` — repo orientation for agents
- **This repo:** `docs/TRACKER.md` (rules + ladder), `docs/DESIGN.md` (D8, D15,
  D21–D25, §S10 details), `firmware/bm_he/`, `firmware/he_spike/`,
  `firmware/ae3_usb/`, `pi/ae3_flash/`

**Hardware:**

- OpenMV AE3 quick reference (pinout, I/O voltages, UART map) —
  https://docs.openmv.io/v5.0.0/openmvcam/quickref/openmv-ae3.html
- Alif E3 datasheet — https://www.mouser.com/datasheet/2/1549/Alif_E3_Datasheet_v2_9-3454193.pdf
- ADIN1110 Linux (kernel netdev) quick start — for V14 / hardware day —
  https://wiki.analog.com/resources/quick-start/adin1110_linux_quick_start_guide

> **[REV-19]** v1 cited the Bristlemouth Discourse "native Linux support"
> thread as bm_sbc lineage. The thread describes an unrelated 2023 prototype.
> Dropped; `bm_sbc/agents.md` is the correct orientation doc.

---

## 9. Open decisions for Nick

1. Deployment chain order — which module is the pass-through in the water?
   (drives V7; carries the 2 Mbps stream implication)
2. ADIN2111 vs ADIN1110 on the expander boards (drives V6; S9's 1110 bridge +
   delta list feeds this — DESIGN §S9, S13 notes)
3. ~~AE3 link: USB CDC or UART pins~~ **Decided [REV-20]: rpmsg + HP bridge over
   the VCP; HP keeps USB.**
4. ~~Bench Ethernet hop: UDP vs AF_PACKET~~ **Decided [REV-24]: UDP now (no T1L
   hardware exists); Sofar raw_eth branch on hardware arrival.**
5. **[NEW]** RTC backend for the AE3 (gates BUILD-4 time sync) — pick when
   BUILD-4 starts.

---

## 10. Change log

### v2 → v3 (this revision — project-context review)

| REV | v2 said | v3 says | Why |
|---|---|---|---|
| REV-20 | BUILD-2: HE claims the USB controller, custom CDC stack; V12 = critical-path verify | AE3 attaches via the INTERIM-2 trait device promoted to a real rpmsg wire + HP MicroPython bridge speaking the `uart_l2` codec over the VCP; Pi side = bm_sbc `--uart` on `/dev/ttyACM*` unchanged. V12 RESOLVED: HP owns USB, keep it. | v2's premise (no MicroPython) false: HE stack is runtime-loaded via stock HP firmware; USB carries REPL, ELF load, DFU flash, recovery. Bridge crash-persistence rule added (traceback → `/flash/bridge_crash.txt`). |
| REV-21 | Console = 3.3 V side-header UART + SWD | Debug = existing 4 KB HE debug ring via HP; no UART wired, no SWD (none on bench, D22); P4/P5 keep hardware-day roles | Zero wiring; proven surface |
| REV-22 | REV-2: "HE runs bm_core as compiled C" | + runtime-ELF via stock remoteproc, nothing flashed, HP untouched; standalone firmware out of scope | The missing fact behind v2's USB error |
| REV-23 | bm_core @ c15e6b1 (review rev) | All nodes build against d4ecc38 (our vendored + bm_sbc-pinned rev); all-nodes bumps only | Wire-format drift risk |
| REV-24 | V14 live option; V8 hat-count concern | Both AOS hats condemned 2026-08-12 — no T1L hardware at all; raw_eth branch preferred on arrival | Facts v2 lacked |
| REV-25 | (unknown) | New V15: HE image ~88% full; middleware slice size-audit gates BUILD-4-on-AE3 | Can invalidate scope |
| REV-26 | "size CDC buffering for sustained throughput" | New V16: stage-0 measured relay gate ≥2 Mbps sustained (target 2×); re-check with capture+encode live | S0 discipline; single-core python pump is the real unknown |
| REV-27 | BUILD-2 scope: flash config + `bm_rtc_*`; V11 open | V11 RESOLVED: FreeRTOS + lwIP 2.2.1 + pinned sys_arch; config/RTC = RAM stubs; persistence + RTC backend = hardware-day / BUILD-4 gate | Answered from `firmware/bm_he` |
| REV-28 | 2 Mbps stream via pub/sub, MTU 1514 | + chunked ≤ ~1400 B (S12 framing, BMV6 adapted); drop counters as loss ledger (D21 philosophy) | Makes the stream concrete |
| — | Light = "Pi 3/4"; console table | Light = nereus000, Telemetry = nereus001, direct eth0 cable; Pi 3/4 unneeded; bench check: eth0 free | Fixture reality |

### v1 → v2 (retained verbatim as breadcrumbs)

| REV | v1 said | v2 says | Error class |
|---|---|---|---|
| REV-1 | AE3 link-up: `link_change(1, true)` | `link_change(0, true)` — 0-based index; v1's call disables TX silently | Critical technical error |
| REV-2 | (implicit) HE core runs MicroPython | HE core runs bm_core as compiled C (per Nick) | Premise update from owner *(precision added in v3 REV-22)* |
| REV-3 | UART pins viable fallback; "not baud-limited" goal | 2 Mbps video ⇒ UART mathematically insufficient (≤1.6 Mbps raw @ 2 Mbaud); USB mandatory | Flawed logic *(transport re-shaped in v3 REV-20)* |
| REV-4 | (omitted) AE3 pin voltages/headers | UART3 RTS/CTS is 1.8 V B2B-only; 3.3 V side UARTs have no flow control | Omission *(console dropped in v3 REV-21)* |
| REV-5 | Nodes have 1/2/1 BM ports | All bm_sbc nodes report 15 ports; logical wiring is 1/2/1 | Technical error |
| REV-6 | (omitted) forwarding semantics | L2 forwards only `ff03::1`; unicast never forwarded; acceptance criteria restated | Omission |
| REV-7 | topology in `bcmp/` | `integrations/topology.c` | Wrong path |
| REV-8 | (omitted) gateway_ipc | Shipped uplink; BUILD-4 must use it (rule §6.5) | Omission |
| REV-9 | Init order missing 2 steps | Added `timer_callback_handler_init`, `bm_pubsub_init` | Omission |
| REV-10 | Stages 0–1 build a multi-process test | `scripts/validate.sh` + `multinode_test.sh` already ship and pass CI | Omission |
| REV-11 | V1: docs-vs-code port number conflict | Port 15 holds only because `num_ports()` is constant; made an invariant | Right answer, wrong reason |
| REV-12 | VPD fires link_change on `enable()`; AE3 `retry_negotiation` stubbed false | Code deliberately defers link-up to `retry_negotiation()`; AE3 must do the same | Inverted reading of code |
| REV-13 | Rate limiter surfaces backpressure bugs | L2 drops silently on queue overflow; limiter hides it; add drop counter | Flawed logic |
| REV-14 | (implicit) MTU consistent | Serial 1522 vs UDP 1514 mismatch; standardized on 1514 | Latent technical error |
| REV-15 | `callbacks->power` = "ADIN power rail" no-op to wire | Never assigned on bench; don't touch | Technical error (minor) |
| REV-16 | Worst-case wire ≈1535 B | 1536 B (`COBS_ENCODE_MAX(1528)+1`) | Arithmetic |
| REV-17 | BUILD-2 = one netdev file | + OS shim, IP stack, flash config, RTC | Understated scope *(resolved in v3 REV-27)* |
| REV-18 | BUILD-3 = add TOML keys | `runtime.cpp` factory refactor; singleton + callbacks-sharing constraints | Understated scope |
| REV-19 | Discourse thread cited as lineage | Unrelated 2023 prototype; replaced with `agents.md` | Miscitation |
