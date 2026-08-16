#!/usr/bin/env bash
# pi/bm_bench/bench-ctl.sh — one request to the Telemetry node's control
# socket (S18 bite B), printed as JSON.
#
# Run ON nereus001:
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh status
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh capture 50 hd color
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh stream 2.0 15 60 50 vga color
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/bench-ctl.sh '{"cmd":"capture","save":false}'
#
# THE DIFFERENCE FROM bm-cmd.sh: that one appends to the operator FIFO and
# the answer comes back on the journal. This one gets a JSON reply here, and
# `status` returns the live receiver ledger — which is what the S18 web tool
# (bite C) polls. Both drive the same handlers inside the app, so either is
# a legitimate way to run a demo.
#
# Camera and light commands are ASYNCHRONOUS: the reply says "accepted", not
# "done". The camera's own answer lands in the next `status` (cam_reply) and
# in the journal as CAM_REPLY.
set -euo pipefail

UNIT=bm-telemetry.service
SOCK=${S18_CTL_SOCK:-/run/bm/bench.sock}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ $# -ge 1 ]] || fail "usage: $0 status | capture [q] [res] [pf] | stream <mbps> <fps> <secs> [q] [res] [pf] | stop | cam-status | light <level> | strobe <on> <off> <n> | '<json>'"

# Same rule as bm-cmd.sh: a request to a socket nobody reads must not look
# like a request that worked. Check the reader before sending.
systemctl is-active --quiet "$UNIT" ||
  fail "$UNIT is not active — sudo systemctl start $UNIT"
[[ -S "$SOCK" ]] ||
  fail "$SOCK missing — the app creates it at startup; check journalctl -u $UNIT | grep ctl:"

exec python3 "$HERE/bench_ctl.py" "$@"
