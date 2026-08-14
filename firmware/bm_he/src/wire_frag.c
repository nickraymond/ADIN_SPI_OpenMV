// wire_frag.c -- see wire_frag.h.

#include <string.h>

#include "wire_frag.h"

// ---- TX -----------------------------------------------------------------

void wire_frag_start(wire_frag_iter_t *it, uint8_t first_cmd, uint8_t port,
                     const uint8_t *frame, uint16_t len) {
    it->frame = frame;
    it->len = len;
    it->off = 0;
    it->port = port;
    it->first_cmd = first_cmd;
}

uint16_t wire_frag_next(wire_frag_iter_t *it, uint8_t *msg,
                        uint16_t max_payload) {
    if (!it->frame || it->off >= it->len || max_payload == 0) {
        return 0;
    }
    uint16_t remaining = (uint16_t)(it->len - it->off);
    uint16_t chunk = remaining < max_payload ? remaining : max_payload;

    wire_hdr_t *hdr = (wire_hdr_t *)msg;
    if (it->off == 0) {
        // First message announces the TOTAL frame length; when the frame
        // fits one message this is byte-identical to the unfragmented
        // S10 wire (hdr.len == payload bytes == total).
        *hdr = (wire_hdr_t){.cmd = it->first_cmd,
                            .port = it->port,
                            .len = it->len};
    } else {
        *hdr = (wire_hdr_t){.cmd = WCMD_FRAG,
                            .port = it->port,
                            .len = chunk};
    }
    memcpy(msg + sizeof(*hdr), it->frame + it->off, chunk);
    it->off = (uint16_t)(it->off + chunk);
    return (uint16_t)(sizeof(*hdr) + chunk);
}

// ---- RX -----------------------------------------------------------------

uint16_t wire_reasm_first(wire_reasm_t *r, uint8_t port, uint16_t total,
                          const uint8_t *data, uint16_t n) {
    if (r->total != 0) {
        // A new frame started while one was open: drop the old assembly,
        // count it, resync on this frame (in-order pipe -- the previous
        // frame's tail is not coming).
        r->errors++;
        r->total = 0;
        r->filled = 0;
    }
    if (total == 0 || total > sizeof(r->buf) || n > total) {
        r->errors++;
        return 0;
    }
    memcpy(r->buf, data, n);
    if (n == total) {
        return total;   // whole frame in one message (r stays idle)
    }
    r->total = total;
    r->filled = n;
    r->port = port;
    return 0;
}

uint16_t wire_reasm_frag(wire_reasm_t *r, const uint8_t *data, uint16_t n) {
    if (r->total == 0) {
        r->errors++;    // continuation with no frame open
        return 0;
    }
    if (n == 0 || (uint16_t)(r->filled + n) > r->total) {
        r->errors++;    // overrun past the announced total
        r->total = 0;
        r->filled = 0;
        return 0;
    }
    memcpy(r->buf + r->filled, data, n);
    r->filled = (uint16_t)(r->filled + n);
    if (r->filled == r->total) {
        uint16_t done = r->total;
        r->total = 0;
        r->filled = 0;
        return done;
    }
    return 0;
}
