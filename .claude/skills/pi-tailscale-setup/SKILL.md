---
name: pi-tailscale-setup
description: Bring a new/rebuilt Raspberry Pi onto the tailnet over SSH — find it on the LAN, set up key auth, install Tailscale, register a hostname, and hand the human the auth URL. Use when a Pi is not reachable via Tailscale, needs Tailscale installed, or after re-flashing an SD card.
---

# Pi Tailscale Setup

*Vendored from bm_cam_legacy (2026-08-10) and adapted for this repo: here
`nereus000` and `nereus001` are OUR bench nodes (S2 link pair) — fine to
work on. Do not touch `bmcam000` (customer test camera).*

Purpose: get a headless Raspberry Pi from "only reachable on the LAN
(maybe)" to "SSH-reachable via Tailscale under a known hostname".
Human-in-the-loop for passwords and the Tailscale authorization click —
the agent never handles the Pi password or Tailscale credentials.

## Assumptions / gotchas learned the hard way

- **Tailscale identity lives on the SD card.** Swapping cards between
  boards moves the tailnet hostname with the card. NEVER trust a hostname —
  always verify the physical board with `cat /proc/device-tree/model`
  before doing anything hardware-specific.
- **Nick's bench has two routers with (historically) the same SSID**:
  UniFi (`192.168.1.x`) and Google Nest WiFi (`192.168.86.x`). The Mac and
  the Pi can silently land on different subnets and be mutually
  unreachable. Check the Mac's own subnet first:
  `ifconfig en0 | grep "inet "`.
- **Tailscale SSH check mode**: nereus000 demands per-session browser
  re-auth (`login.tailscale.com/a/...` URL printed on connect; session
  hangs until approved). Long-running agent ssh commands stall on this —
  approve the URL, or adjust the tailnet SSH policy check period.
- Do not touch `bmcam000` (customer camera). Confirm the target with the
  human if there is any doubt.

## Steps

1. **Find the Pi.** Try in order:
   - `ping <hostname>.local` (mDNS, same subnet only)
   - Known LAN IP from the human
   - Ping-sweep the Mac's subnet and grep ARP for Pi vendor MACs:
     `for i in $(seq 1 254); do (ping -c 1 -t 1 192.168.X.$i >/dev/null 2>&1 &); done; sleep 5; arp -a | grep -iE "b8:27:eb|dc:a6:32|e4:5f:1|d8:3a:dd|2c:cf:67|28:cd:c1"`
   - If not found, the Pi is probably on the *other* router's subnet — ask
     the human to move the Mac to that network or force the Pi over.

2. **Key auth.** If `ssh -o BatchMode=yes pi@<ip>` fails with
   `Permission denied (publickey,password)`, have the **human** run in a
   Mac terminal (not an SSH session):
   `ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@<ip>`
   The agent must not handle the Pi password.

3. **Verify the board** (read-only):
   `hostname; cat /proc/device-tree/model; head -2 /etc/os-release; sudo -n true && echo SUDO_OK`
   Stop and ask the human if the model string doesn't match the expected
   board.

4. **Install Tailscale** (official script, adds apt repo):
   `curl -fsSL https://tailscale.com/install.sh | sh`

5. **Register with the right hostname.** `tailscale up` blocks while
   printing the auth URL, so run it detached and read the URL from a log:
   `sudo sh -c "setsid tailscale up --hostname=<name> </dev/null >/tmp/ts_root.log 2>&1 &"`
   Wait ~15–30 s, then `sudo cat /tmp/ts_root.log` and give the
   `https://login.tailscale.com/a/...` URL to the human to authorize.
   - Gotcha: Debian sets `fs.protected_regular=2` — root gets "Permission
     denied" appending to a `/tmp` file created earlier by user `pi`. Use
     a fresh root-owned log filename.

6. **Verify + clean up:**
   `ssh pi@<name> 'tailscale ip -4'` from the Mac (proves tailnet DNS +
   connectivity), confirm no lingering `tailscale up` processes, remove
   `/tmp/ts_root.log`.

## Success criteria

SSH from the Mac to `pi@<name>` (tailnet name, not LAN IP) works with key
auth, and `cat /proc/device-tree/model` shows the expected board.
