# bm_bench — S15 two-Pi Bristlemouth bench (BENCHSPEC Stage 1)

Real `bm_sbc` nodes on both Pis, linked by the direct eth0↔eth0 cable over
the new `udp_port_device` (BUILD-1) selected via the transport factory
(BUILD-3). Dev access rides wlan0/tailnet throughout; the bench subnet is
`10.42.0.0/24`, no gateway, no DHCP.

| Node | Host | Bench IP | Node ID |
|---|---|---|---|
| Telemetry | nereus001 | 10.42.0.1 | `0xbe9c000000000001` |
| Light | nereus000 | 10.42.0.2 | `0xbe9c000000000002` |
| Camera (S16) | AE3 via Light's USB | — | `0xbe9c000000000003` (reserved) |

Node IDs fixed 2026-08-14 (Nick); never reuse.

## Code + pins (REV-23 discipline — track carefully)

Everything builds from **forks with pinned revs**; upstream is never
tracked live:

- `bm_sbc` fork branch **`feature/udp-transport`** = upstream `17ea904`
  + 2 commits (transport factory; udp_port_device + stream_bench + tests).
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

## Demo 1 — neighbors + BCMP ping across the cable

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

## Demo 2 — rate limiter measured (receiver-side ledger, D21)

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

## Demo 3 — dev access intact

During any run, on either Pi:

```bash
ip route | head -1
```

Default route stays `via 192.168.86.1 dev wlan0`; the SSH session you are
typing in is itself the liveness proof.

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
