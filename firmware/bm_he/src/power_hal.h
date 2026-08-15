// power_hal.h -- board power abstraction (S17 BUILD-4, anti-dup §6.3).
//
// One header, swappable backends. The bench links power_hal_sim.c (a
// simulated duty schedule + synthetic rails); hardware day replaces it
// with a driver for the real regulator / power-monitor chip behind the
// SAME interface -- nothing above this header changes.
//
// Two consumers, matching how power data moves in the BM ecosystem:
//  - duty-cycle timing feeds the shipped power_info service
//    ("bus_power_controller/timing", power_info_service_init) -- the
//    power_hal_power_info_cb adapter below plugs in directly;
//  - instantaneous rail readings (voltage/current) are the shape that
//    rides SENSOR pub/sub topics in Sofar's ecosystem (per-node power in
//    Spotter logs). Carried in the HAL now so the hardware-day chip swap
//    is backend-only; publishing them is future scope, not S17.
#ifndef POWER_HAL_H
#define POWER_HAL_H

#include <stdint.h>

#include "power_info_reply_msg.h"   // PowerInfoReplyData
#include "util.h"                   // BmErr

typedef struct {
    uint32_t total_on_s;      // cumulative powered time
    uint32_t remaining_on_s;  // until the next scheduled power-off
    uint32_t upcoming_off_s;  // length of the next off period
    int32_t  voltage_mv;      // main rail; sim: synthetic
    int32_t  current_ma;      // main rail draw; sim: synthetic
} power_hal_reading_t;

BmErr power_hal_init(void);
power_hal_reading_t power_hal_read(void);

// Adapter with the exact BmPowerInfoStatsCb signature:
//   power_info_service_init(power_hal_power_info_cb, NULL);
PowerInfoReplyData power_hal_power_info_cb(void *arg);

#endif // POWER_HAL_H
