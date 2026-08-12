// bench.h -- protocol brain for the "he-bench" endpoint (host-testable).
#ifndef HE_BENCH_H
#define HE_BENCH_H

#include <stdbool.h>
#include <stdint.h>

typedef bool (*bench_send_t)(void *arg, const void *data, uint32_t len);
typedef uint32_t (*bench_cycles_t)(void);
typedef void (*bench_spi_test_t)(uint32_t result[5]);

typedef struct {
    bench_send_t send;
    void *send_arg;
    bench_cycles_t cycles;
    bench_spi_test_t spi_test;   // NULL on host builds
    uint32_t tick;               // owner updates (FreeRTOS tick / test time)
    struct {
        uint32_t count, bytes, crc_errs, seq_gaps;
        uint32_t cyc_first, cyc_last, last_seq;
    } sink;
    uint32_t pump_left, pump_size, pump_seq;
} bench_t;

void bench_init(bench_t *b, bench_send_t send, void *send_arg,
                bench_cycles_t cycles, bench_spi_test_t spi_test);
void bench_on_message(bench_t *b, const uint8_t *msg, uint32_t len);
void bench_pump_step(bench_t *b);   // call from the main loop while pumping

#endif
