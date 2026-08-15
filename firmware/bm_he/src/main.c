// main.c -- S16 BUILD-2a: bm_core (FreeRTOS bm_os + lwIP + BCMP +
// middleware) on the AE3's M55_HE core, attached to the BM network through
// the real rpmsg-wire NetworkDevice (bm_net_wire) and the HP bridge.
// Loaded at runtime into SRAM9_B (nothing flashed), exactly like the
// bite-1 spike.
//
// Init order mirrors Sofar's own custom-device integration (bm_sbc
// runtime.cpp:470-532): device_init -> config_init -> bm_l2_init ->
// timer_callback_handler_init -> bm_ip_init -> bcmp_init -> topology ->
// bm_service -> pubsub -> middleware -> sys_info -> power/link.
//
// Link discipline (REV-12): the HP bridge announces link state with
// WCMD_LINK; l2 collects UP from retry_negotiation on its 100 ms timer.
// Nothing here fires link-up directly -- the S10 init-ladder auto-link is
// gone with the mock.

#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "bcmp.h"
#include "bm_ip.h"
#include "bm_os.h"
#include "configuration.h"
#include "device.h"
#include "l2.h"
#include "messages/ping.h"
#include "timer_callback_handler.h"
#include "util.h"

#include "bm_service.h"
#include "middleware.h"
#include "pubsub.h"
#include "sys_info_service.h"
#include "topology.h"

#include "power_info_service.h"

#include "bm_he.h"
#include "bm_net_wire.h"
#include "camera_svc.h"
#include "power_hal.h"
#include "he_spike.h"     // rpmsg/MHU scaffold constants + he status page
#include "mhu.h"
#include "rpmsg_remote.h"
#include "wire_frag.h"

#define VDEV_DRIVER_OK 0x04u
// Per-message payload budget after the wire header (492 B); full L2
// frames up to 1514 B span ceil(1514/492) = 4 messages (wire_frag.h).
#define WIRE_MSG_PAYLOAD (RPMSG_BUF_SIZE - 16u /*rpmsg hdr*/ - sizeof(wire_hdr_t))

static he_status_page_t *const SP = (he_status_page_t *)STATUS_PAGE_ADDR;
static bm_status_page_t *const BP = (bm_status_page_t *)BM_STATUS_PAGE_ADDR;

extern void bm_set_err(uint32_t err);   // startup.c, sticky

BmErr bm_stubs_device_init(void);       // bm_stubs.c

static TaskHandle_t s_wire_task;
static rpmsg_remote_t s_rr;
static wire_reasm_t s_reasm;            // HP->HE reassembly (frames + pubs)
// What the open reassembly (or last one-shot) is: WCMD_FRAME_RX -> L2
// inject, WCMD_PUB -> camera publish. The vring is in-order, so frags
// always belong to the most recent first-message -- one buffer serves
// both kinds (S17).
static uint8_t s_reasm_kind = WCMD_FRAME_RX;

// Stream publisher (WCMD_STREAM -> pub/sub on STREAM_TOPIC).
static BmQueue s_stream_q;
static volatile uint32_t s_stream_sent;
static volatile uint32_t s_stream_errs;
static volatile bool s_stream_stop;

// MHU doorbell (IRQ context) -> wake the wire task.
static void doorbell(void) {
    BaseType_t woken = pdFALSE;
    if (s_wire_task) {
        vTaskNotifyGiveFromISR(s_wire_task, &woken);
    }
    portYIELD_FROM_ISR(woken);
}

static void rr_kick_adapter(void *arg) {
    (void)arg;
    he_mhu_kick();
}

// ---- host -> HE wire messages -------------------------------------------

static void wire_send_status(void) {
    struct {
        wire_hdr_t hdr;
        wire_status_t status;
    } __attribute__((packed)) reply;

    netwire_stats_t stats = bm_net_wire_stats();
    reply.hdr = (wire_hdr_t){.cmd = WREP_STATUS,
                             .port = 0,
                             .len = sizeof(wire_status_t)};
    reply.status = (wire_status_t){
        .node_id = node_id(),
        .stack_stage = BP->stage,
        .stack_err = BP->err,
        .tx_frames = stats.tx_frames,
        .rx_frames = stats.rx_frames,
        .tx_oversize = stats.tx_oversize,
        .link_up = stats.link_up ? 1u : 0u,
        .heap_free = (uint32_t)xPortGetFreeHeapSize(),
        .heap_min = (uint32_t)xPortGetMinimumEverFreeHeapSize(),
        .tx_dropped = stats.tx_dropped,
        .frag_errors = s_reasm.errors,
        .stream_sent = s_stream_sent,
        .stream_errs = s_stream_errs,
    };
    const BmIpAddr *ll = bm_ip_get(0);
    const BmIpAddr *ucast = bm_ip_get(1);
    if (ll) {
        memcpy(reply.status.ip_ll, ll, sizeof(reply.status.ip_ll));
    }
    if (ucast) {
        memcpy(reply.status.ip_ucast, ucast, sizeof(reply.status.ip_ucast));
    }
    (void)rr_send(&s_rr, s_rr.peer_addr, &reply, sizeof(reply));
}

