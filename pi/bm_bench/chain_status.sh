#!/usr/bin/env bash
# pi/bm_bench/chain_status.sh — bench preflight, read-only (S18 bite D).
#
# Run ON either Pi, before and after every demo:
#   ~/ADIN_SPI_OpenMV/pi/bm_bench/chain_status.sh
#
# Checks the invariants the S19 session broke by hand, in the order they
# actually bite:
#   * exactly ONE bench_apps process per host, and it is the unit's
#   * exactly ONE producer on the frozen S3 ingest (:8081)
#   * the units are installed but NOT enabled at boot
#   * role-local plumbing (AE3 by-id / stream server / FIFO)
#
# Trust artifacts: prints PASS or FAIL per check and exits non-zero if
# any check FAILed. Changes nothing.
set -uo pipefail   # deliberately not -e: every check runs, then a verdict

BIN=/home/pi/bm_sbc_s15/build/all/bm_sbc_bench_apps
FIFO=/run/bm/telemetry.cmd
AE3=/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00
FAILS=0

pass() { printf '  PASS  %s\n' "$*"; }
info() { printf '  ..    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }

# Processes running $BIN, found by exact executable path via /proc — NOT
# by command-line pattern. `pkill -f`-style patterns matched the driving
# SSH command line during S19 and killed the wrong thing; this cannot.
# (Only processes owned by this user are readable, which is all of them
# here — the units run as pi.)
bench_pids() {
  local d out=""
  for d in /proc/[0-9]*; do
    if [[ "$(readlink -f "$d/exe" 2>/dev/null)" == "$BIN" ]]; then
      out+="${d#/proc/} "
    fi
  done
  echo "$out"
}

check_unit() {   # $1 = unit name
  local unit="$1" active enabled mainpid pids n
  if ! systemctl cat "$unit" >/dev/null 2>&1; then
    fail "$unit is not installed — sudo pi/install_stream_service.sh ${unit%.service}"
    return
  fi
  active=$(systemctl is-active "$unit" 2>/dev/null)
  enabled=$(systemctl is-enabled "$unit" 2>/dev/null)
  mainpid=$(systemctl show -p MainPID --value "$unit" 2>/dev/null)
  info "$unit: active=$active enabled=$enabled MainPID=$mainpid"

  # Install-disabled is deliberate: an enabled bm-light opens the AE3's
  # CDC port at boot and fights mpremote, demo_up.sh and flashing.
  if [[ "$enabled" == "enabled" ]]; then
    fail "$unit is ENABLED at boot — sudo systemctl disable $unit (see install_stream_service.sh)"
  else
    pass "$unit not enabled at boot (dev loop keeps the AE3)"
  fi

  # The singleton property, measured rather than assumed.
  pids=$(bench_pids)
  n=$(echo $pids | wc -w)
  if [[ "$active" == "active" ]]; then
    if [[ "$n" -eq 1 && " $pids" == *" $mainpid "* ]]; then
      pass "exactly one bench_apps process ($mainpid), and it is the unit's"
    else
      fail "expected 1 bench_apps process (the unit's $mainpid), found $n: $pids"
    fi
  else
    if [[ "$n" -eq 0 ]]; then
      pass "no bench_apps process while $unit is $active"
    else
      fail "$unit is $active but $n bench_apps process(es) are running: $pids — a hand-run instance will race the unit"
    fi
  fi
}

echo "chain_status — $(hostname) — $(date -Is)"

case "$(hostname)" in
  nereus000)
    echo "role: Light (AE3 CDC leg)"
    check_unit bm-light.service

    if [[ -e "$AE3" ]]; then
      pass "AE3 present at by-id"
    else
      fail "AE3 not at $AE3 — demo_up.sh, or the ae3-usb-unstick ladder"
    fi

    if [[ -w /sys/class/leds/ACT/brightness && -w /sys/class/leds/ACT/trigger ]]; then
      pass "ACT LED sysfs writable (light HAL)"
    else
      info "ACT LED sysfs not writable yet — bm-light's ExecStartPre sets this on start"
    fi

    # Read-only peek at the far end; no ssh, no new dependency.
    if command -v curl >/dev/null 2>&1; then
      stats=$(curl -s --max-time 3 http://nereus001:8080/stats.json)
      if [[ -n "$stats" ]]; then
        info "nereus001 stream server: $stats"
      else
        info "nereus001:8080 did not answer (stream server down, or no route)"
      fi
    fi
    ;;

  nereus001)
    echo "role: Telemetry (head of chain, S3 ingest producer)"
    check_unit bm-telemetry.service

    if systemctl is-active --quiet t1l-stream-server.service; then
      pass "t1l-stream-server active (frozen S3 receiver owns :8080/:8081)"
    else
      fail "t1l-stream-server is not active — sudo systemctl start t1l-stream-server"
    fi

    # The S6 shim's eth1 source is gone on the bench; left running it
    # crash-loops and contends for the same ingest.
    if systemctl is-active --quiet t1l-chunk-shim.service; then
      fail "t1l-chunk-shim is active — sudo systemctl stop t1l-chunk-shim (it fights the ingest)"
    else
      pass "t1l-chunk-shim stopped"
    fi

    # THE S19 WEDGE. One producer = two socket ends on loopback. Four
    # means two telemetry instances: the server reads one and never the
    # other, and the loser silently fills 2,592,256 B (~1,416 frames)
    # and hangs at exactly t=109.
    ends=$(ss -tn 2>/dev/null | grep -c ':8081')
    case "$ends" in
      0) pass "ingest :8081 idle (no producer connected)" ;;
      2) pass "ingest :8081 has exactly one producer (2 socket ends)" ;;
      *) fail "ingest :8081 shows $ends socket ends — expected 0 or 2; more than one producer WILL wedge silently" ;;
    esac

    if systemctl is-active --quiet bm-telemetry.service; then
      if [[ -p "$FIFO" ]]; then
        pass "command FIFO present at $FIFO (bm-cmd.sh)"
      else
        fail "$FIFO missing while bm-telemetry is active — commands would go nowhere"
      fi
    fi
    ;;

  *)
    fail "unknown host '$(hostname)' — this preflight knows nereus000 (Light) and nereus001 (Telemetry)"
    ;;
esac

echo
if [[ "$FAILS" -eq 0 ]]; then
  echo "PASS: $(hostname) clean. Run this on the other Pi too."
  exit 0
fi
echo "FAIL: $FAILS check(s) failed on $(hostname)."
exit 1
