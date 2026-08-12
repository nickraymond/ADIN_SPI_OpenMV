// bm_he.h -- S10 INTERIM 2: bm_core (bm_os/lwIP/BCMP) on the M55_HE core
// against a mock NetworkDevice whose "wire" is the HP<->HE rpmsg pipe.
//
// Address facts are inherited from the bite-1 spike (he_spike.h carries
// the sources/measurements): OpenAMP SHM 0x60000000..0x60010000 owned by
// the HP host; our image lives in SRAM9_B's upper half. This app reserves
// TWO fixed pages at the top of the region (see bm_he.ld):
//   0x600BFE00  bm status page (this file, magic 'BMHE')
//   0x600BFF00  he_spike status page (he_spike.h, magic 'HESP') -- still
//               written by the reused startup/mhu/rpmsg code, and the
//               first thing the runner checks either way.
#ifndef BM_HE_H
#define BM_HE_H

#include <stdint.h>

// ---- rpmsg endpoint (fake wire + control) ------------------------------
// Address clear of RPMSG_RESERVED_ADDRESSES (1024) and of the bite-1
// bench endpoint (8192), same allocator argument as he_spike.h.
#define WIRE_EPT_ADDR  8200u
#define WIRE_EPT_NAME  "bm-wire"

// ---- wire protocol (host <-> "bm-wire") --------------------------------
// All little-endian. 4-byte header, then payload. rpmsg payload budget is
// 496 B (RPMSG_BUF_SIZE 512 - 16 rpmsg hdr) => max frame 492 B. BCMP
// control traffic (heartbeat ~80 B, info/neighbor replies < 400 B) fits;
// oversize frames are dropped + counted (wire_tx_oversize). Chunking is
// a 2b/S12 concern, deliberately not built here.
typedef struct {
    uint8_t  cmd;
    uint8_t  port;      // BM port number 1..15 (frames), else 0
    uint16_t len;       // payload bytes after this header
} __attribute__((packed)) wire_hdr_t;

#define WCMD_FRAME_TX  0x11u  // HE->HP: frame the stack transmitted
#define WCMD_FRAME_RX  0x12u  // HP->HE: inject frame into the stack
#define WCMD_LINK      0x13u  // HP->HE: port link state; len==0, port=N,
                              //         hdr-only, up/down in cmd's pair
#define WCMD_LINK_UP   0x01u  //         ... carried in 1-byte payload
#define WCMD_QUERY     0x14u  // HP->HE: request status reply
#define WCMD_PING      0x15u  // HP->HE: send a BCMP echo request from the
                              //         stack; payload = wire_ping_t
#define WREP_STATUS    0x94u  // HE->HP: wire_status_t payload

// WCMD_PING payload: target node id + optional echo payload. echo length
// = wire_hdr_t.len - 8 (ping.c copies it into EXPECTED_PAYLOAD and
// validates the reply against it -- send >= 1 byte so the reply check is
// a real payload compare, not a trivial 0 == 0).
typedef struct {
    uint64_t target_node_id;
    uint8_t  echo[];          // wire_hdr_t.len - 8 bytes
} __attribute__((packed)) wire_ping_t;

typedef struct {
    uint64_t node_id;
    uint8_t  ip_ll[16];       // netif addr slot 0 (link-local, fe80::)
    uint8_t  ip_ucast[16];    // netif addr slot 1 (unicast from node id)
    uint32_t stack_stage;     // bm_stage_t
    uint32_t stack_err;       // first failing BmErr, 0 = none
    uint32_t tx_frames;       // mock device: frames the stack sent
    uint32_t rx_frames;       // mock device: frames injected
    uint32_t tx_oversize;     // frames dropped (> wire payload budget)
    uint32_t link_up;         // current mock link state (port 1)
    uint32_t heap_free;       // FreeRTOS xPortGetFreeHeapSize()
    uint32_t heap_min;        // watermark since boot
} __attribute__((packed)) wire_status_t;

// ---- bm status page (peekable via machine.mem32 even with rpmsg down) --
#define BM_STATUS_PAGE_ADDR 0x600BFE00u
typedef struct {
    volatile uint32_t magic;          // 'BMHE'
    volatile uint32_t stage;          // bm_stage_t
    volatile uint32_t err;            // sticky first BmErr / init failure
    volatile uint32_t tick;
    volatile uint32_t tx_frames;
    volatile uint32_t rx_frames;
    volatile uint32_t hb_count;       // WCMD_FRAME_TX frames that parsed
                                      // as BCMP heartbeat (mock counts)
    volatile uint32_t dbg_ring_addr;  // he_dbg ring: address in SRAM9_B
    volatile uint32_t dbg_ring_size;
    volatile uint32_t dbg_ring_widx;  // total bytes ever written (mod size
                                      //   = ring position)
} bm_status_page_t;
#define BM_MAGIC 0x424D4845u          // 'BMHE'

typedef enum {
    BM_STAGE_BOOT = 1,        // Reset_Handler entered
    BM_STAGE_RTOS = 2,        // scheduler running, wire task alive
    BM_STAGE_RPMSG = 3,       // rpmsg up, DRIVER_OK, endpoint announced
    BM_STAGE_L2 = 4,          // bm_l2_init ok
    BM_STAGE_IP = 5,          // bm_ip_init ok (lwIP + netif + addrs)
    BM_STAGE_BCMP = 6,        // bcmp_init ok
    BM_STAGE_RUNNING = 7,     // power on, link up, heartbeats flowing
} bm_stage_t;

// he_dbg.c -- printf into a ring the HP runner can dump.
void he_dbg_init(void);
void he_dbg_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

#endif // BM_HE_H
