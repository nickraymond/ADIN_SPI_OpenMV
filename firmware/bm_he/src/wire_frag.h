// wire_frag.h -- L2-frame fragmentation over the rpmsg wire (S16 BUILD-2a).
//
// The rpmsg pipe carries at most 496 B per message (RPMSG_BUF_SIZE 512 -
// 16 B rpmsg header; DESIGN §S14), so a full 1514 B L2 frame spans 4
// messages. Wire rules (both directions, HP bridge mirrors this in
// python):
//
//  - A frame that fits one message rides exactly as the S10 protocol:
//    one msg, first_cmd (WCMD_FRAME_TX / WCMD_FRAME_RX), hdr.len ==
//    payload bytes in the msg == total frame length. Byte-identical to
//    the pre-frag wire for every frame <= the message budget -- the
//    2a/2b bench traffic is unchanged.
//  - A larger frame: first msg carries first_cmd with hdr.len == TOTAL
//    frame length and a full message worth of payload; each following
//    msg carries WCMD_FRAG with hdr.len == that msg's payload bytes.
//    The vring is in-order FIFO, so no sequence field is needed; the
//    single-link pipe never interleaves frames.
//  - Any inconsistency (FRAG with no frame open, overrun past the
//    announced total, FRAME while a frame is open) drops the frame under
//    assembly, counts an error, and resyncs on the next first_cmd.
//
// Pure functions over caller-owned state: no OS, no rpmsg -- compiled
// unchanged into the host tests.
#ifndef WIRE_FRAG_H
#define WIRE_FRAG_H

#include <stdint.h>

#include "bm_he.h"   // wire_hdr_t, WCMD_FRAG

// ---- TX side: split one L2 frame into wire messages ---------------------

typedef struct {
    const uint8_t *frame;
    uint16_t len;
    uint16_t off;       // bytes emitted so far
    uint8_t port;
    uint8_t first_cmd;  // WCMD_FRAME_TX (HE->HP) or WCMD_FRAME_RX (HP->HE)
} wire_frag_iter_t;

void wire_frag_start(wire_frag_iter_t *it, uint8_t first_cmd, uint8_t port,
                     const uint8_t *frame, uint16_t len);

// Write the next wire message (wire_hdr_t + payload chunk) into msg.
// max_payload is the per-message payload budget AFTER the header (the
// rpmsg budget minus sizeof(wire_hdr_t)). Returns the total message
// length in bytes, or 0 when the frame is fully emitted.
uint16_t wire_frag_next(wire_frag_iter_t *it, uint8_t *msg,
                        uint16_t max_payload);

// ---- RX side: reassemble wire messages into one L2 frame ----------------

typedef struct {
    uint8_t buf[1514];  // NETWIRE_MAX_FRAME; REV-14 is enforced upstream
    uint16_t total;     // announced frame length; 0 = idle
    uint16_t filled;
    uint8_t port;
    uint32_t errors;    // dropped assemblies (see rules above)
} wire_reasm_t;

// Feed the first message of a frame (cmd == first_cmd). Returns the
// complete frame length if the frame fit one message (data is in
// r->buf), else 0 (assembly opened, or error counted).
uint16_t wire_reasm_first(wire_reasm_t *r, uint8_t port, uint16_t total,
                          const uint8_t *data, uint16_t n);

// Feed a WCMD_FRAG continuation. Returns the complete frame length when
// the announced total is reached (data is in r->buf), else 0.
uint16_t wire_reasm_frag(wire_reasm_t *r, const uint8_t *data, uint16_t n);

#endif // WIRE_FRAG_H
