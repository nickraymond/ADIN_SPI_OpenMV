# s5_tx_load.py -- Sprint S5 bite 2: sustained TX load for the loss demo.
#
# Free-runs seq-numbered 1000-byte frames for 65 s at 20 MHz SPI (the
# S0-proven max useful clock; falls back to 5 MHz with a loud note if the
# PHY ID read fails at 20). Pair with bench/frame_counter.py on the Pi:
#
#   ssh pi@nereus001 "cd ~/ADIN_SPI_OpenMV && \
#       sudo python3 bench/frame_counter.py --iface eth1 --duration 60"
#
# Run from nereus000 (start the counter FIRST):
#   mpremote connect <dev> mount firmware/adin_drv exec "import s5_tx_load"

try:
    import machine  # noqa: F401 -- presence check only
    ON_TARGET = True
except ImportError:
    ON_TARGET = False   # host CPython: constants importable for unit tests

import adin_regs as regs
from adin_spi import AdinSpi, AdinError
import s5_frames

DURATION_S = 65            # covers a 60 s counter window with margin
FRAME_LEN = 1000           # payload 986 + 14 B Ethernet header
LOAD_BAUD = 20_000_000     # S0: 4.89 Mbps effective, 0 errors at 20 MHz
FALLBACK_BAUD = 5_000_000  # bite-1 proven clock
STATS_EVERY_MS = 5_000


def bring_up(hal, adin):
    """PHY-ID gate with clock fallback, then MAC + PHY + link."""
    val, _ = adin.read_reg(regs.PHY_ID)
    if val != regs.PHY_ID_VAL:
        print("PHY ID bad at %d MHz (0x%08X) -- falling back to %d MHz"
              % (LOAD_BAUD // 1_000_000, val, FALLBACK_BAUD // 1_000_000))
        hal.set_baudrate(FALLBACK_BAUD)
        hal.reset_pulse()
        val, _ = adin.read_reg(regs.PHY_ID)
        if val != regs.PHY_ID_VAL:
            raise AdinError("adin1110 PHY_ID: 0x%08X at fallback clock too "
                            "-- run s4_first_light for the fallback ladder"
                            % val)
    print("PHY ID: 0x%08X -- OK at %d MHz" % (val, hal.baudrate // 1_000_000))
    space = adin.mac_init()
    print("MAC init: TX FIFO space %d B" % space)
    adin.phy_power_up()
    waited = adin.wait_link()
    print("link UP after %d ms" % waited)


def main():
    import time
    from adin_hal_ae3 import Ae3Hal

    print("S5 TX load -- %d s of %d B seq-numbered frames" %
          (DURATION_S, FRAME_LEN))
    hal = Ae3Hal(baudrate=LOAD_BAUD)
    hal.reset_pulse()
    adin = AdinSpi(hal)
    bring_up(hal, adin)

    template = bytearray(s5_frames.build_eth_frame(0, FRAME_LEN - 14))
    seq = 0
    stalls = 0
    t0 = time.ticks_ms()
    last_stat_t, last_stat_seq = t0, 0
    while True:
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, t0)
        if elapsed >= DURATION_S * 1000:
            break
        s5_frames.patch_seq(template, seq)
        stalls += adin.send_frame(template)
        seq += 1
        if time.ticks_diff(now, last_stat_t) >= STATS_EVERY_MS:
            dt = time.ticks_diff(now, last_stat_t)
            n = seq - last_stat_seq
            print("  t=%3ds  %6d sent  %5.1f fps  %4.2f Mbps"
                  % (elapsed // 1000, seq, n * 1000.0 / dt,
                     n * FRAME_LEN * 8.0 / dt / 1000))
            last_stat_t, last_stat_seq = now, seq

    dt_ms = time.ticks_diff(time.ticks_ms(), t0)
    s0, s1, spi_err = adin.status_summary()
    print("sent %d frames (%d B) in %.1f s -> %.1f fps, %.2f Mbps payload, "
          "%d FIFO stalls" % (adin.tx_frames, adin.tx_bytes, dt_ms / 1000.0,
                              adin.tx_frames * 1000.0 / dt_ms,
                              adin.tx_bytes * 8.0 / dt_ms / 1000, stalls))
    print("MAC status: STATUS0=0x%08X STATUS1=0x%08X SPI_ERR=%s"
          % (s0, s1, "YES -- results suspect" if spi_err else "no"))
    print("verdict lives on the Pi: frame_counter.py must show 0%% loss "
          "over its 60 s window (last seq sent: %d)" % (seq - 1))


if ON_TARGET:
    main()
