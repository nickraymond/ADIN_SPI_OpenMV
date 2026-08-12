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
#include "bm_spike_datapath.h"
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

    printf("[5] bench plumbing (S9 bite 2): open once, count clean reads\n");
    hal_mock_reset_counts();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN1110);
    r = bm_spike_bench_open(&init_r);
    CHECK(r == 0, "bench_open yields a handle despite the identity gate");
    CHECK(init_r == 4 /* ADI_ETH_COMM_TIMEOUT */,
          "bench_open reports the expected 1110 init verdict");
    uint32_t fails = 99;
    hal_mock_reset_counts();
    r = bm_spike_bench_reads(1000, &phyid, &fails);
    printf("  (reads=%d, phyid=0x%08X, fails=%u, polls=%d)\n",
           r, phyid, (unsigned)fails, hal_mock_phyid_reads());
    CHECK(r == 0 && fails == 0, "1000 bench reads, zero failures");
    CHECK(phyid == BM_SPIKE_PHYID_ADIN1110, "bench reads the 1110 PHYID");
    CHECK(hal_mock_phyid_reads() == 1000,
          "exactly n PHYID transactions -- init cost excluded from the "
          "timed window");
    int init_r2 = -1;
    r = bm_spike_bench_open(&init_r2);
    CHECK(r == 0 && init_r2 == 0, "second bench_open is an idempotent no-op");

    printf("[6] S9 bite 3: init bridge on a mocked 1110 (the real target)\n");
    hal_mock_reset_model();
    hal_mock_reset_counts();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN1110);
    hal_mock_set_link(0);
    bm_spike_dp_report_t rep;
    r = bm_spike_dp_init(&rep);
    printf("  (fail_rung=%d mac_init=%s ready=%s cfg=%s phy=%s sync=%s pd=%s"
           " devid=%04X/%04X)\n",
           r, bm_spike_result_str(rep.mac_init), bm_spike_result_str(rep.ready),
           bm_spike_result_str(rep.mac_cfg), bm_spike_result_str(rep.phy_init),
           bm_spike_result_str(rep.sync), bm_spike_result_str(rep.exit_pd),
           rep.devid1, rep.devid2);
    CHECK(r == 0, "bridge reaches READY+synced on a 1110 identity");
    CHECK(rep.mac_init == 4 /* COMM_TIMEOUT */,
          "driver's own init still refused (the identity gate)");
    CHECK(rep.ready == 0 && rep.phyid == BM_SPIKE_PHYID_ADIN1110,
          "waitDeviceReady replica accepts OUR identity");
    CHECK(rep.mac_cfg == 0, "macInit-replica register writes clean");
    CHECK(rep.phy_init == 0, "driver's own PHY_Init passes (DEVID1 + OUI gate)");
    CHECK(rep.devid1 == 0x0283 && rep.devid2 == 0xBC91,
          "MDIO DEVID words read back through the OA path");
    CHECK((hal_mock_mac_reg(0x004) & 0x8000u) != 0,
          "CONFIG0.SYNC set by the driver's SyncConfig");
    CHECK((hal_mock_mac_reg(0x008) & 0x40u) == 0,
          "RESETC W1C'd by the replica");
    int up = -1;
    r = bm_spike_dp_link(&up);
    CHECK(r == 0 && up == 0, "link reports DOWN before AN completes");
    hal_mock_set_link(1);
    r = bm_spike_dp_link(&up);
    CHECK(r == 0 && up == 1, "link reports UP via the driver's PHY layer");

    printf("[7] S9 bite 3: frame TX through the driver's OA data path\n");
    uint8_t frame[500];
    for (uint32_t i = 0; i < sizeof(frame); i++) {
        frame[i] = (uint8_t)(i * 7 + 3);
    }
    uint32_t done = 0;
    r = bm_spike_dp_send(frame, sizeof(frame), &done);
    CHECK(r == 0 && done == 1,
          "500 B submit returns SUCCESS + exactly 1 tx-done callback");
    CHECK(hal_mock_tx_frames() == 1, "mock captured exactly one completed frame");
    CHECK(hal_mock_tx_frame_len() == sizeof(frame), "captured length matches");
    CHECK(memcmp(hal_mock_tx_frame(), frame, sizeof(frame)) == 0,
          "captured bytes are byte-identical (chunk SV/EV/EBO math)");
    r = bm_spike_dp_send(frame, 61, &done);
    CHECK(r == 0 && done == 1 && hal_mock_tx_frame_len() == 61
          && memcmp(hal_mock_tx_frame(), frame, 61) == 0,
          "odd-length (61 B) frame survives the chunk math");
    uint32_t st[8];
    bm_spike_dp_stats(st);
    CHECK(st[0] == 2 && st[2] == 2 /* ADI_MAC_STATE_READY */,
          "2 tx-done callbacks, driver state back to READY");
    CHECK(st[3] == 0 && st[4] == 0 && st[5] == 0 && st[7] == 0,
          "zero OA header/footer/sync/spi errors");
    r = bm_spike_dp_send(frame, 59, &done);
    CHECK(r != 0 && done == 0,
          "sub-minimum (59 B) frame refused by the driver's own check");

    printf("[8] S9 bite 3: bridge degrades to plain driver calls on a 2111\n");
    hal_mock_reset_model();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN2111);
    hal_mock_set_link(1);
    r = bm_spike_dp_init(&rep);
    CHECK(r == 0, "bridge up on a 2111 identity");
    CHECK(rep.mac_init == 0, "driver's own MAC init ran to completion");
    CHECK(rep.ready == -1 && rep.mac_cfg == -1,
          "replica rungs skipped -- no nudge on a real 2111");
    r = bm_spike_dp_send(frame, 100, &done);
    CHECK(r == 0 && done == 1, "TX works through the untouched init path too");

    printf("[9] S9 bite 3: PHY identity refusal is loud\n");
    hal_mock_reset_model();
    hal_mock_set_phyid(BM_SPIKE_PHYID_ADIN1110);
    hal_mock_set_phy_devid(0x1234, 0xBC91);
    r = bm_spike_dp_init(&rep);
    CHECK(r == 5, "bridge fails at the PHY rung");
    CHECK(strcmp(bm_spike_result_str(rep.phy_init), "UNSUPPORTED_DEVICE") == 0,
          "PHY_Init refuses a wrong DEVID1");
    CHECK(rep.devid1 == 0x1234, "report carries the offending DEVID word");
    hal_mock_set_phy_devid(0x0283, 0xBC91);

    printf(failures ? "\nRESULT: FAIL (%d)\n" : "\nRESULT: PASS\n", failures);
    return failures ? 1 : 0;
}
