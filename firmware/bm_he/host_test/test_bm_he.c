// test_bm_he.c -- bm_he host tests (clang + ASan/UBSan, no docker, no
// hardware). Covers what runs off-target unchanged: the rpmsg-wire
// NetworkDevice's trait semantics (S16 promotion: REV-1/12/14 asserted as
// tests), the wire_frag fragmentation rules, the RAM/tick-backed
// integrator stubs, and compile-time ABI locks for every struct the HP
// side unpacks.

#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "bm_he.h"
#include "bm_net_wire.h"
#include "bm_os.h"
#include "bm_configs_generic.h"
#include "bm_rtc.h"
#include "configuration.h"
#include "device.h"
#include "wire_frag.h"

// ---- ABI locks: the HP bridge/runner unpacks these blind ------------------
// s10_bcmp_bench.py / bridge: struct.unpack("<Q16s16sIIIIIIIIIIII") == 88 B.
_Static_assert(sizeof(wire_hdr_t) == 4, "wire_hdr_t must be 4 B");
_Static_assert(sizeof(wire_status_t) == 88, "wire_status_t must be 88 B");
_Static_assert(offsetof(wire_status_t, ip_ll) == 8, "ip_ll @ 8");
_Static_assert(offsetof(wire_status_t, ip_ucast) == 24, "ip_ucast @ 24");
_Static_assert(offsetof(wire_status_t, stack_stage) == 40, "stage @ 40");
_Static_assert(offsetof(wire_status_t, heap_min) == 68, "heap_min @ 68");
_Static_assert(offsetof(wire_status_t, tx_dropped) == 72, "tx_dropped @ 72");
_Static_assert(offsetof(wire_status_t, stream_errs) == 84,
               "stream_errs @ 84");
// bm status page: runner peeks 10 consecutive u32s.
_Static_assert(sizeof(bm_status_page_t) == 40, "bm page = 10 u32s");
_Static_assert(offsetof(bm_status_page_t, dbg_ring_widx) == 36,
               "ring widx @ 36");
// WCMD_PING payload: runner packs "<Q" + echo bytes (Wire.ping_cmd).
_Static_assert(sizeof(wire_ping_t) == 8, "wire_ping_t hdr must be 8 B");
_Static_assert(offsetof(wire_ping_t, echo) == 8, "echo bytes @ 8");
// WCMD_STREAM payload: bridge packs "<IHH" (bridge_cfg stream trigger).
_Static_assert(sizeof(wire_stream_t) == 8, "wire_stream_t must be 8 B");

// ---- host glue ------------------------------------------------------------

void he_dbg_printf(const char *fmt, ...) { (void)fmt; }   // bm_debug sink
void fake_os_advance_ms(uint32_t ms);                     // fake_bm_os.c
BmErr bm_stubs_device_init(void);                         // bm_stubs.c

static int s_checks, s_fails;
#define CHECK(cond)                                                       \
    do {                                                                  \
        s_checks++;                                                       \
        if (!(cond)) {                                                    \
            s_fails++;                                                    \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);        \
        }                                                                 \
    } while (0)

// ---- synthetic frames -----------------------------------------------------
// Layout per bm_core network_frames.h: 14 B Ethernet II + 40 B IPv6 +
// 13 B BcmpHeader + payload. Same constants the runner asserts on.

static size_t build_bcmp_frame(uint8_t *buf, uint16_t bcmp_type,
                               size_t payload_len) {
    size_t bcmp_len = 13 + payload_len;
    memset(buf, 0, 54 + bcmp_len);
    buf[0] = 0x33;  // multicast-ish dst
    buf[12] = 0x86; // ethertype 0x86DD
    buf[13] = 0xDD;
    buf[14] = 0x60; // IPv6 version
    buf[18] = (uint8_t)(bcmp_len >> 8);
    buf[19] = (uint8_t)bcmp_len;
    buf[20] = 0xBC; // next header: BCMP
    buf[54] = (uint8_t)bcmp_type;         // BcmpHeader.type, LE
    buf[55] = (uint8_t)(bcmp_type >> 8);
    return 54 + bcmp_len;
}

