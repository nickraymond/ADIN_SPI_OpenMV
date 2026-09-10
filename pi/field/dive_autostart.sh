#!/usr/bin/env bash
# dive_autostart.sh -- power on the rig and it records. Runs once at boot.
#
# WHY (Nick, 2026-09-10): "I just need to be able to turn on the camera and it
# start recording video from all three cameras." On the boat there is no
# keyboard and often no iPad in hand when the rig goes in the water, so the
# power switch has to be the record button.
#
# HOW: this presses the SAME Start the workbench card presses -- POST
# /api/start for the one dive recipe -- so the page shows the run as LIVE,
# its Stop button still works, the dashboard still counts the segment down,
# and the board lock is owned by the workbench exactly as if Nick had tapped
# it. Nothing here touches a board or a camera directly (one owner per port).
#
# Opt out on the bench without editing anything: touch ~/.no_dive_autostart
# and the unit logs that it stood down.
#
# Installed by:  sudo pi/install_stream_service.sh autostart
# Watch:         journalctl -u dive-autostart -b
set -uo pipefail

RECIPE="${DIVE_RECIPE:-ci-record}"
WB="${WORKBENCH_URL:-http://127.0.0.1:8088}"
OFF_FLAG="${DIVE_AUTOSTART_OFF:-$HOME/.no_dive_autostart}"
WAIT_WB_S="${WAIT_WB_S:-120}"        # workbench answering
WAIT_BOARD_S="${WAIT_BOARD_S:-120}"  # declared board enumerated and ready
WAIT_LIVE_S="${WAIT_LIVE_S:-90}"     # runner reaches live after Start

say() { printf 'dive-autostart: %s\n' "$*"; }

if [ -e "$OFF_FLAG" ]; then
  say "standing down: $OFF_FLAG exists (remove it to record at boot)"
  exit 0
fi

jget() {  # jget <url> <python expr over d>   -- empty string on any failure
  curl -fsS -m 5 "$1" 2>/dev/null | python3 -c "import json,sys
try:
    d=json.load(sys.stdin); print($2)
except Exception:
    print('')" 2>/dev/null
}

# 1. The workbench must be up. It is a separate unit; After= orders the start
#    but does not wait for the socket, so poll the API it will serve.
t0=$(date +%s)
while :; do
  STATE="$(jget "$WB/api/runner" 'd.get("state","")')"
  [ -n "$STATE" ] && break
  if [ $(( $(date +%s) - t0 )) -ge "$WAIT_WB_S" ]; then
    say "FAIL: workbench at $WB never answered in ${WAIT_WB_S}s -- not recording"
    exit 1
  fi
  sleep 3
done
say "workbench up, runner state: $STATE"

case "$STATE" in
  idle|failed) ;;
  *) say "a demo is already running ($STATE) -- leaving it alone"; exit 0 ;;
esac

# 2. The recipe's declared board has to have enumerated. USB boards come up a
#    few seconds after the workbench on a Zero 2 W; the workbench refuses a
#    Start whose board is missing, so wait for preflight to say ready.
t0=$(date +%s)
while :; do
  READY="$(jget "$WB/api/preflight" '",".join(b.get("label","") for b in d.get("boards",[]) if b.get("state")=="ready")')"
  case ",$READY," in *,N6,*) break ;; esac
  if [ $(( $(date +%s) - t0 )) -ge "$WAIT_BOARD_S" ]; then
    say "FAIL: N6 not ready after ${WAIT_BOARD_S}s (ready: '${READY:-none}') -- not recording"
    exit 1
  fi
  sleep 5
done
say "boards ready: $READY"

# 3. Press Start. Same endpoint, same body the page sends.
RESP="$(curl -fsS -m 30 -X POST -H 'Content-Type: application/json' \
          -d "{\"name\":\"$RECIPE\"}" "$WB/api/start" 2>&1)" || {
  say "FAIL: POST /api/start refused: $RESP"
  exit 1
}
say "start accepted: $RESP"

# 4. Trust the artifact, not the 200: the runner has to actually go LIVE.
t0=$(date +%s)
while :; do
  STATE="$(jget "$WB/api/runner" 'd.get("state","")')"
  case "$STATE" in
    live) say "LIVE: $RECIPE is recording"; exit 0 ;;
    failed|idle)
      ERR="$(jget "$WB/api/runner" 'd.get("error") or ""')"
      say "FAIL: runner went '$STATE' after Start: ${ERR:-no error text}"
      exit 1 ;;
  esac
  if [ $(( $(date +%s) - t0 )) -ge "$WAIT_LIVE_S" ]; then
    say "FAIL: runner stuck in '$STATE' ${WAIT_LIVE_S}s after Start"
    exit 1
  fi
  sleep 3
done
