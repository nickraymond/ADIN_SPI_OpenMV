#!/usr/bin/env bash
# Install and configure the LiFePO4wered/Pi+ CLI + daemon on a field rig.
#
# WHY THIS EXISTS: a fresh rig powers itself off ~5 minutes after every boot.
# PI_BOOT_TO (300 s) is a BOOT WATCHDOG -- the Pi+ waits that long for the
# daemon to signal a good boot, then assumes the boot failed and cuts power.
# The cure is the daemon, and the daemon ships only as source, so bring-up is
# a race against the very watchdog it fixes.
#
# So this script is IDEMPOTENT and RESUMABLE: if the watchdog cuts power
# mid-run, press the Pi+ button and run it again. Every step re-checks state
# and skips what is already done, and the log survives on the SD card.
#
# Run as root:  sudo bash pi/field/install_lifepo4.sh
set -u

SRC_DIR=${SRC_DIR:-/opt/LiFePO4wered-Pi}
REPO=${REPO:-https://github.com/xorbit/LiFePO4wered-Pi.git}
LOG=${LOG:-/var/log/lifepo4-bringup.log}

exec > >(tee -a "$LOG") 2>&1
echo "=== install_lifepo4.sh $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "--- $* ---"; }

[ "$(id -u)" = 0 ] || fail "must run as root (sudo)"

step "0. host"
echo "model:    $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
echo "os:       $(. /etc/os-release; echo "$PRETTY_NAME")"
echo "kernel:   $(uname -srm)"
echo "uptime:   $(uptime -p)"

step "1. I2C bus"
# The vendor library talks to the Pi+ over /dev/i2c-1. On a fresh image the
# ARM I2C bus is off in the device tree, and dtparam cannot be turned on at
# runtime -- it needs a boot. Enable it, then say so LOUDLY.
NEED_BOOT=0
if [ -e /dev/i2c-1 ]; then
  echo "/dev/i2c-1 present"
else
  echo "/dev/i2c-1 MISSING -- enabling i2c_arm"
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_i2c 0 || fail "raspi-config do_i2c failed"
  else
    CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
    [ -f "$CFG" ] || fail "no config.txt found (looked in /boot/firmware and /boot)"
    grep -q '^dtparam=i2c_arm=on' "$CFG" || echo 'dtparam=i2c_arm=on' >> "$CFG"
    echo "appended dtparam=i2c_arm=on to $CFG"
  fi
  grep -q '^i2c-dev' /etc/modules 2>/dev/null || echo i2c-dev >> /etc/modules
  modprobe i2c-dev 2>/dev/null || true
  [ -e /dev/i2c-1 ] || NEED_BOOT=1
fi

step "2. build dependencies"
# A watchdog cut mid-apt can leave dpkg half-configured; heal that first.
dpkg --configure -a >/dev/null 2>&1 || true
MISSING=""
for p in git build-essential i2c-tools libsystemd-dev; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
  echo "installing:$MISSING"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq || echo "WARN: apt-get update failed, trying install anyway"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $MISSING || fail "apt-get install failed:$MISSING"
else
  echo "already present: git build-essential i2c-tools libsystemd-dev"
fi

step "3. vendor source"
if [ -d "$SRC_DIR/.git" ]; then
  echo "already cloned at $SRC_DIR"
  git -C "$SRC_DIR" rev-parse --short HEAD
else
  rm -rf "$SRC_DIR"
  git clone --depth 1 "$REPO" "$SRC_DIR" || fail "git clone failed"
  git -C "$SRC_DIR" rev-parse --short HEAD
fi

step "4. build + install"
if command -v lifepo4wered-cli >/dev/null 2>&1; then
  echo "lifepo4wered-cli already installed at $(command -v lifepo4wered-cli)"
else
  make -C "$SRC_DIR" -j"$(nproc)" || fail "make failed"
  make -C "$SRC_DIR" install || fail "make install failed"
  command -v lifepo4wered-cli >/dev/null 2>&1 || fail "make install did not put lifepo4wered-cli on PATH"
fi

step "5. daemon unit"
# Do not assume the unit name -- ask systemd what the vendor installed.
UNIT=$(systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -i lifepo | head -1)
if [ -z "$UNIT" ]; then
  echo "no lifepo* unit found in systemd; searching for an installed daemon binary"
  command -v lifepo4wered-daemon >/dev/null 2>&1 \
    || fail "neither a systemd unit nor lifepo4wered-daemon were installed -- inspect $SRC_DIR"
  fail "daemon binary exists but no unit was installed -- inspect $SRC_DIR for its service file"
fi
echo "unit: $UNIT"
systemctl enable "$UNIT"  >/dev/null 2>&1 || fail "systemctl enable $UNIT failed"
systemctl restart "$UNIT" || fail "systemctl restart $UNIT failed"
sleep 2
STATE=$(systemctl is-active "$UNIT" || true)
echo "is-active: $STATE"
[ "$STATE" = active ] || { systemctl status "$UNIT" --no-pager -l | head -30; fail "$UNIT is not active -- the boot watchdog is still armed"; }

step "6. registers (read only -- setting is a separate, deliberate step)"
for r in VIN VBAT VOUT IOUT AUTO_BOOT AUTO_SHDN_TIME PI_BOOT_TO VBAT_SHDN SHDN_DELAY PI_RUNNING RTC_TIME; do
  printf '%-16s %s\n' "$r" "$(lifepo4wered-cli get "$r" 2>&1 | tr -d '\r')"
done

echo
if [ "$NEED_BOOT" = 1 ]; then
  echo "RESULT: I2C was just enabled and needs a BOOT to appear."
  echo "        Let the watchdog cut power (or press the Pi+ button), then run this again."
  exit 3
fi
echo "RESULT: daemon $UNIT is active -- the 5-minute boot watchdog is answered."
