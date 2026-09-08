#!/bin/bash
# Measure N6 -> Pi USB throughput. Run ON the Pi that owns the board.
#
#   ./usb_throughput.sh /dev/serial/by-id/usb-MicroPython_Pyboard_...-if00
#
# Prints the link's sustained payload rate and what it means for HD 30 fps.
set -euo pipefail
PORT="${1:?usage: usb_throughput.sh <by-id port>}"
MPR="${MPREMOTE:-$HOME/mpv/bin/mpremote}"
PROBE="$(dirname "$0")/usb_throughput_probe.py"
TOTAL_MB=4

case "$PORT" in *by-id*) : ;; *) echo "refusing a non-by-id port" >&2; exit 2 ;; esac

echo "== USB descriptor speed (12 = full speed, 480 = high speed)"
for d in /sys/bus/usb/devices/*/; do
    if [ -f "$d/idVendor" ] && [ "$(cat "$d/idVendor")" = "37c5" ]; then
        printf "  %s  idProduct=%s  speed=%s Mbps\n" \
            "$(basename "$d")" "$(cat "$d/idProduct")" "$(cat "$d/speed")"
    fi
done

echo "== pushing ${TOTAL_MB} MB off the board"
START=$(date +%s.%N)
"$MPR" connect "$PORT" run "$PROBE" > /tmp/n6_usb_dump.bin 2>/tmp/n6_usb_err.txt || {
    echo "FAIL: mpremote returned non-zero -- do NOT retry immediately;"
    echo "      give the port 35 s of silence first."; cat /tmp/n6_usb_err.txt; exit 1; }
END=$(date +%s.%N)

BYTES=$(stat -c%s /tmp/n6_usb_dump.bin)
python3 - "$START" "$END" "$BYTES" "$TOTAL_MB" <<'PY'
import sys
start, end, got, total_mb = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
elapsed = end - start
mb = total_mb * 1024 * 1024
rate = mb / elapsed / (1024 * 1024)
print("  host wall time   %.2f s" % elapsed)
print("  payload          %d MB (captured %d B incl. framing)" % (total_mb, got))
print("  SUSTAINED RATE   %.2f MB/s  (%.1f Mbps)" % (rate, rate * 8))
print()
print("  What HD 1280x800 at 30 fps needs (measured bytes/frame, S31):")
rows = [("MJPEG q90", 426720), ("H.264 quality-matched", 279007),
        ("H.264 32 Mbps", 135260), ("H.264 16 Mbps", 69636),
        ("H.264 8 Mbps", 35884)]
for name, bpf in rows:
    need = bpf * 30 / (1024 * 1024)
    print("    %-24s %6.2f MB/s   %s" % (name, need,
          "FITS" if need <= rate * 0.85 else
          ("MARGINAL" if need <= rate else "DOES NOT FIT")))
print()
print("  'FITS' allows 15% headroom; a link at 100% of its rate has no room")
print("  for a keyframe spike, and every row above is a MEAN.")
PY
