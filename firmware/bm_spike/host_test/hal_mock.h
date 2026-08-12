#ifndef BM_SPIKE_HAL_MOCK_H
#define BM_SPIKE_HAL_MOCK_H

#include <stdint.h>

void hal_mock_set_phyid(uint32_t phyid);
int  hal_mock_phyid_reads(void);
void hal_mock_reset_counts(void);
void hal_mock_corrupt_protection(int enable);

// S9 bite 3 additions -- datapath emulation.

// Restore every mock register (MAC + PHY) and capture buffer to power-on
// defaults. Does NOT touch the phyid/protection knobs above.
void hal_mock_reset_model(void);

// PHY (clause-45) identity served on the MDIO path; defaults 0x0283/0xBC91.
void hal_mock_set_phy_devid(uint16_t devid1, uint16_t devid2);

// AN_STATUS link bit.
void hal_mock_set_link(int up);

// Raw MAC register readout for assertions (mms 0 map only).
uint32_t hal_mock_mac_reg(uint32_t addr);

// TX frames captured from OA data (DNC=1) chunks: count of completed
// (EV-terminated) frames, the last one's length and bytes.
int             hal_mock_tx_frames(void);
uint32_t        hal_mock_tx_frame_len(void);
const uint8_t  *hal_mock_tx_frame(void);

#endif
