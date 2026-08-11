#!/bin/bash
# build_spike.sh -- build OPENMV_AE3 firmware WITH the bm_spike usermod.
#
# Mechanism (no openmv fork, no patch): openmv's modules/micropython.mk
# compiles every *.c in the tree's modules/ dir via SRC_USERMOD wildcard
# and the top Makefile passes USER_C_MODULES=$(TOP_DIR) to MicroPython.
# So: copy our sources + the vendored driver into <openmv>/modules/,
# run the standard build_ae3.sh, then remove the copies (trap'd).
#
# bm_adin2111.c is vendored for reference but NEVER copied/compiled: it is
# bm_core's NetworkDevice wrapper and needs bm_os -- and it defines its own
# HAL_RegisterCallback, which would collide with bm_spike_hal_mp.c.
#
# Usage: build_spike.sh [--openmv-dir DIR] [args passed through to build_ae3.sh]

set -euo pipefail
cd "$(dirname "$0")"

OPENMV_DIR="${HOME}/openmv-dev/openmv"
PASSTHRU=()
while [ $# -gt 0 ]; do
    case "$1" in
        --openmv-dir) OPENMV_DIR="$2"; PASSTHRU+=("$1" "$2"); shift 2 ;;
        *)            PASSTHRU+=("$1"); shift ;;
    esac
done

MOD_DIR="${OPENMV_DIR}/modules"
[ -d "${MOD_DIR}" ] || { echo "FAIL: ${MOD_DIR} not found -- clone via build_ae3.sh first" >&2; exit 1; }

# Everything in src/ + the vendored driver, EXCEPT bm_adin2111.{c,h}
# (see header comment).
SPIKE_SRCS=()
for f in src/*.c src/*.h vendor/adin2111/*.c vendor/adin2111/*.h; do
    case "$(basename "$f")" in
        bm_adin2111.c|bm_adin2111.h) continue ;;
    esac
    SPIKE_SRCS+=("$f")
done

COPIED=()
cleanup() {
    for f in "${COPIED[@]}"; do rm -f "$f"; done
}
trap cleanup EXIT

for src in "${SPIKE_SRCS[@]}"; do
    base=$(basename "$src")
    dst="${MOD_DIR}/${base}"
    [ -e "$dst" ] && { echo "FAIL: ${dst} already exists -- refusing to overwrite" >&2; exit 1; }
    cp "$src" "$dst"
    COPIED+=("$dst")
done
echo "staged ${#COPIED[@]} spike sources into ${MOD_DIR}"

../openmv_build/build_ae3.sh ${PASSTHRU[@]+"${PASSTHRU[@]}"}

echo
echo "bm_spike build done. Flash via pi/ae3_flash (S7 ladder), then on the"
echo "REPL: import s9_oa_spike (or run firmware/bm_spike/s9_oa_spike.py)."
