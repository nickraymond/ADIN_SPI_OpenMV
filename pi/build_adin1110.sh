#!/usr/bin/env bash
# Build + install the ADIN1110 out-of-tree modules and a board overlay
# on a Raspberry Pi. Idempotent — safe to re-run (required after any apt
# kernel upgrade). Needs sudo for the install steps.
#
# Usage:  ./build_adin1110.sh [sg|aos]     (from pi/, on the Pi; default sg)
#   sg  = SG-Electronics SPE shield  (sg-adin1110.dts)
#   aos = AOS BOREALIS hat           (aos-adin1110.dts)
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"

HERE="$(cd "$(dirname "$0")" && pwd)"
DRVDIR="$HERE/drivers/adin1110"
BOARD="${1:-sg}"
case "$BOARD" in
    sg)  OVERLAY_NAME="sg-adin1110" ;;
    aos) OVERLAY_NAME="aos-adin1110" ;;
    *)   echo "FAIL: unknown board '$BOARD' (sg|aos)" >&2; exit 1 ;;
esac
DTS="$HERE/overlays/$OVERLAY_NAME.dts"
BOOTFW="/boot/firmware"
KREL="$(uname -r)"
KDIR="/lib/modules/$KREL/build"

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

step "Sanity: kernel headers for running kernel ($KREL)"
[ -e "$KDIR/Makefile" ] || fail \
  "no kernel headers at $KDIR — install with: sudo apt install linux-headers-rpi-2712 (then re-run)"

step "Build modules (out-of-tree, $DRVDIR)"
make -C "$DRVDIR" KDIR="$KDIR"
for m in adin1110 adin1100; do
    ko="$DRVDIR/$m.ko"
    [ -s "$ko" ] || fail "$ko missing/empty after make"
    modinfo "$ko" >/dev/null || fail "modinfo cannot parse $ko"
    echo "OK: $ko ($(modinfo -F vermagic "$ko" | awk '{print $1}'))"
done

step "Install modules (sudo make install -> /lib/modules/$KREL/extra)"
sudo make -C "$DRVDIR" KDIR="$KDIR" install
for m in adin1110 adin1100; do
    modinfo "$m" >/dev/null 2>&1 || sudo depmod -a
    modinfo "$m" >/dev/null || fail "installed module '$m' not visible to modinfo after depmod"
done

step "Compile overlay ($DTS)"
[ -s "$DTS" ] || fail "overlay source $DTS missing"
dtc -@ -I dts -O dtb -o "/tmp/$OVERLAY_NAME.dtbo" "$DTS"
[ -s "/tmp/$OVERLAY_NAME.dtbo" ] || fail "dtc produced no $OVERLAY_NAME.dtbo"

step "Install overlay -> $BOOTFW/overlays/"
[ -d "$BOOTFW/overlays" ] || fail "$BOOTFW/overlays not found — is this a Raspberry Pi?"
sudo install -m 0644 "/tmp/$OVERLAY_NAME.dtbo" "$BOOTFW/overlays/$OVERLAY_NAME.dtbo"

step "Enable overlay in $BOOTFW/config.txt"
if grep -q "^dtoverlay=$OVERLAY_NAME" "$BOOTFW/config.txt"; then
    echo "already enabled"
else
    printf '\n[all]\n# ADIN1110 SPE board (%s) — added by build_adin1110.sh\ndtoverlay=%s\n' \
        "$BOARD" "$OVERLAY_NAME" | sudo tee -a "$BOOTFW/config.txt" >/dev/null
    echo "added dtoverlay=$OVERLAY_NAME"
fi

echo
echo "DONE. Reboot to load the overlay + driver, then run: ./verify_adin1110.sh"
