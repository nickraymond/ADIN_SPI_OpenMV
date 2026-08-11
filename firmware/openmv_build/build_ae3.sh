#!/bin/bash
# build_ae3.sh -- reproducible OPENMV_AE3 firmware build in docker (Mac host).
#
# Wraps openmv.git's own docker build (docker/Makefile build-firmware --
# reuse before rewriting) with the three things it doesn't do for us:
#   * pins/clones the openmv tree at a requested rev (worktree kept at
#     ~/openmv-dev/openmv unless --openmv-dir says otherwise)
#   * forces the container platform to linux/amd64 (Rosetta) and feeds it
#     the linux-x86_64 SDK -- the SDK is NOT published for linux-aarch64,
#     so an arm64 container cannot work (probed 2026-08-11)
#   * verifies the artifacts (exist, plausible size) and writes a manifest
#     with the git rev + sha256s -- trust artifacts, not exit codes
#
# Usage:
#   build_ae3.sh [--rev <tag|sha|branch>] [--openmv-dir DIR] [--sdk-dir DIR]
#
# Output: <openmv-dir>/build/OPENMV_AE3/bin/ plus MANIFEST.txt in the same
# dir. Copy firmware to the Pi with the scp command printed at the end.

set -euo pipefail

REV="master"
OPENMV_DIR="${HOME}/openmv-dev/openmv"
SDK_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --rev)        REV="$2"; shift 2 ;;
        --openmv-dir) OPENMV_DIR="$2"; shift 2 ;;
        --sdk-dir)    SDK_DIR="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found -- run setup_mac.sh, then launch Docker Desktop"
docker info >/dev/null 2>&1   || fail "docker daemon not running -- launch Docker Desktop"

# Clone or update the openmv tree at the requested rev.
if [ ! -d "${OPENMV_DIR}/.git" ]; then
    mkdir -p "$(dirname "${OPENMV_DIR}")"
    git clone https://github.com/openmv/openmv.git "${OPENMV_DIR}"
fi
git -C "${OPENMV_DIR}" fetch --tags origin
git -C "${OPENMV_DIR}" checkout --quiet "${REV}" || fail "rev '${REV}' not found in openmv.git"
# Detached-HEAD checkouts of a branch name track the remote tip.
if git -C "${OPENMV_DIR}" show-ref --verify --quiet "refs/remotes/origin/${REV}"; then
    git -C "${OPENMV_DIR}" reset --hard "origin/${REV}"
fi
git -C "${OPENMV_DIR}" submodule update --init --depth=50

GIT_DESC=$(git -C "${OPENMV_DIR}" describe --tags --always --dirty)
GIT_SHA10=$(git -C "${OPENMV_DIR}" rev-parse --short=10 HEAD)

# SDK: version must match the tree we're building.
WANT_SDK=$(cat "${OPENMV_DIR}/SDK_VERSION")
SDK_DIR="${SDK_DIR:-${HOME}/openmv-sdk-${WANT_SDK}-linux-x86_64}"
[ -d "${SDK_DIR}" ] || fail "SDK not found at ${SDK_DIR} -- run setup_mac.sh (SDK_VERSION=${WANT_SDK})"
HAVE_SDK=$(cat "${SDK_DIR}/sdk.version" 2>/dev/null || echo "?")
[ "${HAVE_SDK}" = "${WANT_SDK}" ] || \
    fail "SDK ${HAVE_SDK} != ${WANT_SDK} wanted by openmv @ ${GIT_DESC} -- rerun setup_mac.sh with SDK_VERSION=${WANT_SDK}"

echo "== Building OPENMV_AE3 @ ${GIT_DESC} (${GIT_SHA10}) -- amd64 container under Rosetta, ~10-30 min first run"
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
    make -C "${OPENMV_DIR}/docker" build-firmware TARGET=OPENMV_AE3 SDK_DIR="${SDK_DIR}"

# Trust artifacts, not exit codes.
BIN_DIR="${OPENMV_DIR}/build/OPENMV_AE3/bin"
for f in firmware_M55_HP.bin firmware_M55_HE.bin bootloader.bin; do
    [ -s "${BIN_DIR}/${f}" ] || fail "missing/empty artifact: ${BIN_DIR}/${f}"
done
HP_SIZE=$(stat -f%z "${BIN_DIR}/firmware_M55_HP.bin")
[ "${HP_SIZE}" -gt 262144 ] || fail "firmware_M55_HP.bin is ${HP_SIZE} B -- implausibly small"

# The flash verifier greps os.uname().version for this exact 10-hex hash;
# confirm the binary really embeds it before shipping.
strings "${BIN_DIR}/firmware_M55_HP.bin" | grep -q "OpenMV ${GIT_SHA10}" || \
    echo "WARN: 'OpenMV ${GIT_SHA10}' not found in HP binary strings -- verify format on flash day"

MANIFEST="${BIN_DIR}/MANIFEST.txt"
{
    echo "built:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "rev:        ${GIT_DESC}"
    echo "openmv_sha: ${GIT_SHA10}"
    echo "sdk:        ${WANT_SDK} linux-x86_64"
    (cd "${BIN_DIR}" && shasum -a 256 firmware_M55_HP.bin firmware_M55_HE.bin bootloader.bin)
} > "${MANIFEST}"

echo "== OK. Artifacts in ${BIN_DIR}"
cat "${MANIFEST}"
echo
echo "Ship to the Pi (flash later with pi/ae3_flash/flash_ae3.py):"
echo "  ssh pi@nereus000 mkdir -p fw/${GIT_SHA10} && \\"
echo "  scp ${BIN_DIR}/firmware_M55_{HP,HE}.bin ${BIN_DIR}/MANIFEST.txt pi@nereus000:fw/${GIT_SHA10}/"
