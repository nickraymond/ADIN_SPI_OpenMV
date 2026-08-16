// he_dbg.c -- printf into a RAM ring the HP runner dumps via machine.mem32
// (address/size/write-index published on the bm status page). This is the
// only "console" the HE core has; bm_core's bm_debug and lwIP's DIAG both
// land here.

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "bm_he.h"

#define RING_SIZE 4096u
#define LINE_MAX  128u

static char s_ring[RING_SIZE];
static uint32_t s_widx;   // total bytes written; ring pos = s_widx % RING_SIZE

static bm_status_page_t *const BP = (bm_status_page_t *)BM_STATUS_PAGE_ADDR;

void he_dbg_init(void) {
    memset(s_ring, 0, sizeof(s_ring));
    s_widx = 0;
    BP->dbg_ring_addr = (uint32_t)(uintptr_t)s_ring;
    BP->dbg_ring_size = RING_SIZE;
    BP->dbg_ring_widx = 0;
}

void he_dbg_printf(const char *fmt, ...) {
    char line[LINE_MAX];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    if (n <= 0) {
        return;
    }
    if ((uint32_t)n >= sizeof(line)) {
        n = sizeof(line) - 1;
    }

    // Callers are tasks (bm_core/lwIP never log from ISRs in this subset);
    // a critical section keeps interleaved lines whole. Safe pre-scheduler
    // too (just masks interrupts + nesting count on this port).
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        taskENTER_CRITICAL();
    }
    for (int i = 0; i < n; i++) {
        s_ring[s_widx % RING_SIZE] = line[i];
        s_widx++;
    }
    BP->dbg_ring_widx = s_widx;
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        taskEXIT_CRITICAL();
    }
}

// ---- platform glue for he_sample.c (S19 bite 1) --------------------------
// The sampler stays free of FreeRTOS so the host harness compiles it
// unchanged; these three are its only platform dependencies, and this is
// already the file that owns "HE-side plumbing the rest of the app should
// not have to know about".

uint32_t he_plat_heap_free(void) { return (uint32_t)xPortGetFreeHeapSize(); }

uint32_t he_plat_heap_min(void) {
    return (uint32_t)xPortGetMinimumEverFreeHeapSize();
}

uint32_t he_plat_tick_ms(void) {
    // configTICK_RATE_HZ is 1000 (FreeRTOSConfig.h), so ticks are ms.
    return (uint32_t)xTaskGetTickCount();
}

// LWIP_RAND: xorshift32 seeded from the DWT cycle counter at first use.
#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)

uint32_t bm_he_rand(void) {
    static uint32_t state;
    if (state == 0) {
        state = DWT_CYCCNT | 1u;
    }
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}
