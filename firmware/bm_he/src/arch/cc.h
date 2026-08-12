// arch/cc.h -- lwIP platform glue for bm_he (arm-none-eabi-gcc, newlib
// nano). lwIP 2.2 derives its integer types from <stdint.h> by itself;
// only diagnostics and RNG need wiring.
#ifndef LWIP_ARCH_CC_H
#define LWIP_ARCH_CC_H

#include "bm_he.h"

// Diagnostics into the debug ring the HP runner can dump.
#define LWIP_PLATFORM_DIAG(x)  do { he_dbg_printf x; } while (0)

extern void bm_set_err(uint32_t err);
#define LWIP_PLATFORM_ASSERT(x)                                   \
    do {                                                          \
        he_dbg_printf("lwip assert: %s\n", x);                    \
        bm_set_err(0xA55Eu);                                      \
        for (;;) {                                                \
        }                                                         \
    } while (0)

// nd6/ip6 use LWIP_RAND for delays/ids. Not security-relevant here;
// xorshift32 seeded from the DWT cycle counter at first call.
uint32_t bm_he_rand(void);   // he_dbg.c
#define LWIP_RAND() bm_he_rand()

#endif // LWIP_ARCH_CC_H
