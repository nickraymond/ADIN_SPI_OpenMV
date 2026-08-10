#!/usr/bin/env bash
# S2 demo: link + speed test between the two T1L nodes over the pair.
# Wraps ping + iperf3 into one repeatable pass/fail run; numbers are
# parsed from iperf3 JSON (trust artifacts, not exit codes).
#
#   ./bench/t1l_link_test.sh server              # on nereus001 (192.168.7.2)
#   ./bench/t1l_link_test.sh client [peer-ip]    # on nereus000; default peer 192.168.7.2
#
# Pass gates (S2, approved 2026-08-10): ping 0% loss · TCP >= 8.0 Mbps
# each direction · UDP @ 8 Mbps < 1% loss.
set -uo pipefail
export PATH="$PATH:/usr/sbin:/sbin"

MODE="${1:-}"
PEER="${2:-192.168.7.2}"
TCP_GATE_MBPS=8.0
UDP_RATE=8M
UDP_LOSS_GATE=1.0
PING_COUNT=20
IPERF_SECS=10

pass=0; failed=0
ok()  { echo "PASS: $*"; pass=$((pass+1)); }
bad() { echo "FAIL: $*"; failed=$((failed+1)); }
die() { echo "FAIL: $*" >&2; exit 1; }

# --- preflight (both modes): find the ADIN interface, require carrier + IP
ADIN_IF=""
for ifc in /sys/class/net/*; do
    name="$(basename "$ifc")"
    drv="$(ethtool -i "$name" 2>/dev/null | awk '/^driver:/{print tolower($2)}')"
    [ "$drv" = "adin1110" ] && { ADIN_IF="$name"; break; }
done
[ -n "$ADIN_IF" ] || die "no interface with driver adin1110 (hat/overlay — see pi/verify_adin1110.sh)"

CARRIER="$(cat "/sys/class/net/$ADIN_IF/carrier" 2>/dev/null || echo 0)"
MY_IP="$(ip -4 -o addr show "$ADIN_IF" | awk '{print $4}' | cut -d/ -f1)"

echo "== T1L link test [$MODE] on $(hostname): $ADIN_IF ip=${MY_IP:-none} carrier=$CARRIER =="
[ "$CARRIER" = "1" ] || die "$ADIN_IF has no carrier — pair unplugged or far end down"
[ -n "$MY_IP" ] || die "$ADIN_IF has no IPv4 — run: sudo ./pi/setup_t1l_ip.sh <1|2>"
command -v iperf3 >/dev/null || die "iperf3 missing — run: sudo ./pi/setup_t1l_ip.sh <1|2>"

case "$MODE" in
server)
    echo "iperf3 server on $MY_IP — leave running, Ctrl-C to stop"
    exec iperf3 -s -B "$MY_IP"
    ;;
client)
    ;;
*)
    die "usage: $0 server | client [peer-ip]"
    ;;
esac

# --- 1. ping
echo "-- ping $PEER x$PING_COUNT --"
PING_OUT="$(ping -c "$PING_COUNT" -i 0.2 "$PEER" 2>&1 | tail -2)"
echo "$PING_OUT" | sed 's/^/    /'
LOSS="$(echo "$PING_OUT" | grep -o '[0-9.]*% packet loss' | cut -d% -f1)"
if [ "${LOSS:-100}" = "0" ]; then ok "ping: 0% loss"; else bad "ping: ${LOSS:-?}% loss (gate: 0%)"; fi

# --- helper: run iperf3, return Mbps from JSON (sum of received stream)
tcp_mbps() {  # $1 = extra args ("" or -R)
    iperf3 -c "$PEER" -t "$IPERF_SECS" $1 -J 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d['end']['sum_received']['bits_per_second']/1e6, 2))" 2>/dev/null
}

# --- 2. TCP forward + reverse
for dir in forward reverse; do
    [ "$dir" = "reverse" ] && EXTRA="-R" || EXTRA=""
    echo "-- iperf3 TCP $dir ${IPERF_SECS}s --"
    MBPS="$(tcp_mbps "$EXTRA")"
    if [ -z "$MBPS" ]; then
        bad "TCP $dir: iperf3 produced no result (server running on $PEER?)"
    elif python3 -c "import sys; sys.exit(0 if float('$MBPS') >= $TCP_GATE_MBPS else 1)"; then
        ok "TCP $dir: $MBPS Mbps (gate: >= $TCP_GATE_MBPS)"
    else
        bad "TCP $dir: $MBPS Mbps (gate: >= $TCP_GATE_MBPS)"
    fi
done

# --- 3. UDP at target rate, loss %
echo "-- iperf3 UDP $UDP_RATE ${IPERF_SECS}s --"
UDP_JSON="$(iperf3 -c "$PEER" -u -b "$UDP_RATE" -t "$IPERF_SECS" -J 2>/dev/null)"
read -r UDP_MBPS UDP_LOSS <<< "$(echo "$UDP_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d['end']['sum']
print(round(s['bits_per_second']/1e6,2), round(s['lost_percent'],3))" 2>/dev/null)"
if [ -z "${UDP_LOSS:-}" ]; then
    bad "UDP: iperf3 produced no result"
elif python3 -c "import sys; sys.exit(0 if float('$UDP_LOSS') < $UDP_LOSS_GATE else 1)"; then
    ok "UDP: $UDP_MBPS Mbps, $UDP_LOSS% loss (gate: < $UDP_LOSS_GATE%)"
else
    bad "UDP: $UDP_MBPS Mbps, $UDP_LOSS% loss (gate: < $UDP_LOSS_GATE%)"
fi

echo
echo "== $pass passed, $failed failed =="
[ "$failed" -eq 0 ]
