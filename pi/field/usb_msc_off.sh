#!/usr/bin/env bash
# Unbind usb-storage from every OpenMV interface, and install the udev rule
# so later hotplugs are covered too. Idempotent; safe to run repeatedly.
#
# Matched on VID 37c5 (OpenMV) for BOTH boards: 16e3 = AE3, 1206 = N6.
# The boards are never used as disks here -- transfer is serial (mpremote).
set -u
RULE=/etc/udev/rules.d/99-openmv-no-msc.rules

cat > "$RULE" <<'RULEEOF'
# Keep usb-storage off BOTH OpenMV boards (AE3 37c5:16e3, N6 37c5:1206).
# See pi/ae3_flash/99-ae3-no-msc.rules for the measured root cause: probing
# the MSC volume while the board is busy livelocks in USB device resets,
# and every reset re-binds cdc_acm -- presenting as a wedged or missing
# board on healthy hardware. Matched on interface CLASS (08/06/50) so a
# firmware that reorders interfaces cannot silently re-enable the disk.
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface", \
  ATTRS{idVendor}=="37c5", ENV{INTERFACE}=="8/6/80", \
  RUN+="/bin/sh -c 'echo -n %k > /sys/bus/usb/drivers/usb-storage/unbind 2>/dev/null || true'"
RULEEOF

udevadm control --reload 2>/dev/null || true

# Unbind anything already attached (the rule only fires on future adds).
n=0
for dev in /sys/bus/usb/drivers/usb-storage/*:*; do
  [ -e "$dev" ] || continue
  k=$(basename "$dev")
  vid=$(cat "/sys/bus/usb/devices/${k%%:*}/idVendor" 2>/dev/null || echo "")
  if [ "$vid" = "37c5" ]; then
    echo -n "$k" > /sys/bus/usb/drivers/usb-storage/unbind 2>/dev/null && n=$((n+1))
    echo "usb_msc_off: unbound usb-storage from $k"
  fi
done
echo "usb_msc_off: rule installed, $n interface(s) unbound"
