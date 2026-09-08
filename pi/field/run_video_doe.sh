#!/usr/bin/env bash
# Launch the field viewer with an interpreter that can import mpremote.
#
# Debian 13 (trixie) is PEP 668 "externally managed", so mpremote cannot be
# pip-installed into the system python. The rig keeps it in a venv instead.
# A recipe cannot hardcode /home/pi/... -- that would be host-specific in a
# file the other rigs also read -- so the search happens here, in order:
#
#   1. $FIELD_PYTHON            (explicit override wins)
#   2. ~/mpv/bin/python         (this rig's venv; see the bring-up skill)
#   3. python3                  (fine wherever mpremote is importable)
#
# Fails LOUDLY and names the fix rather than starting a viewer whose serial
# boards can never attach -- a page showing one working camera and two dead
# panels is exactly the plausible-but-wrong artifact this repo keeps paying
# for (CLAUDE.md rule 6).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

pick() {
  for py in "${FIELD_PYTHON:-}" "$HOME/mpv/bin/python" "$(command -v python3 || true)"; do
    [ -n "$py" ] && [ -x "$py" ] || continue
    if "$py" -c "import mpremote, serial" >/dev/null 2>&1; then
      echo "$py"; return 0
    fi
  done
  return 1
}

PY="$(pick)" || {
  echo "video-doe: no python with mpremote+pyserial found." >&2
  echo "       tried \$FIELD_PYTHON, ~/mpv/bin/python, python3" >&2
  echo "       fix:  python3 -m venv --system-site-packages ~/mpv \\" >&2
  echo "             && ~/mpv/bin/pip install mpremote pyserial" >&2
  exit 1
}

echo "video-doe: using $PY" >&2
exec "$PY" -u "$ROOT/pi/field/video_doe.py" "$@"
