// he_sample.h -- S19 bite 1: per-chunk instrumentation of the pub/sub
// publish path, written into a fixed RAM page the HP reads with
// machine.mem32.
//
// WHY A PAGE AND NOT THE DEBUG RING (he_dbg.c):
//  1. The failure being measured ends in vApplicationMallocFailedHook,
//     which disables interrupts and spins forever -- no reply to
//     WCMD_QUERY ever comes back, so wire_status_t's heap_free/heap_min
//     (main.c) cannot report the moment that matters. Only RAM read from
//     the other core survives, which is how S18's evidence was won.
//  2. HD is 26 chunks. 26 he_dbg_printf lines at ~60 B wrap the 4 KB ring
//     inside two frames, and vsnprintf under taskENTER_CRITICAL() sits on
//     the very path whose timing is under measurement. Fixed binary
//     records cost a handful of stores.
//
// The page is self-describing (magic + version + capacity + stride) so a
// reader validates before trusting it, and it is at a FIXED address
// carved out of the linker region (bm_he.ld) rather than published
// through bm_status_page_t -- the status page is read by fixed offset in
// three places, and a heap investigation should not depend on a struct
// that heap corruption could scribble on.
//
// Record ordering: the record is written FIRST, `count` is incremented
// AFTER. A halt mid-write therefore leaves a torn record OUTSIDE the
// advertised count -- readers see only whole records.
#ifndef HE_SAMPLE_H
#define HE_SAMPLE_H

#include <stdint.h>

// 1 KB reserved at the top of the APP region, below the two status pages
// (bm_he.ld keeps the linker out of it by shortening MEMORY LENGTH).
#define HE_SAMPLE_PAGE_ADDR 0x600BFA00u
#define HE_SAMPLE_MAGIC     0x504D5348u   // 'HSMP' little-endian
#define HE_SAMPLE_VERSION   1u
#define HE_SAMPLE_CAP       40u           // records; HD = 26 chunks/frame

// One published chunk. Fields are naturally aligned at 24 B; the HP
// unpacks "<HHHBBIIHHI" (bench/probes/s19_pub_probe.py).
typedef struct {
    uint16_t idx;          // chunk index within the frame (chunk header)
    uint16_t count;        // chunks in this frame  (chunk header)
    uint16_t len;          // payload bytes handed to bm_pub
    uint8_t  err;          // BmErr from bm_pub, 0 = BmOK
    uint8_t  txq_depth;    // netwire TX frames queued, undrained, right now
    uint32_t heap_free;    // xPortGetFreeHeapSize() AFTER the publish
    uint32_t heap_min;     // xPortGetMinimumEverFreeHeapSize()
    uint16_t tx_dropped;   // netwire txq-full drops, cumulative
    uint16_t tx_stalls;    // outbound rpmsg ring found full, cumulative
    uint32_t tick_ms;      // scheduler tick at the sample
} __attribute__((packed)) he_sample_rec_t;

typedef struct {
    volatile uint32_t magic;      // HE_SAMPLE_MAGIC once initialized
    volatile uint32_t version;    // HE_SAMPLE_VERSION
    volatile uint32_t capacity;   // HE_SAMPLE_CAP (records before wrap)
    volatile uint32_t count;      // records EVER written; slot = count % cap
    he_sample_rec_t rec[HE_SAMPLE_CAP];
} __attribute__((packed)) he_sample_page_t;   // 16 + 40*24 = 976 B

// Point the sampler at its page and stamp the header. Call once, before
// the scheduler starts. A NULL page disables sampling (every entry point
// below becomes a no-op), which is what the host harness gets by default.
void he_sample_init(void *page);

// Record one publish attempt. Called from camera_svc_publish with the
// chunk header's own idx/count, so the record carries the frame position
// rather than a sample serial number.
void he_sample_pub(uint16_t idx, uint16_t count, uint16_t len, int err);

// wire_pump_tx found the outbound rpmsg ring full and returned with
// the message still pending (main.c). Counted here because the sampler
// is the only place that reports it -- wire_status_t is a frozen 88 B
// ABI and this bite does not touch it. A stall is normal backpressure,
// not a loss: nothing is dropped at this layer any more.
void he_sample_note_tx_stall(void);
uint32_t he_sample_tx_stalls(void);

// Host tests read the page back through this (NULL until initialized).
const he_sample_page_t *he_sample_get_page(void);

#endif // HE_SAMPLE_H
