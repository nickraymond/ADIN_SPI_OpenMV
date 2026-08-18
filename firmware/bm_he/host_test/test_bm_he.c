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
#include "camera_svc.h"
#include "configuration.h"
#include "device.h"
#include "he_sample.h"
#include "power_hal.h"
#include "pubsub.h"
#include "bm_service.h"
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
// S17/S18 camera service ABI: bridge unpacks wire_capture_t "<BBHIHHBB";
// the bm_sbc fork app (apps/bench_apps) replicates camera_req_t /
// camera_rep_t byte-for-byte -- lockstep or not at all (camera_svc.h).
_Static_assert(sizeof(wire_capture_t) == 14, "wire_capture_t must be 14 B");
_Static_assert(offsetof(wire_capture_t, rate_bps) == 4, "rate_bps @ 4");
_Static_assert(offsetof(wire_capture_t, resolution) == 12, "resolution @ 12");
_Static_assert(offsetof(wire_capture_t, pixformat) == 13, "pixformat @ 13");
_Static_assert(sizeof(camera_req_t) == 18, "camera_req_t must be 18 B");
_Static_assert(offsetof(camera_req_t, cmd) == 4, "cmd @ 4");
_Static_assert(offsetof(camera_req_t, rate_bps) == 8, "req rate_bps @ 8");
_Static_assert(offsetof(camera_req_t, resolution) == 16, "req resolution @ 16");
_Static_assert(offsetof(camera_req_t, pixformat) == 17, "req pixformat @ 17");
// S18 reused camera_rep_t's rsvd u16 -- the reply must NOT change size,
// and cmds/pub_bytes must not move (the fork parses by offset).
_Static_assert(sizeof(camera_rep_t) == 24, "camera_rep_t must be 24 B");
_Static_assert(offsetof(camera_rep_t, res_active) == 6, "res_active @ 6");
_Static_assert(offsetof(camera_rep_t, pf_active) == 7, "pf_active @ 7");
_Static_assert(offsetof(camera_rep_t, cmds) == 8, "rep cmds @ 8");
_Static_assert(offsetof(camera_rep_t, pub_bytes) == 20, "pub_bytes @ 20");
// S19 bite 1: the probe reads the sample page out of raw RAM and unpacks
// "<HHHBBIIHHI" per record -- no struct definition travels with it.
_Static_assert(sizeof(he_sample_rec_t) == 24, "he_sample_rec_t must be 24 B");
_Static_assert(offsetof(he_sample_rec_t, heap_free) == 8, "heap_free @ 8");
_Static_assert(offsetof(he_sample_rec_t, heap_min) == 12, "heap_min @ 12");
_Static_assert(offsetof(he_sample_rec_t, tick_ms) == 20, "tick_ms @ 20");
_Static_assert(offsetof(he_sample_page_t, count) == 12, "page count @ 12");
_Static_assert(offsetof(he_sample_page_t, rec) == 16, "records @ 16");
_Static_assert(sizeof(he_sample_page_t) == 16 + 24 * HE_SAMPLE_CAP,
               "page = header + capacity records");
_Static_assert(sizeof(he_sample_page_t) <= 1024,
               "page must fit the 1 KB carved out of bm_he.ld");

// ---- host glue ------------------------------------------------------------

void he_dbg_printf(const char *fmt, ...) { (void)fmt; }   // bm_debug sink

// he_sample.c's platform glue (he_dbg.c owns these on target). Scripted
// here so a record's contents are predictable byte-for-byte.
static uint32_t s_fake_heap_free = 40000, s_fake_heap_min = 30000;
static uint32_t s_fake_tick_ms = 1000;
uint32_t he_plat_heap_free(void) { return s_fake_heap_free; }
uint32_t he_plat_heap_min(void) { return s_fake_heap_min; }
uint32_t he_plat_tick_ms(void) { return s_fake_tick_ms; }
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

