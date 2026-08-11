#!/bin/bash
# build_ae3.sh -- reproducible OPENMV_AE3 firmware build in docker (Mac host).
#
# Wraps openmv.git's docker build with the things it doesn't do for us:
#   * pins/clones the openmv tree at a requested rev (worktree kept at
#     ~/openmv-dev/openmv unless --openmv-dir says otherwise)
#   * forces the container platform to linux/amd64 (Rosetta) and feeds it
#     the linux-x86_64 SDK -- the SDK is NOT published for linux-aarch64,
#     so an arm64 container cannot work (probed 2026-08-11)
#   * verifies the artifacts (exist, plausible size) and writes a manifest
#     with the git rev + sha256s -- trust artifacts, not exit codes
#
# MUST use docker/Makefile's build-firmware-dev target, NOT build-firmware:
# the stock target's build.sh passes BUILD=<dir> on the make command line,
# which propagates via MAKEFLAGS into every sub-make and overrides
# ports/alif/alif.mk's `BUILD := $(BUILD)/$(MCU_CORE)` per-core nesting.
# Both cores then share one object dir and the M55_HE image links against
# HP-configured objects -- FLASH_TEXT overflow + undefined dcd_* refs
# (root-caused 2026-08-11; openmv CI builds AE3 without docker and never
# hits it; build-dev.sh's own comment documents the nesting requirement).
#
# Usage:
#   build_ae3.sh [--rev <tag|sha|branch>] [--openmv-dir DIR] [--sdk-dir DIR]
#                [--incremental]
#
# --incremental skips the pre-build clean (build-firmware-dev is
# incremental by design) -- the fast path for the C dev loop. Default is
# a from-clean build, the one a MANIFEST should describe.
#
# Output: <openmv-dir>/build/OPENMV_AE3/bin/ plus MANIFEST.txt in the same
# dir. Copy firmware to the Pi with the scp command printed at the end.

set -euo pipefail

REV="master"
OPENMV_DIR="${HOME}/openmv-dev/openmv"
SDK_DIR=""
INCREMENTAL=0

while [ $# -gt 0 ]; do
    case "$1" in
        --rev)        REV="$2"; shift 2 ;;
        --openmv-dir) OPENMV_DIR="$2"; shift 2 ;;
        --sdk-dir)    SDK_DIR="$2"; shift 2 ;;
        --incremental) INCREMENTAL=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found -- run setup_mac.sh, then launch Docker Desktop"
docker info >/dev/null 2>&1   || fail "docker daemon not running -- launch Docker Desktop"

# Clone or update the openmv tree at the requested rev. A dirty tree is
# the C dev loop in flight -- build it as-is instead of hard-resetting
# someone's edits away (the manifest rev will carry -dirty).
if [ ! -d "${OPENMV_DIR}/.git" ]; then
    mkdir -p "$(dirname "${OPENMV_DIR}")"
    git clone https://github.com/openmv/openmv.git "${OPENMV_DIR}"
fi
# --untracked-files=no: staged usermod copies (build_spike.sh) don't count;
# --ignore-submodules=dirty: in-tree mpy-cross build artifacts don't count.
if [ -n "$(git -C "${OPENMV_DIR}" status --porcelain --untracked-files=no --ignore-submodules=dirty)" ]; then
    echo "WARN: ${OPENMV_DIR} has uncommitted changes -- skipping rev sync, building the tree as-is"
else
    git -C "${OPENMV_DIR}" fetch --tags origin
    git -C "${OPENMV_DIR}" checkout --quiet "${REV}" || fail "rev '${REV}' not found in openmv.git"
    # Detached-HEAD checkouts of a branch name track the remote tip.
    if git -C "${OPENMV_DIR}" show-ref --verify --quiet "refs/remotes/origin/${REV}"; then
        git -C "${OPENMV_DIR}" reset --hard "origin/${REV}"
    fi
    git -C "${OPENMV_DIR}" submodule update --init --depth=50
fi

GIT_DESC=$(git -C "${OPENMV_DIR}" describe --tags --always --dirty)
GIT_SHA10=$(git -C "${OPENMV_DIR}" rev-parse --short=10 HEAD)

