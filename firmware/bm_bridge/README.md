# bm_bridge — the S16 HP bridge (BUILD-2b) + S14 relay bench + uart_l2 codec

Two generations live here. The **S16 bridge** (`bm_bridge.py` +
`main_bridge.py`) is the production piece: it moves L2 frames between
the HE's bm_core stack (rpmsg endpoint `bm-wire`, `firmware/bm_he`) and
the USB VCP in bm_sbc's `uart_l2` framing, making the AE3 a real BM node
behind the Light Pi's stock `--uart` gateway. The S14 pump
(`s14_relay_pump.py`) stays as the throughput bench that gated it (V16).

The codec (`uart_codec.py`) is shared: bm_sbc's `uart_l2` wire format
(COBS + CRC-32C + 2-byte BE length, `0x00` delimiter), byte-exact
against Sofar's C implementation (golden vectors in
`host_test/test_uart_codec.py`), dual-runtime (MicroPython viper on the
AE3 HP core / CPython on Pi + host tests).

| File | Runs on | Role |
|---|---|---|
| `uart_codec.py` | AE3 HP + Pi + CPython | the codec (permanent) |
| `bm_bridge.py` | AE3 HP | **S16 bridge**: rpmsg ↔ VCP duplex pump (`BridgeCore` = host-testable data plane) |
| `main_bridge.py` | AE3 (as `/flash/main.py`) | S16 boot launcher + crash persistence (`/flash/bridge_crash.txt`) |
| `s14_relay_pump.py` | AE3 HP | S14 rung service: B (local gen) / C (HE relay) / crc c·z·n |
| `main_s14.py` | AE3 (as `/flash/main.py`) | S14 boot launcher + crash persistence |
| `../../bench/s14_relay_counter.py` | Pi (nereus000) | S14 orchestrator + receiver ledger + gate verdict |

## S16 bridge operation

- Deploy `bm_bridge.py`, `uart_codec.py`, the promoted `bm_he.elf`
  (→ `/flash/bm_he.elf`) and `main_bridge.py` (→ `/flash/main.py`); the
  optional `/flash/bridge_cfg.json` arms one-shot triggers:
  `{"stream": {"mbps": 2.0, "payload": 1400, "secs": 600, "delay": 10},
  "ping": {"target": "0xbe9c000000000001", "delay": 5}}` (delays count
  from link-up).
- Service entry = warm `mpremote reset` (rule 1 below). On boot the
  bridge loads the HE ELF once, waits for `bm-wire`, then **holds
  WCMD_LINK down until the first bytes arrive on the VCP** — bm_sbc's
  gateway heartbeats as soon as it opens the tty, and until link-up the
  HE transmits nothing, so the pipe is quiet while unowned. Start order
  therefore: bridge first, then bm_sbc on the Pi.
- While the bridge runs the VCP is a data pipe: REPL unavailable, zero
  prints. State goes to `/flash/bridge_trace.txt` (30 s stats snapshots,
  final ledger, HE debug-ring dump at exit).
- **Stop model (found live, first chain bring-up):** MicroPython scans
  inbound console bytes for 0x03 and COBS frames contain it freely —
  bm_sbc's first heartbeat killed the pump with an injected
  KeyboardInterrupt. The bridge therefore runs with
  `micropython.kbd_intr(-1)`: **ctrl-C / mpremote cannot stop a linked
  bridge.** It stops ITSELF: 30 s of VCP silence after link-up (= the
  Pi side is gone; heartbeats come every 10 s while alive) or 10 min
  with no Pi attach at all → clean exit, kbd_intr restored, HE
  stopped, board at REPL. So: stop bm_sbc, wait ~30 s, then attach.
  One bridge lifetime per demo — the cfg one-shots re-arm on warm
  reset. `sudo uhubctl -l 3 -p 1 -a cycle -d 3` stays the hammer
  (cold boot = REPL on this build).
- Any exit persists to `/flash/bridge_crash.txt`, announces link-down
  to the HE, and stops the HE (end-of-life stop; next session
  warm-resets first). If the bridge died harder (stale HE still
  running at next boot), it refuses to start and names the recovery
  pair.
- Pi-side failure sequence: link death → stop bm_sbc → wait ~30 s →
  `mpremote` attach → read `/flash/bridge_crash.txt` +
  `/flash/bridge_trace.txt`.

## Measured results (2026-08-14, all receiver-side, 0 gaps, 0 CRC errors)

