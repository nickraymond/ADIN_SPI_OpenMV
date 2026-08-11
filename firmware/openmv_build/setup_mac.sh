#!/bin/bash
# setup_mac.sh -- one-time OpenMV firmware dev setup on an Apple Silicon Mac.
#
# Idempotent: safe to re-run; each step checks before acting.
# Installs nothing that needs sudo by itself, but Docker Desktop asks for
# your password ONCE on its own first launch (privileged helper) -- that
# step is yours, not this script's.
#
# What it does:
#   1. brew install --cask docker-desktop   (skipped if docker CLI present)
#   2. Download the OpenMV SDK **linux-x86_64** tarball (the docker build
#      runs an amd64 Linux container under Rosetta; the darwin-arm64 SDK
#      will NOT work inside it), sha256-verified against the vendor's
#      published checksum, into ~/openmv-sdk-<ver>-linux-x86_64.
#   3. Print next steps (OpenMV IDE dmg, VS Code `code` CLI, build_ae3.sh).
#
# SDK version is pinned here to match openmv.git's SDK_VERSION file at the
# rev we build; build_ae3.sh cross-checks and fails loudly on mismatch.

set -euo pipefail

SDK_VERSION="${SDK_VERSION:-1.6.0}"
SDK_TARBALL="openmv-sdk-${SDK_VERSION}-linux-x86_64.tar.xz"
SDK_URL="https://download.openmv.io/sdk/${SDK_TARBALL}"
SDK_DIR="${HOME}/openmv-sdk-${SDK_VERSION}-linux-x86_64"

step()  { printf '\n== %s\n' "$*"; }
fail()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v brew >/dev/null || fail "Homebrew not found -- install from https://brew.sh first"

step "Docker Desktop"
if command -v docker >/dev/null; then
    echo "docker CLI already present: $(command -v docker)"
else
    brew install --cask docker-desktop
    echo "Installed. NOW: open /Applications/Docker.app once, approve the"
    echo "privileged-helper prompt (your password), and wait for 'Docker
    Desktop is running' before building."
fi

step "OpenMV SDK ${SDK_VERSION} (linux-x86_64, for the build container)"
if [ -f "${SDK_DIR}/sdk.version" ] && [ "$(cat "${SDK_DIR}/sdk.version")" = "${SDK_VERSION}" ]; then
    echo "Already installed at ${SDK_DIR}"
else
    tmpfile=$(mktemp)
    trap 'rm -f "$tmpfile"' EXIT
    echo "Downloading ${SDK_URL}"
    curl -fL --progress-bar -o "$tmpfile" "$SDK_URL" || fail "SDK download failed: $SDK_URL"
    expected=$(curl -fsL "${SDK_URL}.sha256" | awk '{print $1}') || fail "checksum fetch failed"
    echo "${expected}  ${tmpfile}" | shasum -a 256 -c - || fail "SDK checksum mismatch -- refusing to install"
    mkdir -p "${SDK_DIR}"
    tar --strip-components=1 -xf "$tmpfile" -C "${SDK_DIR}" || fail "SDK extraction failed"
    echo "${SDK_VERSION}" > "${SDK_DIR}/sdk.version"
    echo "Installed to ${SDK_DIR}"
fi

step "Done. Next steps"
cat <<'EOF'
1. If Docker Desktop was just installed: launch it once and approve the
   password prompt, then leave it running.
2. Build firmware:   firmware/openmv_build/build_ae3.sh
3. Optional, for hands-on flashing at your desk: OpenMV IDE (no brew cask) --
   download the dmg from https://openmv.io/pages/download
4. Optional, VS Code `code` CLI: in VS Code, Cmd+Shift+P ->
   "Shell Command: Install 'code' command in PATH"
EOF
