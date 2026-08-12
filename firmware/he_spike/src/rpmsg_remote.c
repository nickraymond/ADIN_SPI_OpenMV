// rpmsg_remote.c -- the device (remote) half of rpmsg-over-virtio against
// the HP host's fixed shared-memory layout. ~200 lines instead of the
// open-amp + libmetal stack: the layout is pinned by the host build
// (micropython extmod/modopenamp.c), there is exactly one vdev, two
// vrings, features = NS only (no EVENT_IDX), so classic virtio ring
// semantics apply. Wire formats cited per struct below.
//
// Device-role rules (virtio 1.x legacy layout, open-amp compatible):
//   * The host (driver) owns desc tables and avail rings; we only READ
//     them and WRITE used rings.
//   * vring0 = host->remote: host queues FILLED buffers; we consume.
//   * vring1 = remote->host: host queues EMPTY buffers; we take one,
//     fill it, and return it via used with the written length.
//   * One consumed avail entry always produces exactly one used entry,
//     so used->idx doubles as our restart-safe cursor.

#include <string.h>
#include "rpmsg_remote.h"
#include "he_spike.h"

// ---- wire formats ------------------------------------------------------
// vring layouts: lib/open-amp lib/include/openamp/virtio_ring.h (packed).
typedef struct __attribute__((packed)) {
    uint64_t addr;
    uint32_t len;
    uint16_t flags;
    uint16_t next;
} vr_desc_t;

typedef struct __attribute__((packed)) {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[];            // [num]
} vr_avail_t;

typedef struct __attribute__((packed)) {
    uint32_t id;
    uint32_t len;
} vr_used_elem_t;

typedef struct __attribute__((packed)) {
    uint16_t flags;
    uint16_t idx;
    vr_used_elem_t ring[];      // [num]
} vr_used_t;

#define VRING_AVAIL_F_NO_INTERRUPT 1u

// remoteproc resource table as the host lays it out
// (micropython extmod/modopenamp.c openamp_rsc_table_t + openamp_rsc_table_init):
// header {ver,num,reserved[2],offset[num]} then fw_rsc_vdev + 2 vrings.
typedef struct __attribute__((packed)) {
    uint32_t ver;
    uint32_t num;
    uint32_t reserved[2];
    uint32_t offset[2];
    // fw_rsc_vdev (openamp/remoteproc.h)
    uint32_t type;              // RSC_VDEV = 3
    uint32_t id;                // VIRTIO_ID_RPMSG = 7
    uint32_t notifyid;
    uint32_t dfeatures;
    uint32_t gfeatures;
    uint32_t config_len;
    uint8_t status;
    uint8_t num_of_vrings;
    uint8_t reserved2[2];
    // fw_rsc_vdev_vring x2
    struct __attribute__((packed)) {
        uint32_t da;
        uint32_t align;
        uint32_t num;
        uint32_t notifyid;
        uint32_t reserved;
    } vring[2];
} rsc_table_t;

#define RSC_VDEV        3u
#define VIRTIO_ID_RPMSG 7u

// rpmsg header + NS announce (lib/open-amp lib/rpmsg/rpmsg_internal.h;
// NS endpoint addr = 0x35, openamp/rpmsg.h:31; RPMSG_NS_CREATE = 0).
typedef struct __attribute__((packed)) {
    uint32_t src;
    uint32_t dst;
    uint32_t reserved;
    uint16_t len;
    uint16_t flags;
} rpmsg_hdr_t;

typedef struct __attribute__((packed)) {
    char name[32];
    uint32_t addr;
    uint32_t flags;
} rpmsg_ns_msg_t;

#define RPMSG_NS_EPT_ADDR 0x35u
#define RPMSG_NS_CREATE   0u

// ---- memory barriers ---------------------------------------------------
// On target the SHM is non-cacheable normal memory; DMB orders our ring
// writes against the published index. The host harness build gets plain
// compiler barriers, which is sufficient single-threaded.
#if defined(__arm__)
#define rr_dmb() __asm volatile ("dmb" ::: "memory")
#else
#define rr_dmb() __asm volatile ("" ::: "memory")
#endif

// ---- helpers ------------------------------------------------------------
static inline vr_desc_t *DESC(const rpmsg_remote_t *rr, int q) {
    return (vr_desc_t *)rr->vr[q].desc;
}
static inline vr_avail_t *AVAIL(const rpmsg_remote_t *rr, int q) {
    return (vr_avail_t *)rr->vr[q].avail;
}
static inline vr_used_t *USED(const rpmsg_remote_t *rr, int q) {
    return (vr_used_t *)rr->vr[q].used;
}

// vring component addresses from base/num/align, exactly vring_init()
// in openamp/virtio_ring.h: desc, then avail, used aligned up.
static void layout_vring(struct vring_layout *v, uintptr_t base,
                         uint32_t num, uint32_t align) {
    v->desc = (void *)base;
    v->avail = (void *)(base + num * sizeof(vr_desc_t));
    uintptr_t used = (uintptr_t)v->avail
        + sizeof(vr_avail_t) + num * sizeof(uint16_t) + sizeof(uint16_t);
    v->used = (void *)((used + align - 1) & ~(uintptr_t)(align - 1));
    v->num = num;
}

uint32_t rr_max_payload(void) {
    return RPMSG_BUF_SIZE - sizeof(rpmsg_hdr_t);
}

// Bus address (32-bit, target address space) -> dereferenceable pointer.
static inline void *xlate(const rpmsg_remote_t *rr, uint64_t busaddr) {
    return (void *)((uintptr_t)busaddr + rr->addr_offset);
}

