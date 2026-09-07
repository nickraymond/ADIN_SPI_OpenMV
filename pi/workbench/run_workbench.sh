#!/usr/bin/env bash
# Launch the workbench with an interpreter that can import mpremote.
#
# The workbench's reconcile step shells into boards via mpremote, and on
# Debian 13 (PEP 668) that lives in a venv rather than the system python.
# Same picker as pi/field/run_field_stream.sh; falls back to python3, so
# rigs where mpremote IS importable system-wide are unaffected.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

for py in "${FIELD_PYTHON:-}" "$HOME/mpv/bin/python" "$(command -v python3 || true)"; do
  [ -n "$py" ] && [ -x "$py" ] || continue
  if "$py" -c "import mpremote" >/dev/null 2>&1; then PY="$py"; break; fi
done
PY="${PY:-$(command -v python3)}"
echo "workbench: using $PY" >&2
exec "$PY" -u "$ROOT/workbench/workbench.py" "$@"
