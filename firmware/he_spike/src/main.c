// main.c -- S10 bite 1: FreeRTOS on the AE3's M55_HE core serving the
// "he-bench" rpmsg endpoint. Loaded at runtime into SRAM9_B by the HP
// runner (s10_pipe_bench.py); nothing is flashed.

#include "FreeRTOS.h"
#include "task.h"

#include "he_spike.h"
#include "mhu.h"
#include "rpmsg_remote.h"
#include "bench.h"

#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)
#define VDEV_DRIVER_OK 0x04u

static he_status_page_t *const SP = (he_status_page_t *)STATUS_PAGE_ADDR;

static TaskHandle_t s_bench_task;
static rpmsg_remote_t s_rr;
static bench_t s_bench;

static uint32_t cycles_now(void) {
    return DWT_CYCCNT;
}

// MHU doorbell (IRQ context) -> wake the bench task.
static void doorbell(void) {
    BaseType_t woken = pdFALSE;
    if (s_bench_task) {
        vTaskNotifyGiveFromISR(s_bench_task, &woken);
    }
    portYIELD_FROM_ISR(woken);
}

static void rr_kick_adapter(void *arg) {
    (void)arg;
    he_mhu_kick();
}

static bool bench_send_adapter(void *arg, const void *data, uint32_t len) {
    rpmsg_remote_t *rr = arg;
    return rr_send(rr, rr->peer_addr, data, len);
}

static void bench_rx_adapter(void *arg, uint32_t src, const uint8_t *data,
                             uint32_t len) {
    (void)src;
    bench_on_message(arg, data, len);
}

void he_spi_test(uint32_t result[5]);   // spi_probe.c

static void bench_task(void *param) {
    (void)param;
    SP->stage = HE_STAGE_RTOS;

    he_mhu_init(doorbell);

    // Wait for the host's rsc table + virtio DRIVER_OK. rr_init rejects
    // a garbage table, so retrying it doubles as the sync point.
    while (!rr_init(&s_rr, SHM_BASE, 0, BENCH_EPT_ADDR,
                    bench_rx_adapter, &s_bench, rr_kick_adapter, NULL)) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    while (!(rr_vdev_status(&s_rr) & VDEV_DRIVER_OK)) {
        SP->rsc_status = rr_vdev_status(&s_rr);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    SP->rsc_status = rr_vdev_status(&s_rr);
    SP->stage = HE_STAGE_DRIVER_OK;

    bench_init(&s_bench, bench_send_adapter, &s_rr, cycles_now, he_spi_test);

    while (!rr_announce(&s_rr, BENCH_EPT_NAME)) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    he_mhu_kick();
    SP->stage = HE_STAGE_NS_SENT;

    for (;;) {
        SP->stage = HE_STAGE_RUNNING;
        SP->tick = s_bench.tick = xTaskGetTickCount();
        SP->rx_count = s_rr.stat_rx;
        SP->tx_count = s_rr.stat_tx;

        uint32_t got = rr_poll(&s_rr);
        bench_pump_step(&s_bench);

        if (got == 0 && s_bench.pump_size == 0) {
            // Idle: sleep until the host kicks us (1 ms safety net --
            // doorbells can race the ulTaskNotifyTake arm).
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1));
        }
    }
}

// FreeRTOS static-allocation support is off; provide the assert hook.
void vAssertCalled(const char *file, int line) {
    (void)file;
    (void)line;
    he_set_err(HE_ERR_ASSERT);
    taskDISABLE_INTERRUPTS();
    for (;;) {
    }
}

int main(void) {
    xTaskCreate(bench_task, "bench", 1024 /* words */, NULL,
                tskIDLE_PRIORITY + 1, &s_bench_task);
    vTaskStartScheduler();
    he_set_err(HE_ERR_ASSERT);          // scheduler returned: heap too small
    for (;;) {
    }
}