- codec alone on HP: crc32c 7.34 MB/s; full frame_encode 10.1 Mbps (1412 B l2)
- rung B (framing+USB only): **13.1 Mbps**
- rung C (full relay HE→HP→USB→Pi): **5.5 Mbps** — rpmsg-drain-bound
  (matches he_spike's 5.6 Mbps HE→HP ceiling); agg=3: 5.4 Mbps
- rung E: crc32c vs builtin crc32 vs none — **identical** 5.55 Mbps
  (viper CRC-32C is free at this rate)
- rung D gate (600 s, agg 3, ≥2 Mbps): see DEV_LOG S14 entry

## Bench-earned operating rules (violate these and it "hangs")

1. **Cold boot (uhubctl / replug) does NOT run `main.py` on this build.**
   Enter the service with a warm reset: `mpremote connect <by-id> reset`.
2. **mpremote attach kills the service** (injected KeyboardInterrupt —
   by design). pyserial attach is harmless. Never point mpremote at the
   board while a rung is running.
3. **HE lifecycle:** loaded once per service boot, never stopped
   mid-life (a second stop→load cycle in one boot loses the ns
   announcement); every rung C ends with a burst drain so HE is idle
   whenever the service can die. Starting over a stale-but-idle HE
   works; over a stale mid-burst HE it blocks in C. Worst-case recovery:
   `sudo uhubctl -l 3 -p 1 -a cycle -d 3` then a warm reset.
4. **Crash visibility:** while the Pi owns the VCP a traceback is
   invisible — the launcher persists every exit cause to
   `/flash/s14_crash.txt`. Read it FIRST when anything misbehaves.
5. After `mpremote reset` the by-id symlink lingers, drops, reappears:
   wait for absent→present→3 s settle before opening the port.

## Deploy (from the repo checkout, Mac)

```bash
scp firmware/bm_bridge/{s14_relay_pump.py,main_s14.py,uart_codec.py} bench/s14_relay_counter.py pi@nereus000:/tmp/
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  mkdir -p ~/s14 && cp /tmp/s14_relay_counter.py /tmp/uart_codec.py ~/s14/ && \
  mpremote connect $P cp /tmp/s14_relay_pump.py :/flash/s14_relay_pump.py + cp /tmp/uart_codec.py :/flash/uart_codec.py + cp /tmp/main_s14.py :/flash/main.py && \
  mpremote connect $P reset'
```

## Run (on nereus000)

```bash
cd ~/s14
python3 s14_relay_counter.py --rung B --secs 10            # framing+USB only
python3 s14_relay_counter.py --rung C --secs 60            # full relay
python3 s14_relay_counter.py --rung C --secs 60 --agg 3    # S16 chunk shape
python3 s14_relay_counter.py --rung C --secs 60 --crc z    # rung E diagnostic
python3 s14_relay_counter.py --rung C --secs 600 --agg 3 --gate 2.0   # RUNG D GATE
python3 s14_relay_counter.py --quit                        # service -> REPL
```

## S17 bite 0 — capture-relay bench (V16 re-check with capture+encode live)

The S17 pump (`s17_capture_pump.py` + `main_s17.py`) extends the S14
service with rungs **F** (relay + reef-image capture/encode paced at a
target fps) and **G** (F + the JPEG pushed down to HE via
BCMD_SINK_DATA — both rpmsg directions + VCP + camera in one loop, the
real BUILD-4 camera-path shape). Rungs B/C/E delegate to the S14 code
unchanged. Same protocol, banner `S17-PUMP ready`; the counter refuses
F/G against an S14 deploy. Reef facts: the encode source is
`bench/assets/ref_scene/ref_color_320x200.bmp` (S0 pipeline) staged at
`/flash` — the dim bench room compresses ~2× too well for an honest
bitrate (S0 finding). Sensor runs only when the reef fits on the GC
heap (`ref=heap` in the summary); `ref=fb` means encode-only — read the
summary, not assumptions.

Deploy (VCP GATE — displaces the fixture main.py, Nick's go required):

```bash
scp firmware/bm_bridge/{s17_capture_pump.py,s14_relay_pump.py,main_s17.py,uart_codec.py} bench/s14_relay_counter.py bench/assets/ref_scene/ref_color_320x200.bmp pi@nereus000:/tmp/
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  mkdir -p ~/s14 && cp /tmp/s14_relay_counter.py /tmp/uart_codec.py ~/s14/ && \
  mpremote connect $P cp /tmp/s17_capture_pump.py :/flash/s17_capture_pump.py + cp /tmp/s14_relay_pump.py :/flash/s14_relay_pump.py + cp /tmp/uart_codec.py :/flash/uart_codec.py + cp /tmp/ref_color_320x200.bmp :/flash/ref_color_320x200.bmp + cp /tmp/main_s17.py :/flash/main.py && \
  mpremote connect $P reset'
```

Run (on nereus000; order = one variable at a time):

```bash
cd ~/s14
python3 s14_relay_counter.py --rung C --secs 60 --agg 3              # relay-only regression (expect ~5.4)
python3 s14_relay_counter.py --rung F --secs 60 --agg 3              # + capture/encode @15 fps
python3 s14_relay_counter.py --rung G --secs 60 --agg 3              # + sink leg (full camera shape)
python3 s14_relay_counter.py --rung G --secs 600 --agg 3 --gate 2.0  # THE NUMBER (10 min sustained)
python3 s14_relay_counter.py --quit                                  # service -> REPL
```

The rung-G 600 s `mbps_l2` + `cap_fps` pair is the V16-with-capture
number BUILD-4's stream rate target commits against (target = measured
÷ 2, capped at 2.0 Mbps — D-entry at sprint end records the decision).

## Restore the fixture (ALWAYS, at session end)

`/flash/main.py` must go back to the S6 baseline service (byte-identical
to `firmware/ae3_usb/main.py`). **Restore from a quiet board state and
sha-verify AFTER the copy** — a swap attempted while the old service
holds the VCP can silently not land (found live). Cold-cycle first
(cold boot = REPL on this build), then:

```bash
scp firmware/ae3_usb/main.py pi@nereus000:/tmp/main_ae3usb.py
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  sudo -n uhubctl -l 3 -p 1 -a cycle -d 3 >/dev/null && sleep 8 && \
  mpremote connect $P cp /tmp/main_ae3usb.py :/flash/main.py && \
  mpremote connect $P exec "import hashlib; h=hashlib.sha256(); h.update(open(\"/flash/main.py\",\"rb\").read()); print(h.digest().hex()[:16])" && \
  mpremote connect $P reset'
```

Expect sha `55fa6ccfdd3f7f65` (repo `firmware/ae3_usb/main.py`). Then
re-verify the S6 USB baseline per the established custom:

```bash
ssh pi@nereus000 'python3 ADIN_SPI_OpenMV/bench/usb_stream_bench.py --modes QVGA:90 --seconds 20'
```
