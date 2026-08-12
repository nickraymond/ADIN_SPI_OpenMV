// bm_spike_datapath.c -- see bm_spike_datapath.h. Driver files stay
// vendored UNMODIFIED; every workaround for the 1110 lives here and is
// labeled with the adi_mac.c/adin2111.c line it replicates.
//
// Excluded from the M55_HE core build like the rest of the spike.
#if !defined(CORE_M55_HE)

#include <string.h>

#include "adi_mac.h"
#include "adi_phy.h"

#include "bm_spike_verify.h"     // BM_SPIKE_PHYID_ADIN1110
#include "bm_spike_datapath.h"

// LP64-safe sizing, same pattern as bm_spike_verify.c: the driver's
// *_DEVICE_SIZE constants are ILP32 hand-counts.
enum { DP_MAC_MEM_SIZE = (ADI_MAC_DEVICE_SIZE > sizeof(adi_mac_Device_t))
                             ? ADI_MAC_DEVICE_SIZE
                             : sizeof(adi_mac_Device_t) };
enum { DP_PHY_MEM_SIZE = (ADI_PHY_DEVICE_SIZE > sizeof(adi_phy_Device_t))
                             ? ADI_PHY_DEVICE_SIZE
                             : sizeof(adi_phy_Device_t) };

static uint8_t dp_mac_mem[DP_MAC_MEM_SIZE];
static uint8_t dp_phy_mem[DP_PHY_MEM_SIZE];
static adi_mac_Device_t *dp_mac = NULL;
static adi_phy_Device_t *dp_phy = NULL;
static int dp_up = 0;

// One TX frame in flight at a time (the synchronous HAL completes each
// SubmitTxBuffer inline, so a single static buffer/descriptor pair suffices).
static uint8_t dp_frame_buf[MAX_FRAME_SIZE];
static adi_eth_BufDesc_t dp_desc;
static volatile uint32_t dp_tx_done = 0;

void bm_spike_dp_reset(void)
{
    dp_mac = NULL;
    dp_phy = NULL;
    dp_up = 0;
    dp_tx_done = 0;
    memset(dp_mac_mem, 0, sizeof(dp_mac_mem));
    memset(dp_phy_mem, 0, sizeof(dp_phy_mem));
}

// PHY-layer register access wrappers: adin2111.c's PhyRead/PhyWrite route
// through its global device handle (adin2111.c:25-33); ours route through
// the bridge's MAC handle. Same MDIOACC transactions underneath.
static uint32_t dpPhyRead(uint8_t hwAddr, uint32_t regAddr, uint16_t *data)
{
    return (uint32_t)macDriverEntry.PhyRead(dp_mac, hwAddr, regAddr, data);
}

static uint32_t dpPhyWrite(uint8_t hwAddr, uint32_t regAddr, uint16_t data)
{
    return (uint32_t)macDriverEntry.PhyWrite(dp_mac, hwAddr, regAddr, data);
}

// waitDeviceReady replica (adi_mac.c:1107-1157) with OUR chip's identity
// in place of RSTVAL_MAC_PHYID -- the one comparison the 1110 can never
// satisfy. Same retry bounds, same RESETC W1C.
static int dp_wait_device_ready(uint32_t *phyid_out)
{
    uint32_t phyid = 0;
    uint32_t status0 = 0;
    uint32_t retry = 0;
    int ok = 0;

    while (!ok && retry++ < ADI_MAC_INIT_MAX_RETRIES) {
        if ((MAC_ReadRegister(dp_mac, ADDR_MAC_PHYID, &phyid) == ADI_ETH_SUCCESS)
            && (phyid == BM_SPIKE_PHYID_ADIN1110)) {
            ok = 1;
        }
    }
    *phyid_out = phyid;
    if (!ok) {
        return (int)ADI_ETH_COMM_TIMEOUT;
    }

    ok = 0;
    while (!ok && retry++ < ADI_MAC_IF_UP_MAX_RETRIES) {
        if ((MAC_ReadRegister(dp_mac, ADDR_MAC_STATUS0, &status0) == ADI_ETH_SUCCESS)
            && (status0 & BITM_MAC_STATUS0_RESETC)) {
            ok = 1;
            MAC_WriteRegister(dp_mac, ADDR_MAC_STATUS0, BITM_MAC_STATUS0_RESETC);
        }
    }
    return ok ? (int)ADI_ETH_SUCCESS : (int)ADI_ETH_SW_RESET_TIMEOUT;
}

