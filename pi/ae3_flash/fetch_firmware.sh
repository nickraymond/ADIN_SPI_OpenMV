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
if curl -fL -o "${DEST}/firmware.zip" "${URL}" 2>/dev/null; then
    unzip -t "${DEST}/firmware.zip" >/dev/null   # integrity before extract
    unzip -o -j -q "${DEST}/firmware.zip" -d "${DEST}"
else
    # Tagged releases (e.g. v5.0.0) ship ONE combined all-boards zip
    # instead of per-board assets; extract just the AE3 subdir.
    URL="https://github.com/openmv/openmv/releases/download/${TAG}/firmware_${TAG}.zip"
    echo "per-board zip not found; trying combined ${URL}"
    curl -fL -o "${DEST}/firmware.zip" "${URL}"
    unzip -t "${DEST}/firmware.zip" >/dev/null
    unzip -o -j -q "${DEST}/firmware.zip" "OPENMV_AE3/*" -d "${DEST}"
fi

[ -s "${DEST}/firmware_M55_HP.bin" ] || { echo "FAIL: no firmware_M55_HP.bin in zip" >&2; exit 1; }

# The binary embeds "OpenMV <id>; MicroPython <id>" (id = sha10 on dev
# builds, version tag on releases) -- record it so flash_ae3.py can verify
# without a --expect flag.
SHA10=$(strings "${DEST}/firmware_M55_HP.bin" | grep -oE "OpenMV [^ ;]+; MicroPython" | head -1 | awk '{print $2}')
{
    echo "built:      (release ${TAG}, fetched $(date -u +%Y-%m-%dT%H:%M:%SZ))"
    echo "rev:        ${TAG}"
    echo "openmv_sha: ${SHA10:-UNKNOWN}"
    (cd "${DEST}" && sha256sum firmware_M55_HP.bin firmware_M55_HE.bin 2>/dev/null || true)
} > "${DEST}/MANIFEST.txt"

echo "OK: ${DEST}"
cat "${DEST}/MANIFEST.txt"
