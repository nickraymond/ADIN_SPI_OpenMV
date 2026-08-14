# bm_bridge — S14 relay bench (BENCHSPEC V16) + the uart_l2 codec

The codec (`uart_codec.py`) is the permanent piece: bm_sbc's `uart_l2`
wire format (COBS + CRC-32C + 2-byte BE length, `0x00` delimiter),
byte-exact against Sofar's C implementation (golden vectors in
`host_test/test_uart_codec.py`), dual-runtime (MicroPython viper on the
AE3 HP core / CPython on Pi + host tests). The S16 bridge reuses it
as-is.

The rest is the S14 bench: can one HP MicroPython loop relay HE rpmsg
traffic onto the USB VCP with real framing at ≥2 Mbps sustained?

| File | Runs on | Role |
|---|---|---|
| `uart_codec.py` | AE3 HP + Pi + CPython | the codec (permanent) |
| `s14_relay_pump.py` | AE3 HP | rung service: B (local gen) / C (HE relay) / crc c·z·n |
| `main_s14.py` | AE3 (as `/flash/main.py`) | boot launcher + crash persistence |
| `../../bench/s14_relay_counter.py` | Pi (nereus000) | orchestrator + receiver ledger + gate verdict |

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

## Restore the fixture (ALWAYS, at session end)

`/flash/main.py` must go back to the S6 baseline service (byte-identical
to `firmware/ae3_usb/main.py` — verified by sha256 before the swap):

```bash
scp firmware/ae3_usb/main.py pi@nereus000:/tmp/main_ae3usb.py
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00; \
  mpremote connect $P cp /tmp/main_ae3usb.py :/flash/main.py + rm :/flash/s14_crash.txt && mpremote connect $P reset'
```

Then re-verify the S6 USB baseline per the established custom
(`bench/usb_stream_bench.py`).