# SDK: version must match the tree we're building.
WANT_SDK=$(cat "${OPENMV_DIR}/SDK_VERSION")
SDK_DIR="${SDK_DIR:-${HOME}/openmv-sdk-${WANT_SDK}-linux-x86_64}"
[ -d "${SDK_DIR}" ] || fail "SDK not found at ${SDK_DIR} -- run setup_mac.sh (SDK_VERSION=${WANT_SDK})"
HAVE_SDK=$(cat "${SDK_DIR}/sdk.version" 2>/dev/null || echo "?")
[ "${HAVE_SDK}" = "${WANT_SDK}" ] || \
    fail "SDK ${HAVE_SDK} != ${WANT_SDK} wanted by openmv @ ${GIT_DESC} -- rerun setup_mac.sh with SDK_VERSION=${WANT_SDK}"

if [ "${INCREMENTAL}" -eq 0 ]; then
    make -C "${OPENMV_DIR}/docker" clean-dev
fi
echo "== Building OPENMV_AE3 @ ${GIT_DESC} (${GIT_SHA10}) -- amd64 container under Rosetta, ~10-30 min from clean"
# build-firmware-dev, NOT build-firmware -- see header (per-core BUILD
# nesting; the stock target cannot link the M55_HE image).
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
    make -C "${OPENMV_DIR}/docker" build-firmware-dev TARGET=OPENMV_AE3 SDK_DIR="${SDK_DIR}"

# Trust artifacts, not exit codes.
BIN_DIR="${OPENMV_DIR}/build/OPENMV_AE3/bin"
for f in firmware_M55_HP.bin firmware_M55_HE.bin bootloader.bin; do
    [ -s "${BIN_DIR}/${f}" ] || fail "missing/empty artifact: ${BIN_DIR}/${f}"
done
HP_SIZE=$(stat -f%z "${BIN_DIR}/firmware_M55_HP.bin")
[ "${HP_SIZE}" -gt 262144 ] || fail "firmware_M55_HP.bin is ${HP_SIZE} B -- implausibly small"
# HE lives in a 1400 KB flash region (official artifact ~1.19 MB). Too big
# means it linked against HP objects (the bug this script's -dev target
# avoids); too small means a stub.
HE_SIZE=$(stat -f%z "${BIN_DIR}/firmware_M55_HE.bin")
[ "${HE_SIZE}" -gt 262144 ]  || fail "firmware_M55_HE.bin is ${HE_SIZE} B -- implausibly small"
[ "${HE_SIZE}" -le 1433600 ] || fail "firmware_M55_HE.bin is ${HE_SIZE} B -- exceeds the 1400 KB HE flash region"

# The flash verifier cross-checks sys.version against the manifest. The
# embedded label is git-describe reformatted by micropython's
# makeversionhdr.py (dashes become dots: v5.0.0-52.g<sha10>); a tagless
# checkout degrades it to the bare sha10, an exact-tag checkout to the tag.
FW_STRING=$(strings "${BIN_DIR}/firmware_M55_HP.bin" | grep -m1 -oE 'OpenMV [^;]+; MicroPython [^;]+' || true)
OPENMV_LABEL=$(printf '%s' "${FW_STRING}" | sed -nE 's/^OpenMV ([^;]+);.*/\1/p')
case "${OPENMV_LABEL}" in
    *"${GIT_SHA10}"*|"${GIT_DESC}") : ;;
    *) echo "WARN: HP binary label '${OPENMV_LABEL:-<none>}' matches neither ${GIT_SHA10} nor ${GIT_DESC} -- verify on flash day" ;;
esac

MANIFEST="${BIN_DIR}/MANIFEST.txt"
{
    echo "built:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "rev:        ${GIT_DESC}"
    echo "openmv_sha: ${GIT_SHA10}"
    [ -n "${OPENMV_LABEL}" ] && echo "openmv_label: ${OPENMV_LABEL}"
    echo "sdk:        ${WANT_SDK} linux-x86_64"
    (cd "${BIN_DIR}" && shasum -a 256 firmware_M55_HP.bin firmware_M55_HE.bin bootloader.bin)
} > "${MANIFEST}"

echo "== OK. Artifacts in ${BIN_DIR}"
cat "${MANIFEST}"
echo
echo "Ship to the Pi (flash later with pi/ae3_flash/flash_ae3.py):"
echo "  ssh pi@nereus000 mkdir -p fw/${GIT_SHA10} && \\"
echo "  scp ${BIN_DIR}/firmware_M55_{HP,HE}.bin ${BIN_DIR}/MANIFEST.txt pi@nereus000:fw/${GIT_SHA10}/"
