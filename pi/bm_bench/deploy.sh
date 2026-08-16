#!/usr/bin/env bash
# pi/bm_bench/deploy.sh — pin-verify + deploy the S15 bench build on a Pi.
#
# Run ON the Pi (nereus000 or nereus001), from anywhere:
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/deploy.sh
#
# What it does:
#   1. Verifies ~/bm_sbc_s15 is at the pinned bm_sbc + bm_core revs
#      (fails loudly on drift — REV-23 discipline).
#   2. Builds the 'all' preset + runs ctest.
#   3. Installs this directory's TOMLs to ~/bm_bench/ and creates the
#      config-partition dir.
#
# Trust artifacts: the script ends by printing the binary path, its
# build id, and the verified revs. If it didn't print PASS, it didn't work.
#
# Pins (S18 bite B, 2026-08-16 — bm_sbc +2 on top of bite A: the telemetry
# role's loopback JSON control socket (/run/bm/bench.sock) and still-save
# with JSON sidecars, plus a bench_ctl ctest. Pi-side only: NO camera_svc.h
# change, no wire_capture_t change, no bridge or HE firmware change, so the
# AE3 artifacts from S19 stay exactly as they are. bm_core pin UNCHANGED.)
# Fork branches:
#   bm_sbc:  feature/udp-transport (nickraymond/bm_sbc)  — base 17ea904
#   bm_core: bench/d4ecc38-obs     (nickraymond/bm_core) — base d4ecc38
BM_SBC_PIN="8c0ff7a7c6ac55f4025101bed58876890eca52bf"
BM_CORE_PIN="eec6e82f0010fe43b96464bb8215c9c9ffd7665b"

set -euo pipefail

REPO="$HOME/bm_sbc_s15"
BENCH_DIR="$HOME/bm_bench"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -d "$REPO/.git" ]] || fail "$REPO not found — clone the bm_sbc fork first"

# 1. Pin verification -------------------------------------------------------
sbc_rev=$(git -C "$REPO" rev-parse HEAD)
core_rev=$(git -C "$REPO/lib/bm_core" rev-parse HEAD)
[[ "$sbc_rev" == "$BM_SBC_PIN" ]] ||
  fail "bm_sbc rev drift: HEAD=$sbc_rev pinned=$BM_SBC_PIN"
[[ "$core_rev" == "$BM_CORE_PIN" ]] ||
  fail "bm_core rev drift: HEAD=$core_rev pinned=$BM_CORE_PIN"
[[ -z "$(git -C "$REPO" status --porcelain)" ]] ||
  fail "bm_sbc working tree not clean"
echo "pins OK: bm_sbc=${sbc_rev:0:7} bm_core=${core_rev:0:7}"

# 2. Build + test -----------------------------------------------------------
cd "$REPO"
cmake --preset all >/dev/null
cmake --build --preset all 2>&1 | tail -1
ctest --test-dir build/all --output-on-failure | tail -2

# 3. Install bench config ---------------------------------------------------
mkdir -p "$BENCH_DIR/cfg"
cp "$HERE"/telemetry.toml "$HERE"/light.toml "$BENCH_DIR/"
echo "configs installed to $BENCH_DIR/"

# Verdict -------------------------------------------------------------------
BIN="$REPO/build/all/bm_sbc_multinode"
[[ -x "$BIN" ]] || fail "binary missing: $BIN"
echo "PASS: $BIN"
echo "      bm_sbc=$sbc_rev"
echo "      bm_core=$core_rev"
