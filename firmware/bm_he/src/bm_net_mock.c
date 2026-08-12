// bm_net_mock.c -- see bm_net_mock.h. The trait functions run in the
// calling stack task's context (l2 TX task mostly); the pop/inject pair
// runs in the wire task. The only shared state is a bm_os queue and the
// stats word-sized counters.

#include <string.h>

#include "bm_config.h"
#include "bm_net_mock.h"
#include "bm_os.h"
#include "util.h"

// Frame offsets (Ethernet II + IPv6), matching network_frames.h without
// dragging an l2-internal header into the mock's public face:
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
    mock_stats_t stats;
} MOCK;

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

static BmErr mock_send(void *self, uint8_t *data, size_t length,
                       uint8_t port) {
    (void)self;
    if (!data || length == 0 || length > MOCK_MAX_FRAME) {
        return BmEINVAL;
    }

    mock_tx_frame_t frame = {
        .data = bm_malloc(length),
        .len = (uint16_t)length,
        .port = port == 0 ? 1 : port,   // port 0 = "all ports" = our one
    };
    if (!frame.data) {
        return BmENOMEM;
    }
    memcpy(frame.data, data, length);

    if (bm_queue_send(MOCK.txq, &frame, 0) != BmOK) {
        bm_free(frame.data);
        MOCK.stats.tx_dropped++;
        bm_debug("mock: txq full, frame dropped\n");
        return BmENOMEM;
    }

    MOCK.stats.tx_frames++;
    if (frame_is_bcmp_heartbeat(data, (uint16_t)length)) {
        MOCK.stats.hb_seen++;
    }
    return BmOK;
}

static BmErr mock_enable(void *self) {
    (void)self;
    MOCK.stats.enabled = true;
    return BmOK;
}

static BmErr mock_disable(void *self) {
    (void)self;
    MOCK.stats.enabled = false;
    bm_net_mock_set_link(1, false);
    return BmOK;
}

static BmErr mock_enable_port(void *self, uint8_t port_num) {
    (void)self;
    return port_num == 1 ? BmOK : BmEINVAL;
}

static BmErr mock_disable_port(void *self, uint8_t port_num) {
    (void)self;
    return port_num == 1 ? BmOK : BmEINVAL;
}

static BmErr mock_retry_negotiation(void *self, uint8_t port_index,
                                    bool *renegotiated) {
    (void)self;
    (void)port_index;
    if (renegotiated) {
        *renegotiated = false;
    }
    return BmOK;
}

static uint8_t mock_num_ports(void) { return MOCK_NUM_PORTS; }

static BmErr mock_port_stats(void *self, uint8_t port_index, void *stats) {
    (void)self;
    (void)port_index;
    (void)stats;
    return BmOK;
}

static BmErr mock_handle_interrupt(void *self) {
    (void)self;   // no interrupt line on a fake wire
    return BmOK;
}

static const NetworkDeviceTrait MOCK_TRAIT = {
    .send = mock_send,
    .enable = mock_enable,
    .disable = mock_disable,
    .enable_port = mock_enable_port,
    .disable_port = mock_disable_port,
    .retry_negotiation = mock_retry_negotiation,
    .num_ports = mock_num_ports,
    .port_stats = mock_port_stats,
    .handle_interrupt = mock_handle_interrupt,
};

// ---- public -------------------------------------------------------------

NetworkDevice bm_net_mock_device(void) {
    if (!MOCK.txq) {
        MOCK.txq = bm_queue_create(MOCK_TXQ_LEN, sizeof(mock_tx_frame_t));
    }
    NetworkDevice device = {
        .self = &MOCK,
        .trait = &MOCK_TRAIT,
        .callbacks = &MOCK.callbacks,
    };
    return device;
}

bool bm_net_mock_pop_tx(mock_tx_frame_t *frame, uint32_t timeout_ms) {
    if (!frame || !MOCK.txq) {
        return false;
    }
    return bm_queue_receive(MOCK.txq, frame, timeout_ms) == BmOK;
}

void bm_net_mock_inject(uint8_t port, uint8_t *data, uint16_t len) {
    if (!MOCK.callbacks.receive || port != 1 || !data || len == 0) {
        return;
    }
    MOCK.stats.rx_frames++;
    // l2's bm_l2_rx copies into its own buffer before queueing
    // (l2.c:215-233), so handing it a transient buffer is safe.
    MOCK.callbacks.receive(port, data, len);
}

void bm_net_mock_set_link(uint8_t port, bool up) {
    if (port != 1) {
        return;
    }
    MOCK.stats.link_up = up;
    if (MOCK.callbacks.link_change) {
        // l2 expects 0-based port_index here (l2.c:701 passes
        // port_num - 1 through the same path).
        MOCK.callbacks.link_change(0, up);
    }
}

mock_stats_t bm_net_mock_stats(void) { return MOCK.stats; }
