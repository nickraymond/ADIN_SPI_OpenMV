# s5_frame_tx.py -- Sprint S5 bite 1 demo: AE3 transmits seq-numbered
# Ethernet frames onto the T1L pair.
#
# Rig: the S4 harness unchanged (D18/D19: AE3 -> hat #2, hat powered from
# nereus000's 3V3 header) PLUS the crimped pair connected hat #2 <-> hat #1
# on nereus001 (the live Linux reference node). nereus000's t1l-sender
# service must be stopped -- it owns the AE3 USB port.
#
# Watch on nereus001:
#   sudo tcpdump -i eth1 -e -x -c 10 ether proto 0x88b5
#
# Expected here:  link UP, then "sent 200/200 frames ..." with 0 errors.
# Expected there: frames from 02:ad:11:10:00:04, seq incrementing by 1
# (bytes 4..7 after the 'BMS5' magic in the hex dump).
#
# Run from nereus000:
#   mpremote connect <dev> mount firmware/adin_drv exec "import s5_frame_tx"

try:
    import machine  # noqa: F401 -- presence check only
    ON_TARGET = True
except ImportError:
    ON_TARGET = False   # host CPython: pure helpers importable for unit tests

import adin_regs as regs
from adin_spi import AdinSpi, AdinError
from s5_frames import (build_eth_frame, DST_MAC, SRC_MAC, ETHERTYPE, MAGIC,
                       DEFAULT_PAYLOAD_LEN as PAYLOAD_LEN)  # noqa: F401 -- re-export

N_FRAMES = 200
LINK_WAIT_MS = 10_000
PROGRESS_EVERY = 50


# ---------------------------------------------------------------- target main

def wait_link(adin, hal, timeout_ms=LINK_WAIT_MS):
    """Poll for PMA link-up; returns wait in ms or raises loudly."""
    waited = 0
    while waited <= timeout_ms:
        if adin.link_up():
            return waited
        hal.delay_ms(100)
        waited += 100
    raise AdinError(
        "adin1100 PHY: no link after %d ms -- check the pair is plugged "
        "hat #2 <-> hat #1, and on nereus001 compare: ip link show eth1; "
        "ethtool eth1 (its side should show link once ours is up)"
        % timeout_ms)


def main():
    import time
    from adin_hal_ae3 import Ae3Hal

    print("S5 frame TX -- ADIN1110 generic SPI, seq-numbered Ethernet frames")
    hal = Ae3Hal()
    print("SPI %d Hz" % hal.baudrate)
    hal.reset_pulse()
    adin = AdinSpi(hal)

    # Gate on the S4-proven check before touching anything else.
    val, _ = adin.read_reg(regs.PHY_ID)
    if val != regs.PHY_ID_VAL:
        raise AdinError("adin1110 PHY_ID: 0x%08X (expected 0x%08X) -- "
                        "wiring/power regressed; run s4_first_light for the "
                        "fallback ladder" % (val, regs.PHY_ID_VAL))
    print("PHY ID: 0x%08X -- OK" % val)

    space = adin.mac_init()
    print("MAC init: TX FIFO space %d B (sanity: expect ~2 KB or more)"
          % space)
    st1, _ = adin.read_reg(regs.STATUS1)
    if st1 & regs.STATUS1_SPI_ERR:
        print("WARNING: STATUS1.SPI_ERR set (0x%08X) -- header parsing "
              "errors on the wire; results untrustworthy" % st1)

    crsm = adin.phy_power_up()
    print("PHY out of power-down (CRSM_STAT=0x%04X, SYS_RDY=%d)"
          % (crsm, 1 if crsm & regs.CRSM_SYS_RDY else 0))

    waited = wait_link(adin, hal)
    print("link UP after %d ms" % waited)

    stalls_total = 0
    t0 = time.ticks_ms()
    for seq in range(N_FRAMES):
        stalls_total += adin.send_frame(build_eth_frame(seq))
        if (seq + 1) % PROGRESS_EVERY == 0:
            print("  sent %d/%d" % (seq + 1, N_FRAMES))
    dt_ms = time.ticks_diff(time.ticks_ms(), t0)

    frame_len = 14 + PAYLOAD_LEN
    kbps = (N_FRAMES * frame_len * 8) / dt_ms if dt_ms else 0
    print("sent %d/%d frames, %d B each, in %d ms -> %.1f fps, %.0f kbps "
          "on-wire payload, %d FIFO stalls"
          % (N_FRAMES, N_FRAMES, frame_len, dt_ms,
             N_FRAMES * 1000.0 / dt_ms if dt_ms else 0, kbps, stalls_total))
    print("verify on nereus001: tcpdump should show %d frames, "
          "EtherType 0x%04X, seq 0..%d" % (N_FRAMES, ETHERTYPE, N_FRAMES - 1))


if ON_TARGET:
    main()
