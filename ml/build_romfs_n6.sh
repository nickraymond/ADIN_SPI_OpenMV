#!/usr/bin/env bash
# build_romfs_n6.sh -- build an N6 ROMFS image containing OUR model plus every
# model the vendor ships.
#
#   ml/build_romfs_n6.sh <our_model.tflite> [outdir]
#
# WHY THIS EXISTS: on the N6 a compiled model canNOT be loaded from /flash --
# ml.Model() raises "Failed to load network" even for a file that is
# byte-identical to the one in /rom (measured 2026-08-20). stedgeai's
# relocatable binary places its params in xSPI2 (external flash, XIP), which a
# /flash load into heap RAM cannot satisfy. ROMFS is the deployment path.
#
# THE IMAGE REPLACES THE WHOLE PARTITION, so it must carry the vendor's models
# too -- flashing an image without them deletes them from the board. This
# script starts from the board's own romfs_config.json for exactly that reason.
#
# Partition (boards/OPENMV_N6/board_config.h):
#   OMV_ROMFS_PART0_ORIGIN  0x70800000
#   OMV_ROMFS_PART0_LENGTH  0x01800000   (24 MiB)
#
# FLASHING IS NOT DONE HERE AND IS NOT YET SETTLED. OpenMV's own `deploy`
# target uses STM32_Programmer_CLI over SWD with an external loader, which
# needs an ST-LINK probe. The AE3 by contrast flashes over its always-present
# USB DFU bootloader (pi/ae3_flash/) -- whether the N6 offers an equivalent
# route, and whether it can write ONLY the ROMFS partition, is UNVERIFIED.
# Do not guess a flash address: a wrong write needs a recovery reflash.
set -euo pipefail

MODEL="${1:?usage: build_romfs_n6.sh <our_model.tflite> [outdir]}"
WORK="${2:-$HOME/nereus_ml/romfs_n6}"
SDK="${SDK_DIR:-$HOME/openmv-sdk-1.6.0-linux-x86_64}"
OPENMV="${OPENMV_DIR:-$HOME/openmv-dev/openmv}"
IMAGE="${BUILDER_IMAGE:-firmware-builder:latest}"

[ -f "$MODEL" ] || { echo "no model at $MODEL" >&2; exit 1; }
mkdir -p "$WORK"/{in,out,build}
cp "$MODEL" "$WORK/in/$(basename "$MODEL")"

python3 - "$OPENMV" "$WORK" "$(basename "$MODEL")" <<'PY'
import json, os, sys
openmv, work, name = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(os.path.join(openmv, "boards/OPENMV_N6/romfs_config.json")))
cfg["0"]["entries"].insert(0, {"type": "tflite", "path": "/models/" + name,
                               "alignment": 32, "profile": "default"})
json.dump(cfg, open(os.path.join(work, "romfs_config_nereus.json"), "w"), indent=2)
print("entries: %d (ours + the vendor's)" % len(cfg["0"]["entries"]))
PY

docker run --rm --platform linux/amd64 \
    -v "$SDK":/sdk:ro -v "$OPENMV":/omv -v "$WORK":/work "$IMAGE" bash -lc '
        export PATH=/sdk/python/bin:/sdk/stedgeai/Utilities/linux:/sdk/gcc/bin:$PATH
        ln -sfn /work/in /models
        cd /omv
        python3 tools/mkromfs.py --top-dir /omv --out-dir /work/out \
            --build-dir /work/build --stedge-args "--target stm32n6" \
            --config /work/romfs_config_nereus.json --partition 0
    ' 2>&1 | tail -24

echo
ls -l "$WORK/out/romfs0.img" | awk '{print $5" bytes  "$9}'
