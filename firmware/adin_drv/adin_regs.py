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
STATUS1 = 0x09     # adin1110.c:47
IMASK1 = 0x0D      # adin1110.c:52
MDIOACC = 0x20     # adin1110.c:58
TX_FSIZE = 0x30    # adin1110.c:66
TX = 0x31          # adin1110.c:67
TX_SPACE = 0x32    # adin1110.c:68 -- value*2 = free bytes (adin1110.c:915)

# --- Register bits -------------------------------------------------------
CONFIG1_SYNC = 1 << 15        # adin1110.c:37
CONFIG2_CRC_APPEND = 1 << 5   # adin1110.c:42 -- MAC computes+appends FCS
STATUS1_SPI_ERR = 1 << 10     # adin1110.c:49
STATUS1_RX_RDY = 1 << 4       # adin1110.c:50

# --- MDIOACC fields (adin1110.c:59-64, 89-91) ----------------------------
MDIO_TRDONE = 1 << 31         # transaction-done flag
MDIO_ST_C22 = 0x1 << 28       # ST field GENMASK(29,28); clause-22 = 0x1
MDIO_OP_WR = 0x1 << 26        # OP field GENMASK(27,26)
MDIO_OP_RD = 0x3 << 26
MDIO_PRTAD_SHIFT = 21         # PHY address, GENMASK(25,21)
MDIO_DEVAD_SHIFT = 16         # C22: register number, GENMASK(20,16)
MDIO_DATA_MASK = 0xFFFF       # GENMASK(15,0)

# Internal PHY's MDIO address: 1, verified live in S1 ("spi0.0:01",
# DESIGN.md S1 detail); adin1110.c:527 scans addresses 0-2.
PHY_MDIO_ADDR = 1

# --- C22 MMD-indirect access (MII regs 13/14) ----------------------------
# IEEE 802.3 clause 22.2.4.3 mechanism. This is exactly how phylib reaches
# the ADIN1100's clause-45 MMD registers over this same C22-only MDIOACC
# bus on the Pi nodes (adin1110.c:440-502 exposes C22 only; adin1100.c
# uses phy_read_mmd/phy_write_mmd throughout) -- hardware-proven path.
MII_MMD_CTRL = 0x0D           # function + DEVAD select
MII_MMD_DATA = 0x0E           # address, then data
MMD_FUNC_DATA_NOINC = 0x4000  # CTRL function code: data, no post-increment

# MMD device addresses used by the vendored PHY driver (Linux mdio.h
# values, as referenced from adin1100.c)
MMD_PMAPMD = 0x01             # PMA/PMD
MMD_VEND1 = 0x1E              # vendor MMD 1 -- CRSM block

# ADIN1100 vendor registers (adin1100.c:33-42)
CRSM_SFT_PD_CNTRL = 0x8812    # software power-down control
CRSM_SFT_PD_CNTRL_EN = 1 << 0
CRSM_STAT = 0x8818
CRSM_SFT_PD_RDY = 1 << 1      # 1 = PHY is in software power-down
CRSM_SYS_RDY = 1 << 0

# PMA/PMD status 1 (IEEE 802.3 45.2.1.2, register 1.0001): bit 2 = link
# up, LATCHED-LOW (read twice for current state). Same register phylib's
# genphy_c45_read_link polls for this PHY on the Pi nodes.
PMA_STAT1 = 0x0001
PMA_STAT1_LINK = 1 << 2

# --- Frame geometry (adin1110.c:96-103) ----------------------------------
MAX_BUFF = 2048               # SPI burst buffer cap, incl. headers
FRAME_HEADER_LEN = 2          # 2-byte per-frame port header in the FIFO
INTERNAL_SIZE_HEADER_LEN = 2  # FIFO bookkeeping per frame (adin1110.c:995)
FEC_LEN = 4                   # FCS the MAC appends (adin1110.c:103)
MIN_FRAME_WITH_FCS = 64       # pad rule, adin1110.c:380-386

# --- Expected values -----------------------------------------------------
PHY_ID_VAL = 0x0283BC91  # adin1110.c:105; matched live on both hats (S1/S2)