// macInit replica (adi_mac.c:581-703, OA + fcsCheckEn=false path), via the
// driver's own register accessors so framing stays the driver's. The
// irqMask0/1 shadow fields are kept consistent because the driver derives
// masked-status views from them. NOT replicable: the internal macCallback
// registration (static symbol) -- documented in the header.
static int dp_mac_config(void)
{
    adi_eth_Result_e r;
    uint32_t val32;

    uint32_t mask0 = 0xFFFFFFFFu;
    mask0 &= ~(BITM_MAC_IMASK0_TXPEM |
               BITM_MAC_IMASK0_TXBOEM |
               BITM_MAC_IMASK0_TXBUEM |
               BITM_MAC_IMASK0_RXBOEM |
               BITM_MAC_IMASK0_LOFEM |
               BITM_MAC_IMASK0_HDREM |
               BITM_MAC_IMASK0_RESETCM |
               BITM_MAC_IMASK0_TXFCSEM |
               BITM_MAC_IMASK0_CDPEM);
    r = MAC_WriteRegister(dp_mac, ADDR_MAC_IMASK0, mask0);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }
    dp_mac->irqMask0 = mask0;

    // OA build: TX_RDY/RX_RDY stay masked (the !CONFIG_SPI_OA_EN block in
    // macInit is compiled out of this config).
    uint32_t mask1 = 0xFFFFFFFFu;
    mask1 &= ~(BITM_MAC_IMASK1_P1_RX_IFG_ERR_MASK |
               BITM_MAC_IMASK1_SPI_ERR_MASK |
               BITM_MAC_IMASK1_RX_ECC_ERR_MASK |
               BITM_MAC_IMASK1_TX_ECC_ERR_MASK
#if defined(CONFIG_ETH_ADIN2111)
               | BITM_MAC_IMASK1_P2_TXFCSEM
#endif
               );
    r = MAC_WriteRegister(dp_mac, ADDR_MAC_IMASK1, mask1);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }
    dp_mac->irqMask1 = mask1;

    r = MAC_ReadRegister(dp_mac, ADDR_MAC_STATUS0, &val32);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }
    r = MAC_ReadRegister(dp_mac, ADDR_MAC_STATUS1, &val32);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }

    // fcsCheckEn=false: host sends no FCS, MAC appends it.
    r = MAC_ReadRegister(dp_mac, ADDR_MAC_CONFIG0, &val32);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }
    val32 &= ~BITM_MAC_CONFIG0_TXFCSVE;
    r = MAC_WriteRegister(dp_mac, ADDR_MAC_CONFIG0, val32);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }

    r = MAC_ReadRegister(dp_mac, ADDR_MAC_CONFIG2, &val32);
    if (r != ADI_ETH_SUCCESS) {
        return (int)r;
    }
    val32 |= BITM_MAC_CONFIG2_CRC_APPEND;
    r = MAC_WriteRegister(dp_mac, ADDR_MAC_CONFIG2, val32);
    return (int)r;
}