// ---- rpmsg-wire NetworkDevice ---------------------------------------------

static struct {
    uint8_t port;
    uint8_t data[64];
    size_t len;
    int calls;
} RX;

static void test_rx_cb(uint8_t port_num, uint8_t *data, size_t length) {
    RX.calls++;
    RX.port = port_num;
    RX.len = length;
    memcpy(RX.data, data, length < sizeof(RX.data) ? length : sizeof(RX.data));
}

static struct {
    uint8_t idx;
    bool up;
    int calls;
} LINK;

static void test_link_cb(uint8_t port_index, bool is_up) {
    LINK.calls++;
    LINK.idx = port_index;
    LINK.up = is_up;
}

static void test_wire_device(void) {
    NetworkDevice dev = bm_net_wire_device();
    CHECK(dev.trait->num_ports() == 1);
    CHECK(dev.callbacks != NULL);

    // l2 does this at bm_l2_init; we play l2 here.
    dev.callbacks->receive = test_rx_cb;
    dev.callbacks->link_change = test_link_cb;

    // -- send copies the buffer and normalizes port 0 -> 1
    uint8_t frame[128];
    size_t n = build_bcmp_frame(frame, 0x0004 /* info req */, 8);
    CHECK(dev.trait->send(dev.self, frame, n, 0) == BmOK);
    memset(frame, 0xAA, sizeof(frame));   // mutate source after send

    netwire_tx_frame_t out;
    CHECK(bm_net_wire_pop_tx(&out, 0));
    CHECK(out.port == 1);
    CHECK(out.len == n);
    CHECK(out.data[12] == 0x86 && out.data[20] == 0xBC);  // not 0xAA: copied
    bm_free(out.data);
    CHECK(!bm_net_wire_pop_tx(&out, 0));  // queue drained

    // -- heartbeat frames are recognized and counted
    n = build_bcmp_frame(frame, 0x0001 /* heartbeat */, 12);
    CHECK(dev.trait->send(dev.self, frame, n, 1) == BmOK);
    CHECK(bm_net_wire_stats().hb_seen == 1);
    n = build_bcmp_frame(frame, 0x0002 /* echo req */, 12);
    CHECK(dev.trait->send(dev.self, frame, n, 1) == BmOK);
    CHECK(bm_net_wire_stats().hb_seen == 1);   // unchanged
    while (bm_net_wire_pop_tx(&out, 0)) {
        bm_free(out.data);
    }

    // -- REV-14: 1514 network-wide max, enforced at THIS sender + counted
    static uint8_t big[NETWIRE_MAX_FRAME + 1];
    CHECK(dev.trait->send(dev.self, big, sizeof(big), 1) == BmEINVAL);
    CHECK(bm_net_wire_stats().tx_oversize == 1);
    CHECK(dev.trait->send(dev.self, big, NETWIRE_MAX_FRAME, 1) == BmOK);
    CHECK(bm_net_wire_stats().tx_oversize == 1);   // exactly at cap: ok
    CHECK(dev.trait->send(dev.self, NULL, 64, 1) == BmEINVAL);
    while (bm_net_wire_pop_tx(&out, 0)) {
        bm_free(out.data);
    }

    // -- queue-full drops are counted, frames freed (ASan is the referee)
    n = build_bcmp_frame(frame, 0x0004, 8);
    for (int i = 0; i < NETWIRE_TXQ_LEN; i++) {
        CHECK(dev.trait->send(dev.self, frame, n, 1) == BmOK);
    }
    CHECK(dev.trait->send(dev.self, frame, n, 1) == BmENOMEM);
    CHECK(bm_net_wire_stats().tx_dropped == 1);
    while (bm_net_wire_pop_tx(&out, 0)) {
        bm_free(out.data);
    }

    // -- inject delivers to the l2-assigned callback, port preserved
    uint8_t in[32] = {1, 2, 3, 4};
    bm_net_wire_inject(1, in, sizeof(in));
    CHECK(RX.calls == 1 && RX.port == 1 && RX.len == sizeof(in));
    CHECK(RX.data[0] == 1 && RX.data[3] == 4);
    bm_net_wire_inject(2, in, sizeof(in));    // no port 2 on this device
    CHECK(RX.calls == 1);
    CHECK(bm_net_wire_stats().rx_frames == 1);

    // -- REV-12: bridge link-up is NOT reported until retry_negotiation
    bool renegotiated = true;
    CHECK(dev.trait->retry_negotiation(dev.self, 1, &renegotiated) == BmOK);
    CHECK(!renegotiated);                     // no bridge link yet
    CHECK(LINK.calls == 0);

    bm_net_wire_link_state(1, true);          // bridge announces UP
    CHECK(bm_net_wire_stats().bridge_link);
    CHECK(!bm_net_wire_stats().link_up);      // ...but l2 not told yet
    CHECK(LINK.calls == 0);

    // l2's 100 ms timer passes the 1-BASED port number (l2.c:425);
    // link_change must come back 0-BASED (REV-1).
    CHECK(dev.trait->retry_negotiation(dev.self, 1, &renegotiated) == BmOK);
    CHECK(renegotiated);
    CHECK(LINK.calls == 1 && LINK.idx == 0 && LINK.up);
    CHECK(bm_net_wire_stats().link_up);

    // -- second retry is a no-op while the link stays up
    CHECK(dev.trait->retry_negotiation(dev.self, 1, &renegotiated) == BmOK);
    CHECK(!renegotiated);
    CHECK(LINK.calls == 1);

    // -- out-of-range port number rejected (only port 1 exists)
    CHECK(dev.trait->retry_negotiation(dev.self, 0, &renegotiated) ==
          BmEINVAL);
    CHECK(dev.trait->retry_negotiation(dev.self, 2, &renegotiated) ==
          BmEINVAL);

    // -- bridge DOWN fires immediately, 0-based
    bm_net_wire_link_state(1, false);
    CHECK(LINK.calls == 2 && LINK.idx == 0 && !LINK.up);
    CHECK(!bm_net_wire_stats().link_up);
    CHECK(!bm_net_wire_stats().bridge_link);

    // -- up again via the retry path, then disable drops the link
    bm_net_wire_link_state(1, true);
    CHECK(dev.trait->retry_negotiation(dev.self, 1, &renegotiated) == BmOK);
    CHECK(renegotiated && LINK.calls == 3 && LINK.up);
    CHECK(dev.trait->disable(dev.self) == BmOK);
    CHECK(LINK.calls == 4 && !LINK.up);
    CHECK(!bm_net_wire_stats().link_up);

    // -- enable does NOT touch the link (REV-12)
    CHECK(dev.trait->enable(dev.self) == BmOK);
    CHECK(LINK.calls == 4);

    // -- misc trait entries behave
    CHECK(dev.trait->handle_interrupt(dev.self) == BmOK);
    CHECK(dev.trait->enable_port(dev.self, 1) == BmOK);
    CHECK(dev.trait->enable_port(dev.self, 2) == BmEINVAL);
}

