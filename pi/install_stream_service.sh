#!/usr/bin/env bash
# install_stream_service.sh — install + start one S3 stream service via systemd.
#
#   sudo pi/install_stream_service.sh receiver   # on nereus001: stream_server
#   sudo pi/install_stream_service.sh sender     # on nereus000: t1l_sender
#   sudo pi/install_stream_service.sh shim       # on nereus001: chunk_shim (S6)
#
# Idempotent: re-running reinstalls the unit and restarts the service.
set -euo pipefail

ROLE="${1:-}"
case "$ROLE" in
  receiver) UNIT=t1l-stream-server.service ;;
  sender)   UNIT=t1l-sender.service ;;
  shim)     UNIT=t1l-chunk-shim.service ;;
  *) echo "usage: $0 receiver|sender|shim" >&2; exit 1 ;;
esac

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/services/$UNIT"
[ -f "$SRC" ] || { echo "!! missing $SRC" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "!! run with sudo" >&2; exit 1; }

install -m 644 "$SRC" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 2
systemctl --no-pager --lines=3 status "$UNIT" || {
  echo "!! $UNIT failed to start — journalctl -u $UNIT" >&2
  exit 1
}
echo "OK: $UNIT installed and running"
