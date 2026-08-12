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
#define SHM_SIZE        0x00010000u
#define RSC_ADDR        (SHM_BASE)
#define VRING0_ADDR     (SHM_BASE + 0x0400u + 0x1000u)  /* see note below */
#define VRING1_ADDR     (SHM_BASE + 0x0400u + 0x0000u)  /* see note below */
// NOTE on the two lines above: modopenamp.c names its addresses from the
// HOST's perspective: VRING_RX_ADDR (+0x000 after rsc) is where the host
// RECEIVES, i.e. the remote->host ring (vring1 in rsc-table numbering);
// VRING_TX_ADDR (+0x1000) is host->remote (vring0). We keep rsc-table
// numbering here (vring0 = host->remote) -- rpmsg_remote.c reads the
// authoritative addresses from the rsc table at init anyway and only
// falls back to these on a malformed table.
#define VRING_NUM       64u          // VRING_NUM_BUFFS, modopenamp.c:73
#define VRING_ALIGN     32u          // VRING_ALIGNMENT, modopenamp.c:71
#define RPMSG_BUF_SIZE  512u         // open-amp RPMSG_BUFFER_SIZE default

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
