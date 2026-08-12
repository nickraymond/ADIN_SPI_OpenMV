// bench.c -- the "he-bench" endpoint's protocol brain. Pure logic: no
// FreeRTOS, no MMIO, host-testable. Wire formats in he_spike.h.

#include <string.h>
#include "he_spike.h"
#include "bench.h"

// CRC-32 (IEEE, reflected), 256-entry table built on first use. Table
// lookup keeps validation ~5 cycles/byte -- far above the 5 Mbps gate,
// so integrity checking never masquerades as a pipe limit.
static uint32_t crc_tab[256];
static int crc_ready;

uint32_t he_crc32(const uint8_t *p, uint32_t n) {
    if (!crc_ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) {
                c = (c & 1u) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            }
            crc_tab[i] = c;
        }
        crc_ready = 1;
    }
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c = crc_tab[(c ^ *p++) & 0xFFu] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFu;
}

static inline uint32_t rd32(const uint8_t *p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

static inline void wr32(uint8_t *p, uint32_t v) {
    memcpy(p, &v, 4);
}

void bench_init(bench_t *b, bench_send_t send, void *send_arg,
                bench_cycles_t cycles, bench_spi_test_t spi_test) {
    memset(b, 0, sizeof(*b));
    b->send = send;
    b->send_arg = send_arg;
    b->cycles = cycles;
    b->spi_test = spi_test;
}

// One pump frame: [1B BPUMP_DATA][3B pad][u32 seq][u32 crc][fill];
// crc covers the fill only. Returns frame length.
static uint32_t make_pump_frame(uint8_t *out, uint32_t seq, uint32_t size) {
    if (size < 12u) {
        size = 12u;
    }
    out[0] = BPUMP_DATA;
    out[1] = out[2] = out[3] = 0;
    wr32(out + 4, seq);
    for (uint32_t i = 12; i < size; i++) {
        out[i] = (uint8_t)(seq + i);
    }
    wr32(out + 8, he_crc32(out + 12, size - 12));
    return size;
}

// Drive the pump: called from the main loop while pump_left > 0 so rx
// processing stays live between bursts. Sends until the tx ring back-
// pressures, then yields.
void bench_pump_step(bench_t *b) {
    uint8_t frame[512 - 16];
    if (!b->pump_size) {
        return;                          // idle
    }
    while (b->pump_left) {
        uint32_t len = make_pump_frame(frame, b->pump_seq, b->pump_size);
        if (!b->send(b->send_arg, frame, len)) {
            return;                      // ring full; try again next loop
        }
        b->pump_seq++;
        b->pump_left--;
    }
    // Done: one summary reply.
    uint8_t rep[8];
    rep[0] = BREP(BCMD_PUMP);
    rep[1] = rep[2] = rep[3] = 0;
    wr32(rep + 4, b->pump_seq);
    b->send(b->send_arg, rep, sizeof(rep));
    b->pump_size = 0;
}

void bench_on_message(bench_t *b, const uint8_t *msg, uint32_t len) {
    if (len < 1u) {
        return;
    }
    uint8_t rep[64];
    switch (msg[0]) {
        case BCMD_PING: {
            rep[0] = BREP(BCMD_PING);
            rep[1] = rep[2] = rep[3] = 0;
            wr32(rep + 4, 160000000u);   // nominal M55_HE_CPU_FREQ_HZ
            wr32(rep + 8, b->tick);
            wr32(rep + 12, b->cycles ? b->cycles() : 0u);
            b->send(b->send_arg, rep, 16);
            break;
        }
        case BCMD_SINK_RESET: {
            memset(&b->sink, 0, sizeof(b->sink));
            rep[0] = BREP(BCMD_SINK_RESET);
            b->send(b->send_arg, rep, 1);
            break;
        }
        case BCMD_SINK_DATA: {
            if (len < 12u) {
                b->sink.crc_errs++;
                break;
            }
            uint32_t seq = rd32(msg + 4);
            uint32_t crc = rd32(msg + 8);
            uint32_t now = b->cycles ? b->cycles() : 0u;
            if (b->sink.count == 0u) {
                b->sink.cyc_first = now;
            } else if (seq != b->sink.last_seq + 1u) {
                b->sink.seq_gaps++;
            }
            b->sink.cyc_last = now;
            b->sink.last_seq = seq;
            if (he_crc32(msg + 12, len - 12) != crc) {
                b->sink.crc_errs++;
            }
            b->sink.count++;
            b->sink.bytes += len;
            break;                       // no reply -- throughput path
        }
        case BCMD_SINK_QUERY: {
            rep[0] = BREP(BCMD_SINK_QUERY);
            rep[1] = rep[2] = rep[3] = 0;
            wr32(rep + 4, b->sink.count);
            wr32(rep + 8, b->sink.bytes);
            wr32(rep + 12, b->sink.crc_errs);
            wr32(rep + 16, b->sink.seq_gaps);
            wr32(rep + 20, b->sink.cyc_first);
            wr32(rep + 24, b->sink.cyc_last);
            b->send(b->send_arg, rep, 28);
            break;
        }
        case BCMD_PUMP: {
            if (len < 12u) {
                break;
            }
            b->pump_left = rd32(msg + 4);
            b->pump_size = rd32(msg + 8);
            b->pump_seq = 0;
            if (b->pump_size < 12u || b->pump_size > 496u) {
                b->pump_size = 480u;
            }
            // frames flow from bench_pump_step() in the main loop
            break;
        }
        case BCMD_ECHO: {
            if (len > sizeof(rep) - 1u) {
                len = sizeof(rep) - 1u;
            }
            rep[0] = BREP(BCMD_ECHO);
            memcpy(rep + 1, msg + 1, len - 1);
            b->send(b->send_arg, rep, len);
            break;
        }
        case BCMD_SPI_TEST: {
            uint32_t r[5] = {0, 0, 0, 0, 0};
            if (b->spi_test) {
                b->spi_test(r);
            }
            rep[0] = BREP(BCMD_SPI_TEST);
            rep[1] = rep[2] = rep[3] = 0;
            for (int i = 0; i < 5; i++) {
                wr32(rep + 4 + 4 * i, r[i]);
            }
            b->send(b->send_arg, rep, 24);
            break;
        }
        default:
            break;                       // unknown: ignore, stay alive
    }
}
