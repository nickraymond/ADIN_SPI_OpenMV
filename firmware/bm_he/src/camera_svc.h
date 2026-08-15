// camera_svc.h -- the "camera/control" bm_service (S17 BUILD-4).
//
// Control plane for the camera node: trigger a single capture, start/
// stop the chunked stream, query status. Rides bm_service (req/rep on
// pub/sub, ff03::1 -- crosses the pass-through node, REV-6); the data
// plane is CAMERA_STREAM_TOPIC (bm_he.h).
//
// WIRE CONTRACT: requests and replies are packed little-endian structs,
// NOT CBOR. Deliberate (recorded for the PR review): the shipped
// cbor_service_helper is config-map-only, tinycbor encode/parse for a
// bench service costs flash the 93.1%-full HE image doesn't have
// (REV-25), and both ends of this service are ours. The camera_req_t /
// camera_rep_t layouts below are REPLICATED in the bm_sbc fork app
// (apps/bench_apps) with sizeof static_asserts -- change them in
// lockstep or not at all.
//
// Chunk header (inside each CAMERA_STREAM_TOPIC payload, 10 B, packed
// LE, adapted from S6's BMV6 -- firmware/adin_drv/s6_video.py):
//   frame_seq u32 | chunk_idx u16 | chunk_count u16 | payload_len u16
// followed by payload_len JPEG bytes. Total <= CAMERA_MAX_PAYLOAD.
// The header is built by the HP bridge (chunk source) and parsed by the
// Telemetry app (reassembler); it is documented here because this is
// the service's public data-plane format.
#ifndef CAMERA_SVC_H
#define CAMERA_SVC_H

#include <stdbool.h>
#include <stdint.h>

#include "bm_he.h"
#include "util.h"   // BmErr

#define CAMERA_SERVICE "camera/control"

#define CAMERA_REQ_MAGIC 0x314D4143u   // 'CAM1' little-endian

#define CAMERA_CMD_CAPTURE 1u   // single frame
#define CAMERA_CMD_STREAM  2u   // continuous stream
#define CAMERA_CMD_STATUS  3u   // counters only, no side effect
#define CAMERA_CMD_STOP    4u   // stop a running stream

typedef struct {
    uint32_t magic;       // CAMERA_REQ_MAGIC guards against stray traffic
    uint8_t  cmd;         // CAMERA_CMD_*
    uint8_t  quality;     // JPEG q, 0 = bridge default (50, D20)
    uint16_t fps_x10;     // stream pacing x10, 0 = bridge default
    uint32_t rate_bps;    // payload-rate cap, 0 = fps-paced only
    uint16_t secs;        // stream duration, 0 = bridge default
    uint16_t payload_max; // chunk ceiling, 0 = CAMERA_MAX_PAYLOAD
} __attribute__((packed)) camera_req_t;    // 16 B

typedef struct {
    uint32_t magic;       // CAMERA_REQ_MAGIC (echoed)
    uint8_t  ok;          // 1 = command accepted / status valid
    uint8_t  mode_active; // CAMERA_MODE_* currently commanded
    uint16_t rsvd;        // 0
    uint32_t cmds;        // accepted commands since boot
    uint32_t pub_ok;      // stream chunks published (bm_pub BmOK)
    uint32_t pub_errs;    // failed/oversize publishes
    uint32_t pub_bytes;   // payload bytes published
} __attribute__((packed)) camera_rep_t;    // 24 B

// Register the service. Call after bm_service_init()/bm_pubsub_init().
BmErr camera_svc_init(void);

// Wire-task side: fetch-and-clear the pending capture command staged by
// the service handler (single-slot mailbox, last-wins by design).
bool camera_svc_take_pending(wire_capture_t *out);

// WCMD_PUB path: validate + publish one chunk payload on
// CAMERA_STREAM_TOPIC, counting into the service's ledger.
void camera_svc_publish(const uint8_t *payload, uint16_t len);

// ---- pure core (host-tested; no bm_service/OS dependency) --------------
// Parses req_data, updates the mailbox/ledger, fills the reply. Returns
// true if a reply should be sent (i.e. request was well-formed enough
// to answer). Exposed for the host harness; production entry is the
// bm_service callback inside camera_svc.c.
bool camera_svc_handle(const uint8_t *req_data, uint32_t req_len,
                       camera_rep_t *rep);

#endif // CAMERA_SVC_H