// ---- wire_frag ------------------------------------------------------------

#define TEST_MSG_PAYLOAD 492u   // WIRE_MSG_PAYLOAD on target

static void test_wire_frag(void) {
    static uint8_t frame[1514];
    static uint8_t msg[4 + TEST_MSG_PAYLOAD];
    for (size_t i = 0; i < sizeof(frame); i++) {
        frame[i] = (uint8_t)(i * 13);
    }
    wire_frag_iter_t it;
    wire_reasm_t r;
    memset(&r, 0, sizeof(r));

    // -- small frame: one message, byte-identical to the S10 wire
    wire_frag_start(&it, WCMD_FRAME_TX, 1, frame, 80);
    uint16_t n = wire_frag_next(&it, msg, TEST_MSG_PAYLOAD);
    CHECK(n == 4 + 80);
    const wire_hdr_t *hdr = (const wire_hdr_t *)msg;
    CHECK(hdr->cmd == WCMD_FRAME_TX && hdr->port == 1 && hdr->len == 80);
    CHECK(memcmp(msg + 4, frame, 80) == 0);
    CHECK(wire_frag_next(&it, msg, TEST_MSG_PAYLOAD) == 0);   // done

    // ...and reassembles without opening an assembly
    uint16_t done = wire_reasm_first(&r, 1, 80, msg + 4, 80);
    CHECK(done == 80 && r.total == 0 && r.errors == 0);
    CHECK(memcmp(r.buf, frame, 80) == 0);

    // -- max frame: 1514 B -> 4 messages (492+492+492+38), round-trips
    wire_frag_start(&it, WCMD_FRAME_TX, 1, frame, sizeof(frame));
    int msgs = 0;
    done = 0;
    while ((n = wire_frag_next(&it, msg, TEST_MSG_PAYLOAD)) != 0) {
        hdr = (const wire_hdr_t *)msg;
        uint16_t in_msg = (uint16_t)(n - 4);
        if (msgs == 0) {
            CHECK(hdr->cmd == WCMD_FRAME_TX && hdr->len == sizeof(frame));
            CHECK(in_msg == TEST_MSG_PAYLOAD);
            done = wire_reasm_first(&r, hdr->port, hdr->len, msg + 4,
                                    in_msg);
            CHECK(done == 0);   // assembly opened
        } else {
            CHECK(hdr->cmd == WCMD_FRAG && hdr->len == in_msg);
            done = wire_reasm_frag(&r, msg + 4, in_msg);
        }
        msgs++;
    }
    CHECK(msgs == 4);
    CHECK(done == sizeof(frame));
    CHECK(memcmp(r.buf, frame, sizeof(frame)) == 0);
    CHECK(r.errors == 0 && r.total == 0);

    // -- error: continuation with no frame open
    CHECK(wire_reasm_frag(&r, frame, 10) == 0);
    CHECK(r.errors == 1);

    // -- error: new first-msg while a frame is open (old assembly dropped,
    //    new frame resyncs)
    CHECK(wire_reasm_first(&r, 1, 600, frame, 492) == 0);   // open
    CHECK(wire_reasm_first(&r, 1, 80, frame, 80) == 80);    // resync
    CHECK(r.errors == 2);
    CHECK(r.total == 0);

    // -- error: overrun past the announced total
    CHECK(wire_reasm_first(&r, 1, 600, frame, 492) == 0);
    CHECK(wire_reasm_frag(&r, frame, 200) == 0);            // 692 > 600
    CHECK(r.errors == 3 && r.total == 0);

    // -- error: announced total exceeds the 1514 buffer
    CHECK(wire_reasm_first(&r, 1, 1515, frame, 492) == 0);
    CHECK(r.errors == 4 && r.total == 0);

    // -- error: first message claims more bytes than the total
    CHECK(wire_reasm_first(&r, 1, 40, frame, 80) == 0);
    CHECK(r.errors == 5 && r.total == 0);
}

