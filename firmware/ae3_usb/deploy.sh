#!/usr/bin/env bash
# deploy.sh — copy the vendored AE3 USB capture/stream service to the board.
#
# Usage (on the Pi the AE3 is plugged into):
#   firmware/ae3_usb/deploy.sh                 # auto-find the OpenMV port
#   firmware/ae3_usb/deploy.sh /dev/serial/by-id/usb-OpenMV_...-if00
#
# Copies all board files in ONE mpremote connection (chained with '+'),
# shared modules first and main.py LAST, then resets the board so the
# service starts. See README.md in this directory for provenance.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$PATH:$HOME/.local/bin"

PORT="${1:-auto}"
if [ "$PORT" = "auto" ]; then
  PORT="$(ls /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*-if00 2>/dev/null | head -1 || true)"
  [ -n "$PORT" ] || {
    echo "!! deploy: no OpenMV camera under /dev/serial/by-id/ (usb-OpenMV_OpenMV_Camera_*-if00)" >&2
    echo "   Is the AE3 plugged in? (N.B. /dev/ttyACM0 may be the N6 — use the by-id path.)" >&2
    exit 1
  }
fi

command -v mpremote >/dev/null || {
  echo "!! deploy: mpremote not found (pip install --user mpremote)" >&2
  exit 1
}

# Best-effort: break a running capture service to the REPL so mpremote's
# raw-REPL entry doesn't race the service's stdin reader. Harmless if the
# board is already at a REPL (or pyserial is missing).
python3 - "$PORT" <<'PY' 2>/dev/null || true
import sys, time
try:
    import serial
except ImportError:
    sys.exit(0)
s = serial.Serial(sys.argv[1], 115200, timeout=0.5)
s.write(b"\x03\x03")
time.sleep(0.5)
s.close()
PY

echo "deploying to $PORT"
mpremote connect "$PORT" \
  cp "$DIR/command_protocol.py" :command_protocol.py + \
  cp "$DIR/device_info.py"      :device_info.py + \
  cp "$DIR/capture_service.py"  :capture_service.py + \
  cp "$DIR/board_config.py"     :board_config.py + \
  cp "$DIR/boot.py"             :boot.py + \
  cp "$DIR/main.py"             :main.py + \
  reset
echo "deployed; board resetting — service up in ~2 s"
