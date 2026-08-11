# adin_regs.py -- ADIN1110 register constants (Sprint S4)
#
# Every value here is sourced from the vendored mainline Linux driver
# (pi/drivers/adin1110/adin1110.c), which runs against these exact hats
# on the Pi nodes -- hardware-proven, not datasheet-transcribed.
# Cite the source line when adding a constant. Do not guess.

# --- SPI command header bits (adin1110.c:93-94) --------------------------
CD = 0x80          # control/data flag: set for register access
WRITE = 0x20       # set for write, clear for read

# --- Frame geometry, generic SPI without CRC (adin1110.c:96-101) ---------
RD_HEADER_LEN = 3  # [cmd, addr_lo, turnaround]
WR_HEADER_LEN = 2  # [cmd, addr_lo]
REG_LEN = 4        # registers are 32-bit, big-endian on the wire

# --- MAC register addresses ----------------------------------------------
PHY_ID = 0x01      # adin1110.c:31  -- readable without MDIO
CONFIG1 = 0x04     # adin1110.c:36
CONFIG2 = 0x06     # adin1110.c:39
STATUS0 = 0x08     # adin1110.c:45
IMASK1 = 0x0D      # adin1110.c:52

# --- Expected values -----------------------------------------------------
PHY_ID_VAL = 0x0283BC91  # adin1110.c:105; matched live on both hats (S1/S2)
