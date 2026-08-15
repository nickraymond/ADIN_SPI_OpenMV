// power_hal_sim.c -- simulated power backend (bench; BENCHSPEC BUILD-4).
//
// Duty model: the node is "always on" while benched; the schedule
// pretends a deployment duty cycle of SIM_ON_S on / SIM_OFF_S off,
// phase-locked to boot. Rails: 12 V nominal with a slow +/-200 mV
// triangle, 180 mA baseline -- obviously synthetic values (round
// numbers on purpose: nobody should mistake them for measurements).
//
// Kept permanently as the CI/regression backend (anti-dup §6.4): the
// hardware driver lands NEXT TO this file, never replaces it.

#include "power_hal.h"

#include "bm_os.h"

#define SIM_ON_S 3300u
#define SIM_OFF_S 300u
#define SIM_PERIOD_S (SIM_ON_S + SIM_OFF_S)

static uint32_t uptime_s(void) {
    return bm_ticks_to_ms(bm_get_tick_count()) / 1000u;
}

BmErr power_hal_init(void) {
    return BmOK;   // sim: nothing to probe (a real chip would ID here)
}

power_hal_reading_t power_hal_read(void) {
    uint32_t up = uptime_s();
    uint32_t phase = up % SIM_PERIOD_S;
    // 100 s period triangle wave, +/-200 mV around 12.000 V.
    uint32_t t = up % 100u;
    int32_t tri = (t < 50u) ? (int32_t)t : (int32_t)(100u - t);
    power_hal_reading_t r = {
        .total_on_s = up,
        .remaining_on_s = phase < SIM_ON_S ? SIM_ON_S - phase : 0u,
        .upcoming_off_s = SIM_OFF_S,
        .voltage_mv = 11800 + tri * 8,     // 11.8 .. 12.2 V
        .current_ma = 180,
    };
    return r;
}

PowerInfoReplyData power_hal_power_info_cb(void *arg) {
    (void)arg;
    power_hal_reading_t r = power_hal_read();
    PowerInfoReplyData d = {
        .total_on_s = r.total_on_s,
        .remaining_on_s = r.remaining_on_s,
        .upcoming_off_s = r.upcoming_off_s,
    };
    return d;
}
