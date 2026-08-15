#!/bin/bash
# build_bm_he.sh -- cross-build bm_he.elf/.bin on the Mac using the D23
# docker environment (firmware-builder image + linux-x86_64 OpenMV SDK
# under Rosetta). From the openmv clone this build takes only headers +
# sources by reference (lwIP 2.2.1 at lib/micropython/lib/lwip); nothing
# in that tree is modified. The FreeRTOS kernel and rpmsg/MHU scaffold are
# shared with ../he_spike (one copy).
#
# Usage: build_bm_he.sh [--openmv-dir DIR] [--sdk-dir DIR] [--clean]
#
# Output: firmware/bm_he/build/bm_he.{elf,bin,map} + MANIFEST.txt.
# Ship to the Pi with the scp command printed at the end.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OPENMV_DIR="${HOME}/openmv-dev/openmv"
SDK_DIR=""
CLEAN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --openmv-dir) OPENMV_DIR="$2"; shift 2 ;;
        --sdk-dir)    SDK_DIR="$2"; shift 2 ;;
        --clean)      CLEAN=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found -- run firmware/openmv_build/setup_mac.sh"
docker info >/dev/null 2>&1   || fail "docker daemon not running -- launch Docker Desktop"
docker image inspect firmware-builder:latest >/dev/null 2>&1 \
    || fail "firmware-builder image missing -- run firmware/openmv_build/build_ae3.sh once to create it"
[ -d "${OPENMV_DIR}/lib/micropython/lib/lwip/src/core" ] \
    || fail "lwIP not found in openmv clone at ${OPENMV_DIR} (submodule checked out?)"

WANT_SDK=$(cat "${OPENMV_DIR}/SDK_VERSION")
SDK_DIR="${SDK_DIR:-${HOME}/openmv-sdk-${WANT_SDK}-linux-x86_64}"
[ -x "${SDK_DIR}/gcc/bin/arm-none-eabi-gcc" ] || fail "SDK gcc not found at ${SDK_DIR}/gcc/bin"

[ "${CLEAN}" -eq 1 ] && rm -rf "${HERE}/build"

# he_spike must be mounted too (shared FreeRTOS + rpmsg/MHU sources).
SPIKE_DIR="$(cd "${HERE}/../he_spike" && pwd)"

DOCKER_DEFAULT_PLATFORM=linux/amd64 docker run --rm \
    -v "${HERE}:/work" \
    -v "${SPIKE_DIR}:/he_spike:ro" \
    -v "${OPENMV_DIR}:/openmv:ro" \
    -v "${SDK_DIR}:/sdk:ro" \
    -w /work \
    firmware-builder:latest \
    make CROSS=/sdk/gcc/bin/arm-none-eabi- OPENMV_DIR=/openmv SPIKE=/he_spike

# Trust artifacts, not exit codes.
BIN="${HERE}/build/bm_he.bin"
[ -s "${BIN}" ] || fail "missing/empty ${BIN}"
SZ=$(stat -f%z "${BIN}" 2>/dev/null || stat -c%s "${BIN}")
[ "${SZ}" -gt 32768 ]  || fail "bm_he.bin is ${SZ} B -- implausibly small for a bm_core+lwIP image"
[ "${SZ}" -lt 261632 ] || fail "bm_he.bin is ${SZ} B -- exceeds the 256K-512B APP region"

# The image must be linked at the SRAM9_B upper-half base -- the runner
# pokes/boots this exact address.
BASE=$(docker run --rm -v "${SDK_DIR}:/sdk:ro" -v "${HERE}:/work" -w /work \
    --platform linux/amd64 firmware-builder:latest \
    /sdk/gcc/bin/arm-none-eabi-readelf -l build/bm_he.elf \
    | awk '/LOAD/ {print $3; exit}')
[ "${BASE}" = "0x60080000" ] || fail "first LOAD segment at ${BASE}, want 0x60080000"

GIT_DESC=$(cd "${HERE}" && git describe --always --dirty 2>/dev/null || echo "?")
{
    echo "built:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "repo_rev:   ${GIT_DESC}"
    echo "bm_core:    $(grep '^Revision:' "${HERE}/vendor/bm_core/PROVENANCE.txt" | awk '{print $2}')"
    echo "lwip:       2.2.1 (openmv tree) + contrib sys_arch $(grep '^Revision:' "${HERE}/vendor/lwip_contrib/PROVENANCE.txt" | awk '{print $2}')"
    echo "freertos:   $(head -1 "${SPIKE_DIR}/vendor/freertos/PROVENANCE.txt")"
    echo "openmv_dir: ${OPENMV_DIR} @ $(cd "${OPENMV_DIR}" && git rev-parse --short=10 HEAD)"
    echo "sdk:        ${WANT_SDK} linux-x86_64"
    echo "load_base:  0x60080000 (SRAM9_B upper half)"
    (cd "${HERE}/build" && shasum -a 256 bm_he.elf bm_he.bin)
} > "${HERE}/build/MANIFEST.txt"

echo "== OK. Artifacts in ${HERE}/build"
cat "${HERE}/build/MANIFEST.txt"
echo
echo "Ship to the Pi:"
echo "  ssh pi@nereus000 mkdir -p bm_he && \\"
echo "  scp ${HERE}/build/bm_he.{elf,bin} ${HERE}/build/MANIFEST.txt ${HERE}/s10_bcmp_bench.py ${HERE}/s10_peer.py pi@nereus000:bm_he/"
