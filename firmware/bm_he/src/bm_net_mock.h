// bm_net_mock.h -- mock NetworkDevice for S10 INTERIM 2. Implements
// bm_core's NetworkDevice trait (network_device.h) with the HP<->HE
// rpmsg pipe as the fake wire: frames the stack sends are queued for the
// wire task to forward to the HP runner (WCMD_FRAME_TX); frames the
// runner injects (WCMD_FRAME_RX) enter the stack through the l2-assigned
// receive callback. One port, like the ADIN1110.
//
// Deliberately free of rpmsg/FreeRTOS-config specifics beyond bm_os.h so
// the host test harness drives it exactly as the target does.
#ifndef BM_NET_MOCK_H
#define BM_NET_MOCK_H

#include <stdbool.h>
#include <stdint.h>

#include "network_device.h"

#define MOCK_NUM_PORTS      1
#define MOCK_TXQ_LEN        16
#define MOCK_MAX_FRAME      1518u  // MTU 1500 + eth hdr + margin; the
                                   // WIRE (rpmsg) budget is smaller --
                                   // oversize is dropped AT THE PUMP and
                                   // counted, not an error to the stack

// One queued TX frame (bm_malloc'd payload, freed by the consumer).
typedef struct {
    uint8_t *data;
    uint16_t len;
    uint8_t port;   // egress port 1..15 (0 = all-ports send, expanded
                    //   to port 1 -- single-port device)
} mock_tx_frame_t;

typedef struct {
    uint32_t tx_frames;      // accepted from the stack
    uint32_t tx_dropped;     // queue-full drops
    uint32_t rx_frames;      // injected into the stack
    uint32_t hb_seen;        // TX frames that parse as BCMP heartbeat
    bool link_up;            // port 1 state as last reported
    bool enabled;
} mock_stats_t;

// Build the NetworkDevice (trait + self + callbacks storage). Creates the
// internal TX queue; call once before bm_l2_init.
NetworkDevice bm_net_mock_device(void);

// Wire-task side: pop the next stack-sent frame (blocks up to timeout_ms;
// returns false on timeout). Caller must bm_free(frame->data).
bool bm_net_mock_pop_tx(mock_tx_frame_t *frame, uint32_t timeout_ms);

// Wire-task side: deliver an injected frame into the stack (calls the
// l2-assigned receive callback; l2 copies the buffer before queueing, so
// `data` may be transient). port is the BM ingress port number (1-based).
void bm_net_mock_inject(uint8_t port, uint8_t *data, uint16_t len);

// Drive the mock's PHY: report link state to l2 (starts BCMP heartbeats
// on up, via bcmp's link-change callback).
void bm_net_mock_set_link(uint8_t port, bool up);

mock_stats_t bm_net_mock_stats(void);

#endif // BM_NET_MOCK_H
