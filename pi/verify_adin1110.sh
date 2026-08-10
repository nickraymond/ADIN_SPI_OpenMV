#!/usr/bin/env bash
# Post-reboot verification for the ADIN1110 bring-up (S1 demo checks).
# Trusts artifacts, not exit codes: checks the module is loaded, the
# driver probed, the interface exists, and ethtool names the driver.
# Exits nonzero with a named failing check otherwise.
set -uo pipefail
export PATH="$PATH:/usr/sbin:/sbin"

pass=0; failed=0
ok()   { echo "PASS: $*"; pass=$((pass+1)); }
bad()  { echo "FAIL: $*"; failed=$((failed+1)); }

echo "== S1 verify: ADIN1110 on $(hostname), kernel $(uname -r) =="

# 1. overlay actually applied (device node exists in the live tree)
if [ -d /proc/device-tree/soc ] || [ -d /proc/device-tree ]; then
    if grep -rqs "adi,adin1110" /proc/device-tree/ 2>/dev/null; then
        ok "overlay applied — adi,adin1110 node in live device tree"
    else
        bad "no adi,adin1110 node in /proc/device-tree — overlay not applied (check config.txt + reboot)"
    fi
fi

# 2. modules loaded
for m in adin1110 adin1100; do
    if lsmod | grep -q "^$m"; then ok "module $m loaded"; else bad "module $m not loaded"; fi
done

# 3. driver probe visible in kernel log
if sudo dmesg | grep -iq "adin1110"; then
    ok "dmesg mentions adin1110:"
    sudo dmesg | grep -i "adin1110\|adin1100" | tail -5 | sed 's/^/    /'
else
    bad "no adin1110 lines in dmesg"
fi

# 4. a network interface backed by driver 'adin1110' exists
ADIN_IF=""
for ifc in /sys/class/net/*; do
    name="$(basename "$ifc")"
    drv="$(ethtool -i "$name" 2>/dev/null | awk '/^driver:/{print $2}')"
    if [ "$drv" = "adin1110" ]; then ADIN_IF="$name"; break; fi
done
if [ -n "$ADIN_IF" ]; then
    ok "interface '$ADIN_IF' uses driver adin1110"
    ip -br link show "$ADIN_IF" | sed 's/^/    /'
    ethtool -i "$ADIN_IF" | sed 's/^/    /'
else
    bad "no network interface reports driver 'adin1110' (ip -br link: $(ip -br link | awk '{print $1}' | tr '\n' ' '))"
fi

echo
echo "== $pass passed, $failed failed =="
[ "$failed" -eq 0 ]
