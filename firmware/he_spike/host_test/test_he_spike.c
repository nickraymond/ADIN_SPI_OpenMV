// test_he_spike.c -- host harness for the HE spike's rpmsg remote + bench
// protocol. Plays the HP HOST side against a malloc'd fake SHM laid out
// exactly like micropython extmod/modopenamp.c does on the AE3 (rsc table
// @ +0, vring0 host->remote @ +0x1400, vring1 remote->host @ +0x400,
// buffer pool @ +0x2400). The remote code under test runs its real 32-bit
// target address arithmetic via rr's addr_offset.
//
// Deliberately independent implementation of the ring structs -- a layout
// disagreement between this file and rpmsg_remote.c is a test failure,
// not a shared bug.

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>

#include "he_spike.h"
#include "rpmsg_remote.h"
#include "bench.h"

// ---- fake SHM ----------------------------------------------------------
#define TGT_BASE   0x60000000u
#define TGT_V0     0x60001400u   /* rsc vring0 = host->remote (VRING_TX_ADDR) */
#define TGT_V1     0x60000400u   /* rsc vring1 = remote->host (VRING_RX_ADDR) */
#define TGT_POOL   0x60002400u
#define NUM        64u
#define ALIGN      32u
#define BUFSZ      512u

static uint8_t shm[0x10000] __attribute__((aligned(64)));
#define OFF ((intptr_t)shm - (intptr_t)TGT_BASE)
static void *T2H(uint32_t t) { return shm + (t - TGT_BASE); }

// ---- independent ring view ----------------------------------------------
typedef struct __attribute__((packed)) {
    uint64_t addr; uint32_t len; uint16_t flags; uint16_t next;
} t_desc;
typedef struct __attribute__((packed)) {
    uint16_t flags; uint16_t idx; uint16_t ring[NUM];
} t_avail;
typedef struct __attribute__((packed)) {
    uint32_t id; uint32_t len;
} t_uelem;
typedef struct __attribute__((packed)) {
    uint16_t flags; uint16_t idx; t_uelem ring[NUM];
} t_used;

typedef struct {
    t_desc *desc; t_avail *avail; t_used *used;
    uint16_t last_used;          // host's used-ring cursor
} hq_t;

static hq_t q0, q1;              // vring0 (host tx), vring1 (host rx)

static void hq_init(hq_t *q, uint32_t tgt_base) {
    q->desc = (t_desc *)T2H(tgt_base);
    q->avail = (t_avail *)((uint8_t *)q->desc + NUM * sizeof(t_desc));
    uintptr_t u = (uintptr_t)q->avail + sizeof(t_avail) + sizeof(uint16_t);
    q->used = (t_used *)((u + ALIGN - 1) & ~(uintptr_t)(ALIGN - 1));
    q->last_used = 0;
}

// rsc table, mirroring openamp_rsc_table_init (modopenamp.c:286).
typedef struct __attribute__((packed)) {
    uint32_t ver, num, reserved[2], offset[2];
    uint32_t type, id, notifyid, dfeatures, gfeatures, config_len;
    uint8_t status, num_of_vrings, r2[2];
    struct __attribute__((packed)) {
        uint32_t da, align, num, notifyid, reserved;
    } vring[2];
} t_rsc;

static void host_openamp_init(void) {
    memset(shm, 0, sizeof(shm));
    t_rsc *r = (t_rsc *)shm;
    r->ver = 1;
    r->num = 2;
    r->type = 3;                 // RSC_VDEV
    r->id = 7;                   // VIRTIO_ID_RPMSG
    r->dfeatures = 1;            // VIRTIO_RPMSG_F_NS
    r->num_of_vrings = 2;
    r->vring[0] = (typeof(r->vring[0])) {TGT_V0, ALIGN, NUM, 0, 0};
    r->vring[1] = (typeof(r->vring[1])) {TGT_V1, ALIGN, NUM, 1, 0};
    r->status = 0x07;            // ACK|DRIVER|DRIVER_OK

    hq_init(&q0, TGT_V0);
    hq_init(&q1, TGT_V1);

    // Host pre-queues NUM empty rx buffers on vring1 (pool bufs 0..63).
    for (uint32_t i = 0; i < NUM; i++) {
        q1.desc[i] = (t_desc) {.addr = TGT_POOL + i * BUFSZ, .len = BUFSZ};
        q1.avail->ring[q1.avail->idx % NUM] = (uint16_t)i;
        q1.avail->idx++;
    }
}

// ---- host send / receive -------------------------------------------------
typedef struct __attribute__((packed)) {
    uint32_t src, dst, reserved; uint16_t len, flags;
} t_hdr;

#define HOST_EPT 1025u
#define POOL_TX_FIRST 64u        // pool bufs 64..109 are host tx buffers
#define POOL_TX_COUNT 40u
static uint32_t tx_next;         // round-robin; recycled via used ring

