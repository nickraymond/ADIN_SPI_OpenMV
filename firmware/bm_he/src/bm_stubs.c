// bm_stubs.c -- the integrator-supplied backends bm_core expects
// (bm_configs_generic.h, bm_dfu_generic.h, bm_rtc.h) plus device identity.
// Interim scope (S10 INTERIM 2): everything RAM-/tick-backed -- there is
// no flash, no RTC and no fleet on this core yet. Semantics mirror
// bm_sbc's platform_linux.cpp so the swap to real backends is shaped.
//
// Persistence honesty: a HE reload wipes ALL RAM including these stores,
// so RAM backing loses nothing a real reboot wouldn't -- except dfu_core's
// reboot-info struct, which bm_core itself keeps in .noinit (preserved,
// see bm_he.ld).

#include <string.h>

#include "bm_config.h"
#include "bm_configs_generic.h"
#include "bm_dfu_generic.h"
#include "bm_os.h"
#include "bm_rtc.h"
#include "configuration.h"
#include "device.h"

// ---- device identity ----------------------------------------------------
// Camera node id per BENCHSPEC/pi/bm_bench: be9c…01 Telemetry / …02 Light /
// …03 Camera. Fixed by Nick 2026-08-14, never reused. (The S10 interim id
// 0x424D4845AE30BEEF retired with the mock; real UID-derived ids are a
// hardware-day question, SPEC §Open questions.)
#define BM_HE_NODE_ID 0xBE9C000000000003ull

BmErr bm_stubs_device_init(void) {
  DeviceCfg cfg = {
      .node_id = BM_HE_NODE_ID,
      .git_sha = 0,               // stamped by build? interim: 0
      .device_name = "bm_camera",
      .version_string = "bm_he S16 BUILD-2",
      .vendor_id = 0,
      .product_id = 0,
      .hw_ver = 0,
      .ver_major = 0,
      .ver_minor = 1,
      .ver_patch = 0,
      .sn = {0},
  };
  return device_init(cfg);
}

// ---- config partitions: RAM-backed --------------------------------------
// configuration.c loads each partition once at config_init (a zeroed store
// fails its CRC check and degrades to a fresh empty partition -- verified
// in source, configuration.c:139-151) and writes it back on save_config.

static uint8_t s_config_store[BM_CFG_PARTITION_COUNT][sizeof(ConfigPartition)];

bool bm_config_read(BmConfigPartition partition, uint32_t offset,
                    uint8_t *buffer, size_t length, uint32_t timeout_ms) {
  (void)timeout_ms;
  if (partition >= BM_CFG_PARTITION_COUNT || !buffer ||
      offset + length > sizeof(ConfigPartition)) {
    return false;
  }
  memcpy(buffer, &s_config_store[partition][offset], length);
  return true;
}

bool bm_config_write(BmConfigPartition partition, uint32_t offset,
                     uint8_t *buffer, size_t length, uint32_t timeout_ms) {
  (void)timeout_ms;
  if (partition >= BM_CFG_PARTITION_COUNT || !buffer ||
      offset + length > sizeof(ConfigPartition)) {
    return false;
  }
  memcpy(&s_config_store[partition][offset], buffer, length);
  return true;
}

void bm_config_reset(void) {
  // A real node reboots here (called after config save w/ restart). The
  // interim node has nothing to reboot into -- log it, keep running.
  bm_debug("bm_config_reset: ignored (interim RAM-backed config)\n");
}

// ---- DFU flash hooks: RAM scratch ---------------------------------------
// No firmware update happens in the interim; dfu_client still wants a
// flash area to open/erase/write during its (never-exercised) update path.
// A small scratch keeps the API honest without pretending to be MRAM.

#define DFU_SCRATCH_SIZE 4096u
static uint8_t s_dfu_scratch[DFU_SCRATCH_SIZE];

BmErr bm_dfu_client_set_confirmed(void) { return BmOK; }

BmErr bm_dfu_client_set_pending_and_reset(void) {
  bm_debug("dfu: set_pending_and_reset ignored (interim, no flash)\n");
  return BmOK;
}

BmErr bm_dfu_client_fail_update_and_reset(void) {
  bm_debug("dfu: fail_update_and_reset ignored (interim, no flash)\n");
  return BmOK;
}

BmErr bm_dfu_client_flash_area_open(const void **flash_area) {
  if (!flash_area) {
    return BmEINVAL;
  }
  *flash_area = s_dfu_scratch;
  return BmOK;
}

BmErr bm_dfu_client_flash_area_close(const void *flash_area) {
  return flash_area == s_dfu_scratch ? BmOK : BmEINVAL;
}

