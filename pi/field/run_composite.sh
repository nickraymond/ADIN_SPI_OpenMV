#!/usr/bin/env bash
# Launch the composite demo with an interpreter that has mpremote + numpy.
# Same picker as run_field_stream.sh (PEP 668 keeps mpremote in a venv).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
for py in "${FIELD_PYTHON:-}" "$HOME/mpv/bin/python" "$(command -v python3 || true)"; do
  [ -n "$py" ] && [ -x "$py" ] || continue
  if "$py" -c "import mpremote, serial, numpy, PIL" >/dev/null 2>&1; then PY="$py"; break; fi
done
if [ -z "${PY:-}" ]; then
  echo "composite: no python with mpremote+pyserial+numpy+PIL found" >&2
  echo "  fix: sudo apt-get install -y python3-numpy python3-pil" >&2
  echo "       python3 -m venv --system-site-packages ~/mpv && ~/mpv/bin/pip install mpremote" >&2
  exit 1
fi
echo "composite: using $PY" >&2
exec "$PY" -u "$ROOT/pi/field/composite_run.py" "$@"
