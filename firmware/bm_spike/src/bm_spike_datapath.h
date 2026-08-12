// bm_spike_datapath.h -- S9 bite 3: OA data-path smoke on top of the
// UNMODIFIED bm_core adin2111 driver (vendor/adin2111, bm_core @ d4ecc38).
//
// The driver's own init can never complete on an ADIN1110: waitDeviceReady
// polls PHYID == 0x0283BCA1 (the 2111 identity, adi_mac.c:1128) and
// adin2111_Init then waits on a port-2 PHY that a 1110 does not have
// (adin2111.c:169). Both are init-path gates only -- the OA control and
// data state machines themselves are chip-agnostic (bite-1/2 measured).
//
// So this bridge drives the exported macDriverEntry/phyDriverEntry tables
// directly and supplies exactly what the failed init path skipped:
//   1. macDriverEntry.Init      -> expected COMM_TIMEOUT, handle + soft
//                                  reset happened anyway (bite-1 verdict)
//   2. waitDeviceReady replica  -> poll PHYID == OUR identity, W1C RESETC
//   3. macInit replica          -> IMASK0/1 + FCS config register writes
//                                  (values verbatim from adi_mac.c:589-699)
//   4. state = READY            -> the one-line bridge past the identity
//                                  gate; the state field lives in OUR
//                                  memory (cfg.pDevMem), not the driver's
//   5. phyDriverEntry.Init      -> passes on our PHY: checkIdentity gates
//                                  DEVID1==0x0283 + OUI==0x2F only, and
//                                  phyStaticConfig is a no-op at rev 1
//   6. SyncConfig + ExitSoftwarePowerdown -> bm_adin2111.c's enable order
//
// On a real 2111 (mac_init == SUCCESS) rungs 2-4 are skipped -- the
// bridge degrades to the plain driver call sequence. This asymmetry is
// delta item 3 for the S13 1110-vs-2111 notes.
//
// Known non-replicable piece: macInit also registers the driver's static
// macCallback (IRQ servicing). TX is submit-driven and completes inline
// through the synchronous HAL's SPI callback, so the smoke needs no IRQ
// path; RX/IRQ servicing is S10+ scope (needs bm_os anyway).

#ifndef BM_SPIKE_DATAPATH_H
#define BM_SPIKE_DATAPATH_H

#include <stdint.h>

// Per-rung results for the runner's verdict lines. adi_eth_Result_e
// values as int; -1 = rung not attempted.
typedef struct {
    int      mac_init;   // rung 1: COMM_TIMEOUT(4) expected on a 1110
    int      ready;      // rung 2: 0 = PHYID matched + RESETC cleared
    uint32_t phyid;      // raw PHYID readback from rung 2
    int      mac_cfg;    // rung 3: macInit-replica register writes
    int      phy_init;   // rung 5: driver's own PHY_Init verdict
    uint16_t devid1;     // MMD1_DEV_ID1 via the driver's MDIO path
    uint16_t devid2;     // MMD1_DEV_ID2 (OUI+model+rev)
    int      sync;       // rung 6a: MAC_SyncConfig (CONFIG0.SYNC)
    int      exit_pd;    // rung 6b: PHY_ExitSoftwarePowerdown
} bm_spike_dp_report_t;

// Run the init bridge. Returns 0 when the MAC is READY+synced and the
// PHY is out of software powerdown (link then negotiates on its own;
// poll bm_spike_dp_link). Nonzero = which rung failed is in *rep.
int bm_spike_dp_init(bm_spike_dp_report_t *rep);

// Link status via the driver's PHY layer (AN_STATUS). Returns the
// adi_eth_Result_e as int; *up = 1 when the link is up.
int bm_spike_dp_link(int *up);

// Submit one raw Ethernet frame (no FCS -- CONFIG2.CRC_APPEND is set, the
// MAC appends it). len must be within the driver's MIN/MAX_FRAME_SIZE
// (60..1518). With the synchronous HAL the whole OA transaction completes
// inside this call; *done returns how many TX-done callbacks fired during
// it (want exactly 1). Returns SubmitTxBuffer's adi_eth_Result_e as int.
int bm_spike_dp_send(const uint8_t *frame, uint32_t len, uint32_t *done);

// Trust artifacts: counters for the verdict, not just result codes.
// out[0] tx-done callbacks total     out[1] current TXC credits (footer)
// out[2] driver state (2 = READY)    out[3] hdr parity errors
// out[4] footer parity errors        out[5] footer SYNC=0 errors
// out[6] frames dropped (FD)         out[7] last spiErr (echo mismatch)
void bm_spike_dp_stats(uint32_t out[8]);

// Drop the persistent handles/memory so the next dp_init starts from
// scratch. Same C-statics-survive-soft-reset reality as the bench handle
// (bm_spike_verify.h); bm_spike.fresh() calls this too.
void bm_spike_dp_reset(void);

#endif
