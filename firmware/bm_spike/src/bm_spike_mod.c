// bm_spike_mod.c -- MicroPython usermod exposing the S9 spike verdicts.
//
// Built against exactly one HAL, chosen at stage time by build_spike.sh
// --hal (bm_spike_hal_choice.h is generated there):
//
//   --hal mp   (default, bite-1 baseline)     --hal alif  (bite 2)
//   r1, phyid, r2 = bm_spike.verify(spi, cs)  bm_spike.setup(hz)
//   us, fails, ph = bm_spike.bench(spi,cs,n)  r1, phyid, r2 = bm_spike.verify()
//                                             us, fails, ph = bm_spike.bench(n)
//                                             bm_spike.irq_trampoline -> Pin.irq
//                                             bm_spike.stats() / stats_clear()
//                                             bm_spike.actual_hz()
//   bm_spike.HAL == "mp"                      bm_spike.HAL == "alif"
//
// Auto-registered by openmv's modules/micropython.mk wildcard when these
// sources are copied into the openmv tree's modules/ dir (build_spike.sh).

#if !defined(CORE_M55_HE)   // HE image cannot fit the spike; HP-only
#include "py/runtime.h"
#include "py/obj.h"
#include "py/mphal.h"

#include "bm_spike_verify.h"
#include "bm_spike_hal_choice.h"

#if !defined(BM_SPIKE_HAL_ALIF)
#error "bm_spike_hal_choice.h did not define BM_SPIKE_HAL_ALIF (stage via build_spike.sh)"
#endif

#if !BM_SPIKE_HAL_ALIF
extern void bm_spike_hal_bind(mp_obj_t spi, mp_obj_t cs);
#else
extern int bm_spike_hal_alif_setup(uint32_t hz);
extern uint32_t bm_spike_hal_alif_actual_hz(void);
extern bool bm_spike_hal_alif_ready(void);
extern void bm_spike_hal_alif_irq_fire(void);
extern void bm_spike_hal_alif_stats(uint32_t out[4]);
extern void bm_spike_hal_alif_stats_clear(void);
#endif

// Shared verdict body -- identical across HALs so bite 2's result is
// directly comparable with bite 1's.
static mp_obj_t bm_spike_run_verdicts(void)
{
    uint32_t phyid = 0;
    int init_r = -1;
    int r1 = bm_spike_read_phyid(&phyid, &init_r);
    const char *v1;
    if (r1 == 0 && phyid == BM_SPIKE_PHYID_ADIN1110) {
        v1 = "OA TRANSPORT OK on ADIN1110 (driver framing works on our silicon)";
    } else if (r1 == 0 && phyid == BM_SPIKE_PHYID_ADIN2111) {
        v1 = "OA TRANSPORT OK (ADIN2111 identity?!)";
    } else if (r1 == 0) {
        v1 = "read ok but UNEXPECTED PHYID (strap mode / wiring suspect)";
    } else {
        v1 = "OA read FAILED (strap mode / wiring / protection suspect)";
    }
    mp_printf(&mp_plat_print,
              "verdict 1 -- OA register read: read=%d (%s) PHYID=0x%08X\n"
              "  [MAC-init result=%d (%s); COMM_TIMEOUT here on a 1110 = the\n"
              "   2111 identity gate, which fires inside MAC-layer init]\n"
              "  -> %s\n",
              r1, bm_spike_result_str(r1), (unsigned)phyid,
              init_r, bm_spike_result_str(init_r), v1);

    int r2 = bm_spike_full_init();
    mp_printf(&mp_plat_print,
              "verdict 2 -- adin2111_Init unmodified: result=%d (%s)\n  -> %s\n",
              r2, bm_spike_result_str(r2),
              (r2 == 0) ? "full init PASSED"
                        : "init refused (COMM_TIMEOUT here = the 2111-only "
                          "PHYID equality gate in waitDeviceReady, per source)");

    mp_obj_t items[3] = {
        mp_obj_new_int(r1),
        mp_obj_new_int_from_uint(phyid),
        mp_obj_new_int(r2),
    };
    return mp_obj_new_tuple(3, items);
}

