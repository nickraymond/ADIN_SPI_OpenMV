// he_sample.c -- see he_sample.h. Deliberately free of FreeRTOS and
// rpmsg specifics: the three platform values it needs arrive through the
// he_plat_* externs (he_dbg.c on target, the test file on host), exactly
// like he_dbg_printf is a sink in the host harness. That keeps the whole
// sampler compiled unchanged by the host tests.

#include "he_sample.h"

#include <string.h>

#include "bm_net_wire.h"

// Platform glue -- he_dbg.c on target, host_test/test_bm_he.c on host.
uint32_t he_plat_heap_free(void);
uint32_t he_plat_heap_min(void);
uint32_t he_plat_tick_ms(void);

static he_sample_page_t *s_page;
static uint32_t s_rpmsg_drops;

void he_sample_init(void *page) {
    s_page = (he_sample_page_t *)page;
    s_rpmsg_drops = 0;
    if (!s_page) {
        return;
    }
    memset(s_page, 0, sizeof(*s_page));
    s_page->capacity = HE_SAMPLE_CAP;
    s_page->version = HE_SAMPLE_VERSION;
    s_page->count = 0;
    // Magic last: a reader that catches us mid-init sees no magic rather
    // than a header promising records that are not there yet.
    s_page->magic = HE_SAMPLE_MAGIC;
}

void he_sample_pub(uint16_t idx, uint16_t count, uint16_t len, int err) {
    if (!s_page) {
        return;
    }
    netwire_stats_t st = bm_net_wire_stats();
    uint32_t depth = st.txq_pushed - st.txq_popped;

    he_sample_rec_t *r = &s_page->rec[s_page->count % HE_SAMPLE_CAP];
    r->idx = idx;
    r->count = count;
    r->len = len;
    // BmErr is small and positive in this stack; saturate rather than
    // wrap so an unexpected value stays visibly abnormal.
    r->err = (err < 0 || err > 255) ? 255u : (uint8_t)err;
    r->txq_depth = depth > 255u ? 255u : (uint8_t)depth;
    r->heap_free = he_plat_heap_free();
    r->heap_min = he_plat_heap_min();
    r->tx_dropped = st.tx_dropped > 0xFFFFu ? 0xFFFFu
                                            : (uint16_t)st.tx_dropped;
    r->rpmsg_drops = s_rpmsg_drops > 0xFFFFu ? 0xFFFFu
                                             : (uint16_t)s_rpmsg_drops;
    r->tick_ms = he_plat_tick_ms();

    // Record complete -> publish it (see he_sample.h, torn-record rule).
    s_page->count = s_page->count + 1;
}

void he_sample_note_rpmsg_drop(void) { s_rpmsg_drops++; }

uint32_t he_sample_rpmsg_drops(void) { return s_rpmsg_drops; }

const he_sample_page_t *he_sample_get_page(void) { return s_page; }
