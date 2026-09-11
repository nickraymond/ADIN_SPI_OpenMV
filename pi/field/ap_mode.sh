#!/usr/bin/env bash
# ap_mode.sh -- the rig as its own wifi network (NetworkManager AP mode).
#
#   ap_mode.sh up      bring the AP up on wlan0 (creates the profile if missing)
#   ap_mode.sh down    drop the AP and let wlan0 rejoin the best client profile
#   ap_mode.sh auto    THE FALLBACK: give the home wifi AP_FALLBACK_S seconds;
#                      if wlan0 is not a connected client by then, start the
#                      nereus-ap unit. Runs at boot (nereus-ap-fallback.service)
#                      and after every `down`, so a rig at sea is never left
#                      hunting for a wifi that is not there (Nick, 2026-09-10).
#   ap_mode.sh status  one line of JSON about wlan0, safe for any user
#
# BORROWED, NOT BUILT: this is Nick's field-proven recipe from
# nereus-vision-dev device/docs/nereus_wlan0_ap_setup.md -- NetworkManager AP
# with ipv4.method shared, which gives the rig 10.42.0.1 and hands clients
# DHCP from NM's own dnsmasq. Two changes for this rig (Nick, 2026-09-10):
# the SSID is the HOSTNAME, and there is no password.
#
# ONE RADIO. wlan0 is either the home-wifi client or the AP, never both, so
# `up` takes the interface from the client and `down` hands it back. Every
# `nmcli` here needs root (pi gets "Insufficient privileges"), which is why
# the workbench never calls this directly: it starts/stops/enables the
# nereus-ap.service unit that wraps it, through the systemctl sudo rule.
#
# WHY autoconnect=no on the AP profile: NetworkManager would otherwise pick
# the AP at every boot on its own (an AP profile is always "available"), and
# the boot-time choice belongs to `systemctl enable nereus-ap`, so it is one
# switch with one owner and `systemctl is-enabled` answers "what will happen
# at the next power-on".
set -uo pipefail

IFACE="${AP_IFACE:-wlan0}"
SSID="${AP_SSID:-$(hostname)}"
AP_CON="${AP_CON:-${SSID}-ap}"
AP_ADDR="10.42.0.1"          # what ipv4.method=shared assigns; verified below
AP_FALLBACK_S="${AP_FALLBACK_S:-60}"
AP_UNIT="${AP_UNIT:-nereus-ap}"
FALLBACK_UNIT="${FALLBACK_UNIT:-nereus-ap-fallback}"

say() { printf 'ap-mode: %s\n' "$*"; }

ensure_profile() {
  if ! nmcli -t -f NAME con show | grep -qx "$AP_CON"; then
    say "creating AP profile $AP_CON (ssid=$SSID, open, $IFACE)"
    nmcli con add type wifi ifname "$IFACE" con-name "$AP_CON" ssid "$SSID" \
      mode ap 802-11-wireless.band bg ipv4.method shared ipv6.method disabled \
      connection.autoconnect no >/dev/null || return 1
  fi
  # Re-asserted every time: a renamed rig gets a renamed network, and the
  # boot choice stays with systemd (see header).
  nmcli con modify "$AP_CON" 802-11-wireless.ssid "$SSID" \
    connection.autoconnect no 802-11-wireless-security.key-mgmt "" 2>/dev/null || true
}

active_on_iface() {
  nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | awk -F: -v i="$IFACE" '$2==i {print $1; exit}'
}

iface_ip() {
  ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1
}

mode_now() {
  local act
  act="$(active_on_iface)"
  [ -z "$act" ] && { echo off; return; }
  if [ "$(nmcli -g 802-11-wireless.mode con show "$act" 2>/dev/null)" = "ap" ]; then echo ap; else echo client; fi
}

# Wait up to $1 seconds for wlan0 to be a client WITH an address.
wait_client() {
  local t0 now
  t0=$(date +%s)
  while :; do
    if [ "$(mode_now)" = "client" ] && [ -n "$(iface_ip)" ]; then return 0; fi
    now=$(date +%s)
    [ $((now - t0)) -ge "$1" ] && return 1
    sleep 2
  done
}

status() {
  local act mode ip
  act="$(active_on_iface)"
  ip="$(iface_ip)"
  mode="off"
  if [ -n "$act" ]; then
    if [ "$(nmcli -g 802-11-wireless.mode con show "$act" 2>/dev/null)" = "ap" ]; then
      mode="ap"
    else
      mode="client"
    fi
  fi
  printf '{"iface":"%s","mode":"%s","active":"%s","ip":"%s","ssid":"%s","ap_profile":"%s"}\n' \
    "$IFACE" "$mode" "$act" "${ip:-}" "$SSID" "$AP_CON"
}

case "${1:-status}" in
  up)
    ensure_profile || { say "FAIL: could not create $AP_CON"; exit 1; }
    say "bringing up $AP_CON on $IFACE"
    nmcli con up "$AP_CON" >/dev/null || { say "FAIL: nmcli con up $AP_CON"; exit 1; }
    # Trust the artifact: the interface must actually hold the AP address.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      [ "$(iface_ip)" = "$AP_ADDR" ] && break
      sleep 1
    done
    if [ "$(iface_ip)" != "$AP_ADDR" ]; then
      say "FAIL: $IFACE is '$(iface_ip)', expected $AP_ADDR"; status; exit 1
    fi
    say "AP up: join '$SSID' (no password), open http://$AP_ADDR:8088/"
    status
    ;;
  down)
    say "dropping $AP_CON; $IFACE rejoins its client profile"
    nmcli con down "$AP_CON" >/dev/null 2>&1 || true
    # `device connect` activates the best autoconnect-able profile for the
    # interface -- whatever the home wifi is called on this rig.
    nmcli dev connect "$IFACE" >/dev/null 2>&1 || say "note: no client profile came up on $IFACE"
    # Re-arm the fallback so "switch to home wifi" tapped at sea, where there
    # is no home wifi, brings the AP back by itself in $AP_FALLBACK_S s.
    if command -v systemctl >/dev/null 2>&1; then
      systemctl start --no-block "$FALLBACK_UNIT" >/dev/null 2>&1 || true
    fi
    status
    ;;
  auto)
    case "$(mode_now)" in
      ap) say "already AP; nothing to do"; status; exit 0 ;;
    esac
    say "giving the home wifi ${AP_FALLBACK_S}s on $IFACE"
    if wait_client "$AP_FALLBACK_S"; then
      say "home wifi ok: $(active_on_iface) at $(iface_ip); staying a client"
      status; exit 0
    fi
    say "no client connection on $IFACE after ${AP_FALLBACK_S}s -- FALLING BACK to AP"
    # Through the unit, not `up` directly, so the switch's state (active)
    # tells the truth on the dashboard.
    if systemctl start "$AP_UNIT"; then
      say "AP fallback up: join '$SSID', open http://$AP_ADDR:8088/"
      status; exit 0
    fi
    say "FAIL: could not start $AP_UNIT"; status; exit 1
    ;;
  status)
    status
    ;;
  *)
    echo "usage: $0 up|down|auto|status" >&2; exit 2 ;;
esac