// Raw register passthrough (both HALs; mp build needs a prior bind via
// verify/bench). Raises on driver error so a bad read can't masquerade as
// data.
static mp_obj_t bm_spike_read_reg_fn(mp_obj_t addr_in)
{
    uint32_t val = 0;
    int r = bm_spike_reg_read((uint16_t)mp_obj_get_int(addr_in), &val);
    if (r != 0) {
        mp_raise_msg_varg(&mp_type_RuntimeError,
                          MP_ERROR_TEXT("reg read failed: %s"), bm_spike_result_str(r));
    }
    return mp_obj_new_int_from_uint(val);
}
static MP_DEFINE_CONST_FUN_OBJ_1(bm_spike_read_reg_obj, bm_spike_read_reg_fn);

static mp_obj_t bm_spike_write_reg_fn(mp_obj_t addr_in, mp_obj_t val_in)
{
    int r = bm_spike_reg_write((uint16_t)mp_obj_get_int(addr_in),
                               (uint32_t)mp_obj_get_int_truncated(val_in));
    if (r != 0) {
        mp_raise_msg_varg(&mp_type_RuntimeError,
                          MP_ERROR_TEXT("reg write failed: %s"), bm_spike_result_str(r));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(bm_spike_write_reg_obj, bm_spike_write_reg_fn);

// Shared bench body: open the bench MAC handle (init timeout expected on a
// 1110, reported not timed), then time n PHYID round trips.
static mp_obj_t bm_spike_run_bench(uint32_t n)
{
    int init_r = -1;
    if (bm_spike_bench_open(&init_r) != 0) {
        mp_raise_msg_varg(&mp_type_RuntimeError,
                          MP_ERROR_TEXT("bench: no MAC handle (init=%d)"), init_r);
    }
    uint32_t phyid = 0, fails = 0;
    mp_uint_t t0 = mp_hal_ticks_us();
    bm_spike_bench_reads(n, &phyid, &fails);
    mp_uint_t t1 = mp_hal_ticks_us();

    mp_obj_t items[3] = {
        mp_obj_new_int_from_uint((uint32_t)(t1 - t0)),
        mp_obj_new_int_from_uint(fails),
        mp_obj_new_int_from_uint(phyid),
    };
    return mp_obj_new_tuple(3, items);
}

#if !BM_SPIKE_HAL_ALIF
// ---------------- mp HAL (bite-1 baseline) ----------------

static mp_obj_t bm_spike_verify_fn(mp_obj_t spi_in, mp_obj_t cs_in)
{
    bm_spike_hal_bind(spi_in, cs_in);
    return bm_spike_run_verdicts();
}
static MP_DEFINE_CONST_FUN_OBJ_2(bm_spike_verify_obj, bm_spike_verify_fn);

static mp_obj_t bm_spike_bench_fn(mp_obj_t spi_in, mp_obj_t cs_in, mp_obj_t n_in)
{
    bm_spike_hal_bind(spi_in, cs_in);
    return bm_spike_run_bench((uint32_t)mp_obj_get_int(n_in));
}
static MP_DEFINE_CONST_FUN_OBJ_3(bm_spike_bench_obj, bm_spike_bench_fn);

static const mp_rom_map_elem_t bm_spike_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_bm_spike) },
    { MP_ROM_QSTR(MP_QSTR_HAL), MP_ROM_QSTR(MP_QSTR_mp) },
    { MP_ROM_QSTR(MP_QSTR_verify), MP_ROM_PTR(&bm_spike_verify_obj) },
    { MP_ROM_QSTR(MP_QSTR_bench), MP_ROM_PTR(&bm_spike_bench_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_reg), MP_ROM_PTR(&bm_spike_read_reg_obj) },
    { MP_ROM_QSTR(MP_QSTR_write_reg), MP_ROM_PTR(&bm_spike_write_reg_obj) },
};

#else
// ---------------- alif-native HAL (bite 2) ----------------

static void bm_spike_require_setup(void)
{
    if (!bm_spike_hal_alif_ready()) {
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("call bm_spike.setup(hz) first"));
    }
}

