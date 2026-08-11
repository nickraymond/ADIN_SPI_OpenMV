// test_verify.c -- host tests: the UNMODIFIED vendored driver's OA control
// path + the spike verdict-1 function against hal_mock.c's simulated ADIN.
//
// What this proves before any hardware or docker exists:
//   1. our call sequence drives the driver's OA framing end-to-end
//      (header build, echo check, protected read decode)
//   2. the identity gate is real COMPILED behavior, demonstrated: with a
//      1110 identity, MAC-layer init burns all ADI_MAC_INIT_MAX_RETRIES
//      (25000) PHYID polls and returns COMM_TIMEOUT; with a 2111 identity
//      the poll exits promptly (waitDeviceReady, adi_mac.c:1128)
//   3. the transport still works past the failed init: PHYID reads back
//      correctly on the 1110 -- the on-target verdict-1 logic
//   4. protection-error detection works (mock corrupts complements)
//
// Deliberately NOT tested on host: bm_spike_full_init(). The driver's
// *_DEVICE_SIZE constants are hand-counted for ILP32 targets and
// adin2111_Init() hardcodes them internally, so full init is not portable
// to an LP64 host (returns INVALID_PARAM before touching SPI). Verdict 2
// runs on the AE3, where the constants are sized as ADI intended.
//
// Run: host_test/run_host_tests.sh

#include <stdio.h>
#include <string.h>

#include "ADIN2111_mac_addr_rdef.h"   // RSTVAL_MAC_PHYID -- driver's constant
#include "bm_spike_verify.h"
#include "hal_mock.h"

static int failures = 0;

#define CHECK(cond, msg) do { \
        if (cond) { printf("  ok: %s\n", msg); } \
        else { printf("  FAIL: %s\n", msg); failures++; } \
    } while (0)

int main(void)
{
    uint32_t phyid = 0;
    int init_r = -1;
    int r;

    printf("[1] verdict 1 against a mocked ADIN1110 (our silicon)\n");
    hal_mock_reset_counts();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN1110);
    r = bm_spike_read_phyid(&phyid, &init_r);
    printf("  (init=%d %s, read=%d %s, phyid=0x%08X, polls=%d)\n",
           init_r, bm_spike_result_str(init_r), r, bm_spike_result_str(r),
           phyid, hal_mock_phyid_reads());
    CHECK(init_r == 4 /* ADI_ETH_COMM_TIMEOUT */,
          "MAC-layer init hits the identity gate (COMM_TIMEOUT)");
    CHECK(hal_mock_phyid_reads() >= 25000,
          "gate burned ADI_MAC_INIT_MAX_RETRIES PHYID polls");
    CHECK(r == 0, "PHYID read past the failed init is SUCCESS");
    CHECK(phyid == BM_SPIKE_PHYID_ADIN1110, "PHYID readback is 0x0283BC91");

    printf("[2] verdict 1 against a mocked ADIN2111 identity\n");
    hal_mock_reset_counts();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN2111);
    r = bm_spike_read_phyid(&phyid, &init_r);
    printf("  (init=%d %s, read=%d %s, phyid=0x%08X, polls=%d)\n",
           init_r, bm_spike_result_str(init_r), r, bm_spike_result_str(r),
           phyid, hal_mock_phyid_reads());
    CHECK(hal_mock_phyid_reads() < 100, "identity poll exits promptly");
    CHECK(r == 0 && phyid == BM_SPIKE_PHYID_ADIN2111,
          "PHYID readback follows the device identity");

    printf("[3] the identity-gate premise, from the driver's own constant\n");
    CHECK(RSTVAL_MAC_PHYID == BM_SPIKE_PHYID_ADIN2111,
          "driver's RSTVAL_MAC_PHYID is the 2111 identity");
    CHECK(BM_SPIKE_PHYID_ADIN1110 != RSTVAL_MAC_PHYID,
          "our 1110 PHYID fails the equality poll");

    printf("[4] protection handling on control reads (corrupted complements)\n");
    // Vendored-driver quirk, pinned here so a future driver bump surfaces
    // any change: on control reads the OA state machine only propagates the
    // header-echo check (spiErr); oaCtrlCmdReadData's PROTECTION_ERROR is
    // dropped (adi_spi_oa.c CONTROL_END), and the output word is left
    // unwritten. Callers see SUCCESS + no data -- so the spike must judge
    // the PHYID VALUE, never the result code alone.
    hal_mock_reset_counts();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN1110);
    hal_mock_corrupt_protection(1);
    r = bm_spike_read_phyid(&phyid, &init_r);
    hal_mock_corrupt_protection(0);
    CHECK(r == 0 && phyid == 0,
          "corruption yields SUCCESS + unwritten (0) data -- driver swallows "
          "PROTECTION_ERROR on ctrl reads");
    CHECK(phyid != BM_SPIKE_PHYID_ADIN1110,
          "corrupted read can never fake a valid PHYID");

    printf(failures ? "\nRESULT: FAIL (%d)\n" : "\nRESULT: PASS\n", failures);
    return failures ? 1 : 0;
}