static void wire_rx(void *arg, uint32_t src, const uint8_t *data,
                    uint32_t len) {
    (void)arg;
    (void)src;
    if (len < sizeof(wire_hdr_t)) {
        return;
    }
    const wire_hdr_t *hdr = (const wire_hdr_t *)data;
    const uint8_t *payload = data + sizeof(wire_hdr_t);
    uint16_t in_msg = (uint16_t)(len - sizeof(wire_hdr_t));

    switch (hdr->cmd) {
    case WCMD_FRAME_RX: {
        // hdr->len is the TOTAL frame length (wire_frag.h); this message
        // carries min(total, budget) bytes of it.
        s_reasm_kind = WCMD_FRAME_RX;
        uint16_t done = wire_reasm_first(&s_reasm, hdr->port, hdr->len,
                                         payload, in_msg);
        if (done) {
            // l2 copies before queueing, the reasm buffer may be reused.
            bm_net_wire_inject(hdr->port, s_reasm.buf, done);
        }
        break;
    }
    case WCMD_PUB: {
        // Same reassembly path as WCMD_FRAME_RX; completion publishes on
        // CAMERA_STREAM_TOPIC instead of injecting into L2.
        s_reasm_kind = WCMD_PUB;
        uint16_t done = wire_reasm_first(&s_reasm, hdr->port, hdr->len,
                                         payload, in_msg);
        if (done) {
            camera_svc_publish(s_reasm.buf, done);
        }
        break;
    }
    case WCMD_FRAG: {
        uint16_t done = wire_reasm_frag(&s_reasm, payload, in_msg);
        if (done) {
            if (s_reasm_kind == WCMD_PUB) {
                camera_svc_publish(s_reasm.buf, done);
            } else {
                bm_net_wire_inject(s_reasm.port, s_reasm.buf, done);
            }
        }
        break;
    }
    case WCMD_LINK:
        bm_net_wire_link_state(hdr->port, hdr->len >= 1 && in_msg >= 1 &&
                                              payload[0] == WCMD_LINK_UP);
        break;
    case WCMD_QUERY:
        wire_send_status();
        break;
    case WCMD_PING: {
        if (in_msg < hdr->len) {
            break;   // truncated control message
        }
        // Send a BCMP echo request to the GLOBAL multicast address
        // (ff03::1) -- the class L2 forwards through the pass-through
        // node (REV-6), and what bm_sbc's multinode app itself pings.
        // Link-local ff02::1 stops at the direct neighbor (measured
        // live, S16 rehearsal: a Camera->Telemetry ping via ll never
        // crossed Light). The reply is validated inside ping.c (id +
        // payload match) and narrated on the debug ring -- runners/
        // demos grep for it.
        if (hdr->len < sizeof(wire_ping_t)) {
            break;
        }
        uint64_t target;
        memcpy(&target, payload, sizeof(target));   // payload may be unaligned
        uint16_t echo_len = (uint16_t)(hdr->len - sizeof(wire_ping_t));
        BmErr perr = bcmp_send_ping_request(
            target, &multicast_global_addr,
            echo_len ? payload + sizeof(wire_ping_t) : NULL, echo_len);
        he_dbg_printf("wire: ping 0x%08lx%08lx (%u B) err %d\n",
                      (unsigned long)(target >> 32),
                      (unsigned long)(target & 0xFFFFFFFFu),
                      (unsigned)echo_len, (int)perr);
        break;
    }
    case WCMD_STREAM: {
        if (hdr->len < sizeof(wire_stream_t) ||
            in_msg < sizeof(wire_stream_t) || !s_stream_q) {
            break;
        }
        wire_stream_t cfg;
        memcpy(&cfg, payload, sizeof(cfg));         // may be unaligned
        if (cfg.rate_bps == 0) {
            s_stream_stop = true;   // polled by a running publisher
        } else if (bm_queue_send(s_stream_q, &cfg, 0) != BmOK) {
            he_dbg_printf("wire: stream cmd dropped (busy)\n");
        }
        break;
    }
    default:
        he_dbg_printf("wire: unknown cmd 0x%02x\n", hdr->cmd);
        break;
    }
}

// ---- HE -> host: forward stack TX frames ---------------------------------

