// bm_he.h -- bm_core (bm_os/lwIP/BCMP + middleware) on the M55_HE core.
// S10 INTERIM 2 brought the stack up against a mock device; S16 BUILD-2a
// promoted it: the NetworkDevice (bm_net_wire) is real, the rpmsg pipe is
// THE wire, and the HP bridge relays frames to/from the USB VCP in
// bm_sbc's uart_l2 framing.
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
// 496 B (RPMSG_BUF_SIZE 512 - 16 rpmsg hdr) => 492 B of frame bytes per
// message. Frames up to 492 B ride in one WCMD_FRAME_* message exactly as
// the S10 protocol; larger frames (max 1514, REV-14) are fragmented per
// wire_frag.h: first message carries hdr.len == TOTAL frame length,
// continuations carry WCMD_FRAG (in-order vring, no seq needed).
typedef struct {
    uint8_t  cmd;
    uint8_t  port;      // BM port number 1..15 (frames), else 0
    uint16_t len;       // WCMD_FRAME_*: TOTAL frame bytes (may exceed this
                        //   message); WCMD_FRAG: payload bytes in this
                        //   message; others: payload bytes after header
} __attribute__((packed)) wire_hdr_t;

#define WCMD_FRAME_TX  0x11u  // HE->HP: frame the stack transmitted
#define WCMD_FRAME_RX  0x12u  // HP->HE: inject frame into the stack
#define WCMD_LINK      0x13u  // HP->HE: port link state; port=N,
                              //         up/down in 1-byte payload
#define WCMD_LINK_UP   0x01u
#define WCMD_QUERY     0x14u  // HP->HE: request status reply
#define WCMD_PING      0x15u  // HP->HE: send a BCMP echo request from the
                              //         stack; payload = wire_ping_t
#define WCMD_FRAG      0x16u  // either direction: continuation of the
                              //         preceding WCMD_FRAME_* message
#define WCMD_STREAM    0x17u  // HP->HE: start/stop the pub/sub stream
                              //         publisher; payload = wire_stream_t
#define WREP_STATUS    0x94u  // HE->HP: wire_status_t payload

// WCMD_STREAM payload. rate_bps == 0 stops a running stream. Payloads are
// seq-stamped (u32 LE at offset 0) and published on STREAM_TOPIC, which
// bm_sbc's stream_bench app (S15) subscribes to at the far nodes.
#define STREAM_TOPIC        "s15/stream"
#define STREAM_MAX_PAYLOAD  1400u   // REV-28 chunk ceiling
typedef struct {
    uint32_t rate_bps;    // offered payload rate, bits/second (0 = stop)
    uint16_t payload_len; // bytes per publish, <= STREAM_MAX_PAYLOAD
    uint16_t secs;        // publish duration
} __attribute__((packed)) wire_stream_t;

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
    uint32_t tx_frames;       // netwire: frames the stack sent
    uint32_t rx_frames;       // netwire: frames injected
    uint32_t tx_oversize;     // netwire: send() rejects > 1514 (REV-14)
    uint32_t link_up;         // as reported to l2 (port 1)
    uint32_t heap_free;       // FreeRTOS xPortGetFreeHeapSize()
    uint32_t heap_min;        // watermark since boot
    uint32_t tx_dropped;      // netwire: TX queue-full drops (drop ledger)
    uint32_t frag_errors;     // RX reassembly drops (wire_frag.h rules)
    uint32_t stream_sent;     // stream publisher: successful bm_pub calls
    uint32_t stream_errs;     // stream publisher: failed bm_pub calls
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
