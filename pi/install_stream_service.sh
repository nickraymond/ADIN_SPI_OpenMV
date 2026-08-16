#!/usr/bin/env bash
# install_stream_service.sh — install one bench systemd unit.
#
# S3/S6 stream path (installed ENABLED — these are the standing fixture):
#   sudo pi/install_stream_service.sh receiver   # on nereus001: stream_server
#   sudo pi/install_stream_service.sh sender     # on nereus000: t1l_sender
#   sudo pi/install_stream_service.sh shim       # on nereus001: chunk_shim (S6)
#
# BM bench nodes (installed DISABLED — S18 bite D):
#   sudo pi/install_stream_service.sh light      # on nereus000
#   sudo pi/install_stream_service.sh telemetry  # on nereus001
#   sudo pi/install_stream_service.sh bench-web  # on nereus001 (S18 bite C)
#
# The two BM nodes are deliberately NOT enabled at boot. bm-light opens
# the AE3's CDC port, and a node that grabs it at every boot fights
# mpremote, demo_up.sh and firmware flashing — the dev loop has to win by
# default. You start them per session with `systemctl start`.
#
# Idempotent: re-running reinstalls the unit (and restarts it, for the
# enabled-at-boot roles).
set -euo pipefail

ROLE="${1:-}"
case "$ROLE" in
  receiver)  UNIT=t1l-stream-server.service; AUTOSTART=yes ;;
  sender)    UNIT=t1l-sender.service;        AUTOSTART=yes ;;
  shim)      UNIT=t1l-chunk-shim.service;    AUTOSTART=yes ;;
  light)     UNIT=bm-light.service;          AUTOSTART=no  ;;
  telemetry) UNIT=bm-telemetry.service;      AUTOSTART=no  ;;
  bench-web) UNIT=bench-web.service;         AUTOSTART=no  ;;
  *) echo "usage: $0 receiver|sender|shim|light|telemetry|bench-web" >&2; exit 1 ;;
esac

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/services/$UNIT"
[ -f "$SRC" ] || { echo "!! missing $SRC" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "!! run with sudo" >&2; exit 1; }

install -m 644 "$SRC" "/etc/systemd/system/$UNIT"
systemctl daemon-reload

if [ "$AUTOSTART" = "yes" ]; then
  systemctl enable --now "$UNIT"
  sleep 2
  systemctl --no-pager --lines=3 status "$UNIT" || {
    echo "!! $UNIT failed to start — journalctl -u $UNIT" >&2
    exit 1
  }
  echo "OK: $UNIT installed and running"
  exit 0
fi

# Disabled-at-boot roles: install only, and undo any earlier enable so
# re-running this script is genuinely idempotent.
systemctl disable "$UNIT" >/dev/null 2>&1 || true
STATE="$(systemctl is-enabled "$UNIT" 2>/dev/null || true)"
[ "$STATE" != "enabled" ] || {
  echo "!! $UNIT still reports enabled after disable" >&2
  exit 1
}
echo "OK: $UNIT installed, NOT enabled at boot (state: $STATE)"
echo "    start:  sudo systemctl start ${UNIT%.service}"
echo "    watch:  journalctl -u ${UNIT%.service} -f"
echo "    check:  pi/bm_bench/chain_status.sh"
