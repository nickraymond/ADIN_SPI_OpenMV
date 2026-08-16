// bm_net_wire.c -- see bm_net_wire.h. The trait functions run in the
// calling stack task's context (l2 TX task mostly); the pop/inject pair
// runs in the wire task; retry_negotiation runs in the FreeRTOS timer
// task (l2's 100 ms renegotiation timer). The only shared state is a
// bm_os queue, word-sized counters, and the two link flags (word-sized,
// single-writer per transition direction).

#include <string.h>

#include "bm_config.h"
#include "bm_net_wire.h"
#include "bm_os.h"
#include "util.h"

// Frame offsets (Ethernet II + IPv6), matching network_frames.h without
// dragging an l2-internal header into the device's public face:
//   ethertype @ 12 (0x86DD), IPv6 next-header @ 20 (BCMP = 0xBC),
//   BCMP header starts @ 54 (14 eth + 40 IPv6); BcmpHeader.type is its
//   first u16, little-endian per the packed struct on this LE core.
#define ETHERTYPE_OFFSET 12u
#define NEXT_HEADER_OFFSET 20u
#define BCMP_TYPE_OFFSET 54u
#define MIN_BCMP_FRAME (BCMP_TYPE_OFFSET + 2u)

static struct {
    BmQueue txq;
    NetworkDeviceCallbacks callbacks;   // l2/bcmp fill these at init
    netwire_stats_t stats;
} WIRE;

static bool frame_is_bcmp_heartbeat(const uint8_t *data, uint16_t len) {
    if (len < MIN_BCMP_FRAME) {
        return false;
    }
    uint16_t ethertype = (uint16_t)((data[ETHERTYPE_OFFSET] << 8) |
                                    data[ETHERTYPE_OFFSET + 1]);
    if (ethertype != ethernet_type_ipv6 ||
        data[NEXT_HEADER_OFFSET] != ip_proto_bcmp) {
        return false;
    }
    uint16_t type = (uint16_t)(data[BCMP_TYPE_OFFSET] |
                               (data[BCMP_TYPE_OFFSET + 1] << 8));
    return type == 0x01;   // BcmpHeartbeatMessage, messages.h:471
}

// ---- NetworkDevice trait ------------------------------------------------

static BmErr wire_send(void *self, uint8_t *data, size_t length,
                       uint8_t port) {
    (void)self;
    if (!data || length == 0) {
        return BmEINVAL;
    }
    if (length > NETWIRE_MAX_FRAME) {
        // REV-14: 1514 is the network-wide max, enforced at this sender.
        WIRE.stats.tx_oversize++;
        bm_debug("netwire: oversize TX rejected (%u B > %u)\n",
                 (unsigned)length, (unsigned)NETWIRE_MAX_FRAME);
        return BmEINVAL;
    }

    netwire_tx_frame_t frame = {
        .data = bm_malloc(length),
        .len = (uint16_t)length,
        .port = port == 0 ? 1 : port,   // port 0 = "all ports" = our one
    };
    if (!frame.data) {
        return BmENOMEM;
    }
    memcpy(frame.data, data, length);

    if (bm_queue_send(WIRE.txq, &frame, 0) != BmOK) {
        bm_free(frame.data);
        WIRE.stats.tx_dropped++;
        bm_debug("netwire: txq full, frame dropped\n");
        return BmENOMEM;
    }

    WIRE.stats.tx_frames++;
    WIRE.stats.txq_pushed++;
    if (frame_is_bcmp_heartbeat(data, (uint16_t)length)) {
        WIRE.stats.hb_seen++;
    }
    return BmOK;
}

static BmErr wire_enable(void *self) {
    (void)self;
    WIRE.stats.enabled = true;
    // Do NOT fire link_change here (REV-12): the L2 thread starts its
    // renegotiation timers concurrently with bm_l2_init/enable; link-up
    // is reported from wire_retry_negotiation on the 100 ms timer.
    return BmOK;
}

static BmErr wire_disable(void *self) {
    (void)self;
    WIRE.stats.enabled = false;
    bm_net_wire_link_state(1, false);
    return BmOK;
}