int bm_spike_dp_init(bm_spike_dp_report_t *rep)
{
    memset(rep, 0, sizeof(*rep));
    rep->mac_init = rep->ready = rep->mac_cfg = -1;
    rep->phy_init = rep->sync = rep->exit_pd = -1;

    bm_spike_dp_reset();

    adi_mac_DriverConfig_t cfg = {
        .pDevMem = dp_mac_mem,
        .devMemSize = sizeof(dp_mac_mem),
        .fcsCheckEn = false,
    };
    rep->mac_init = (int)macDriverEntry.Init(&dp_mac, &cfg, NULL);
    if (dp_mac == NULL) {
        return 1;
    }

    if (rep->mac_init != (int)ADI_ETH_SUCCESS) {
        // The expected 1110 path: identity gate fired, chip is freshly
        // soft-reset, state stuck at INITIALIZED. Bridge it.
        rep->ready = dp_wait_device_ready(&rep->phyid);
        if (rep->ready != (int)ADI_ETH_SUCCESS) {
            return 2;
        }
        rep->mac_cfg = dp_mac_config();
        if (rep->mac_cfg != (int)ADI_ETH_SUCCESS) {
            return 3;
        }
        // The bridge past the identity gate: MAC_Init would have done this
        // at adi_mac.c:574 had waitDeviceReady accepted our PHYID. The
        // field lives in dp_mac_mem (ours), the driver is untouched.
        dp_mac->state = ADI_MAC_STATE_READY;
    } else {
        // Real 2111 (or future driver rev that knows the 1110): the whole
        // init ran; record the identity for the report and skip the bridge.
        MAC_ReadRegister(dp_mac, ADDR_MAC_PHYID, &rep->phyid);
    }

    // Report the raw MDIO identity regardless of what PHY_Init decides --
    // if checkIdentity refuses, these two words say why.
    dpPhyRead(1, ADDR_MMD1_DEV_ID1, &rep->devid1);
    dpPhyRead(1, ADDR_MMD1_DEV_ID2, &rep->devid2);

    adi_phy_DriverConfig_t pcfg = {
        .addr = 1,                       // 1110 internal PHY = MDIO addr 1
        .pDevMem = dp_phy_mem,           // (S5 + mainline adin1110.c:177)
        .devMemSize = sizeof(dp_phy_mem),
        .enableIrq = false,
    };
    rep->phy_init = (int)phyDriverEntry.Init(&dp_phy, &pcfg, NULL,
                                             dpPhyRead, dpPhyWrite);
    if (rep->phy_init != (int)ADI_ETH_SUCCESS) {
        return 5;
    }

    // bm_adin2111.c's enable order (bm_adin2111.c:327): SyncConfig first,
    // then ports out of software powerdown.
    rep->sync = (int)macDriverEntry.SyncConfig(dp_mac);
    if (rep->sync != (int)ADI_ETH_SUCCESS) {
        return 6;
    }
    rep->exit_pd = (int)phyDriverEntry.ExitSoftwarePowerdown(dp_phy);
    if (rep->exit_pd != (int)ADI_ETH_SUCCESS) {
        return 7;
    }

    dp_up = 1;
    return 0;
}

int bm_spike_dp_link(int *up)
{
    *up = 0;
    if (dp_phy == NULL) {
        return (int)ADI_ETH_INVALID_HANDLE;
    }
    adi_phy_LinkStatus_e st = ADI_PHY_LINK_STATUS_DOWN;
    int r = (int)phyDriverEntry.GetLinkStatus(dp_phy, &st);
    *up = (st == ADI_PHY_LINK_STATUS_UP) ? 1 : 0;
    return r;
}

static void dpTxDoneCb(void *pCBParam, uint32_t Event, void *pArg)
{
    (void)pCBParam;
    (void)Event;
    (void)pArg;
    dp_tx_done++;
}

int bm_spike_dp_send(const uint8_t *frame, uint32_t len, uint32_t *done)
{
    *done = 0;
    if (!dp_up || dp_mac == NULL) {
        return (int)ADI_ETH_DEVICE_UNINITIALIZED;
    }
    if (len > sizeof(dp_frame_buf)) {
        return (int)ADI_ETH_PARAM_OUT_OF_RANGE;
    }
    memcpy(dp_frame_buf, frame, len);

    memset(&dp_desc, 0, sizeof(dp_desc));
    dp_desc.pBuf = dp_frame_buf;
    dp_desc.bufSize = sizeof(dp_frame_buf);
    dp_desc.trxSize = len;
    dp_desc.cbFunc = dpTxDoneCb;
    dp_desc.egressCapt = ADI_MAC_EGRESS_CAPTURE_NONE;
#if defined(CONFIG_ETH_ADIN2111)
    dp_desc.port = 0;        // 1110: single PHY; header VS bit ends up 0
#endif
    dp_desc.refCount = 1;    // single-port value, adin2111.c:738

    adi_mac_FrameHeader_t header;
    header.VALUE16 = 0;
    header.PORT = 0;
    header.EGRESS_CAPTURE = ADI_MAC_EGRESS_CAPTURE_NONE;

    uint32_t before = dp_tx_done;
    int r = (int)macDriverEntry.SubmitTxBuffer(dp_mac, header, &dp_desc);
    *done = dp_tx_done - before;
    return r;
}

void bm_spike_dp_stats(uint32_t out[8])
{
    out[0] = dp_tx_done;
    out[1] = dp_mac ? dp_mac->oaTxc : 0;
    out[2] = dp_mac ? (uint32_t)dp_mac->state : 0;
    out[3] = dp_mac ? dp_mac->oaErrorStats.hdrParityErrorCount : 0;
    out[4] = dp_mac ? dp_mac->oaErrorStats.ftrParityErrorCount : 0;
    out[5] = dp_mac ? dp_mac->oaErrorStats.syncErrorCount : 0;
    out[6] = dp_mac ? dp_mac->oaErrorStats.fdCount : 0;
    out[7] = dp_mac ? dp_mac->spiErr : 0;
}

#endif // !CORE_M55_HE
