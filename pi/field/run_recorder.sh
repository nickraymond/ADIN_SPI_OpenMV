#!/usr/bin/env bash
# Launch the video recorder with an interpreter that can import mpremote.
#
# Same problem and same solution as run_field_stream.sh, and it bit this
# recorder for real on nereus002: Debian 13 is PEP 668 "externally managed",
# so mpremote cannot be pip-installed into the system python and the rig keeps
# it in a venv. Started under plain python3 the recorder found no boards at all
# and reported "ModuleNotFoundError: No module named 'mpremote'" -- correctly,
# but the fix belongs here rather than in a recipe, because a recipe is read by
# every rig and must not hardcode /home/pi/...
#
# Search order:
#   1. $FIELD_PYTHON            (explicit override wins)
#   2. ~/mpv/bin/python         (the field rig's venv; see field-rig-bringup)
#   3. python3                  (fine wherever mpremote is importable)
#
# Fails LOUDLY and names the fix rather than starting a recorder that can never
# attach a board -- a page that records nothing while looking healthy is the
# plausible-but-wrong artifact this repo keeps paying for (CLAUDE.md rule 6).
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
  echo "recorder: no python with mpremote+pyserial found." >&2
  echo "          tried \$FIELD_PYTHON, ~/mpv/bin/python, python3" >&2
  echo "          fix:  python3 -m venv --system-site-packages ~/mpv \\" >&2
  echo "                && ~/mpv/bin/pip install mpremote pyserial" >&2
  exit 1
}

echo "recorder: using $PY" >&2
exec "$PY" -u "$ROOT/pi/field/recorder_web.py" "$@"
