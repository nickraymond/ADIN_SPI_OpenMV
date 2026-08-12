// mhu.h -- minimal HP<->HE doorbell over the RTSS-secure MHU pair.
// See mhu.c header for the register-layout provenance.
#ifndef HE_MHU_H
#define HE_MHU_H

#include <stdbool.h>
#include <stdint.h>

void he_mhu_init(void (*rx_doorbell)(void));
bool he_mhu_kick(void);        // notify the HP host (poll-complete, ~us)
void he_mhu_rx_irq(void);      // vector table entry (IRQ 41)

#endif
