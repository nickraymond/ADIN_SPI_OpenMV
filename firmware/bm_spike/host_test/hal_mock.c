// hal_mock.c -- host-side adi_hal.h implementation simulating an ADIN
// MAC-PHY on the far end of the OA SPI link (protection enabled), so the
// UNMODIFIED vendored driver runs end-to-end on the host.
//
// Wire format implemented from vendor/adin2111/adi_spi_oa.c (not guessed):
//   control TX = 4 B cmd header (big-endian, bitfields per
//   adi_mac_OaCtrlCmdHeader_t) ... total exchange for cnt words =
//   8 B (header + echo) + 8 B/word (data + bitwise complement, PROT_EN).
//   RX[4:8] must echo TX[0:4] exactly (CONTROL_END check); data words are
//   big-endian, each followed by its complement (oaCtrlCmdReadData).

#include <stdint.h>
#include <string.h>

#include "adi_hal.h"
#include "adi_spi_oa.h"    // adi_mac_OaCtrlCmdHeader_t -- driver's own layout
#include "hal_mock.h"

static uint32_t s_phyid = 0x0283BC91u;  // default: our ADIN1110
static int s_phyid_reads = 0;
static int s_corrupt_protection = 0;

static HAL_Callback_t s_spi_cb = NULL;
static void *s_spi_cb_param = NULL;

void hal_mock_set_phyid(uint32_t phyid)      { s_phyid = phyid; }
int  hal_mock_phyid_reads(void)              { return s_phyid_reads; }
void hal_mock_reset_counts(void)             { s_phyid_reads = 0; }
void hal_mock_corrupt_protection(int enable) { s_corrupt_protection = enable; }

static uint32_t be32_load(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static void be32_store(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

// Register model: only what the spike paths touch. Unknown registers
// read as 0 (matches an ADIN with nothing latched).
static uint32_t mock_reg_read(uint32_t mms, uint32_t addr)
{
    if (mms == 0 && addr == 0x001u) {   // ADDR_MAC_PHYID
        s_phyid_reads++;
        return s_phyid;
    }
    if (mms == 0 && addr == 0x008u) {   // ADDR_MAC_STATUS0
        return 0x00000040u;             // RESETC set: reset always "done"
    }
    return 0;
}

uint32_t HAL_SpiReadWrite(uint8_t *pBufferTx, uint8_t *pBufferRx, uint32_t nbBytes, bool useDma)
{
    (void)useDma;
    memset(pBufferRx, 0, nbBytes);

    adi_mac_OaCtrlCmdHeader_t hdr;
    hdr.VALUE32 = be32_load(pBufferTx);

    if (hdr.DNC == 0 && nbBytes >= 8) {
        // Control transaction: echo header, then serve/absorb data words.
        memcpy(&pBufferRx[4], &pBufferTx[0], 4);
        uint32_t cnt = (uint32_t)hdr.LEN + 1;
        for (uint32_t i = 0; i < cnt && (8 + 8 * i + 8) <= nbBytes; i++) {
            uint8_t *slot = &pBufferRx[8 + 8 * i];
            if (hdr.WNR == 0) {   // read
                uint32_t val = mock_reg_read(hdr.MMS, hdr.ADDR + i);
                be32_store(&slot[0], val);
                uint32_t raw = be32_load(&slot[0]);
                uint32_t comp = s_corrupt_protection ? raw : ~raw;
                slot[4] = (uint8_t)(comp >> 24);
                slot[5] = (uint8_t)(comp >> 16);
                slot[6] = (uint8_t)(comp >> 8);
                slot[7] = (uint8_t)comp;
            }
            // writes: absorbed (register model is read-only for the spike)
        }
    }
    // Data (DNC=1) transactions never happen in the spike paths; RX stays 0.

    if (s_spi_cb) {
        s_spi_cb(s_spi_cb_param, 0, NULL);
    }
    return 0;
}

uint32_t HAL_SpiRegisterCallback(HAL_Callback_t const *spiCallback, void *hDevice)
{
    s_spi_cb = (HAL_Callback_t)(void *)spiCallback;
    s_spi_cb_param = hDevice;
    return 0;
}

uint32_t HAL_RegisterCallback(HAL_Callback_t const *intCallback, void *hDevice)
{
    (void)intCallback;
    (void)hDevice;
    return 0;
}

uint32_t HAL_EnterCriticalSection(void) { return 0; }
uint32_t HAL_ExitCriticalSection(void)  { return 0; }
uint32_t HAL_EnableIrq(void)            { return 0; }
uint32_t HAL_DisableIrq(void)           { return 0; }
uint32_t HAL_GetEnableIrq(void)         { return 0; }
uint32_t HAL_SetPendingIrq(void)        { return 0; }
uint32_t HAL_GetPendingIrq(void)        { return 0; }
uint32_t HAL_Init_Hook(void)            { return 0; }
uint32_t HAL_UnInit_Hook(void)          { return 0; }