static void test_backpressure_gate(void) {
    // S22 bite 1b: the RX-pause latch engages at NETWIRE_TXQ_HIGH_WATER
    // queued bytes and releases at NETWIRE_TXQ_LOW_WATER -- with
    // hysteresis, so a queue hovering between the marks cannot thrash
    // the gate. Frames are camera-chunk-sized (1,400 B): the burst this
    // exists for.
    NetworkDevice dev = bm_net_wire_device();
    static uint8_t frame[1400];
    memset(frame, 0xC3, sizeof(frame));
    netwire_tx_frame_t out;

    uint32_t engages0 = bm_net_wire_bp_engages();
    CHECK(!bm_net_wire_rx_backpressure());   // empty queue: no pause

    // 5 x 1400 = 7,000 B -- between the marks from below: still open.
    for (int i = 0; i < 5; i++) {
        CHECK(dev.trait->send(dev.self, frame, sizeof(frame), 1) == BmOK);
    }
    CHECK(!bm_net_wire_rx_backpressure());
    CHECK(bm_net_wire_bp_engages() == engages0);

    // 6th frame crosses 8,192: latch engages.
    CHECK(dev.trait->send(dev.self, frame, sizeof(frame), 1) == BmOK);
    CHECK(bm_net_wire_rx_backpressure());
    CHECK(bm_net_wire_bp_engages() == engages0 + 1);

    // Pop to 4,200 B -- between the marks from above: STAYS paused.
    for (int i = 0; i < 3; i++) {
        CHECK(bm_net_wire_pop_tx(&out, 0));
        bm_free(out.data);
    }
    CHECK(bm_net_wire_rx_backpressure());

    // One more pop -> 2,800 B <= low water: releases.
    CHECK(bm_net_wire_pop_tx(&out, 0));
    bm_free(out.data);
    CHECK(!bm_net_wire_rx_backpressure());

    // Re-crossing engages again (the counter is per episode).
    for (int i = 0; i < 4; i++) {
        CHECK(dev.trait->send(dev.self, frame, sizeof(frame), 1) == BmOK);
    }
    CHECK(bm_net_wire_rx_backpressure());
    CHECK(bm_net_wire_bp_engages() == engages0 + 2);

    while (bm_net_wire_pop_tx(&out, 0)) {
        bm_free(out.data);
    }
    CHECK(!bm_net_wire_rx_backpressure());
}

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

// ---- S17: camera service + power HAL --------------------------------------
// Fakes for the two middleware symbols camera_svc.c touches; the real
// bm_service/pubsub are exercised on target (they're Sofar's, vendored).

static BmServiceHandler s_svc_handler;
static char s_svc_name[64];
bool bm_service_register(size_t service_strlen, const char *service,
                         BmServiceHandler service_handler) {
    if (service_strlen >= sizeof(s_svc_name)) {
        return false;
    }
    memcpy(s_svc_name, service, service_strlen);
    s_svc_name[service_strlen] = '\0';
    s_svc_handler = service_handler;
    return true;
}

static BmErr s_pub_ret = BmOK;
static char s_pub_topic[64];
static uint16_t s_pub_len;
static uint8_t s_pub_first;
static uint8_t s_pub_version;
static int s_pub_calls;
BmErr bm_pub(const char *topic, const void *data, uint16_t len, uint8_t type,
             uint8_t version) {
    (void)type;
    s_pub_calls++;
    snprintf(s_pub_topic, sizeof(s_pub_topic), "%s", topic);
    s_pub_len = len;
    s_pub_first = len ? ((const uint8_t *)data)[0] : 0;
    s_pub_version = version;
    return s_pub_ret;
}

static bool svc_call(const void *req, size_t req_len, camera_rep_t *rep) {
    // Drive the service exactly as bm_service.c would: through the
    // registered callback with its reply-buffer contract.
    uint8_t buf[64];
    size_t blen = sizeof(buf);
    bool handled = s_svc_handler(strlen(CAMERA_SERVICE), CAMERA_SERVICE,
                                 req_len, (uint8_t *)(uintptr_t)req, &blen,
                                 buf);
    if (handled && rep) {
        CHECK(blen == sizeof(*rep));
        memcpy(rep, buf, sizeof(*rep));
    }
    return handled;
}