static void host_send(uint32_t dst, const void *payload, uint32_t len) {
    uint32_t buf = TGT_POOL + (POOL_TX_FIRST + (tx_next % POOL_TX_COUNT)) * BUFSZ;
    tx_next++;
    t_hdr *h = (t_hdr *)T2H(buf);
    h->src = HOST_EPT;
    h->dst = dst;
    h->reserved = 0;
    h->len = (uint16_t)len;
    h->flags = 0;
    memcpy((uint8_t *)h + sizeof(*h), payload, len);

    uint16_t d = (uint16_t)(q0.avail->idx % NUM);
    q0.desc[d] = (t_desc) {.addr = buf, .len = sizeof(*h) + len};
    q0.avail->ring[q0.avail->idx % NUM] = d;
    q0.avail->idx++;
}

// Pop one message the remote produced on vring1; returns payload length or
// -1 if none. Recycles the buffer back to avail (as open-amp's host does).
static int host_recv(uint32_t *src, uint32_t *dst, uint8_t *out,
                     uint32_t cap) {
    if (q1.last_used == q1.used->idx) {
        return -1;
    }
    t_uelem *e = &q1.used->ring[q1.last_used % NUM];
    t_desc *d = &q1.desc[e->id % NUM];
    t_hdr *h = (t_hdr *)T2H((uint32_t)d->addr);
    uint32_t len = h->len < cap ? h->len : cap;
    *src = h->src;
    *dst = h->dst;
    memcpy(out, (uint8_t *)h + sizeof(*h), len);
    q1.last_used++;
    // recycle
    q1.avail->ring[q1.avail->idx % NUM] = (uint16_t)(e->id % NUM);
    q1.avail->idx++;
    return (int)len;
}

// ---- glue for the code under test ----------------------------------------
static uint32_t kicks;
static void kick_cb(void *arg) { (void)arg; kicks++; }

static bench_t bench;
static rpmsg_remote_t rr;

static bool bench_send(void *arg, const void *data, uint32_t len) {
    rpmsg_remote_t *r = arg;
    return rr_send(r, r->peer_addr, data, len);
}

static uint32_t fake_cycles_v;
static uint32_t fake_cycles(void) { return fake_cycles_v += 160; }

static void rx_cb(void *arg, uint32_t src, const uint8_t *data,
                  uint32_t len) {
    (void)src;
    bench_on_message(arg, data, len);
}

// ---- tiny check machinery -------------------------------------------------
static int failures, checks;
#define CHECK(cond, ...) do { \
        checks++; \
        if (!(cond)) { failures++; printf("FAIL [%d] ", __LINE__); \
                       printf(__VA_ARGS__); printf("\n"); } \
} while (0)

static void wr32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }

static bool remote_up(void) {
    return rr_init(&rr, (uintptr_t)shm, OFF, BENCH_EPT_ADDR,
                   rx_cb, &bench, kick_cb, NULL);
}