bool rr_init(rpmsg_remote_t *rr, uintptr_t shm_base, intptr_t addr_offset,
             uint32_t ept_addr,
             rr_rx_cb_t rx_cb, void *rx_arg,
             rr_kick_cb_t kick_cb, void *kick_arg) {
    const rsc_table_t *rsc = (const rsc_table_t *)shm_base;

    if (rsc->ver != 1u || rsc->type != RSC_VDEV || rsc->id != VIRTIO_ID_RPMSG
        || rsc->num_of_vrings != 2u
        || rsc->vring[0].num == 0u || rsc->vring[0].num > 1024u
        || rsc->vring[0].num != rsc->vring[1].num
        || rsc->vring[0].da == 0u || rsc->vring[1].da == 0u) {
        return false;
    }

    memset(rr, 0, sizeof(*rr));
    rr->shm_base = shm_base;
    rr->addr_offset = addr_offset;
    rr->ept_addr = ept_addr;
    rr->rx_cb = rx_cb;
    rr->rx_arg = rx_arg;
    rr->kick_cb = kick_cb;
    rr->kick_arg = kick_arg;

    // The rsc table's .da fields are the authority; on target they are
    // global addresses both cores resolve identically. The host harness
    // rewrites them to point into its fake SHM.
    for (int q = 0; q < 2; q++) {
        layout_vring(&rr->vr[q], (uintptr_t)xlate(rr, rsc->vring[q].da),
                     rsc->vring[q].num, rsc->vring[q].align);
        // Restart-safe cursor: every avail entry we ever consumed was
        // answered by one used entry.
        rr->consumed[q] = USED(rr, q)->idx;
    }
    return true;
}

uint8_t rr_vdev_status(const rpmsg_remote_t *rr) {
    // Volatile: callers poll this while the host is still initializing.
    return ((volatile const rsc_table_t *)rr->shm_base)->status;
}

static void kick(rpmsg_remote_t *rr, int q) {
    if (!(AVAIL(rr, q)->flags & VRING_AVAIL_F_NO_INTERRUPT) && rr->kick_cb) {
        rr->kick_cb(rr->kick_arg);
    }
}

// Return one buffer to the host through q's used ring.
static void push_used(rpmsg_remote_t *rr, int q, uint16_t head,
                      uint32_t written) {
    vr_used_t *u = USED(rr, q);
    u->ring[u->idx % rr->vr[q].num] =
        (vr_used_elem_t) {.id = head, .len = written};
    rr_dmb();
    u->idx++;
    rr_dmb();
}

bool rr_send(rpmsg_remote_t *rr, uint32_t dst, const void *data,
             uint32_t len) {
    if (len > rr_max_payload()) {
        return false;
    }
    vr_avail_t *a = AVAIL(rr, 1);
    rr_dmb();
    if (rr->consumed[1] == a->idx) {     // no free tx buffer right now
        rr->stat_tx_stall++;
        return false;
    }
    uint16_t head = a->ring[rr->consumed[1] % rr->vr[1].num];
    if (head >= rr->vr[1].num) {
        return false;                    // corrupt ring; refuse loudly
    }
    vr_desc_t *d = &DESC(rr, 1)[head];
    rpmsg_hdr_t *h = (rpmsg_hdr_t *)xlate(rr, d->addr);
    uint32_t room = d->len;
    if (room < sizeof(*h) + len) {
        return false;
    }
    h->src = rr->ept_addr;
    h->dst = dst;
    h->reserved = 0;
    h->len = (uint16_t)len;
    h->flags = 0;
    memcpy((uint8_t *)h + sizeof(*h), data, len);

    rr->consumed[1]++;
    push_used(rr, 1, head, sizeof(*h) + len);
    rr->stat_tx++;
    kick(rr, 1);
    return true;
}

bool rr_announce(rpmsg_remote_t *rr, const char *name) {
    rpmsg_ns_msg_t ns;
    memset(&ns, 0, sizeof(ns));
    strncpy(ns.name, name, sizeof(ns.name) - 1);
    ns.addr = rr->ept_addr;
    ns.flags = RPMSG_NS_CREATE;
    return rr_send(rr, RPMSG_NS_EPT_ADDR, &ns, sizeof(ns));
}

uint32_t rr_poll(rpmsg_remote_t *rr) {
    vr_avail_t *a = AVAIL(rr, 0);
    uint32_t n = 0;

    rr_dmb();
    while (rr->consumed[0] != a->idx) {
        uint16_t head = a->ring[rr->consumed[0] % rr->vr[0].num];
        uint32_t written = 0;
        if (head < rr->vr[0].num) {
            vr_desc_t *d = &DESC(rr, 0)[head];
            const rpmsg_hdr_t *h = (const rpmsg_hdr_t *)xlate(rr, d->addr);
            if (d->len >= sizeof(*h) && h->len <= d->len - sizeof(*h)) {
                if (h->dst == rr->ept_addr && rr->rx_cb) {
                    rr->peer_addr = h->src;
                    rr->rx_cb(rr->rx_arg, h->src,
                              (const uint8_t *)h + sizeof(*h), h->len);
                }
                // Messages for other endpoints (none exist) are dropped;
                // the buffer is recycled either way.
            }
            written = d->len;
        }
        rr->consumed[0]++;
        push_used(rr, 0, head, written);
        rr->stat_rx++;
        n++;
        rr_dmb();
    }
    if (n) {
        kick(rr, 0);
    }
    return n;
}
