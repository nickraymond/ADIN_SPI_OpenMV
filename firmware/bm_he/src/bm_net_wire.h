// bm_net_wire.h -- the AE3's real NetworkDevice (S16 BUILD-2a). Implements
// bm_core's NetworkDevice trait (network_device.h) with the HP<->HE rpmsg
// pipe as the wire: frames the stack sends are queued for the wire task to
// forward to the HP bridge (WCMD_FRAME_TX, fragmented per wire_frag.h);
// frames the bridge injects (WCMD_FRAME_RX) enter the stack through the
// l2-assigned receive callback. One port, like the ADIN1110 -- this device
// is the bench stand-in the ADIN driver replaces on hardware day.
//
// Promotion from the S10 mock (bm_net_mock, kept semantics-compatible for
// the 2a/2b regression bench):
//  - link-up is no longer scripted: the bridge announces link state
//    (WCMD_LINK -> bm_net_wire_link_state) and l2's 100 ms renegotiation
//    timer collects it via retry_negotiation (BENCHSPEC REV-12; the same
//    enable()-race rule bm_sbc's virtual/udp devices obey). Link DOWN
//    still fires immediately.
//  - send() enforces the 1514 B network-wide max frame (REV-14) at the
//    sender; rejects are counted, not fatal.
//
// Deliberately free of rpmsg/FreeRTOS-config specifics beyond bm_os.h so
// the host test harness drives it exactly as the target does.
#ifndef BM_NET_WIRE_H
#define BM_NET_WIRE_H

#include <stdbool.h>
#include <stdint.h>

#include "network_device.h"

#define NETWIRE_NUM_PORTS 1
#define NETWIRE_TXQ_LEN   16
#define NETWIRE_MAX_FRAME 1514u  // BENCHSPEC REV-14: network-wide max L2
                                 // frame, enforced HERE at the sender (the
                                 // Light node's logged drop is the
                                 // backstop, not the mechanism)

// One queued TX frame (bm_malloc'd payload, freed by the consumer).
typedef struct {
    uint8_t *data;
    uint16_t len;
    uint8_t port;   // egress port 1..15 (0 = all-ports send, expanded
                    //   to port 1 -- single-port device)
} netwire_tx_frame_t;

typedef struct {
    uint32_t tx_frames;      // accepted from the stack
    uint32_t tx_dropped;     // queue-full drops
    uint32_t tx_oversize;    // send() rejects > NETWIRE_MAX_FRAME (REV-14)
    uint32_t rx_frames;      // injected into the stack
    uint32_t hb_seen;        // TX frames that parse as BCMP heartbeat
    bool link_up;            // as reported to l2 (via retry_negotiation)
    bool bridge_link;        // as last announced by the HP bridge
    bool enabled;
} netwire_stats_t;

// Build the NetworkDevice (trait + self + callbacks storage). Creates the
// internal TX queue; call once before bm_l2_init.
NetworkDevice bm_net_wire_device(void);

// Wire-task side: pop the next stack-sent frame (blocks up to timeout_ms;
// returns false on timeout). Caller must bm_free(frame->data).
bool bm_net_wire_pop_tx(netwire_tx_frame_t *frame, uint32_t timeout_ms);

// Wire-task side: deliver an injected frame into the stack (calls the
// l2-assigned receive callback; l2 copies the buffer before queueing, so
// `data` may be transient). port is the BM ingress port number (1-based).
void bm_net_wire_inject(uint8_t port, uint8_t *data, uint16_t len);

// Bridge link announcement (WCMD_LINK). UP is only recorded here -- l2
// learns of it from retry_negotiation on its own 100 ms timer (REV-12:
// firing link_change outside that path races the L2 thread's timer
// startup). DOWN fires callbacks->link_change(0, false) immediately.
void bm_net_wire_link_state(uint8_t port, bool up);

netwire_stats_t bm_net_wire_stats(void);

#endif // BM_NET_WIRE_H
