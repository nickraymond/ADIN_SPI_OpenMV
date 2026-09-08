#!/bin/bash
# build_n6.sh -- reproducible OPENMV_N6 firmware build in docker (Mac host).
#
# Sibling of build_ae3.sh. Same docker/Rosetta/SDK plumbing, same
# trust-artifacts-not-exit-codes verification, two deliberate differences:
#
#   1. It defaults to a SEPARATE tree (~/openmv-dev/openmv-n6), not the AE3
#      dev clone. That clone carries this repo's in-flight AE3 patches
#      (framebuffer sticky-highwater, jpege MVE colorconvert); an N6 build
#      must not silently inherit them and then be reported as "upstream".
#
#   2. --pr <N> builds an unmerged upstream pull request head by number.
#      That is the whole point for S31: hardware H.264 on the N6 lives in
#      openmv/openmv#3247, which is upstream work, not a private fork.
#
# The docker/Makefile safe.directory patch (firmware/openmv_patches/0003) is
# REQUIRED, not optional: without it the container's `git submodule update`
# dies with "detected dubious ownership" -> make Error 128 (re-measured
# 2026-09-07). It is applied here as a real commit on a local build branch
# rather than as a dirty-tree edit, so the firmware's embedded version label
# stays clean and checkable instead of degrading to "...dirty".
#
# Usage:
#   build_n6.sh                        # upstream master
#   build_n6.sh --rev v5.0.1           # exact release tag
#   build_n6.sh --pr 3247              # an unmerged PR head
#   build_n6.sh --pr 3247 --incremental
#   build_n6.sh --openmv-dir DIR --sdk-dir DIR
#
# Output: <openmv-dir>/build/OPENMV_N6/bin/ plus MANIFEST.txt alongside.
# THIS SCRIPT DOES NOT FLASH ANYTHING. See N6_H264_FLASH.md.

set -euo pipefail

REV="master"
PR=""
OPENMV_DIR="${HOME}/openmv-dev/openmv-n6"
SDK_DIR=""
INCREMENTAL=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAFEDIR_PATCH="${HERE}/../openmv_patches/0003-docker-makefile-git-safedir.patch"

while [ $# -gt 0 ]; do
    case "$1" in
        --rev)         REV="$2"; shift 2 ;;
        --pr)          PR="$2"; shift 2 ;;
        --openmv-dir)  OPENMV_DIR="$2"; shift 2 ;;
        --sdk-dir)     SDK_DIR="$2"; shift 2 ;;
        --incremental) INCREMENTAL=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found -- run setup_mac.sh, then launch Docker Desktop"
docker info >/dev/null 2>&1   || fail "docker daemon not running -- launch Docker Desktop (open -a Docker)"
[ -f "${SAFEDIR_PATCH}" ]     || fail "missing ${SAFEDIR_PATCH}"

# ---------------------------------------------------------------------------
# Tree: clone or sync to the requested rev / PR head.
# ---------------------------------------------------------------------------
if [ ! -d "${OPENMV_DIR}/.git" ]; then
    mkdir -p "$(dirname "${OPENMV_DIR}")"
    # Seed from a sibling clone when one exists -- same filesystem means
    # hardlinked objects, so the second tree costs ~0 disk for history.
    if [ -d "${HOME}/openmv-dev/openmv/.git" ]; then
        git clone --quiet "${HOME}/openmv-dev/openmv" "${OPENMV_DIR}"
        git -C "${OPENMV_DIR}" remote set-url origin https://github.com/openmv/openmv.git
    else
        git clone --quiet https://github.com/openmv/openmv.git "${OPENMV_DIR}"
    fi
fi

if [ -n "$(git -C "${OPENMV_DIR}" status --porcelain --untracked-files=no --ignore-submodules=dirty)" ]; then
    echo "WARN: ${OPENMV_DIR} has uncommitted changes -- skipping rev sync, building the tree as-is"
    UPSTREAM_REF="$(git -C "${OPENMV_DIR}" rev-parse HEAD)"