static void test_camera_svc(void) {
    CHECK(camera_svc_init() == BmOK);
    CHECK(s_svc_handler != NULL);
    CHECK(strcmp(s_svc_name, "camera/control") == 0);

    wire_capture_t cap;
    camera_rep_t rep;

    // Nothing pending before any command.
    CHECK(!camera_svc_take_pending(&cap));

    // Single capture: accepted, mailbox filled, params passed through.
    camera_req_t req = {.magic = CAMERA_REQ_MAGIC,
                        .cmd = CAMERA_CMD_CAPTURE,
                        .quality = 60,
                        .payload_max = 1000};
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 1 && rep.magic == CAMERA_REQ_MAGIC);
    CHECK(rep.mode_active == CAMERA_MODE_SINGLE && rep.cmds == 1);
    CHECK(camera_svc_take_pending(&cap));
    CHECK(cap.mode == CAMERA_MODE_SINGLE && cap.quality == 60 &&
          cap.payload_max == 1000);
    // Unset geometry stays 0 = "bridge default" (the bridge owns them).
    CHECK(cap.resolution == CAMERA_RES_DEFAULT &&
          cap.pixformat == CAMERA_PF_DEFAULT);
    CHECK(!camera_svc_take_pending(&cap));   // mailbox is fetch-and-clear

    // S18: resolution + pixformat ride through to the bridge and are
    // echoed back as the commanded pair.
    req = (camera_req_t){.magic = CAMERA_REQ_MAGIC,
                         .cmd = CAMERA_CMD_CAPTURE,
                         .resolution = CAMERA_RES_HD,
                         .pixformat = CAMERA_PF_MONO};
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 1);
    CHECK(rep.res_active == CAMERA_RES_HD && rep.pf_active == CAMERA_PF_MONO);
    CHECK(camera_svc_take_pending(&cap));
    CHECK(cap.resolution == CAMERA_RES_HD && cap.pixformat == CAMERA_PF_MONO);

    // Out-of-range geometry is REFUSED, not clamped (camera_svc.h): no
    // reply.ok, no mailbox entry, no command counted.
    uint32_t cmds_ok = rep.cmds;
    req.resolution = CAMERA_RES_MAX + 1;
    req.pixformat = CAMERA_PF_COLOR;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 0 && rep.cmds == cmds_ok);
    CHECK(!camera_svc_take_pending(&cap));
    req.resolution = CAMERA_RES_VGA;
    req.pixformat = CAMERA_PF_MAX + 1;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 0 && rep.cmds == cmds_ok);
    CHECK(!camera_svc_take_pending(&cap));
    // A refusal must not disturb the previously commanded geometry.
    CHECK(rep.res_active == CAMERA_RES_HD && rep.pf_active == CAMERA_PF_MONO);

    // Stop keeps the geometry (the sensor is still holding it); only the
    // mode goes idle.
    req = (camera_req_t){.magic = CAMERA_REQ_MAGIC, .cmd = CAMERA_CMD_STOP};
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.mode_active == CAMERA_MODE_STOP);
    CHECK(rep.res_active == CAMERA_RES_HD && rep.pf_active == CAMERA_PF_MONO);
    CHECK(camera_svc_take_pending(&cap) && cap.mode == CAMERA_MODE_STOP);

    // Stream: fields through, payload_max clamped to REV-28's ceiling.
    // (Counter is checked as a delta -- an absolute here would have to be
    // rebased every time a case is inserted above.)
    cmds_ok = rep.cmds;
    req = (camera_req_t){.magic = CAMERA_REQ_MAGIC,
                         .cmd = CAMERA_CMD_STREAM,
                         .fps_x10 = 150,
                         .rate_bps = 2000000,
                         .secs = 600,
                         .payload_max = 1500};
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 1 && rep.mode_active == CAMERA_MODE_STREAM &&
          rep.cmds == cmds_ok + 1);
    CHECK(camera_svc_take_pending(&cap));
    CHECK(cap.mode == CAMERA_MODE_STREAM && cap.fps_x10 == 150 &&
          cap.rate_bps == 2000000 && cap.secs == 600);
    CHECK(cap.payload_max == CAMERA_MAX_PAYLOAD);

    // Last-wins mailbox: two commands before a take -> the second one.
    req.cmd = CAMERA_CMD_CAPTURE;
    CHECK(svc_call(&req, sizeof(req), &rep));
    req.cmd = CAMERA_CMD_STOP;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(camera_svc_take_pending(&cap) && cap.mode == CAMERA_MODE_STOP);
    CHECK(rep.mode_active == CAMERA_MODE_STOP);

    // Status: no side effect on the mailbox or the command counter.
    uint32_t cmds_before = rep.cmds;
    req.cmd = CAMERA_CMD_STATUS;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 1 && rep.cmds == cmds_before);
    CHECK(!camera_svc_take_pending(&cap));

    // Malformed: wrong length / wrong magic -> unanswered (bm_service
    // sends no reply when the handler returns false).
    CHECK(!svc_call(&req, sizeof(req) - 1, NULL));
    req.magic = 0xDEADBEEF;
    CHECK(!svc_call(&req, sizeof(req), NULL));
    // Unknown command: answered, not accepted.
    req.magic = CAMERA_REQ_MAGIC;
    req.cmd = 99;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.ok == 0);

    // Publish path: ok / pub-failure / size-guard, all counted.
    s_pub_calls = 0;
    uint8_t payload[CAMERA_MAX_PAYLOAD + 1];
    memset(payload, 0x5A, sizeof(payload));
    s_pub_ret = BmOK;
    camera_svc_publish(payload, 1200);
    CHECK(s_pub_calls == 1 && s_pub_len == 1200);
    CHECK(strcmp(s_pub_topic, CAMERA_STREAM_TOPIC) == 0);
    CHECK(s_pub_version == BM_COMMON_PUB_SUB_VERSION);
    s_pub_ret = BmENOMEM;
    camera_svc_publish(payload, 100);
    s_pub_ret = BmOK;
    camera_svc_publish(payload, CAMERA_MAX_PAYLOAD + 1);  // over REV-28
    camera_svc_publish(payload, 0);                       // empty
    CHECK(s_pub_calls == 2);   // the guards never reached bm_pub
    req.cmd = CAMERA_CMD_STATUS;
    CHECK(svc_call(&req, sizeof(req), &rep));
    CHECK(rep.pub_ok == 1 && rep.pub_errs == 3 && rep.pub_bytes == 1200);
}

