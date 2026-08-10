#!/usr/bin/env bash
# One-time T1L node setup (S2): static IP on the ADIN1110 interface via
# NetworkManager, plus iperf3. Idempotent — safe to re-run.
#
#   sudo ./pi/setup_t1l_ip.sh 1     # nereus000 -> 192.168.7.1/24
#   sudo ./pi/setup_t1l_ip.sh 2     # nereus001 -> 192.168.7.2/24
#
# The profile has NO gateway and never-default routing, so the T1L link
# cannot hijack the default route from wlan0/tailscale.
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"

die() { echo "FAIL: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo (needs nmcli system changes + apt)"
NODE="${1:-}"
case "$NODE" in
    1|2) IP="192.168.7.$NODE" ;;
    *) die "usage: sudo $0 <1|2>   (1 = .7.1, 2 = .7.2)" ;;
esac

# Find the ADIN1110-backed interface — never assume it is eth1.
ADIN_IF=""
for ifc in /sys/class/net/*; do
    name="$(basename "$ifc")"
    drv="$(ethtool -i "$name" 2>/dev/null | awk '/^driver:/{print tolower($2)}')"
    [ "$drv" = "adin1110" ] && { ADIN_IF="$name"; break; }
done
[ -n "$ADIN_IF" ] || die "no interface with driver adin1110 — hat mounted? overlay in config.txt? (see pi/verify_adin1110.sh)"
echo "ADIN1110 interface: $ADIN_IF"

if ! command -v iperf3 >/dev/null; then
    echo "installing iperf3..."
    apt-get install -y iperf3 </dev/null
fi
command -v iperf3 >/dev/null || die "iperf3 still missing after install"

# Create or update the 't1l' NM profile. never-default + no gateway.
if nmcli -t -f NAME con show | grep -qx "t1l"; then
    echo "updating existing 't1l' profile"
    nmcli con mod t1l connection.interface-name "$ADIN_IF" \
        ipv4.method manual ipv4.addresses "$IP/24" ipv4.gateway "" \
        ipv4.never-default yes ipv6.method disabled \
        connection.autoconnect yes
else
    echo "creating 't1l' profile"
    nmcli con add type ethernet ifname "$ADIN_IF" con-name t1l \
        ipv4.method manual ipv4.addresses "$IP/24" \
        ipv4.never-default yes ipv6.method disabled \
        connection.autoconnect yes
fi

# Activate. With no carrier (pair unplugged) activation waits/fails —
# that's fine, autoconnect grabs it when the link comes up.
nmcli con up t1l >/dev/null 2>&1 || echo "note: profile not active yet (no carrier?) — will autoconnect when the pair links up"

echo "== state =="
ip -br addr show "$ADIN_IF"
echo "PASS: $ADIN_IF configured as $IP/24 (profile 't1l')"