static BmErr wire_enable_port(void *self, uint8_t port_num) {
    (void)self;
    return port_num == 1 ? BmOK : BmEINVAL;
}

static BmErr wire_disable_port(void *self, uint8_t port_num) {
    (void)self;
    return port_num == 1 ? BmOK : BmEINVAL;
}

// Load-bearing (REV-12), not a stub: this is the ONLY place link-up is
// reported to l2. l2 passes the 1-based port NUMBER here (l2.c:425 seeds
// the timer id from port_num 1..N; measured, and the same convention
// bm_sbc's udp_retry_negotiation documents) while link_change wants the
// 0-based index (REV-1) -- with num_ports()==1 a 1-based link_change call
// sets a mask outside all_ports_mask and TX dies silently inside L2.
static BmErr wire_retry_negotiation(void *self, uint8_t port_num,
                                    bool *renegotiated) {
    (void)self;
    if (renegotiated) {
        *renegotiated = false;
    }
    if (port_num != 1) {
        return BmEINVAL;
    }
    if (WIRE.stats.bridge_link && !WIRE.stats.link_up) {
        WIRE.stats.link_up = true;
        if (renegotiated) {
            *renegotiated = true;
        }
        if (WIRE.callbacks.link_change) {
            WIRE.callbacks.link_change(0, true);   // 0-based (REV-1)
        }
    }
    return BmOK;
}

static uint8_t wire_num_ports(void) { return NETWIRE_NUM_PORTS; }

static BmErr wire_port_stats(void *self, uint8_t port_index, void *stats) {
    (void)self;
    (void)port_index;
    (void)stats;
    return BmOK;
}

static BmErr wire_handle_interrupt(void *self) {
    (void)self;   // no interrupt line on an rpmsg wire
    return BmOK;
}

// NOTE (REV-15): callbacks->power is never assigned on this bench (only
// bristlemouth_init() assigns it, which is not called here) and nothing
// in this device may invoke it.
static const NetworkDeviceTrait WIRE_TRAIT = {
    .send = wire_send,
    .enable = wire_enable,
    .disable = wire_disable,
    .enable_port = wire_enable_port,
    .disable_port = wire_disable_port,
    .retry_negotiation = wire_retry_negotiation,
    .num_ports = wire_num_ports,
    .port_stats = wire_port_stats,
    .handle_interrupt = wire_handle_interrupt,
};

// ---- public -------------------------------------------------------------

NetworkDevice bm_net_wire_device(void) {
    if (!WIRE.txq) {
        WIRE.txq = bm_queue_create(NETWIRE_TXQ_LEN,
                                   sizeof(netwire_tx_frame_t));
    }
    NetworkDevice device = {
        .self = &WIRE,
        .trait = &WIRE_TRAIT,
        .callbacks = &WIRE.callbacks,
    };
    return device;
}

bool bm_net_wire_pop_tx(netwire_tx_frame_t *frame, uint32_t timeout_ms) {
    if (!frame || !WIRE.txq) {
        return false;
    }
    if (bm_queue_receive(WIRE.txq, frame, timeout_ms) != BmOK) {
        return false;
    }
    WIRE.stats.txq_popped++;
    return true;
}

void bm_net_wire_inject(uint8_t port, uint8_t *data, uint16_t len) {
    if (!WIRE.callbacks.receive || port != 1 || !data || len == 0) {
        return;
    }
    WIRE.stats.rx_frames++;
    // l2's bm_l2_rx copies into its own buffer before queueing
    // (l2.c:215-233), so handing it a transient buffer is safe.
    WIRE.callbacks.receive(port, data, len);
}

void bm_net_wire_link_state(uint8_t port, bool up) {
    if (port != 1) {
        return;
    }
    WIRE.stats.bridge_link = up;
    if (!up && WIRE.stats.link_up) {
        // DOWN is immediate (matches every bm_sbc device: only UP races
        // the renegotiation-timer startup).
        WIRE.stats.link_up = false;
        if (WIRE.callbacks.link_change) {
            // l2 expects the 0-based port_index here (l2.c:701 passes
            // port_num - 1 through the same path).
            WIRE.callbacks.link_change(0, false);
        }
    }
}

netwire_stats_t bm_net_wire_stats(void) { return WIRE.stats; }
