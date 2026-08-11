# adin_bringup.py -- shared AE3-side bring-up: PHY-ID gate with clock
# fallback, then MAC init + PHY power-up + link wait.
#
# Extracted from s5_tx_load.py (S5-demo-proven sequence) so S6+ scripts
# don't copy it; s5_tx_load.py keeps its own inline copy as a historical
# demo artifact. Portable except for hal.set_baudrate/reset_pulse, which
# every HAL provides.

import adin_regs as regs
from adin_spi import AdinError

FALLBACK_BAUD = 5_000_000    # bite-1-proven bring-up clock (S4)


def bring_up(hal, adin, fallback_baud=FALLBACK_BAUD):
    """PHY-ID gate with clock fallback, then MAC + PHY + link."""
    val, _ = adin.read_reg(regs.PHY_ID)
    if val != regs.PHY_ID_VAL:
        print("PHY ID bad at %d MHz (0x%08X) -- falling back to %d MHz"
              % (hal.baudrate // 1_000_000, val, fallback_baud // 1_000_000))
        hal.set_baudrate(fallback_baud)
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