int main(void) {
    uint8_t buf[512];
    uint32_t src, dst;

    // [1] a zeroed table must be rejected
    memset(shm, 0, sizeof(shm));
    CHECK(!remote_up(), "init accepted a zeroed rsc table");

    // [2] init + NS announce
    host_openamp_init();
    CHECK(remote_up(), "init rejected a valid table");
    CHECK(rr_vdev_status(&rr) == 0x07, "vdev status readback");
    bench_init(&bench, bench_send, &rr, fake_cycles, NULL);
    CHECK(rr_announce(&rr, BENCH_EPT_NAME), "announce send failed");
    int n = host_recv(&src, &dst, buf, sizeof(buf));
    CHECK(n == 40, "NS announce len %d != 40", n);
    CHECK(dst == 0x35u, "NS dst 0x%x", dst);
    CHECK(src == BENCH_EPT_ADDR, "NS src %u", src);
    CHECK(memcmp(buf, BENCH_EPT_NAME, strlen(BENCH_EPT_NAME) + 1) == 0,
          "NS name mismatch");
    CHECK(rd32(buf + 32) == BENCH_EPT_ADDR, "NS addr field");
    CHECK(kicks > 0, "announce did not kick the host");

    // [3] echo round trip (also sets peer_addr)
    buf[0] = BCMD_ECHO;
    memcpy(buf + 1, "hello-he", 8);
    host_send(BENCH_EPT_ADDR, buf, 9);
    CHECK(rr_poll(&rr) == 1, "poll consumed != 1");
    n = host_recv(&src, &dst, buf, sizeof(buf));
    CHECK(n == 9 && buf[0] == BREP(BCMD_ECHO), "echo reply hdr");
    CHECK(memcmp(buf + 1, "hello-he", 8) == 0, "echo payload");
    CHECK(dst == HOST_EPT, "echo went to %u, want %u", dst, HOST_EPT);

    // [4] ping
    buf[0] = BCMD_PING;
    host_send(BENCH_EPT_ADDR, buf, 1);
    rr_poll(&rr);
    n = host_recv(&src, &dst, buf, sizeof(buf));
    CHECK(n == 16 && buf[0] == BREP(BCMD_PING), "ping reply");
    CHECK(rd32(buf + 4) == 160000000u, "ping core_hz");

    // [5] sink accounting: 100 good, 1 bad crc, 1 seq gap
    buf[0] = BCMD_SINK_RESET;
    host_send(BENCH_EPT_ADDR, buf, 1);
    rr_poll(&rr);
    host_recv(&src, &dst, buf, sizeof(buf));   // ack

    uint32_t seq = 0;
    uint8_t frame[480];
    for (int i = 0; i < 100; i++, seq++) {
        memset(frame, 0, sizeof(frame));
        frame[0] = BCMD_SINK_DATA;
        wr32(frame + 4, seq);
        for (uint32_t k = 12; k < sizeof(frame); k++) {
            frame[k] = (uint8_t)(seq * 3 + k);
        }
        wr32(frame + 8, he_crc32(frame + 12, sizeof(frame) - 12));
        host_send(BENCH_EPT_ADDR, frame, sizeof(frame));
        rr_poll(&rr);              // ring is 64 deep; drain as we go
    }
    frame[0] = BCMD_SINK_DATA;     // bad crc
    wr32(frame + 4, seq++);
    wr32(frame + 8, 0xDEADBEEFu);
    host_send(BENCH_EPT_ADDR, frame, sizeof(frame));
    seq++;                         // skip one -> gap
    frame[0] = BCMD_SINK_DATA;
    wr32(frame + 4, seq++);
    wr32(frame + 8, he_crc32(frame + 12, sizeof(frame) - 12));
    host_send(BENCH_EPT_ADDR, frame, sizeof(frame));
    rr_poll(&rr);

    buf[0] = BCMD_SINK_QUERY;
    host_send(BENCH_EPT_ADDR, buf, 1);
    rr_poll(&rr);
    n = host_recv(&src, &dst, buf, sizeof(buf));
    CHECK(n == 28 && buf[0] == BREP(BCMD_SINK_QUERY), "sink query reply");
    CHECK(rd32(buf + 4) == 102, "sink count %u != 102", rd32(buf + 4));
    CHECK(rd32(buf + 12) == 1, "crc_errs %u != 1", rd32(buf + 12));
    CHECK(rd32(buf + 16) == 1, "seq_gaps %u != 1", rd32(buf + 16));
    CHECK(rd32(buf + 24) > rd32(buf + 20), "cyc_last <= cyc_first");

    // [6] pump 200 frames of 480 B -- 3x ring wrap + back-pressure
    uint8_t p[16];
    p[0] = BCMD_PUMP;
    p[1] = p[2] = p[3] = 0;
    wr32(p + 4, 200);
    wr32(p + 8, 480);
    host_send(BENCH_EPT_ADDR, p, 12);
    rr_poll(&rr);

    uint32_t got = 0, bad = 0, done = 0, expect_seq = 0;
    for (int spins = 0; spins < 10000 && !done; spins++) {
        bench_pump_step(&bench);
        while ((n = host_recv(&src, &dst, buf, sizeof(buf))) >= 0) {
            if (buf[0] == BPUMP_DATA) {
                if (rd32(buf + 4) != expect_seq++
                    || he_crc32(buf + 12, n - 12) != rd32(buf + 8)) {
                    bad++;
                }
                got++;
            } else if (buf[0] == BREP(BCMD_PUMP)) {
                done = 1;
                CHECK(rd32(buf + 4) == 200, "pump done count");
            }
        }
    }
    CHECK(done, "pump never finished");
    CHECK(got == 200, "pump frames %u != 200", got);
    CHECK(bad == 0, "%u bad pump frames", bad);
    CHECK(rr.stat_tx_stall > 0, "expected tx back-pressure across 3 wraps");

    // [7] remote restart mid-session: cursors resume from used->idx
    rpmsg_remote_t rr2;
    CHECK(rr_init(&rr2, (uintptr_t)shm, OFF, BENCH_EPT_ADDR,
                  rx_cb, &bench, kick_cb, NULL), "re-init failed");
    memcpy(&rr, &rr2, sizeof(rr));
    buf[0] = BCMD_ECHO;
    buf[1] = 0x5A;
    host_send(BENCH_EPT_ADDR, buf, 2);
    CHECK(rr_poll(&rr) == 1, "post-restart poll");
    n = host_recv(&src, &dst, buf, sizeof(buf));
    CHECK(n == 2 && buf[0] == BREP(BCMD_ECHO) && buf[1] == 0x5A,
          "post-restart echo");

    printf("%s: %d checks, %d failures\n",
           failures ? "FAIL" : "PASS", checks, failures);
    return failures ? 1 : 0;
}
