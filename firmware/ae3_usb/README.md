# firmware/ae3_usb — vendored AE3 USB capture/stream service

Vendored from `nereus-camera-test-rig` @ `f11befe` (checked out on nereus000,
copied 2026-08-10 for Sprint S3 bite 1), with **one local patch** (the `reboot`
action — see §Known firmware crash). Same pattern as the vendored kernel driver
in `pi/drivers/adin1110/` (DESIGN.md D12): reuse the proven piece, pin its
provenance, record every change here.

| File | Origin (repo-relative) | Local changes |
|---|---|---|
| `main.py` | `openmv/ae3/main.py` | + `reboot` action handler |
| `boot.py` | `openmv/ae3/boot.py` | none |
| `board_config.py` | `openmv/ae3/board_config.py` | none |
| `command_protocol.py` | `openmv/common/command_protocol.py` | + `"reboot"` in `ALLOWED_ACTIONS` |
| `capture_service.py` | `openmv/common/capture_service.py` | none |
| `device_info.py` | `openmv/common/device_info.py` | none |

`command_protocol.py` is also imported by the host side
(`pi/stream/usb_frame_source.py`) — one source of truth for the wire format.

## Known firmware crash: one stream session per boot

Measured on the AE3 (fw **v1.28.0-49 / 2026-07-02**, sensor PAG7936, bench runs
2026-08-10): the **second `start_stream` session after a boot hard-crashes the
board** — no error response, USB CDC goes dead, and depending on the crash
flavor the board either drops off USB entirely (`error -71`, needs a physical
power cycle) or keeps enumerating with a dead CDC (recoverable with
`uhubctl -l 1 -p 2 -a cycle`). After a crash the firmware boots into a
safe-mode REPL and skips `main.py`.

Established by elimination, one variable at a time:

- First session per boot: works in every mode (QVGA/VGA/HD) — hundreds of
  frames, zero gaps, valid JPEGs.
- Command loop without sensor use: stable (repeated `get_device_info` fine,
  board alive 30+ s after a completed session).
- Second `start_stream` (any mode, same or different settings): crash, 2/2.
- MicroPython soft reset (Ctrl-D) does NOT clear the condition; a full
  `machine.reset()` DOES.
- S0's `bench/ae3_video_bench.py` did repeated `sensor.reset()` in one script
  via mpremote raw-REPL without crashing — the trigger involves the service
  context, not `sensor.reset()` alone. Root cause inside the firmware unknown
  (flagged in SPEC.md §Open questions; candidate OpenMV upstream report).

**Workaround (the local patch):** hosts send the `reboot` action between
sessions — the service replies `{"rebooting": true}` and calls
`machine.reset()` (~6 s round trip). `pi/stream/usb_frame_source.py:reboot_board()`
wraps this, with a REPL-reset fallback for the post-crash safe-mode state, and
`bench/usb_stream_bench.py` reboots before every mode.

Why: S3's first TODO is "AE3 → Pi 5 over USB (existing setup)" — this IS the
existing setup. The `start_stream` action pushes framed JPEGs (one JSON header
line with `seq`/`size_bytes`/dims, then exactly `size_bytes` of JPEG) until the
host sends any byte or `max_seconds` expires.

Deploy (from nereus000, AE3 on USB):

    firmware/ae3_usb/deploy.sh          # auto-finds the OpenMV by-id port
    firmware/ae3_usb/deploy.sh /dev/serial/by-id/usb-OpenMV_...-if00

Files go to the board root **flat** (the board imports them as top-level
modules), `main.py` last so a mid-deploy reset never launches a new main
against stale modules; then the board resets and the service starts.

Note: deploying replaces the board's previous `main.py` (as found 2026-08-10:
a 218-byte LED-blink stub, nothing of value). Board firmware at vendoring
time: OpenMV/MicroPython 1.28.0 on OPENMV_AE3 — `img.compress()` and the
legacy `sensor` API verified live on that firmware before vendoring.
