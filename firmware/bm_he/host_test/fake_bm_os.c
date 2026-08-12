// fake_bm_os.c -- minimal host-side bm_os backend so the mock device and
// stubs run under clang+ASan exactly as they do on target. Only what
// bm_net_mock.c + bm_stubs.c actually call is implemented; anything else
// aborts loudly.

#include <assert.h>
#include <stdlib.h>
#include <string.h>

#include "bm_os.h"

void *bm_malloc(size_t size) { return malloc(size); }
void bm_free(void *ptr) { free(ptr); }

// ---- queue: single-threaded FIFO (the host tests never block) ----------

typedef struct {
    uint8_t *items;
    uint32_t len, item_size, head, count;
} fake_queue_t;

BmQueue bm_queue_create(uint32_t queue_length, uint32_t item_size) {
    fake_queue_t *q = calloc(1, sizeof(*q));
    assert(q);
    q->items = calloc(queue_length, item_size);
    assert(q->items);
    q->len = queue_length;
    q->item_size = item_size;
    return q;
}

void bm_queue_delete(BmQueue queue) {
    fake_queue_t *q = queue;
    free(q->items);
    free(q);
}

BmErr bm_queue_send(BmQueue queue, const void *item, uint32_t timeout_ms) {
    (void)timeout_ms;
    fake_queue_t *q = queue;
    if (q->count == q->len) {
        return BmENOMEM;   // matches bm_freertos.c's queue-full mapping
    }
    uint32_t tail = (q->head + q->count) % q->len;
    memcpy(&q->items[tail * q->item_size], item, q->item_size);
    q->count++;
    return BmOK;
}

BmErr bm_queue_receive(BmQueue queue, void *item, uint32_t timeout_ms) {
    (void)timeout_ms;
    fake_queue_t *q = queue;
    if (q->count == 0) {
        return BmETIMEDOUT;
    }
    memcpy(item, &q->items[q->head * q->item_size], q->item_size);
    q->head = (q->head + 1) % q->len;
    q->count--;
    return BmOK;
}

BmErr bm_queue_send_to_front_from_isr(BmQueue queue, const void *item) {
    (void)queue;
    (void)item;
    assert(0 && "not used by bm_he host tests");
}

// ---- tick clock: test-controlled ----------------------------------------

static uint32_t s_fake_ticks;

void fake_os_advance_ms(uint32_t ms) { s_fake_ticks += ms; }

uint32_t bm_get_tick_count(void) { return s_fake_ticks; }
uint32_t bm_get_tick_count_from_isr(void) { return s_fake_ticks; }
uint32_t bm_ms_to_ticks(uint32_t ms) { return ms; }      // 1 kHz, like target
uint32_t bm_ticks_to_ms(uint32_t ticks) { return ticks; }
void bm_delay(uint32_t ms) { fake_os_advance_ms(ms); }
