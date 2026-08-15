#!/usr/bin/env bash
# pi/bm_bench/demo_up.sh — stage the AE3 for the S17 demo, one command.
#
# Run ON nereus000:  ~/ADIN_SPI_OpenMV/pi/bm_bench/demo_up.sh
#
# Exists because the AE3 always goes back to the S6 fixture at session
# end (standing rule), so every demo day starts with: swap main.py to
# the bridge launcher, warm reset, wait for the by-id settle. This
# script does exactly that and verifies each step (trust artifacts).
#
# After it prints READY, start the apps in order:
#   nereus000:  S17_ROLE=light ~/bm_sbc_s15/build/all/bm_sbc_bench_apps --init ~/bm_bench/light.toml
#   nereus001:  S17_ROLE=telemetry BM_SBC_GATEWAY_IPC=/tmp/s17_ipc.sock \
#               ~/bm_sbc_s15/build/all/bm_sbc_bench_apps --init ~/bm_bench/telemetry.toml
# then type `stream 2.0 15 60` at the Telemetry CLI and open
# http://nereus001:8080/stream.

set -euo pipefail
export PATH="$PATH:$HOME/.local/bin"

P=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00
REPO="$HOME/ADIN_SPI_OpenMV"
LAUNCHER="$REPO/firmware/bm_bridge/main_bridge.py"
# sha16 of firmware/bm_bridge/main_bridge.py (the bridge launcher)
WANT_MAIN="170e637ce5d8c8bb"

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -f "$LAUNCHER" ]] || fail "$LAUNCHER missing — repo checkout stale?"
[[ -e "$P" ]] || fail "AE3 not on USB at $P (unstick ladder: ae3-usb-unstick skill)"

# A running bench_apps holds the tty via bm_sbc — stop it first.
if ps -eo args | grep "bm_sbc_s15/build/all" | grep -vq grep; then
  fail "a bm_sbc app is running on this Pi — Ctrl-C it first"
fi

# If a previous bridge is still alive, mpremote can't attach; the fix is
# 30+ s of zero port contact (its quiet-exit), so don't retry in a loop.
if ! mpremote connect "$P" exec "print(1)" >/dev/null 2>&1; then
  fail "board busy (bridge still running?) — wait 40 s untouched, run again"
fi

# Board must carry the staged S17 files (bm_he.elf etc. stay resident).
mpremote connect "$P" exec '
import os
need = ("bm_he.elf", "bm_bridge.py", "uart_codec.py")
have = set(os.listdir("/flash"))
missing = [f for f in need if f not in have]
print("MISSING:" + ",".join(missing) if missing else "staged-files-ok")
' | grep -q "staged-files-ok" || fail "S17 files missing on /flash — run the README §S17 deploy first"

mpremote connect "$P" cp "$LAUNCHER" :/flash/main.py >/dev/null

GOT=$(mpremote connect "$P" exec \
  'import hashlib; h=hashlib.sha256(); h.update(open("/flash/main.py","rb").read()); print(h.digest().hex()[:16])')
[[ "$GOT" == *"$WANT_MAIN"* ]] || fail "main.py sha $GOT != $WANT_MAIN after copy"
echo "bridge launcher staged (sha $WANT_MAIN)"

mpremote connect "$P" reset >/dev/null 2>&1 || true

# by-id settle: absent -> present -> hold (bench-earned dance).
for _ in $(seq 1 20); do [[ -e "$P" ]] || break; sleep 0.5; done
for _ in $(seq 1 30); do [[ -e "$P" ]] && break; sleep 0.5; done
[[ -e "$P" ]] || fail "by-id never came back after reset"
sleep 3
[[ -e "$P" ]] || fail "by-id did not settle"

echo "READY: bridge booted (phase 1, waiting for the Light node)."
echo "Start Light on THIS Pi, then Telemetry on nereus001 (commands in"
echo "this script's header). Reminder: the fixture-restore rule means"
echo "you run this script at the START of every demo day."
