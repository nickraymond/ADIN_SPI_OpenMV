#!/bin/bash
# fetch_firmware.sh -- pull a known OpenMV AE3 release firmware onto the Pi.
#
# Usage: fetch_firmware.sh <tag>        e.g. v5.0.0, development
# Output: ~/fw/<tag>/ with the zip contents + MANIFEST.txt (sha256s and the
# OpenMV sha10 extracted from the binary itself, so flash_ae3.py can verify).

set -euo pipefail
TAG="${1:?usage: fetch_firmware.sh <release-tag>}"
DEST="${HOME}/fw/${TAG}"
URL="https://github.com/openmv/openmv/releases/download/${TAG}/firmware_OPENMV_AE3.zip"

mkdir -p "${DEST}"
curl -fL -o "${DEST}/firmware.zip" "${URL}"
unzip -t "${DEST}/firmware.zip" >/dev/null   # integrity before extract
unzip -o -j -q "${DEST}/firmware.zip" -d "${DEST}"

[ -s "${DEST}/firmware_M55_HP.bin" ] || { echo "FAIL: no firmware_M55_HP.bin in zip" >&2; exit 1; }

# The binary embeds "OpenMV <sha10>; MicroPython <sha10>" -- record it so
# flash_ae3.py can verify without a --expect flag.
SHA10=$(strings "${DEST}/firmware_M55_HP.bin" | grep -oE "OpenMV [0-9a-f]{8,12}" | head -1 | awk '{print $2}')
{
    echo "built:      (release ${TAG}, fetched $(date -u +%Y-%m-%dT%H:%M:%SZ))"
    echo "rev:        ${TAG}"
    echo "openmv_sha: ${SHA10:-UNKNOWN}"
    (cd "${DEST}" && sha256sum firmware_M55_HP.bin firmware_M55_HE.bin 2>/dev/null || true)
} > "${DEST}/MANIFEST.txt"

echo "OK: ${DEST}"
cat "${DEST}/MANIFEST.txt"