static mp_obj_t bm_spike_setup_fn(mp_obj_t hz_in)
{
    mp_int_t hz = mp_obj_get_int(hz_in);
    if (hz <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("hz must be > 0"));
    }
    bm_spike_hal_alif_setup((uint32_t)hz);
    return mp_obj_new_int_from_uint(bm_spike_hal_alif_actual_hz());
}
static MP_DEFINE_CONST_FUN_OBJ_1(bm_spike_setup_obj, bm_spike_setup_fn);

static mp_obj_t bm_spike_verify_fn(void)
{
    bm_spike_require_setup();
    return bm_spike_run_verdicts();
}
static MP_DEFINE_CONST_FUN_OBJ_0(bm_spike_verify_obj, bm_spike_verify_fn);

static mp_obj_t bm_spike_bench_fn(mp_obj_t n_in)
{
    bm_spike_require_setup();
    return bm_spike_run_bench((uint32_t)mp_obj_get_int(n_in));
}
static MP_DEFINE_CONST_FUN_OBJ_1(bm_spike_bench_obj, bm_spike_bench_fn);

// Hard-IRQ safe: no allocation, no Python execution. Registered by the
// runner as Pin("P5").irq(handler=bm_spike.irq_trampoline, hard=True).
static mp_obj_t bm_spike_irq_trampoline_fn(mp_obj_t pin_in)
{
    (void)pin_in;
    bm_spike_hal_alif_irq_fire();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(bm_spike_irq_trampoline_obj, bm_spike_irq_trampoline_fn);

static mp_obj_t bm_spike_stats_fn(void)
{
    uint32_t s[4];
    bm_spike_hal_alif_stats(s);
    mp_obj_t items[4] = {
        mp_obj_new_int_from_uint(s[0]),   // xfers
        mp_obj_new_int_from_uint(s[1]),   // bytes
        mp_obj_new_int_from_uint(s[2]),   // stalls
        mp_obj_new_int_from_uint(s[3]),   // irqs
    };
    return mp_obj_new_tuple(4, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(bm_spike_stats_obj, bm_spike_stats_fn);

static mp_obj_t bm_spike_stats_clear_fn(void)
{
    bm_spike_hal_alif_stats_clear();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(bm_spike_stats_clear_obj, bm_spike_stats_clear_fn);

static mp_obj_t bm_spike_actual_hz_fn(void)
{
    bm_spike_require_setup();
    return mp_obj_new_int_from_uint(bm_spike_hal_alif_actual_hz());
}
static MP_DEFINE_CONST_FUN_OBJ_0(bm_spike_actual_hz_obj, bm_spike_actual_hz_fn);

static const mp_rom_map_elem_t bm_spike_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_bm_spike) },
    { MP_ROM_QSTR(MP_QSTR_HAL), MP_ROM_QSTR(MP_QSTR_alif) },
    { MP_ROM_QSTR(MP_QSTR_setup), MP_ROM_PTR(&bm_spike_setup_obj) },
    { MP_ROM_QSTR(MP_QSTR_verify), MP_ROM_PTR(&bm_spike_verify_obj) },
    { MP_ROM_QSTR(MP_QSTR_bench), MP_ROM_PTR(&bm_spike_bench_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq_trampoline), MP_ROM_PTR(&bm_spike_irq_trampoline_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&bm_spike_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats_clear), MP_ROM_PTR(&bm_spike_stats_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_actual_hz), MP_ROM_PTR(&bm_spike_actual_hz_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_reg), MP_ROM_PTR(&bm_spike_read_reg_obj) },
    { MP_ROM_QSTR(MP_QSTR_write_reg), MP_ROM_PTR(&bm_spike_write_reg_obj) },
};

#endif // BM_SPIKE_HAL_ALIF

static MP_DEFINE_CONST_DICT(bm_spike_globals, bm_spike_globals_table);

const mp_obj_module_t bm_spike_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&bm_spike_globals,
};

MP_REGISTER_MODULE(MP_QSTR_bm_spike, bm_spike_module);

#endif // !CORE_M55_HE
