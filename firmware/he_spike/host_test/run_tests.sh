#!/bin/bash
# run_tests.sh -- build + run the HE-spike host tests (plain clang, no
# docker, no hardware). Follows the bm_spike host_test pattern.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${HERE}/build"
mkdir -p "${OUT}"

cc -std=gnu11 -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
    -Wall -Wextra -Werror -Wno-unused-parameter \
    -I"${HERE}/../src" \
    "${HERE}/test_he_spike.c" \
    "${HERE}/../src/rpmsg_remote.c" \
    "${HERE}/../src/bench.c" \
    -o "${OUT}/test_he_spike"

"${OUT}/test_he_spike"