static void test_power_hal(void) {
    CHECK(power_hal_init() == BmOK);
    // fake clock continues from the RTC tests; measure deltas, not
    // absolutes.
    power_hal_reading_t a = power_hal_read();
    CHECK(a.remaining_on_s + a.upcoming_off_s > 0);
    fake_os_advance_ms(10 * 1000);
    power_hal_reading_t b = power_hal_read();
    CHECK(b.total_on_s == a.total_on_s + 10);
    CHECK(a.remaining_on_s == 0 || b.remaining_on_s <= a.remaining_on_s ||
          b.remaining_on_s > 3000);   // rollover into the next on-period
    CHECK(b.upcoming_off_s == 300);
    CHECK(b.voltage_mv >= 11800 && b.voltage_mv <= 12200);
    CHECK(b.current_ma > 0);
    // Service adapter mirrors the HAL timing fields.
    PowerInfoReplyData d = power_hal_power_info_cb(NULL);
    power_hal_reading_t c = power_hal_read();
    CHECK(d.total_on_s == c.total_on_s);
    CHECK(d.upcoming_off_s == c.upcoming_off_s);
}

// ---- S19 bite 1: the publish-path sampler --------------------------------

// Build a chunk payload the way the HP bridge does (camera_svc.h: 10 B
// LE header, then JPEG bytes) so the sampler is fed real frame positions.
static void chunk_fill(uint8_t *buf, uint32_t seq, uint16_t idx,
                       uint16_t count, uint16_t data_len) {
    memcpy(buf + 0, &seq, 4);
    memcpy(buf + 4, &idx, 2);
    memcpy(buf + 6, &count, 2);
    memcpy(buf + 8, &data_len, 2);
    memset(buf + 10, 0xC3, data_len);
}

