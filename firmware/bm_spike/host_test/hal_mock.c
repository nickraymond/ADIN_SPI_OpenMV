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
//
// S9 bite 3 grows the model from read-only to what the datapath bridge
// exercises:
//   - writable MAC registers (CONFIG0/2, IMASK0/1, STATUS0 as W1C,
//     RESET re-latching RESETC) -- the macInit-replica surface
//   - the MDIOACC engine (MD_ADDR/MD_WR/MD_RD, TRDONE immediately) over a
//     small clause-45 PHY model (DEVIDs, software powerdown handshake,
//     IRQ mask/status, AN control/status) -- the PHY_Init surface
//   - OA data (DNC=1) transactions: TX chunks are parsed per the
//     adi_mac_OaTxHeader_t layout, frame bytes captured for byte-exact
//     assertions, and each chunk answered with a well-formed footer
//     (SYNC=1, TXC=31, odd parity) -- the SubmitTxBuffer surface

#include <stdint.h>
#include <string.h>

#include "adi_hal.h"
#include "adi_spi_oa.h"    // OA header/footer layouts -- driver's own
#include "hal_mock.h"

static uint32_t s_phyid = 0x0283BC91u;  // default: our ADIN1110
static int s_phyid_reads = 0;
static int s_corrupt_protection = 0;

static HAL_Callback_t s_spi_cb = NULL;
static void *s_spi_cb_param = NULL;

// ---- MAC register model (mms 0) ----------------------------------------
// Definitions carry the same power-on defaults hal_mock_reset_model()
// restores, so tests that predate the model (which never call it) see a
// freshly powered chip too.
static uint32_t s_config0 = 0x00000006u;
static uint32_t s_config2 = 0;
static uint32_t s_imask0 = 0x00001FBFu;
static uint32_t s_imask1 = 0xFFFFFFFFu;
static uint32_t s_status0 = 0x00000040u;
static uint32_t s_mdioacc[2];

// ---- clause-45 PHY model (key = DEVAD<<16 | regaddr) -------------------
static uint16_t s_devid1 = 0x0283u;
static uint16_t s_devid2 = 0xBC91u;     // OUI 0x2F, model 9, rev 1
static int s_link_up = 0;
static uint16_t s_sft_pd_cntrl;
static uint16_t s_crsm_irq_mask;
static uint16_t s_subsys_irq_mask;
static uint16_t s_an_control;
static uint32_t s_mdio_cur_addr;        // data word of the last MD_ADDR op

// ---- TX frame capture from data chunks ---------------------------------
static uint8_t s_cap_buf[2048];
static uint32_t s_cap_len = 0;
static uint32_t s_cap_final_len = 0;
static int s_cap_frames = 0;

void hal_mock_set_phyid(uint32_t phyid)      { s_phyid = phyid; }
int  hal_mock_phyid_reads(void)              { return s_phyid_reads; }
void hal_mock_reset_counts(void)             { s_phyid_reads = 0; }
void hal_mock_corrupt_protection(int enable) { s_corrupt_protection = enable; }

void hal_mock_set_phy_devid(uint16_t devid1, uint16_t devid2)
{
    s_devid1 = devid1;
    s_devid2 = devid2;
}

void hal_mock_set_link(int up)               { s_link_up = up; }
int  hal_mock_tx_frames(void)                { return s_cap_frames; }
uint32_t hal_mock_tx_frame_len(void)         { return s_cap_final_len; }
const uint8_t *hal_mock_tx_frame(void)       { return s_cap_buf; }

void hal_mock_reset_model(void)
{
    s_config0 = 0x00000006u;    // measured reset default on our chip
    s_config2 = 0;
    s_imask0 = 0x00001FBFu;     // measured post-reset default (bite 2)
    s_imask1 = 0xFFFFFFFFu;
    s_status0 = 0x00000040u;    // RESETC pending from power-on
    s_mdioacc[0] = s_mdioacc[1] = 0;
    s_sft_pd_cntrl = 0;
    s_crsm_irq_mask = 0;
    s_subsys_irq_mask = 0;
    s_an_control = 0;
    s_mdio_cur_addr = 0;
    s_cap_len = s_cap_final_len = 0;
    s_cap_frames = 0;
}

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