else
    if [ -n "${PR}" ]; then
        # Fetch to FETCH_HEAD, not to a local branch: a local branch of the
        # same name may already be checked out here, and git refuses to
        # fetch into a checked-out branch.
        git -C "${OPENMV_DIR}" fetch --quiet --force origin "pull/${PR}/head" \
            || fail "could not fetch openmv/openmv PR #${PR}"
        UPSTREAM_REF="$(git -C "${OPENMV_DIR}" rev-parse FETCH_HEAD)"
    else
        git -C "${OPENMV_DIR}" fetch --quiet --tags origin
        git -C "${OPENMV_DIR}" rev-parse --verify --quiet "origin/${REV}" >/dev/null \
            && UPSTREAM_REF="origin/${REV}" || UPSTREAM_REF="${REV}"
    fi
    git -C "${OPENMV_DIR}" checkout --quiet --detach "${UPSTREAM_REF}" \
        || fail "rev '${UPSTREAM_REF}' not found in openmv.git"
fi

UPSTREAM_SHA=$(git -C "${OPENMV_DIR}" rev-parse HEAD)
UPSTREAM_DESC=$(git -C "${OPENMV_DIR}" describe --tags --always)

# ---------------------------------------------------------------------------
# Build branch: upstream head + the docker safe.directory harness patch, as a
# commit. Keeps `git describe` clean so the embedded label is verifiable.
# The patch touches docker/Makefile ONLY -- no compiled source.
# ---------------------------------------------------------------------------
BUILD_BRANCH="build/n6-${UPSTREAM_SHA:0:10}"
git -C "${OPENMV_DIR}" branch -D "${BUILD_BRANCH}" >/dev/null 2>&1 || true
git -C "${OPENMV_DIR}" checkout --quiet -b "${BUILD_BRANCH}" "${UPSTREAM_SHA}"
if git -C "${OPENMV_DIR}" apply --check "${SAFEDIR_PATCH}" >/dev/null 2>&1; then
    git -C "${OPENMV_DIR}" apply "${SAFEDIR_PATCH}"
    git -C "${OPENMV_DIR}" -c user.name="build_n6.sh" -c user.email="build@local" \
        commit --quiet -m "docker: allow git safe.directory in the dev container (build harness only)" \
        -- docker/Makefile
    echo "== applied build-harness patch 0003 (docker/Makefile only)"
else
    echo "== build-harness patch 0003 already present upstream -- skipped"
fi
git -C "${OPENMV_DIR}" submodule update --init --depth=50 --quiet

GIT_DESC=$(git -C "${OPENMV_DIR}" describe --tags --always --dirty)
GIT_SHA10=$(git -C "${OPENMV_DIR}" rev-parse --short=10 HEAD)

# ---------------------------------------------------------------------------
# SDK: version must match the tree we're building.
# ---------------------------------------------------------------------------
WANT_SDK=$(cat "${OPENMV_DIR}/SDK_VERSION")
SDK_DIR="${SDK_DIR:-${HOME}/openmv-sdk-${WANT_SDK}-linux-x86_64}"
[ -d "${SDK_DIR}" ] || fail "SDK not found at ${SDK_DIR} -- run setup_mac.sh (SDK_VERSION=${WANT_SDK})"
HAVE_SDK=$(cat "${SDK_DIR}/sdk.version" 2>/dev/null || echo "?")
[ "${HAVE_SDK}" = "${WANT_SDK}" ] || \
    fail "SDK ${HAVE_SDK} != ${WANT_SDK} wanted by openmv @ ${GIT_DESC} -- rerun setup_mac.sh with SDK_VERSION=${WANT_SDK}"

if [ "${INCREMENTAL}" -eq 0 ]; then
    make -C "${OPENMV_DIR}/docker" clean-dev
