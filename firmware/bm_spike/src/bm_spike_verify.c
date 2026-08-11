// bm_spike_verify.c -- see bm_spike_verify.h. Driver files are vendored
// UNMODIFIED; everything chip-specific lives there, everything spike-
// specific lives here.

#include <string.h>

#include "adin2111.h"
#include "adi_mac.h"

#include "bm_spike_verify.h"

int bm_spike_read_phyid(uint32_t *phyid, int *init_result)
{
    // MAC-layer-only bring-up: same config fields bm_adin2111.c passes
    // down, minus the PHY/port layers that verdict 2 exercises.
    // ADI_MAC_DEVICE_SIZE is hand-counted for ILP32 targets and comes up
    // short of sizeof(adi_mac_Device_t) on an LP64 host, so size for both.
    enum { MAC_MEM_SIZE = (ADI_MAC_DEVICE_SIZE > sizeof(adi_mac_Device_t))
                              ? ADI_MAC_DEVICE_SIZE
                              : sizeof(adi_mac_Device_t) };
    static uint8_t mac_mem[MAC_MEM_SIZE];
    adi_mac_DriverConfig_t cfg = {
        .pDevMem = mac_mem,
        .devMemSize = sizeof(mac_mem),
        .fcsCheckEn = false,
    };
    adi_mac_Device_t *mac = NULL;

    *phyid = 0;
    // MAC_Init is static; macDriverEntry is the driver's exported route
    // (same table adin2111.c itself uses, adin2111.c:121). Its result is
    // the identity-gate verdict (COMM_TIMEOUT expected on a 1110, see
    // header); the handle is assigned before the embedded reset, and
    // MAC_ReadRegister only requires state != UNINITIALIZED.
    *init_result = (int)macDriverEntry.Init(&mac, &cfg, NULL);
    if (mac == NULL) {
        return *init_result;
    }
    return (int)MAC_ReadRegister(mac, ADDR_MAC_PHYID, phyid);
}

int bm_spike_full_init(void)
{
    // Mirrors bm_adin2111.c's DEVICE_STRUCT / DEVICE_MEMORY / DRIVER_CONFIG
    // pattern (bm_core @ d4ecc38, lines 21-33) so this is the driver called
    // exactly the way bm_core calls it.
    static adin2111_DeviceStruct_t dev;
    static uint8_t dev_mem[ADIN2111_DEVICE_SIZE];
    adin2111_DriverConfig_t cfg = {
        .pDevMem = dev_mem,
        .devMemSize = sizeof(dev_mem),
        .fcsCheckEn = false,
        .tsTimerPin = ADIN2111_TS_TIMER_MUX_NA,
        .tsCaptPin = ADIN2111_TS_CAPT_MUX_NA,
    };
    memset(&dev, 0, sizeof(dev));
    return (int)adin2111_Init(&dev, &cfg);
}

const char *bm_spike_result_str(int result)
{
    switch ((adi_eth_Result_e)result) {
    case ADI_ETH_SUCCESS:            return "SUCCESS";
    case ADI_ETH_MDIO_TIMEOUT:       return "MDIO_TIMEOUT";
    case ADI_ETH_COMM_ERROR:         return "COMM_ERROR";
    case ADI_ETH_COMM_TIMEOUT:       return "COMM_TIMEOUT";
    case ADI_ETH_UNSUPPORTED_DEVICE: return "UNSUPPORTED_DEVICE";
    case ADI_ETH_HW_ERROR:           return "HW_ERROR";
    case ADI_ETH_SPI_ERROR:          return "SPI_ERROR";
    case ADI_ETH_PROTECTION_ERROR:   return "PROTECTION_ERROR";
    case ADI_ETH_SW_RESET_TIMEOUT:   return "SW_RESET_TIMEOUT";
    case ADI_ETH_VALUE_MISMATCH_ERROR: return "VALUE_MISMATCH_ERROR";
    default:                         return "OTHER";
    }
}
