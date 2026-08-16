#!/usr/bin/env bash
# pi/bm_bench/bm-cmd.sh — send one operator command to the Telemetry node.
#
# Run ON nereus001:
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bm-cmd.sh capture 50 hd color
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bm-cmd.sh stream 2.0 15 600
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bm-cmd.sh stop
#
# Replaces "type into the running bench_apps" from the S16/S17/S19 start
# order. The unit runs the app with a FIFO as stdin (see
# pi/services/bm-telemetry.service), so a command is one line appended to
# that FIFO; the reply comes back on the journal, not here:
#
#   journalctl -u bm-telemetry -f
#
# `help` lists the app's own commands.
set -euo pipefail

FIFO=/run/bm/telemetry.cmd
UNIT=bm-telemetry.service

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ $# -ge 1 ]] || fail "usage: $0 <command> [args...]   (try: $0 help)"

# A command written into a FIFO nobody reads looks exactly like a command
# that worked. Check the reader first.
systemctl is-active --quiet "$UNIT" ||
  fail "$UNIT is not active — sudo systemctl start $UNIT"
[[ -p "$FIFO" ]] ||
  fail "$FIFO missing or not a FIFO — the unit creates it; check journalctl -u $UNIT"

# The app holds the FIFO open read-write, so this open never blocks while
# it is alive. Bound it anyway: a hung write here would be indistinguishable
# from a hung bench.
timeout 3 sh -c "printf '%s\\n' \"\$1\" > \"\$2\"" _ "$*" "$FIFO" ||
  fail "write to $FIFO timed out — is $UNIT wedged? journalctl -u $UNIT -n 50"

echo "sent: $*"
echo "reply: journalctl -u $UNIT -f   (markers: CAM_REPLY / LIGHT_REPLY / TEL_STAT)"