fi
echo "== Building OPENMV_N6 @ ${GIT_DESC} (upstream ${UPSTREAM_DESC}) -- amd64 under Rosetta, ~15-40 min from clean"
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
    make -C "${OPENMV_DIR}/docker" build-firmware-dev TARGET=OPENMV_N6 SDK_DIR="${SDK_DIR}"

# ---------------------------------------------------------------------------
# Trust artifacts, not exit codes.
# ---------------------------------------------------------------------------
BIN_DIR="${OPENMV_DIR}/build/OPENMV_N6/bin"
# The 7 files an official firmware_OPENMV_N6.zip carries (checked against the
# 2026-09-05 development release).
for f in firmware.bin firmware.elf bootloader.bin bootloader.elf openmv.bin romfs0.img; do
    [ -s "${BIN_DIR}/${f}" ] || fail "missing/empty artifact: ${BIN_DIR}/${f}"
done
FW_SIZE=$(stat -f%z "${BIN_DIR}/firmware.bin")
# Official N6 firmware.bin is ~1.99 MB (dev release 2026-09-05: 1,987,840 B).
# Too small = a stub; too large = it will not fit the N6's firmware slot.
[ "${FW_SIZE}" -gt 1048576 ] || fail "firmware.bin is ${FW_SIZE} B -- implausibly small"
[ "${FW_SIZE}" -lt 4194304 ] || fail "firmware.bin is ${FW_SIZE} B -- implausibly large"
BL_SIZE=$(stat -f%z "${BIN_DIR}/bootloader.bin")
[ "${BL_SIZE}" -gt 16384 ] || fail "bootloader.bin is ${BL_SIZE} B -- implausibly small"

# The embedded label is git-describe reformatted by micropython's
# makeversionhdr.py (dashes become dots).
FW_STRING=$(strings "${BIN_DIR}/firmware.elf" | grep -m1 -oE 'OpenMV [^;]+; MicroPython [^;]+' || true)
OPENMV_LABEL=$(printf '%s' "${FW_STRING}" | sed -nE 's/^OpenMV ([^;]+);.*/\1/p')
case "${OPENMV_LABEL}" in
    *"${GIT_SHA10}"*|"${GIT_DESC}") : ;;
    *) echo "WARN: firmware label '${OPENMV_LABEL:-<none>}' matches neither ${GIT_SHA10} nor ${GIT_DESC} -- verify on flash day" ;;
esac
case "${OPENMV_LABEL}" in
    *dirty*) echo "WARN: firmware label is -dirty; the manifest rev does not fully describe this build" ;;
esac

# Feature probe: does this image actually carry the H.264 binding? A build
# that "succeeded" without it is the exact failure this repo keeps hitting.
if strings "${BIN_DIR}/firmware.bin" | grep -q 'H264Encoder'; then
    H264="yes"
else
    H264="no"
fi

MANIFEST="${BIN_DIR}/MANIFEST.txt"
{
    echo "built:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "target:       OPENMV_N6"
    echo "upstream:     ${UPSTREAM_DESC} (${UPSTREAM_SHA})"
    [ -n "${PR}" ] && echo "upstream_pr:  openmv/openmv#${PR}"
    echo "build_rev:    ${GIT_DESC}"
    echo "build_sha:    ${GIT_SHA10}"
    echo "harness:      0003-docker-makefile-git-safedir.patch (docker/Makefile only)"
    [ -n "${OPENMV_LABEL}" ] && echo "openmv_label: ${OPENMV_LABEL}"
    echo "sdk:          ${WANT_SDK} linux-x86_64"
    echo "codec.H264Encoder in image: ${H264}"
    (cd "${BIN_DIR}" && shasum -a 256 firmware.bin bootloader.bin openmv.bin romfs0.img)
} > "${MANIFEST}"

echo "== OK. Artifacts in ${BIN_DIR}"
cat "${MANIFEST}"
echo
echo "NOT FLASHED. Flash procedure: firmware/openmv_build/N6_H264_FLASH.md"