static void test_he_sample(void) {
    static he_sample_page_t page;
    uint8_t chunk[CAMERA_MAX_PAYLOAD + 1];

    memset(&page, 0xEE, sizeof(page));   // init must clear, not append
    he_sample_init(&page);
    CHECK(he_sample_get_page() == &page);
    CHECK(page.magic == HE_SAMPLE_MAGIC && page.version == HE_SAMPLE_VERSION);
    CHECK(page.capacity == HE_SAMPLE_CAP && page.count == 0);
    CHECK(he_sample_tx_stalls() == 0);

    // A published chunk carries its own position; the record must show
    // the frame's idx/count, not a sample serial.
    netwire_stats_t before = bm_net_wire_stats();
    uint32_t depth0 = before.txq_pushed - before.txq_popped;
    s_pub_ret = BmOK;
    s_fake_heap_free = 40000;
    s_fake_heap_min = 30000;
    s_fake_tick_ms = 1111;
    chunk_fill(chunk, 7, 3, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.count == 1);
    CHECK(page.rec[0].idx == 3 && page.rec[0].count == 26);
    CHECK(page.rec[0].len == 1400 && page.rec[0].err == 0);
    CHECK(page.rec[0].heap_free == 40000 && page.rec[0].heap_min == 30000);
    CHECK(page.rec[0].tick_ms == 1111);
    CHECK(page.rec[0].txq_depth == depth0);

    // Heap moves between chunks -- that motion IS the drain curve.
    s_fake_heap_free = 22000;
    s_fake_heap_min = 21000;
    s_fake_tick_ms = 1123;
    chunk_fill(chunk, 7, 4, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.count == 2);
    CHECK(page.rec[1].idx == 4 && page.rec[1].heap_free == 22000);
    CHECK(page.rec[1].heap_min == 21000 && page.rec[1].tick_ms == 1123);

    // A failed bm_pub records the BmErr rather than vanishing.
    s_pub_ret = BmENOMEM;
    chunk_fill(chunk, 7, 5, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.count == 3 && page.rec[2].err == (uint8_t)BmENOMEM);
    s_pub_ret = BmOK;

    // Guard rejections never reach bm_pub but are still sampled -- a
    // malformed chunk must be distinguishable from a publish failure.
    int calls = s_pub_calls;
    chunk_fill(chunk, 7, 6, 26, 1390);
    camera_svc_publish(chunk, CAMERA_MAX_PAYLOAD + 1);
    CHECK(s_pub_calls == calls);
    CHECK(page.count == 4 && page.rec[3].err == (uint8_t)BmEINVAL);
    CHECK(page.rec[3].idx == 6 && page.rec[3].len == CAMERA_MAX_PAYLOAD + 1);

    // A payload too short to hold a chunk header reports position 0/0
    // instead of reading past it (ASan would catch the alternative).
    camera_svc_publish(chunk, 4);
    CHECK(page.count == 5 && page.rec[4].idx == 0 && page.rec[4].count == 0);

    // Undrained TX frames are the suspected heap sink, so depth has to
    // track the queue, not the publish count.
    NetworkDevice dev = bm_net_wire_device();
    uint8_t frame[256];
    memset(frame, 0x11, sizeof(frame));
    CHECK(dev.trait->send(dev.self, frame, sizeof(frame), 1) == BmOK);
    chunk_fill(chunk, 7, 7, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.rec[5].txq_depth == depth0 + 1);
    netwire_tx_frame_t popped;
    CHECK(bm_net_wire_pop_tx(&popped, 0));
    bm_free(popped.data);
    chunk_fill(chunk, 7, 8, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.rec[6].txq_depth == depth0);

    // TX ring stalls (main.c wire_pump_tx) surface in the next record.
    he_sample_note_tx_stall();
    he_sample_note_tx_stall();
    CHECK(he_sample_tx_stalls() == 2);
    chunk_fill(chunk, 7, 9, 26, 1390);
    camera_svc_publish(chunk, 1400);
    CHECK(page.rec[7].tx_stalls == 2);

    // Wrap: count is the total ever written, slot is count % capacity, so
    // a long run overwrites oldest-first and the reader can still order it.
    uint32_t base = page.count;
    for (uint32_t i = 0; i < HE_SAMPLE_CAP; i++) {
        chunk_fill(chunk, 8, (uint16_t)i, (uint16_t)HE_SAMPLE_CAP, 100);
        camera_svc_publish(chunk, 110);
    }
    CHECK(page.count == base + HE_SAMPLE_CAP);
    CHECK(page.rec[(base + HE_SAMPLE_CAP - 1) % HE_SAMPLE_CAP].idx ==
          HE_SAMPLE_CAP - 1);
    CHECK(page.rec[base % HE_SAMPLE_CAP].idx == 0);

    // Disabled sampler = silent no-op; the publish path must not care.
    he_sample_init(NULL);
    CHECK(he_sample_get_page() == NULL);
    chunk_fill(chunk, 9, 0, 1, 100);
    camera_svc_publish(chunk, 110);
    he_sample_note_tx_stall();
    CHECK(he_sample_tx_stalls() == 1);
}

