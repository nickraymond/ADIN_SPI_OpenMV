// main.c -- S10 INTERIM 2: bm_core (FreeRTOS bm_os + lwIP + BCMP) on the
// AE3's M55_HE core against the mock NetworkDevice, wired to the HP
// runner over rpmsg. Loaded at runtime into SRAM9_B (nothing flashed),
// exactly like the bite-1 spike.
//
// Init order mirrors Sofar's own custom-device integration (bm_sbc
// runtime.cpp:470-532): device_init -> config_init -> bm_l2_init ->
// timer_callback_handler_init -> bm_ip_init -> bcmp_init -> power/link.

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

#if AUDIT_MIDDLEWARE
#include "bm_service.h"
#include "middleware.h"
#include "pubsub.h"
#include "sys_info_service.h"
#include "topology.h"
#endif

#include "bm_he.h"
#include "bm_net_mock.h"
#include "he_spike.h"     // rpmsg/MHU scaffold constants + he status page
#include "mhu.h"
#include "rpmsg_remote.h"

#define VDEV_DRIVER_OK 0x04u
#define WIRE_MAX_FRAME (RPMSG_BUF_SIZE - 16u /*rpmsg hdr*/ - sizeof(wire_hdr_t))

static he_status_page_t *const SP = (he_status_page_t *)STATUS_PAGE_ADDR;
static bm_status_page_t *const BP = (bm_status_page_t *)BM_STATUS_PAGE_ADDR;

extern void bm_set_err(uint32_t err);   // startup.c, sticky

BmErr bm_stubs_device_init(void);       // bm_stubs.c

static TaskHandle_t s_wire_task;
static rpmsg_remote_t s_rr;
static uint32_t s_tx_oversize;

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

    mock_stats_t stats = bm_net_mock_stats();
    reply.hdr = (wire_hdr_t){.cmd = WREP_STATUS,
                             .port = 0,
                             .len = sizeof(wire_status_t)};
    reply.status = (wire_status_t){
        .node_id = node_id(),
        .stack_stage = BP->stage,
        .stack_err = BP->err,
        .tx_frames = stats.tx_frames,
        .rx_frames = stats.rx_frames,
        .tx_oversize = s_tx_oversize,
        .link_up = stats.link_up ? 1u : 0u,
        .heap_free = (uint32_t)xPortGetFreeHeapSize(),
        .heap_min = (uint32_t)xPortGetMinimumEverFreeHeapSize(),
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
    if (len < sizeof(wire_hdr_t) + hdr->len) {
        return;
    }

    switch (hdr->cmd) {
    case WCMD_FRAME_RX:
        // l2 copies before queueing, transient buffer is fine.
        bm_net_mock_inject(hdr->port, (uint8_t *)payload, hdr->len);
        break;
    case WCMD_LINK:
        bm_net_mock_set_link(hdr->port,
                             hdr->len >= 1 && payload[0] == WCMD_LINK_UP);
        break;
    case WCMD_QUERY:
        wire_send_status();
        break;
    case WCMD_PING: {
        // Send a BCMP echo request to the multicast link-local address,
        // same as bm_sbc's app-thread usage; the ping reply is validated
        // inside ping.c (id + payload match) and narrated on the debug
        // ring -- the runner greps for it (verdict E).
        if (hdr->len < sizeof(wire_ping_t)) {
            break;
        }
        uint64_t target;
        memcpy(&target, payload, sizeof(target));   // payload may be unaligned
        uint16_t echo_len = (uint16_t)(hdr->len - sizeof(wire_ping_t));
        BmErr perr = bcmp_send_ping_request(
            target, &multicast_ll_addr,
            echo_len ? payload + sizeof(wire_ping_t) : NULL, echo_len);
        he_dbg_printf("wire: ping 0x%08lx%08lx (%u B) err %d\n",
                      (unsigned long)(target >> 32),
                      (unsigned long)(target & 0xFFFFFFFFu),
                      (unsigned)echo_len, (int)perr);
        break;
    }
    default:
        he_dbg_printf("wire: unknown cmd 0x%02x\n", hdr->cmd);
        break;
    }
}

// ---- HE -> host: forward stack TX frames ---------------------------------

static void wire_pump_tx(void) {
    mock_tx_frame_t frame;
    static uint8_t msg[RPMSG_BUF_SIZE];   // wire task only

    while (bm_net_mock_pop_tx(&frame, 0)) {
        if (frame.len > WIRE_MAX_FRAME) {
            s_tx_oversize++;
            he_dbg_printf("wire: oversize frame dropped (%u B)\n",
                          frame.len);
        } else {
            wire_hdr_t *hdr = (wire_hdr_t *)msg;
            *hdr = (wire_hdr_t){.cmd = WCMD_FRAME_TX,
                                .port = frame.port,
                                .len = frame.len};
            memcpy(msg + sizeof(*hdr), frame.data, frame.len);
            // Retry briefly: the host recycles buffers as it reads.
            for (int tries = 0; tries < 100; tries++) {
                if (rr_send(&s_rr, s_rr.peer_addr, msg,
                            sizeof(*hdr) + frame.len)) {
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(1));
            }
        }
        bm_free(frame.data);
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

#if AUDIT_MIDDLEWARE
    // S14 / BENCHSPEC V15 size audit: the BUILD-4 middleware slice,
    // initialized in bm_sbc's runtime.cpp order so nothing is linked
    // dead. Compiled only under AUDIT_MIDDLEWARE=1.
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
    he_dbg_printf("audit: middleware slice up\n");
#endif

    if ((err = bm_l2_netif_set_power(true)) != BmOK) {
        bm_set_err(err);
        return;
    }
    // Fire link-up AFTER init completes -- never from inside enable()
    // (bm_sbc's virtual_port_device.cpp:198 documents the l2 timer race).
    bm_net_mock_set_link(1, true);
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
    // status page before the runner ever asks.
    NetworkDevice device = bm_net_mock_device();
    bm_init_ladder(device);

    for (;;) {
        mock_stats_t stats = bm_net_mock_stats();
        BP->tick = xTaskGetTickCount();
        BP->tx_frames = stats.tx_frames;
        BP->rx_frames = stats.rx_frames;
        BP->hb_count = stats.hb_seen;
        SP->tick = BP->tick;
        SP->rx_count = s_rr.stat_rx;
        SP->tx_count = s_rr.stat_tx;

        uint32_t got = rr_poll(&s_rr);
        wire_pump_tx();

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
