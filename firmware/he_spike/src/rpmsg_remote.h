// rpmsg_remote.h -- device-role rpmsg over the fixed vring layout the HP
// host (micropython modopenamp) sets up. Deliberately free of FreeRTOS,
// MHU and MMIO dependencies so the exact same code runs under the host
// test harness against a malloc'd fake SHM.
#ifndef RPMSG_REMOTE_H
#define RPMSG_REMOTE_H

#include <stdbool.h>
#include <stdint.h>

typedef struct rpmsg_remote rpmsg_remote_t;

typedef void (*rr_rx_cb_t)(void *arg, uint32_t src, const uint8_t *data,
                           uint32_t len);
typedef void (*rr_kick_cb_t)(void *arg);

struct rpmsg_remote {
    uintptr_t shm_base;        // rsc table base (target: 0x60000000)
    intptr_t addr_offset;      // added to every bus address in the rsc
                               // table / descriptors before dereferencing.
                               // 0 on target (global == local); the host
                               // harness sets fake_shm - 0x60000000 so
                               // tests run the target's 32-bit address
                               // arithmetic unchanged.
    rr_rx_cb_t rx_cb;          // messages addressed to our endpoint
    void *rx_arg;
    rr_kick_cb_t kick_cb;      // notify the host (target: MHU doorbell)
    void *kick_arg;
    uint32_t ept_addr;         // our fixed endpoint address
    uint32_t peer_addr;        // last host src that talked to us
    // internals
    struct vring_layout {
        volatile void *desc;
        volatile void *avail;
        volatile void *used;
        uint32_t num;
    } vr[2];                   // [0] host->remote, [1] remote->host
    uint32_t consumed[2];      // device-side ring cursors
    uint32_t stat_rx, stat_tx, stat_tx_stall;
};

// Parse + sanity-check the host's rsc table; snapshot ring cursors from
// used->idx (self-healing across remote restarts). Returns false and
// touches nothing on a malformed table.
bool rr_init(rpmsg_remote_t *rr, uintptr_t shm_base, intptr_t addr_offset,
             uint32_t ept_addr,
             rr_rx_cb_t rx_cb, void *rx_arg,
             rr_kick_cb_t kick_cb, void *kick_arg);

// Host's virtio status byte (rsc vdev.status); DRIVER_OK bit = 0x04.
uint8_t rr_vdev_status(const rpmsg_remote_t *rr);

// Send our name-service announce (RPMSG_NS_CREATE) for `name`.
bool rr_announce(rpmsg_remote_t *rr, const char *name);

// Drain host->remote ring; delivers matching messages to rx_cb.
// Returns number of messages consumed.
uint32_t rr_poll(rpmsg_remote_t *rr);

// Send one message to dst (usually rr->peer_addr). Returns false if no
// free tx buffer (caller retries; the host recycles buffers as it reads).
bool rr_send(rpmsg_remote_t *rr, uint32_t dst, const void *data,
             uint32_t len);

// Max payload per message (RPMSG_BUF_SIZE - 16-byte rpmsg header).
uint32_t rr_max_payload(void);

#endif
