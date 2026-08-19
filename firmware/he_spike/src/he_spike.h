// he_spike.h -- shared constants for the S10 bite-1 HE spike.
// Every hardware address below carries its source; do not edit without
// re-verifying (project rule: never invent hardware facts).
#ifndef HE_SPIKE_H
#define HE_SPIKE_H

#include <stdint.h>

// ---- OpenAMP shared memory (owned by the HP host) ---------------------
// Base + size: _openamp_shm_region_start/_end = 0x60000000/0x60010000 in
// BOTH flashed images' link maps (build/OPENMV_AE3/M55_{HP,HE}/*.map, D24
// build); region = SRAM9_A (boards/OPENMV_AE3/board_config.h:103).
// Layout inside: micropython extmod/modopenamp.c + ports/alif/mpmetalport.h
//   rsc table   @ +0        (METAL_RSC_SIZE = 1024)
//   vring0 (host->remote) @ +0x0400   (VRING_RX_ADDR = METAL_SHM_ADDR)
//   vring1 (remote->host) @ +0x1400   (VRING_TX_ADDR = +0x1000)
//   buffer pool @ +0x2400 .. 0x10000  (VRING_BUFF_ADDR)
#define SHM_BASE        0x60000000u
#define SHM_SIZE        0x00020000u  // S23: 128K (32x1544 pool)
#define RSC_ADDR        (SHM_BASE)
#define METAL_RSC_SIZE  1024u
// Ring direction, MEASURED live 2026-08-12 (ring dump while the host was
// initialized): rsc vring0 (da 0x60001400) is the ring the host pre-fills
// with 64 EMPTY buffers = the host's RX = the REMOTE'S TX ring; rsc
// vring1 (da 0x60000400) is where the host queues its own sends (avail
// flags = NO_INTERRUPT) = the REMOTE'S RX ring. Matches open-amp's host
// role mapping (rvq = vrings_info[0]); modopenamp.c's "VRING0 host to
// remote" comment refers to notify IDs, not data direction.
// Descriptor .addr fields are OFFSETS into the buffer region relative to
// SHM_BASE + METAL_RSC_SIZE (measured: first pool buffer = 0x2000 ->
// 0x60002400), not absolute addresses.
// S23 bite 2 (2026-08-18): buffers resized 512 -> 1544 so one 1400 B
// camera chunk (or one full 1514 B L2 frame + 4 B wire header) rides ONE
// rpmsg message -- the measured tax was ~0.57 ms PER MESSAGE, not per
// byte. Lockstep with the HP firmware patches (extmod.mk
// RPMSG_BUFFER_SIZE=1544, modopenamp.c VRING_NUM_BUFFS=32,
// board_config.h OMV_OPENAMP_SIZE=128K). Pool arithmetic at 128 KB SHM:
// rsc 1 K + rings 2x4 K + 2x32x1544 = 98,816 B of the 120,832 B pool.
// (The first cut kept SHM at 64 K with 16 slots; 16 measurably starved
// HD's 55-chunk bursts -- 3.10 -> 2.50 fps -- so depth came back.)
// VRING_NUM is a VALIDATION BOUND here (rsc rejects >1024); the real
// count arrives in the host's rsc table. A stale-ELF/new-host mismatch
// is safe by construction: rr_send checks the descriptor's own capacity
// per message (dbg_reason 3) and RX reads actual lengths -- degraded,
// never corrupt.
#define VRING_NUM       32u          // VRING_NUM_BUFFS, modopenamp.c
#define VRING_ALIGN     32u          // VRING_ALIGNMENT, modopenamp.c
#define RPMSG_BUF_SIZE  1544u        // lockstep: RPMSG_BUFFER_SIZE (0004)

// ---- our app / bench ---------------------------------------------------
#define BENCH_EPT_ADDR  8192u        // above RPMSG_RESERVED_ADDRESSES=1024
                                     // and clear of both sides' dynamic
                                     // allocators (openamp/rpmsg.h)
#define BENCH_EPT_NAME  "he-bench"

// Status page: fixed window the HP runner peeks with machine.mem32 --
// works even when rpmsg is down. Address matches he_spike.ld.
#define STATUS_PAGE_ADDR 0x600BFF00u
typedef struct {
    volatile uint32_t magic;        // 'HESP' = 0x48455350
    volatile uint32_t stage;        // he_stage_t
    volatile uint32_t tick;         // FreeRTOS tick, proves scheduler alive
    volatile uint32_t err;          // he_err_t, sticky first error
    volatile uint32_t rsc_status;   // last vdev status byte seen
    volatile uint32_t rx_count;     // vring0 messages consumed
    volatile uint32_t tx_count;     // vring1 messages produced
    volatile uint32_t irq_count;    // MHU RX doorbells
    volatile uint32_t dbg_reason;   // last rr_send failure reason
    volatile uint32_t dbg_a;        // reason-specific (see rpmsg_remote.c)
    volatile uint32_t dbg_b;
} he_status_page_t;
#define HE_MAGIC 0x48455350u

typedef enum {
    HE_STAGE_BOOT = 1,       // Reset_Handler entered
    HE_STAGE_RTOS = 2,       // FreeRTOS scheduler running
    HE_STAGE_DRIVER_OK = 3,  // host's vdev DRIVER_OK seen
    HE_STAGE_NS_SENT = 4,    // name-service announce sent
    HE_STAGE_RUNNING = 5,    // bench endpoint serving
} he_stage_t;

typedef enum {
    HE_ERR_NONE = 0,
    HE_ERR_HARDFAULT = 1,
    HE_ERR_RSC_BAD = 2,      // rsc table failed sanity check
    HE_ERR_ASSERT = 3,       // configASSERT fired
    HE_ERR_TX_STALL = 4,     // no free tx buffer within timeout
} he_err_t;

// ---- bench protocol (host <-> "he-bench" endpoint) ---------------------
// All little-endian. First byte = command / reply code (reply = cmd|0x80).
#define BCMD_PING       0x01u   // -> BREP_PING {u32 core_hz, u32 tick, u32 cyccnt}
#define BCMD_SINK_RESET 0x02u   // -> BREP ack
#define BCMD_SINK_DATA  0x03u   // [1B cmd][3B pad][u32 seq][u32 crc][payload] no reply
#define BCMD_SINK_QUERY 0x04u   // -> BREP {u32 count,bytes,crc_errs,seq_gaps,cyc_first,cyc_last}
#define BCMD_PUMP       0x05u   // [1B cmd][3B pad][u32 count][u32 size] -> data msgs + BREP done
#define BCMD_ECHO       0x06u   // -> BREP + payload verbatim
#define BCMD_SPI_TEST   0x07u   // -> BREP {u32 flags,u32 tx_crc,u32 rx_crc,u32 irqs,u32 ctrlr0}
#define BREP(c)         ((c) | 0x80u)
#define BPUMP_DATA      0x45u   // remote->host pump frame: [1B][3B pad][u32 seq][u32 crc][fill]

// BCMD_SPI_TEST result flags
#define SPI_T_PINMUX_OK   (1u << 0)
#define SPI_T_INIT_OK     (1u << 1)
#define SPI_T_LOOP_MATCH  (1u << 2)  // rx == tx pattern through internal loopback
#define SPI_T_IRQ_SEEN    (1u << 3)  // SPI0 IRQ fired on the HE NVIC

uint32_t he_crc32(const uint8_t *p, uint32_t n);   // bench + tests share it
void he_set_err(uint32_t err);                     // startup.c, sticky

#endif // HE_SPIKE_H
