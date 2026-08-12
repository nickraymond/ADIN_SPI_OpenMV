#!/bin/bash
# run_tests.sh -- build + run the bm_he host tests (plain clang, no
# docker, no hardware). Follows the bm_spike/he_spike host_test pattern.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${HERE}/../src"
BM="${HERE}/../vendor/bm_core"
OUT="${HERE}/build"
mkdir -p "${OUT}"

# On-target sources compiled UNCHANGED (that's the point of the harness):
# bm_net_mock.c, bm_stubs.c, and vendored device.c. bm_config.h's
# bm_debug -> he_dbg_printf resolves to the test's sink.
cc -std=gnu11 -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
    -Wall -Wextra -Werror -Wno-unused-parameter \
    -I"${SRC}" \
    -I"${BM}/bcmp" -I"${BM}/common" -I"${BM}/network" \
    -I"${BM}/third_party" -I"${BM}/third_party/crc" \
    -I"${BM}/third_party/tinycbor/src" \
    -DCBOR_CUSTOM_ALLOC_INCLUDE='"tinycbor_alloc.h"' \
    "${HERE}/test_bm_he.c" \
    "${HERE}/fake_bm_os.c" \
    "${SRC}/bm_net_mock.c" \
    "${SRC}/bm_stubs.c" \
    "${BM}/common/device.c" \
    -o "${OUT}/test_bm_he"

"${OUT}/test_bm_he"
