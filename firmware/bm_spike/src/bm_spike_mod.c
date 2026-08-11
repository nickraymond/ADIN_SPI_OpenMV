// bm_spike_mod.c -- MicroPython usermod exposing the S9 spike verdicts.
//
//   import bm_spike
//   r1, phyid, r2 = bm_spike.verify(spi, cs)   # prints both verdict lines
//
// spi = machine.SPI instance, cs = machine.Pin (callable, active-low CS).
// Auto-registered by openmv's modules/micropython.mk wildcard when these
// sources are copied into the openmv tree's modules/ dir (build_spike.sh).

#if !defined(CORE_M55_HE)   // HE image cannot fit the spike; HP-only
#include "py/runtime.h"
#include "py/obj.h"

#include "bm_spike_verify.h"

extern void bm_spike_hal_bind(mp_obj_t spi, mp_obj_t cs);

static mp_obj_t bm_spike_verify_fn(mp_obj_t spi_in, mp_obj_t cs_in)
{
    bm_spike_hal_bind(spi_in, cs_in);

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
static MP_DEFINE_CONST_FUN_OBJ_2(bm_spike_verify_obj, bm_spike_verify_fn);

static const mp_rom_map_elem_t bm_spike_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_bm_spike) },
    { MP_ROM_QSTR(MP_QSTR_verify), MP_ROM_PTR(&bm_spike_verify_obj) },
};
static MP_DEFINE_CONST_DICT(bm_spike_globals, bm_spike_globals_table);

const mp_obj_module_t bm_spike_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&bm_spike_globals,
};

MP_REGISTER_MODULE(MP_QSTR_bm_spike, bm_spike_module);

#endif // !CORE_M55_HE
