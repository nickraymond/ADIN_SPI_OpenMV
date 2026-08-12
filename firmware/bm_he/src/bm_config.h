// bm_config.h -- bm_core integrator config for bm_he (template:
// vendor/bm_core-adjacent bm_config_template.h). Debug output goes to the
// he_dbg ring (dumped by the HP runner); there is no stdio on this core.
#ifndef __BM_CONFIG_H__
#define __BM_CONFIG_H__

#include "bm_he.h"

#define bm_app_name "bm_he"

#define bm_debug(format, ...) he_dbg_printf(format, ##__VA_ARGS__)

// dfu_core's reboot-info struct: survives soft resets (bm_he.ld + the
// Reset_Handler's bss-only zeroing).
#define bm_noinit_ram_attribute section(".noinit")

#endif
