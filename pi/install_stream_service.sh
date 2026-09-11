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
# S25 workbench (installed ENABLED — "fresh boot → page answers" is the point;
# bite 1 opens no serial port, so boot-time start cannot fight the dev loop):
#   sudo pi/install_stream_service.sh workbench  # on nereus000 (S25 bite 1)
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
  workbench) UNIT=workbench.service;         AUTOSTART=yes ;;
  # S29 field rig (nereus002-class). All three are enabled at boot: they are
  # bench-stability fixtures, not demos, and each one exists because its
  # absence cost this bench a session. See .claude/skills/field-rig-bringup.
  powersave) UNIT=wifi-powersave-off.service; AUTOSTART=yes ;;
  usb-msc)   UNIT=field-usb-msc-off.service;  AUTOSTART=yes ;;
  power-log) UNIT=field-power-log.service;    AUTOSTART=yes ;;
  # Always-on, review-only library on :8093 so the workbench can offer a
  # one-tap "Review dives" link instead of a start-a-recipe dance.
  review)    UNIT=video-review.service;       AUTOSTART=yes ;;
  # Belt to the powersave braces: re-asserts power_save off every minute and
  # logs only when it had to, so a re-enable leaves evidence.
  ps-guard)  UNIT=wifi-powersave-guard.timer; AUTOSTART=yes ;;
  # Power on = record. Presses the dive card's Start once the workbench and
  # the N6 are up; opt out on the bench with ~/.no_dive_autostart.
  autostart) UNIT=dive-autostart.service;     AUTOSTART=yes ;;
  # The rig as its own wifi network. Installed DISABLED: enable/disable is
  # the dashboard's AP toggle, and installing must not flip it.
  ap)        UNIT=nereus-ap.service;          AUTOSTART=no  ;;
  # Home wifi first, rig AP if none within 60 s. Enabled at boot: this is
  # the "never stranded at sea" guarantee (Nick, 2026-09-10).
  ap-fallback) UNIT=nereus-ap-fallback.service; AUTOSTART=yes ;;
  *) echo "usage: $0 receiver|sender|shim|light|telemetry|bench-web|workbench|powersave|usb-msc|power-log|review|ps-guard|autostart|ap|ap-fallback" >&2; exit 1 ;;
esac

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/services/$UNIT"
[ -f "$SRC" ] || { echo "!! missing $SRC" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "!! run with sudo" >&2; exit 1; }

install -m 644 "$SRC" "/etc/systemd/system/$UNIT"
systemctl daemon-reload

if [ "$AUTOSTART" = "yes" ]; then
  # enable --now STARTS a stopped unit but does NOT restart a running one, so
  # re-running this after a git pull left the OLD code serving while printing
  # OK. Measured 2026-09-08 on nereus000: the workbench kept a two-day-old
  # process alive and reported 11 recipes when 15 were on disk. Enable, then
  # restart unconditionally -- that is what "idempotent" has to mean when the
  # point of re-running is to pick up new code.
  systemctl enable "$UNIT"
  systemctl restart "$UNIT"
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
