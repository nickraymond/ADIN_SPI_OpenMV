#!/usr/bin/env bash
# pi/bm_bench/demo_up.sh — stage the AE3 for the S17 demo, one command.
#
# Run ON nereus000:  ~/ADIN_SPI_OpenMV/pi/bm_bench/demo_up.sh [--scene ref|sensor]
#
# --scene ref  puts the bridge in reef-reference mode (S18 matrix): the
# encoder is fed the stored S0 reef image for the commanded mode instead
# of the dark bench scene. The scene key is written EVERY run (default
# sensor), so a matrix session can never leak ref mode into the next
# demo day. Ref assets are staged idempotently (size-checked); if /flash
# is too tight for the 5.2 MB raw set the 1.1 MB q95 JPEG set is staged
# instead and the bridge's loader falls back to it by name.
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

SCENE="sensor"
if [[ "${1:-}" == "--scene" ]]; then
  SCENE="${2:-}"
  case "$SCENE" in ref|sensor) ;; *) fail "--scene takes ref|sensor" ;; esac
elif [[ -n "${1:-}" ]]; then
  fail "unknown argument: $1 (usage: demo_up.sh [--scene ref|sensor])"
fi

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

# S18: keep /flash's bridge code in step with the checkout (this is how
# the 20 s REINIT_MIN_QUIET_MS build finally deploys — B2 left the 6 s
# build on the board). sha16 compare, copy only on mismatch, re-verify.
board_sha() {
  mpremote connect "$P" exec \
    "import hashlib; h=hashlib.sha256(); h.update(open('/flash/$1','rb').read()); print(h.digest().hex()[:16])" \
    2>/dev/null || echo "missing"
}
for f in bm_bridge.py uart_codec.py; do
  WANT=$(sha256sum "$REPO/firmware/bm_bridge/$f" | cut -c1-16)
  GOT=$(board_sha "$f")
  if [[ "$GOT" != *"$WANT"* ]]; then
    mpremote connect "$P" cp "$REPO/firmware/bm_bridge/$f" ":/flash/$f" >/dev/null
    GOT=$(board_sha "$f")
    [[ "$GOT" == *"$WANT"* ]] || fail "$f sha $GOT != $WANT after copy"
    echo "$f SYNCED to /flash ($WANT)"
  else
    echo "$f current on /flash ($WANT)"
  fi
done

# S18 reef-matrix: ref-scene staging, idempotent and size-checked. The
# raw set is byte-comparable to the S0 encode table; the JPEG set is the
# flash-tight fallback (bridge loads raw first by name, then .jpg).
ASSETS="$REPO/bench/assets/ref_scene"
RAW_SET="ref_color_320x200.bmp ref_mono_320x200.pgm ref_color_640x400.bmp ref_mono_640x400.pgm ref_color_1280x800.bmp ref_mono_1280x800.pgm"
JPG_SET="ref_color_320x200.jpg ref_mono_320x200.jpg ref_color_640x400.jpg ref_mono_640x400.jpg ref_color_1280x800.jpg ref_mono_1280x800.jpg"
[[ -d "$ASSETS" ]] || fail "$ASSETS missing — repo checkout stale?"

board_inventory() {
  mpremote connect "$P" exec '
import os
try:
    os.mkdir("/flash/ref_scene")
except OSError:
    pass
st = os.statvfs("/flash")
print("FREE:%d" % (st[0] * st[3]))
for f in os.listdir("/flash/ref_scene"):
    try:
        print("HAVE:%s:%d" % (f, os.stat("/flash/ref_scene/" + f)[6]))
    except OSError:
        pass'
}

declare -A HAVE LOCAL
FREE=0
while IFS= read -r line; do
  line="${line//$'\r'/}"   # mpremote output is CRLF — the documented trap
  case "$line" in
    FREE:*) FREE="${line#FREE:}" ;;
    HAVE:*) rest="${line#HAVE:}"; HAVE["${rest%%:*}"]="${rest##*:}" ;;
  esac
done < <(board_inventory)

for f in $RAW_SET $JPG_SET; do
  LOCAL["$f"]=$(stat -c%s "$ASSETS/$f") || fail "$ASSETS/$f missing"
done

# A wrong-size copy of an EXPECTED file is a truncated stage; remove it
# so the bridge's raw-first preference can never pick up a corrupt file.
for f in $RAW_SET $JPG_SET; do
  if [[ -n "${HAVE[$f]:-}" && "${HAVE[$f]}" != "${LOCAL[$f]}" ]]; then
    echo "ref_scene/$f wrong size (${HAVE[$f]} != ${LOCAL[$f]}) — removing"
    mpremote connect "$P" fs rm ":/flash/ref_scene/$f" >/dev/null
    unset "HAVE[$f]"
  fi
done

need_bytes() {   # bytes still to copy for the given set
  local total=0 f
  for f in $1; do
    [[ -n "${HAVE[$f]:-}" ]] || total=$((total + LOCAL[$f]))
  done
  echo "$total"
}

NEED_RAW=$(need_bytes "$RAW_SET")
NEED_JPG=$(need_bytes "$JPG_SET")
MARGIN=262144    # keep a little flash free for traces + crash files
if (( FREE > NEED_RAW + MARGIN )); then
  PICK_SET="$RAW_SET"; PICK_NAME="raw (BMP/PGM)"
elif (( FREE > NEED_JPG + MARGIN )); then
  PICK_SET="$JPG_SET"; PICK_NAME="q95 JPEG fallback"
  echo "NOTE: /flash too tight for the raw set (free $FREE, need $NEED_RAW)"
else
  fail "/flash too tight even for the JPEG ref set (free $FREE, need $NEED_JPG)"
fi

COPIED=0
for f in $PICK_SET; do
  if [[ -z "${HAVE[$f]:-}" ]]; then
    mpremote connect "$P" cp "$ASSETS/$f" ":/flash/ref_scene/$f" >/dev/null
    COPIED=$((COPIED + 1))
  fi
done
if (( COPIED > 0 )); then
  # trust artifacts: re-inventory and verify every file of the chosen set
  declare -A HAVE2
  while IFS= read -r line; do
    line="${line//$'\r'/}"
    case "$line" in
      HAVE:*) rest="${line#HAVE:}"; HAVE2["${rest%%:*}"]="${rest##*:}" ;;
    esac
  done < <(board_inventory)
  for f in $PICK_SET; do
    [[ "${HAVE2[$f]:-}" == "${LOCAL[$f]}" ]] || \
      fail "ref_scene/$f size ${HAVE2[$f]:-absent} != ${LOCAL[$f]} after copy"
  done
fi
echo "ref scene staged: $PICK_NAME ($COPIED copied)"

# Scene key written EVERY run (default sensor) so ref mode cannot leak
# into the next session. Merge, don't clobber — cfg carries one-shots too.
GOT=$(mpremote connect "$P" exec "
import json
try:
    cfg = json.load(open('/flash/bridge_cfg.json'))
except Exception:
    cfg = {}
cfg['scene'] = '$SCENE'
open('/flash/bridge_cfg.json', 'w').write(json.dumps(cfg))
print('cfg-scene-ok:' + cfg['scene'])")
[[ "$GOT" == *"cfg-scene-ok:$SCENE"* ]] || fail "bridge_cfg scene write failed: $GOT"
echo "bridge scene: $SCENE"

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
