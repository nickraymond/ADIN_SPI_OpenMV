#!/bin/bash
# build_he_spike.sh -- cross-build he_spike.elf/.bin on the Mac using the
# D23 docker environment (firmware-builder image + linux-x86_64 OpenMV SDK
# under Rosetta). Only the Alif DFP headers/sources are taken from the
# openmv clone; nothing in that tree is modified.
#
# Usage: build_he_spike.sh [--openmv-dir DIR] [--sdk-dir DIR] [--clean]
#
# Output: firmware/he_spike/build/he_spike.{elf,bin,map} + MANIFEST.txt.
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
[ -d "${OPENMV_DIR}/lib/alif/drivers" ] || fail "openmv clone not found at ${OPENMV_DIR}"

WANT_SDK=$(cat "${OPENMV_DIR}/SDK_VERSION")
SDK_DIR="${SDK_DIR:-${HOME}/openmv-sdk-${WANT_SDK}-linux-x86_64}"
[ -x "${SDK_DIR}/gcc/bin/arm-none-eabi-gcc" ] || fail "SDK gcc not found at ${SDK_DIR}/gcc/bin"

[ "${CLEAN}" -eq 1 ] && rm -rf "${HERE}/build"

DOCKER_DEFAULT_PLATFORM=linux/amd64 docker run --rm \
    -v "${HERE}:/work" \
    -v "${OPENMV_DIR}:/openmv:ro" \
    -v "${SDK_DIR}:/sdk:ro" \
    -w /work \
    firmware-builder:latest \
    make CROSS=/sdk/gcc/bin/arm-none-eabi- OPENMV_DIR=/openmv

# Trust artifacts, not exit codes.
BIN="${HERE}/build/he_spike.bin"
ELF="${HERE}/build/he_spike.elf"
[ -s "${BIN}" ] || fail "missing/empty ${BIN}"
SZ=$(stat -f%z "${BIN}" 2>/dev/null || stat -c%s "${BIN}")
[ "${SZ}" -gt 4096 ]   || fail "he_spike.bin is ${SZ} B -- implausibly small"
[ "${SZ}" -lt 261888 ] || fail "he_spike.bin is ${SZ} B -- exceeds the 256K-256B APP region"

# The image must be linked at the SRAM9_B upper-half base -- the runner
# pokes/boots this exact address.
BASE=$(docker run --rm -v "${SDK_DIR}:/sdk:ro" -v "${HERE}:/work" -w /work \
    --platform linux/amd64 firmware-builder:latest \
    /sdk/gcc/bin/arm-none-eabi-readelf -l build/he_spike.elf \
    | awk '/LOAD/ {print $3; exit}')
[ "${BASE}" = "0x60080000" ] || fail "first LOAD segment at ${BASE}, want 0x60080000"

GIT_DESC=$(cd "${HERE}" && git describe --always --dirty 2>/dev/null || echo "?")
{
    echo "built:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "repo_rev:   ${GIT_DESC}"
    echo "freertos:   $(head -1 "${HERE}/vendor/freertos/PROVENANCE.txt")"
    echo "openmv_dir: ${OPENMV_DIR} @ $(cd "${OPENMV_DIR}" && git rev-parse --short=10 HEAD)"
    echo "sdk:        ${WANT_SDK} linux-x86_64"
    echo "load_base:  0x60080000 (SRAM9_B upper half)"
    (cd "${HERE}/build" && shasum -a 256 he_spike.elf he_spike.bin)
} > "${HERE}/build/MANIFEST.txt"

echo "== OK. Artifacts in ${HERE}/build"
cat "${HERE}/build/MANIFEST.txt"
echo
echo "Ship to the Pi:"
echo "  ssh pi@nereus000 mkdir -p he_spike && \\"
echo "  scp ${HERE}/build/he_spike.{elf,bin} ${HERE}/build/MANIFEST.txt ${HERE}/s10_pipe_bench.py pi@nereus000:he_spike/"
