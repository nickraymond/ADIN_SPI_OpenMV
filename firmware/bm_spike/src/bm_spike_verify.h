// bm_spike_verify.h -- S9 bite 1: the two spike verdicts, on top of the
// UNMODIFIED bm_core adin2111 driver (vendor/adin2111, bm_core @ d4ecc38).
//
// Portable core: compiled both into the AE3 firmware (usermod) and the
// host test harness (clang + hal_mock.c). The HAL binding is whatever
// implementation of adi_hal.h is linked in.

#ifndef BM_SPIKE_VERIFY_H
#define BM_SPIKE_VERIFY_H

#include <stdint.h>

// ADIN1110 (our silicon, S1/S4-measured) vs ADIN2111 (driver's expectation,
// RSTVAL_MAC_PHYID in ADIN2111_mac_addr_rdef.h).
#define BM_SPIKE_PHYID_ADIN1110 (0x0283BC91u)
#define BM_SPIKE_PHYID_ADIN2111 (0x0283BCA1u)

// Verdict 1 -- OA transport: MAC-layer init + MAC_ReadRegister(ADDR_MAC_PHYID)
// through the driver's own OA framing (protection on).
//
// Source-pinned subtlety (adi_mac.c:568): MAC_Init itself soft-resets the
// MAC-PHY and runs waitDeviceReady's PHYID==RSTVAL_MAC_PHYID poll -- the
// 2111 identity gate fires at MAC-layer init, not just full init. On a
// 1110, *init_result is therefore EXPECTED to be ADI_ETH_COMM_TIMEOUT;
// the device handle is valid regardless (assigned before the reset), and
// the PHYID read afterwards is the actual transport verdict. Returns the
// read's adi_eth_Result_e as int; *phyid holds the raw readback.
int bm_spike_read_phyid(uint32_t *phyid, int *init_result);

// Verdict 2 -- unmodified full init: adin2111_Init() with the same static
// memory pattern bm_adin2111.c uses. Source-predicted result on a 1110:
// ADI_ETH_COMM_TIMEOUT from waitDeviceReady's PHYID equality poll.
int bm_spike_full_init(void);

// Short name for an adi_eth_Result_e value ("SUCCESS", "COMM_TIMEOUT", ...).
const char *bm_spike_result_str(int result);

#endif
