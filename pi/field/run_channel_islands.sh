#!/usr/bin/env bash
# Channel Islands dive recording: all four streams, in 5-minute segments.
#
# ONE recipe, TWO recorders, because the two halves of this rig have nothing
# in common but the card:
#   * the IMX708 is on CSI and is driven by picamera2 (imx_dive_recorder.py),
#     which is the only way to get a science stream and an H.264 proxy out of
#     one camera at once -- rpicam-vid's --codec takes exactly one value.
#   * the N6 and AE3 are USB boards pumped over mpremote (recorder.py), which
#     needs the venv interpreter (PEP 668) that the CSI side must not use.
# Merging them into one process would mean one interpreter serving both, and
# there isn't one. So they run side by side in this script's process group,
# which is what the workbench signals on Stop -- SIGINT reaches both.
#
# SEGMENTS: 5 minutes, Nick's call. The IMX recorder segments internally by
# rolling only its ENCODERS, so its white balance and focus survive a segment
# boundary. The board recorder has no such concept, so it is re-run per
# segment in the loop below; a board restart costs a second of footage and
# nothing else, because the boards carry no cross-segment state worth keeping.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

SEGMENT_S="${SEGMENT_S:-300}"
ROOTDIR="${RECORD_ROOT:-/home/pi/recordings}"
WB="${WB_MODE:-auto}"                 # auto = ordinary recording (card 1)
FOCUS="${FOCUS_MODE:-manual}"
LENS="${LENS_POSITION:-1.82}"         # dioptres; bm_cam_legacy bmcam000
JPEG_Q="${JPEG_Q:-90}"
BOARDS="${BOARDS:-N6,AE3}"

# Same interpreter problem, same solution as run_recorder.sh: mpremote lives
# in a venv and the system python cannot import it. Fail LOUDLY rather than
# starting a run that can never attach a board.
pick_venv() {
  for py in "${FIELD_PYTHON:-}" "$HOME/mpv/bin/python" "$(command -v python3 || true)"; do
    [ -n "$py" ] && [ -x "$py" ] || continue
    if "$py" -c "import mpremote, serial" >/dev/null 2>&1; then echo "$py"; return 0; fi
  done
  return 1
}
VENV_PY="$(pick_venv)" || {
  echo "channel-islands: no python with mpremote+pyserial found." >&2
  echo "          fix: python3 -m venv --system-site-packages ~/mpv \\" >&2
  echo "               && ~/mpv/bin/pip install mpremote pyserial" >&2
  exit 1
}
# picamera2 is a SYSTEM package (apt), deliberately not in the venv.
SYS_PY="$(command -v python3)"
"$SYS_PY" -c "import picamera2" >/dev/null 2>&1 || {
  echo "channel-islands: picamera2 not importable by $SYS_PY." >&2
  echo "          fix: sudo apt-get install -y python3-picamera2" >&2
  exit 1
}

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PREFIX="dive_$STAMP"
mkdir -p "$ROOTDIR" || { echo "channel-islands: cannot create $ROOTDIR" >&2; exit 1; }
echo "channel-islands: prefix=$PREFIX segment=${SEGMENT_S}s wb=$WB focus=$FOCUS lens=$LENS q=$JPEG_Q" >&2

CHILDREN=()
cleanup() {
  echo "channel-islands: stopping (closing current segment)…" >&2
  for pid in "${CHILDREN[@]:-}"; do kill -INT "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  sync
  echo "channel-islands: stopped; sessions ${PREFIX}_s* under $ROOTDIR" >&2
}
trap cleanup INT TERM

# IMX: one long-lived process; it rolls its own segments internally so the
# WB/focus lock is never re-taken.
"$SYS_PY" "$HERE/imx_dive_recorder.py" \
  --root "$ROOTDIR" --session-prefix "$PREFIX" --recipe channel-islands \
  --segment-s "$SEGMENT_S" --jpeg-q "$JPEG_Q" \
  --wb "$WB" --focus "$FOCUS" --lens-position "$LENS" &
CHILDREN+=($!)
IMX_PID=$!

# Boards: re-run per segment, into the SAME session directory the IMX is
# writing for that segment -- "${PREFIX}_s0000", "_s0001", and so on. That is
# what makes a dive ONE timestamped event holding all three cameras instead
# of an IMX tree beside a separate board tree (Nick, 2026-09-09).
#
# The two are aligned by CLOCK, not handshaked: both start together and both
# use $SEGMENT_S, so they stay together to within about a second over a dive.
# Both manifest writers merge rather than overwrite, so even if a boundary
# slips neither camera can be dropped from the page.
#
# THE BOARDS FOLLOW THE IMX'S CLOCK. They used to count their own segments on
# their own 300 s timer, which drifted: measured over 2.5 h, 26 board segments
# against 22 IMX ones, so a session directory ended up holding cameras from
# different moments and the last four had no IMX at all. Now each pass asks
# the IMX which segment is open and how long is left, and records exactly that
# remainder -- a follower cannot drift from what it is following.
CURRENT="$ROOTDIR/${PREFIX}_current.json"
(
  while kill -0 "$IMX_PID" 2>/dev/null; do
    read -r SEG LEFT <<<"$("$SYS_PY" - "$CURRENT" <<'PY' 2>/dev/null
import json, sys, time
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("", ""); raise SystemExit
left = float(d.get("ends_unix", 0)) - time.time()
print(d.get("segment", ""), "%.0f" % max(0.0, left))
PY
)"
    if [ -z "${SEG:-}" ]; then sleep 2; continue; fi
    # Too little of the segment left to be worth a board start-up; wait for
    # the next one rather than writing a two-second stub.
    if [ "${LEFT:-0}" -lt 20 ]; then sleep 3; continue; fi
    SESSION="$(printf '%s_s%04d' "$PREFIX" "$SEG")"
    LOG="$ROOTDIR/$SESSION/boards.log"
    mkdir -p "$ROOTDIR/$SESSION"
    "$VENV_PY" "$ROOT/pi/field/recorder.py" \
      --root "$ROOTDIR" --session "$SESSION" --cameras "$BOARDS" --fps 30 \
      --duration "$LEFT" --no-transcode >> "$LOG" 2>&1 \
      || echo "channel-islands: board segment $SEG failed (see $LOG)" >&2
  done
) &
CHILDREN+=($!)

wait "$IMX_PID"
cleanup