BmErr bm_dfu_client_flash_area_write(const void *flash_area, uint32_t off,
                                     const void *src, uint32_t len) {
  if (flash_area != s_dfu_scratch || !src ||
      off + len > DFU_SCRATCH_SIZE) {
    return BmEINVAL;
  }
  memcpy(&s_dfu_scratch[off], src, len);
  return BmOK;
}

BmErr bm_dfu_client_flash_area_erase(const void *flash_area, uint32_t off,
                                     uint32_t len) {
  if (flash_area != s_dfu_scratch || off + len > DFU_SCRATCH_SIZE) {
    return BmEINVAL;
  }
  memset(&s_dfu_scratch[off], 0xFF, len);
  return BmOK;
}

uint32_t bm_dfu_client_flash_area_get_size(const void *flash_area) {
  return flash_area == s_dfu_scratch ? DFU_SCRATCH_SIZE : 0;
}

BmErr bm_dfu_host_get_chunk(uint32_t offset, uint8_t *buffer, size_t len,
                            uint32_t timeouts) {
  (void)offset;
  (void)buffer;
  (void)len;
  (void)timeouts;
  return BmEINVAL;   // this node never hosts an update
}

void bm_dfu_core_lpm_peripheral_active(void) {}
void bm_dfu_core_lpm_peripheral_inactive(void) {}

// ---- RTC: tick-derived fake epoch ----------------------------------------
// bm_rtc_set stores a base; gets return base + uptime. 1 ms resolution
// (FreeRTOS tick). Good enough for BCMP time replies on a mock wire;
// INTERIM 3's golden-capture cross-check is the real referee.

static uint64_t s_rtc_base_us;   // epoch µs at s_rtc_base_tick, 0 = unset
static uint64_t s_rtc_base_tick;

static const uint16_t DAYS_BEFORE_MONTH[12] = {0,   31,  59,  90,  120, 151,
                                               181, 212, 243, 273, 304, 334};

static uint64_t days_from_civil(uint16_t y, uint8_t m, uint8_t d) {
  // Days since 1970-01-01, valid 1970..2105; leap rule: /4 !/100 or /400.
  uint64_t days = 0;
  for (uint16_t yy = 1970; yy < y; yy++) {
    days += ((yy % 4 == 0 && yy % 100 != 0) || yy % 400 == 0) ? 366 : 365;
  }
  days += DAYS_BEFORE_MONTH[m - 1];
  if (m > 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)) {
    days += 1;
  }
  return days + d - 1;
}

static uint64_t uptime_us(void) {
  return (uint64_t)bm_ticks_to_ms(bm_get_tick_count()) * 1000ull;
}

BmErr bm_rtc_set(const RtcTimeAndDate *t) {
  if (!t || t->year < 1970 || t->month < 1 || t->month > 12) {
    return BmEINVAL;
  }
  uint64_t us = days_from_civil(t->year, t->month, t->day) * 86400000000ull;
  us += (uint64_t)t->hour * 3600000000ull;
  us += (uint64_t)t->minute * 60000000ull;
  us += (uint64_t)t->second * 1000000ull;
  us += (uint64_t)t->ms * 1000ull;
  s_rtc_base_us = us;
  s_rtc_base_tick = uptime_us();
  return BmOK;
}

static uint64_t rtc_now_us(void) {
  return s_rtc_base_us + (uptime_us() - s_rtc_base_tick);
}

BmErr bm_rtc_get(RtcTimeAndDate *t) {
  if (!t) {
    return BmEINVAL;
  }
  if (s_rtc_base_us == 0) {
    return BmEIO;   // never set: honest "no RTC", callers handle it
  }
  uint64_t us = rtc_now_us();
  uint64_t days = us / 86400000000ull;
  uint64_t rem = us % 86400000000ull;

  uint16_t y = 1970;
  for (;;) {
    uint16_t ydays =
        ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) ? 366 : 365;
    if (days < ydays) {
      break;
    }
    days -= ydays;
    y++;
  }
  uint8_t m = 12;
  while (m > 1) {
    uint16_t start = DAYS_BEFORE_MONTH[m - 1];
    if (m > 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)) {
      start += 1;
    }
    if (days >= start) {
      days -= start;
      break;
    }
    m--;
  }
  t->year = y;
  t->month = m;
  t->day = (uint8_t)(days + 1);
  t->hour = (uint8_t)(rem / 3600000000ull);
  t->minute = (uint8_t)((rem / 60000000ull) % 60);
  t->second = (uint8_t)((rem / 1000000ull) % 60);
  t->ms = (uint16_t)((rem / 1000ull) % 1000);
  return BmOK;
}

uint64_t bm_rtc_get_micro_seconds(RtcTimeAndDate *t) {
  if (t) {
    bm_rtc_get(t);
  }
  return s_rtc_base_us ? rtc_now_us() : uptime_us();
}