// ---- config stubs ---------------------------------------------------------

static void test_config_stubs(void) {
    uint8_t wr[64], rd[64];
    for (size_t i = 0; i < sizeof(wr); i++) {
        wr[i] = (uint8_t)(i * 7);
    }

    CHECK(bm_config_write(BM_CFG_PARTITION_USER, 100, wr, sizeof(wr), 10));
    memset(rd, 0, sizeof(rd));
    CHECK(bm_config_read(BM_CFG_PARTITION_USER, 100, rd, sizeof(rd), 10));
    CHECK(memcmp(wr, rd, sizeof(wr)) == 0);

    // partitions are independent
    memset(rd, 0, sizeof(rd));
    CHECK(bm_config_read(BM_CFG_PARTITION_SYSTEM, 100, rd, sizeof(rd), 10));
    CHECK(rd[0] == 0 && rd[63] == 0);

    // bounds: a read/write must stay inside one ConfigPartition
    CHECK(!bm_config_write(BM_CFG_PARTITION_USER,
                           sizeof(ConfigPartition) - 8, wr, 16, 10));
    CHECK(!bm_config_read(BM_CFG_PARTITION_USER,
                          sizeof(ConfigPartition) - 8, rd, 16, 10));
    CHECK(!bm_config_write(BM_CFG_PARTITION_COUNT, 0, wr, 8, 10));
    CHECK(!bm_config_read(BM_CFG_PARTITION_COUNT, 0, rd, 8, 10));
}

