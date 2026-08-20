#!/usr/bin/env bash
# compile_model.sh -- compile a .tflite for the AE3 (Vela) or N6 (STEdgeAI).
#
#   ml/compile_model.sh ae3 <model.tflite> [outdir]
#   ml/compile_model.sh n6  <model.tflite> [outdir]
#
# There is nothing to install: OpenMV's SDK already carries both compilers
# (vela 5.0.0, ST Edge AI Core 4.0.0) and this drives OpenMV's own
# tools/modelc.py, the same script that builds the models shipped in ROM.
# Both are linux-x86_64, so they run in the firmware-builder container that
# firmware/openmv_build/ already set up -- not natively on macOS.
#
# VERIFIED 2026-08-20: compiling lib/models/person_detect.tflite for the N6
# produced 274,272 bytes -- byte-for-byte the size OpenMV ships in the N6's
# ROM (DESIGN S24 inventory). The route reproduces the vendor's own artifact.
#
# The compilers take an int8/uint8 NHWC .tflite. Note the N6's output is NOT
# a tflite at all: stedgeai emits a relocatable Neural-ART binary and modelc
# renames it to .tflite, which is why the same model differs in size between
# the two boards.
set -euo pipefail

TARGET="${1:?usage: compile_model.sh <ae3|n6> <model.tflite> [outdir]}"
MODEL="${2:?missing model path}"
OUTDIR="${3:-$HOME/nereus_ml/exports/${TARGET}}"

SDK="${SDK_DIR:-$HOME/openmv-sdk-1.6.0-linux-x86_64}"
OPENMV="${OPENMV_DIR:-$HOME/openmv-dev/openmv}"
IMAGE="${BUILDER_IMAGE:-firmware-builder:latest}"

[ -d "$SDK" ]    || { echo "no SDK at $SDK (firmware/openmv_build/setup_mac.sh)" >&2; exit 1; }
[ -d "$OPENMV" ] || { echo "no openmv tree at $OPENMV" >&2; exit 1; }
[ -f "$MODEL" ]  || { echo "no model at $MODEL" >&2; exit 1; }

# Vela args and the ROM optimize/profile settings come from the boards' own
# romfs_config.json -- these are what OpenMV compiles its shipped models with.
case "$TARGET" in
  ae3) ARGS=(--vela-args "--system-config RTSS_HP_DTCM_MRAM --accelerator-config ethos-u55-256 --memory-mode Shared_Sram --optimise Performance") ;;
  n6)  ARGS=(--stedge-args "--target stm32n6") ;;
  *)   echo "target must be ae3 or n6" >&2; exit 1 ;;
esac

mkdir -p "$OUTDIR"
MODEL_ABS="$(cd "$(dirname "$MODEL")" && pwd)/$(basename "$MODEL")"

docker run --rm --platform linux/amd64 \
    -v "$SDK":/sdk:ro -v "$OPENMV":/omv -v "$OUTDIR":/out \
    -v "$(dirname "$MODEL_ABS")":/in:ro \
    "$IMAGE" bash -lc "
        # /sdk/gcc/bin is REQUIRED for the N6: stedgeai's --relocatable step
        # links with arm-none-eabi-gcc and fails with a bare 'not found'
        # otherwise, after appearing to succeed at generation.
        export PATH=/sdk/python/bin:/sdk/stedgeai/Utilities/linux:/sdk/gcc/bin:\$PATH
        cd /omv
        python3 tools/modelc.py --input /in/$(basename "$MODEL_ABS") \
            --build-dir /out $(printf '%q ' "${ARGS[@]}")
    "

echo
echo "--- $TARGET output ---"
ls -l "$OUTDIR"/*.tflite 2>/dev/null | awk '{print $5" bytes  "$9}'