static void wire_pump_tx(void) {
    netwire_tx_frame_t frame;
    static uint8_t msg[RPMSG_BUF_SIZE];   // wire task only

    while (bm_net_wire_pop_tx(&frame, 0)) {
        wire_frag_iter_t it;
        wire_frag_start(&it, WCMD_FRAME_TX, frame.port, frame.data,
                        frame.len);
        uint16_t n;
        while ((n = wire_frag_next(&it, msg, WIRE_MSG_PAYLOAD)) != 0) {
            // Retry briefly: the host recycles buffers as it reads.
            for (int tries = 0; tries < 100; tries++) {
                if (rr_send(&s_rr, s_rr.peer_addr, msg, n)) {
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(1));
            }
        }
        bm_free(frame.data);
    }
}

// Forward a pending camera/control command (service handler mailbox) to
// the HP bridge, which owns capture/encode/chunking.
static void wire_pump_capture(void) {
    wire_capture_t cap;
    if (!camera_svc_take_pending(&cap)) {
        return;
    }
    struct {
        wire_hdr_t hdr;
        wire_capture_t cap;
    } __attribute__((packed)) msg;
    msg.hdr = (wire_hdr_t){.cmd = WREP_CAPTURE,
                           .port = 0,
                           .len = sizeof(wire_capture_t)};
    msg.cap = cap;
    for (int tries = 0; tries < 100; tries++) {
        if (rr_send(&s_rr, s_rr.peer_addr, &msg, sizeof(msg))) {
            he_dbg_printf("camera: cmd mode %u -> bridge\n", cap.mode);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    he_dbg_printf("camera: cmd mode %u DROPPED (rpmsg busy)\n", cap.mode);
}

// ---- stream publisher (WCMD_STREAM) --------------------------------------

static void stream_task(void *param) {
    (void)param;
    static uint8_t payload[STREAM_MAX_PAYLOAD];   // stream task only
    memset(payload, 0xA5, sizeof(payload));

    for (;;) {
        wire_stream_t cfg;
        if (bm_queue_receive(s_stream_q, &cfg, UINT32_MAX) != BmOK) {
            continue;
        }
        if (cfg.rate_bps == 0 || cfg.payload_len == 0) {
            continue;
        }
        if (cfg.payload_len > STREAM_MAX_PAYLOAD) {
            cfg.payload_len = STREAM_MAX_PAYLOAD;
        }
        s_stream_stop = false;
        s_stream_sent = 0;
        s_stream_errs = 0;
        he_dbg_printf("stream: start %lu bps, %u B, %u s\n",
                      (unsigned long)cfg.rate_bps, cfg.payload_len,
                      cfg.secs);

        // Quota pacing (same scheme as stream_bench's tx role): publish
        // whatever the offered rate owes by now, then sleep. bm_pub
        // blocks in the L2 enqueue when the wire is saturated, so
        // overload shows as wall-clock stretch, not drops (D27).
        uint32_t seq = 0;
        uint64_t sent_bytes = 0;
        uint32_t t0 = bm_get_tick_count();
        for (;;) {
            uint32_t el_ms = bm_ticks_to_ms(bm_get_tick_count() - t0);
            if (s_stream_stop || el_ms >= (uint32_t)cfg.secs * 1000u) {
                break;
            }
            uint64_t quota = (uint64_t)cfg.rate_bps / 8u * el_ms / 1000u;
            while (sent_bytes < quota && !s_stream_stop) {
                memcpy(payload, &seq, sizeof(seq));   // u32 LE seq stamp
                if (bm_pub(STREAM_TOPIC, payload, cfg.payload_len, 0,
                           BM_COMMON_PUB_SUB_VERSION) == BmOK) {
                    s_stream_sent = s_stream_sent + 1;
                } else {
                    s_stream_errs = s_stream_errs + 1;
                }
                seq++;
                sent_bytes += cfg.payload_len;
            }
            bm_delay(5);
        }
        uint32_t wall_ms = bm_ticks_to_ms(bm_get_tick_count() - t0);
        he_dbg_printf("stream: done sent %lu errs %lu wall %lu ms\n",
                      (unsigned long)s_stream_sent,
                      (unsigned long)s_stream_errs,
                      (unsigned long)wall_ms);
    }
}

// ---- bring-up + pump ------------------------------------------------------

static void bm_init_ladder(NetworkDevice device) {
    BmErr err;

    if ((err = bm_stubs_device_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    config_init();   // RAM store is fresh -> loads empty partitions

    if ((err = bm_l2_init(device)) != BmOK) {
        bm_set_err(err);
        return;
    }
    BP->stage = BM_STAGE_L2;

    if ((err = timer_callback_handler_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    if ((err = bm_ip_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    BP->stage = BM_STAGE_IP;
    he_dbg_printf("ip: ll=%s\n", bm_ip_get_str(0));
    he_dbg_printf("ip: ucast=%s\n", bm_ip_get_str(1));

    if ((err = bcmp_init(device)) != BmOK) {
        bm_set_err(err);
        return;
    }
    BP->stage = BM_STAGE_BCMP;

    // Middleware slice, initialized in bm_sbc's runtime.cpp order.
    // Promoted from the S14 AUDIT_MIDDLEWARE size-audit build (V15: fits
    // at 91.6% and runs) -- BUILD-2/4 use it for real.
    if ((err = topology_init(device.trait->num_ports())) != BmOK) {
        bm_set_err(err);
        return;
    }
    if ((err = bm_service_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    if ((err = bm_pubsub_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    if ((err = bm_middleware_init()) != BmOK) {
        bm_set_err(err);
        return;
    }
    sys_info_service_init();

    // S17 BUILD-4: power service against the simulated HAL backend
    // (power_hal_sim.c; regulator driver swaps in on hardware day), then
    // the camera/control service (camera_svc.c).
    if ((err = power_hal_init()) != BmOK ||
        (err = power_info_service_init(power_hal_power_info_cb, NULL)) !=
            BmOK) {
        bm_set_err(err);
        return;
    }
    if ((err = camera_svc_init()) != BmOK) {
        bm_set_err(err);
        return;
    }

    // Stream publisher: waits on WCMD_STREAM configs.
    s_stream_q = bm_queue_create(1, sizeof(wire_stream_t));
    if (!s_stream_q ||
        xTaskCreate(stream_task, "stream", 768 /* words */, NULL,
                    tskIDLE_PRIORITY + 1, NULL) != pdPASS) {
        bm_set_err(BmENOMEM);
        return;
    }

    if ((err = bm_l2_netif_set_power(true)) != BmOK) {
        bm_set_err(err);
        return;
    }
    // No link-up here (REV-12): the HP bridge announces WCMD_LINK and
    // l2's 100 ms renegotiation timer collects it via the device's
    // retry_negotiation. RUNNING means "ladder complete", link may
    // still be down until the bridge attaches.
    BP->stage = BM_STAGE_RUNNING;
    // newlib-nano printf has no %llx -- print the id in halves.
    he_dbg_printf("bm stack RUNNING, node 0x%08lx%08lx\n",
                  (unsigned long)(node_id() >> 32),
                  (unsigned long)(node_id() & 0xFFFFFFFFu));
}

static void wire_task(void *param) {
    (void)param;
    BP->stage = BM_STAGE_RTOS;

    he_mhu_init(doorbell);
    while (!rr_init(&s_rr, SHM_BASE, 0, WIRE_EPT_ADDR, wire_rx, NULL,
                    rr_kick_adapter, NULL)) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    while (!(rr_vdev_status(&s_rr) & VDEV_DRIVER_OK)) {
        SP->rsc_status = rr_vdev_status(&s_rr);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    SP->rsc_status = rr_vdev_status(&s_rr);
    while (!rr_announce(&s_rr, WIRE_EPT_NAME)) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    he_mhu_kick();
    BP->stage = BM_STAGE_RPMSG;

    // The BM stack comes up from this task so its failures land on the
    // status page before the bridge ever asks.
    NetworkDevice device = bm_net_wire_device();
    bm_init_ladder(device);

    for (;;) {
        netwire_stats_t stats = bm_net_wire_stats();
        BP->tick = xTaskGetTickCount();
        BP->tx_frames = stats.tx_frames;
        BP->rx_frames = stats.rx_frames;
        BP->hb_count = stats.hb_seen;
        SP->tick = BP->tick;
        SP->rx_count = s_rr.stat_rx;
        SP->tx_count = s_rr.stat_tx;

        uint32_t got = rr_poll(&s_rr);
        wire_pump_tx();
        wire_pump_capture();

        if (got == 0) {
            // Idle: wake on doorbell, 1 ms safety net (kick can race the
            // notify arm -- same pattern as the bite-1 bench loop).
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1));
        }
    }
}

// ---- FreeRTOS hooks -------------------------------------------------------

void vAssertCalled(const char *file, int line) {
    (void)file;
    (void)line;
    he_set_err(HE_ERR_ASSERT);
    bm_set_err(0xDEADu);
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}

void vApplicationMallocFailedHook(void) {
    he_dbg_printf("freertos: malloc failed\n");
    bm_set_err(0xA110Cu);
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}

void vApplicationStackOverflowHook(TaskHandle_t task, char *name) {
    (void)task;
    he_dbg_printf("freertos: stack overflow in %s\n", name ? name : "?");
    bm_set_err(0x570Fu);
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}

int main(void) {
    he_dbg_init();
    xTaskCreate(wire_task, "wire", 1024 /* words */, NULL,
                tskIDLE_PRIORITY + 6, &s_wire_task);
    vTaskStartScheduler();
    he_set_err(HE_ERR_ASSERT);          // scheduler returned: heap too small
    for (;;) {
    }
}