// ---- RTC stub -------------------------------------------------------------

static void test_rtc_stub(void) {
    RtcTimeAndDate t;

    // unset RTC: honest error; micro_seconds falls back to uptime
    CHECK(bm_rtc_get(&t) == BmEIO);
    fake_os_advance_ms(250);
    uint64_t us0 = bm_rtc_get_micro_seconds(NULL);
    fake_os_advance_ms(250);
    CHECK(bm_rtc_get_micro_seconds(NULL) - us0 == 250000ull);

    // set + advance
    RtcTimeAndDate set = {.year = 2026, .month = 8, .day = 12,
                          .hour = 12, .minute = 0, .second = 0, .ms = 0};
    CHECK(bm_rtc_set(&set) == BmOK);
    fake_os_advance_ms(1500);
    CHECK(bm_rtc_get(&t) == BmOK);
    CHECK(t.year == 2026 && t.month == 8 && t.day == 12);
    CHECK(t.hour == 12 && t.minute == 0 && t.second == 1 && t.ms == 500);

    // leap-year rollover: 2028-02-29 23:59:59 + 2 s -> 2028-03-01
    RtcTimeAndDate leap = {.year = 2028, .month = 2, .day = 29,
                           .hour = 23, .minute = 59, .second = 59, .ms = 0};
    CHECK(bm_rtc_set(&leap) == BmOK);
    fake_os_advance_ms(2000);
    CHECK(bm_rtc_get(&t) == BmOK);
    CHECK(t.year == 2028 && t.month == 3 && t.day == 1);
    CHECK(t.hour == 0 && t.minute == 0 && t.second == 1);

    // rejects garbage
    RtcTimeAndDate bad = {.year = 1960, .month = 1, .day = 1};
    CHECK(bm_rtc_set(&bad) == BmEINVAL);
    CHECK(bm_rtc_set(NULL) == BmEINVAL);
}

// ---- device identity -------------------------------------------------------

static void test_device_identity(void) {
    CHECK(bm_stubs_device_init() == BmOK);
    // Camera node id (BENCHSPEC / pi/bm_bench: be9c…03, fixed by Nick
    // 2026-08-14). Must match the runner's NODE_ID constant.
    CHECK(node_id() == 0xBE9C000000000003ull);
    uint8_t mac[6];
    CHECK(mac_address(mac, sizeof(mac)) == BmOK);
    // device.c: last 4 MAC bytes = low 32 bits of the node id.
    CHECK(mac[2] == 0x00 && mac[3] == 0x00 && mac[4] == 0x00 &&
          mac[5] == 0x03);
    CHECK(strcmp(device_name(), "bm_camera") == 0);
}

int main(void) {
    test_wire_device();
    test_wire_frag();
    test_config_stubs();
    test_rtc_stub();
    test_device_identity();
    printf("bm_he host tests: %d checks, %d failures\n", s_checks, s_fails);
    return s_fails ? 1 : 0;
}