// S19 bite 2: the TX queue is bounded by BYTES as well as frames,
// because 16 x 1,488 B exceeds the 20,712 B free heap (DESIGN §S19) --
// at the production chunk size the fatal allocation beat the survivable
// queue-full drop. The bound must bite BEFORE the frame count does.
static void test_txq_byte_bound(void) {
    NetworkDevice dev = bm_net_wire_device();
    netwire_tx_frame_t f;
    static uint8_t frame[1500];
    memset(frame, 0x77, sizeof(frame));

    while (bm_net_wire_pop_tx(&f, 0)) {       // start from empty
        bm_free(f.data);
    }
    netwire_stats_t before = bm_net_wire_stats();
    CHECK(before.txq_pushed == before.txq_popped);
    CHECK(before.txq_bytes_in == before.txq_bytes_out);

    // 1,400 B camera chunks: 8 fit under 12 KB, the 9th must be refused
    // with the queue only half full by frame count.
    int accepted = 0;
    for (int i = 0; i < NETWIRE_TXQ_LEN; i++) {
        if (dev.trait->send(dev.self, frame, 1400, 1) == BmOK) {
            accepted++;
        } else {
            break;
        }
    }
    netwire_stats_t after = bm_net_wire_stats();
    CHECK(accepted == 8);
    CHECK(accepted < NETWIRE_TXQ_LEN);
    CHECK(after.txq_bytes_in - after.txq_bytes_out == 8u * 1400u);
    CHECK(after.tx_dropped == before.tx_dropped + 1);
    CHECK(after.tx_dropped_bytes == before.tx_dropped_bytes + 1);
    // The refused frame must not be counted as sent (S16 ledger honesty).
    CHECK(after.tx_frames == before.tx_frames + 8);

    // Under the bound there is still room for a small frame -- the bound
    // refuses what does not fit, not everything after the first refusal.
    CHECK(dev.trait->send(dev.self, frame, 800, 1) == BmOK);

    // Draining returns the bytes, and the queue accepts full frames again.
    int popped = 0;
    uint32_t bytes = 0;
    while (bm_net_wire_pop_tx(&f, 0)) {
        bytes += f.len;
        bm_free(f.data);
        popped++;
    }
    CHECK(popped == 9 && bytes == 8u * 1400u + 800u);
    netwire_stats_t end = bm_net_wire_stats();
    CHECK(end.txq_bytes_in == end.txq_bytes_out);
    CHECK(dev.trait->send(dev.self, frame, 1400, 1) == BmOK);
    while (bm_net_wire_pop_tx(&f, 0)) {
        bm_free(f.data);
    }
}

int main(void) {
    test_wire_device();
    test_backpressure_gate();
    test_wire_frag();
    test_config_stubs();
    test_rtc_stub();
    test_device_identity();
    test_camera_svc();
    test_he_sample();
    test_txq_byte_bound();
    test_power_hal();
    printf("bm_he host tests: %d checks, %d failures\n", s_checks, s_fails);
    return s_fails ? 1 : 0;
}
