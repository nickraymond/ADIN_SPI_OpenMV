# firmware/ae3_usb — vendored AE3 USB capture/stream service

Vendored **unmodified** from `nereus-camera-test-rig` @ `f11befe` (checked out on
nereus000, copied 2026-08-10 for Sprint S3 bite 1). Same pattern as the vendored
kernel driver in `pi/drivers/adin1110/` (DESIGN.md D12): reuse the proven piece,
pin its provenance, change nothing without a reason recorded here.

| File | Origin (repo-relative) | Role |
|---|---|---|
| `main.py` | `openmv/ae3/main.py` | boot entry: USB CDC shim + JSON command dispatch loop |
| `boot.py` | `openmv/ae3/boot.py` | no-op boot hook (USB stays at firmware default) |
| `board_config.py` | `openmv/ae3/board_config.py` | AE3 facts: framesize/pixformat allowlists, stream defaults |
| `command_protocol.py` | `openmv/common/command_protocol.py` | wire format (JSON line + framed binary) — also imported by the host side (`pi/stream/usb_frame_source.py`), one source of truth |
| `capture_service.py` | `openmv/common/capture_service.py` | sensor config, `start_stream` framed-JPEG loop |
| `device_info.py` | `openmv/common/device_info.py` | `get_device_info` payload builder |

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
