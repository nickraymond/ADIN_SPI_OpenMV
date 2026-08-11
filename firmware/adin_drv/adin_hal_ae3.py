# adin_hal_ae3.py -- AE3 board HAL for the ADIN1110 driver (Sprint S4)
#
# THIN LAYER: everything board-specific lives here; the protocol core
# (adin_spi.py) sees only the small call surface below. Porting to the N6
# (or to C) means rewriting this file only.
#
# Pin plan (SPEC.md, D2/D3):
#   P0/P1/P2 = SPI0 MOSI/MISO/SCLK
#   P3       = CS, manual GPIO (SS is not peripheral-driven on the AE3)
#   P4       = ADIN RESET_N out (active low; hat has 100k pull-up + RC)
#   P5       = ADIN INT_N in, INTERNAL PULL-UP -- the AOS board has no
#              INT_N pull-up (D14); Pi-side the overlay fixes this, here
#              the AE3 must supply it (D18).

import time
import machine

SPI_BUS = 0
DEFAULT_BAUD = 5_000_000        # bring-up speed per SPEC; raise later
PIN_CS = "P3"
PIN_RESET = "P4"
PIN_IRQ = "P5"

# Reset timing from the vendored driver (adin1110.c:1101-1108): hold
# RESET_N low 10 ms, then wait 90 ms before the first SPI transaction.
RESET_LOW_MS = 10
RESET_SETTLE_MS = 90


class Ae3Hal:
    def __init__(self, baudrate=DEFAULT_BAUD):
        self.rst = machine.Pin(PIN_RESET, machine.Pin.OUT, value=1)
        self.irq = machine.Pin(PIN_IRQ, machine.Pin.IN, machine.Pin.PULL_UP)
        self._init_spi(baudrate)

    def _init_spi(self, baudrate):
        """(Re)init SPI, then claim CS back as GPIO.

        Defensive ordering: machine.SPI() runs BEFORE the CS Pin is
        configured, so that if SPI init ever claims the P3 pad for
        peripheral SS, the later Pin() call routes the pad back to a
        GPIO we control. (Suspected during S4 bring-up; the failures
        turned out to be miswiring, so this order is precaution, not a
        proven requirement -- first light passed with it in place.)
        CS idles high (deasserted); RESET_N idles high (not in reset).
        """
        self.baudrate = baudrate
        self.spi = machine.SPI(SPI_BUS, baudrate=baudrate,
                               polarity=0, phase=0)
        self.cs = machine.Pin(PIN_CS, machine.Pin.OUT, value=1)

    def set_baudrate(self, baudrate):
        """Re-init SPI at a new clock (used by the bring-up clock sweep)."""
        self.spi.deinit()
        self._init_spi(baudrate)

    def xfer(self, tx):
        """Full-duplex transfer with CS held for the whole transaction."""
        rx = bytearray(len(tx))
        self.cs.value(0)
        try:
            self.spi.write_readinto(tx, rx)
        finally:
            self.cs.value(1)
        return rx

    def reset_pulse(self):
        """Hardware-reset the ADIN1110 per the driver's proven timing.

        The SPI lines must stay quiet during and right after reset -- the
        MISO pin doubles as a config strap (adin1110.c:1096-1098). CS is
        already high and nothing touches the bus until this returns.
        """
        self.rst.value(0)
        time.sleep_ms(RESET_LOW_MS)
        self.rst.value(1)
        time.sleep_ms(RESET_SETTLE_MS)

    def irq_level(self):
        """Current INT_N level (1 = idle/pulled up, 0 = asserted)."""
        return self.irq.value()