// Odd parity over 4 bytes, same convention as MAC_CalculateParity.
static uint32_t odd_parity32(uint32_t v)
{
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

static uint16_t phy_reg_read(uint32_t cl45)
{
    switch (cl45) {
    case 0x1E0002u: return s_devid1;                      // MMD1_DEV_ID1
    case 0x1E0003u: return s_devid2;                      // MMD1_DEV_ID2
    case 0x1E0010u: return 0;                             // CRSM_IRQ_STATUS
    case 0x1E0020u: return s_crsm_irq_mask;               // CRSM_IRQ_MASK
    case 0x1E8812u: return s_sft_pd_cntrl;                // CRSM_SFT_PD_CNTRL
    case 0x1E8818u: return (s_sft_pd_cntrl & 1u) ? 0x0002u : 0; // CRSM_STAT.PD_RDY
    case 0x1F0011u: return 0;                             // PHY_SUBSYS_IRQ_STATUS
    case 0x1F0021u: return s_subsys_irq_mask;             // PHY_SUBSYS_IRQ_MASK
    case 0x070200u: return s_an_control;                  // AN_CONTROL
    case 0x070201u: return s_link_up ? 0x0004u : 0;       // AN_STATUS.LINK
    default:        return 0;
    }
}

static void phy_reg_write(uint32_t cl45, uint16_t data)
{
    switch (cl45) {
    case 0x1E0020u: s_crsm_irq_mask = data; break;
    case 0x1E8812u: s_sft_pd_cntrl = data; break;
    case 0x1F0021u: s_subsys_irq_mask = data; break;
    case 0x070200u: s_an_control = data; break;
    default: break;                                       // absorbed
    }
}

// MDIOACC engine: TRDONE is set the moment the op is written, so the
// driver's waitMdioReady poll succeeds on its first read.
#define MDIO_TRDONE (1u << 31)

static void mdio_write(uint32_t idx, uint32_t val)
{
    uint32_t op = (val >> 26) & 0x3u;
    uint32_t devad = (val >> 16) & 0x1Fu;
    uint16_t data = (uint16_t)(val & 0xFFFFu);

    if (op == 0x0u) {                       // MD_ADDR
        s_mdio_cur_addr = data;
        s_mdioacc[idx] = val | MDIO_TRDONE;
    } else if (op == 0x1u) {                // MD_WR
        phy_reg_write((devad << 16) | s_mdio_cur_addr, data);
        s_mdioacc[idx] = val | MDIO_TRDONE;
    } else if (op == 0x3u) {                // MD_RD
        uint16_t v = phy_reg_read((devad << 16) | s_mdio_cur_addr);
        s_mdioacc[idx] = (val & 0xFFFF0000u) | v | MDIO_TRDONE;
    } else {                                // MD_INC_RD: not used by driver
        s_mdioacc[idx] = val | MDIO_TRDONE;
    }
}

static uint32_t mock_reg_read(uint32_t mms, uint32_t addr)
{
    if (mms != 0) {
        return 0;
    }
    switch (addr) {
    case 0x001u:                            // ADDR_MAC_PHYID
        s_phyid_reads++;
        return s_phyid;
    case 0x004u: return s_config0;          // ADDR_MAC_CONFIG0
    case 0x006u: return s_config2;          // ADDR_MAC_CONFIG2
    case 0x008u: return s_status0;          // ADDR_MAC_STATUS0
    case 0x009u: return 0;                  // ADDR_MAC_STATUS1
    case 0x00Cu: return s_imask0;           // ADDR_MAC_IMASK0
    case 0x00Du: return s_imask1;           // ADDR_MAC_IMASK1
    case 0x020u: return s_mdioacc[0];       // ADDR_MAC_MDIOACC_0_
    case 0x021u: return s_mdioacc[1];       // ADDR_MAC_MDIOACC_1_
    default:     return 0;
    }
}

static void mock_reg_write(uint32_t mms, uint32_t addr, uint32_t val)
{
    if (mms != 0) {
        return;
    }
    switch (addr) {
    case 0x003u:                            // ADDR_MAC_RESET: soft reset
        s_status0 |= 0x00000040u;           // RESETC re-latches
        s_config0 = 0x00000006u;
        break;
    case 0x004u: s_config0 = val; break;
    case 0x006u: s_config2 = val; break;
    case 0x008u: s_status0 &= ~val; break;  // W1C
    case 0x00Cu: s_imask0 = val; break;
    case 0x00Du: s_imask1 = val; break;
    case 0x020u: mdio_write(0, val); break;
    case 0x021u: mdio_write(1, val); break;
    default: break;                         // absorbed
    }
}

// OA data transaction: each 68-byte group is 4 B TX header + 64 B data on
// MOSI, answered by 64 B data + 4 B footer on MISO (adi_spi_oa.c DATA_END
// reads the footer at chunkStart + chunkSize).
static void mock_data_xfer(uint8_t *tx, uint8_t *rx, uint32_t nbBytes)
{
    const uint32_t chunk = 64;
    for (uint32_t off = 0; off + 4 + chunk <= nbBytes; off += 4 + chunk) {
        adi_mac_OaTxHeader_t h;
        h.VALUE32 = be32_load(&tx[off]);

        if (h.DV) {
            uint32_t start = h.SV ? (uint32_t)h.SWO * 4u : 0u;
            uint32_t end = h.EV ? (uint32_t)h.EBO + 1u : chunk;
            if (h.SV) {
                s_cap_len = 0;
            }
            if (end > start && s_cap_len + (end - start) <= sizeof(s_cap_buf)) {
                memcpy(&s_cap_buf[s_cap_len], &tx[off + 4 + start], end - start);
                s_cap_len += end - start;
            }
            if (h.EV) {
                s_cap_frames++;
                s_cap_final_len = s_cap_len;
            }
        }

        adi_mac_OaRxFooter_t f;
        f.VALUE32 = 0;
        f.SYNC = 1;
        f.TXC = 31;
        f.RCA = 0;
        // Driver's own parity trick (oaCreateNextChunk): preset P=1, then
        // assign the parity of the whole word -- the result is always odd,
        // which is what the DATA_END footer check requires.
        f.P = 1;
        f.P = odd_parity32(f.VALUE32);
        be32_store(&rx[off + chunk], f.VALUE32);
    }
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
            } else {              // write: data word, complement ignored
                uint32_t val = be32_load(&pBufferTx[4 + 8 * i]);
                mock_reg_write(hdr.MMS, hdr.ADDR + i, val);
            }
        }
    } else if (hdr.DNC == 1) {
        mock_data_xfer(pBufferTx, pBufferRx, nbBytes);
    }

    if (s_spi_cb) {
        s_spi_cb(s_spi_cb_param, 0, NULL);
    }
    return 0;
}

uint32_t hal_mock_mac_reg(uint32_t addr)
{
    // Assertion-side peek; does not bump the PHYID read counter.
    switch (addr) {
    case 0x001u: return s_phyid;
    default:     return mock_reg_read(0, addr);
    }
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
