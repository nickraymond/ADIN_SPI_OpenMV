// bm_spike_hal_mp.c -- adi_hal.h implementation for the AE3 spike, backed
// by MicroPython objects: a machine.SPI for transfers and a machine.Pin
// (callable) for manual CS. Reuses the S4-proven P0-P3 SPI path
// byte-for-byte; throughput is irrelevant for a verify spike (D2: CS is
// manual GPIO on the AE3 anyway).
//
// Blocking model: HAL_SpiReadWrite runs the full-duplex transfer
// synchronously, then invokes the registered SPI callback inline -- the
// driver's oaStateMachine is fully re-entrant under this pattern
// (spiCallback -> oaStateMachine advances CONTROL_START -> CONTROL_END).
// IRQ hooks are stubs: the spike only does polled control reads.
//
// NOT compiled on the host -- host tests link hal_mock.c instead.

#if !defined(CORE_M55_HE)   // HE image cannot fit the spike; HP-only
#include "py/runtime.h"
#include "py/obj.h"
#include "py/objarray.h"

#include "adi_hal.h"

static mp_obj_t s_spi = MP_OBJ_NULL;
static mp_obj_t s_cs = MP_OBJ_NULL;
static HAL_Callback_t s_spi_cb = NULL;
static void *s_spi_cb_param = NULL;
static HAL_Callback_t s_int_cb = NULL;
static void *s_int_cb_param = NULL;

void bm_spike_hal_bind(mp_obj_t spi, mp_obj_t cs)
{
    s_spi = spi;
    s_cs = cs;
    s_spi_cb = NULL;
    s_int_cb = NULL;
}

uint32_t HAL_SpiReadWrite(uint8_t *pBufferTx, uint8_t *pBufferRx, uint32_t nbBytes, bool useDma)
{
    (void)useDma;
    if (s_spi == MP_OBJ_NULL || s_cs == MP_OBJ_NULL) {
        return 1;
    }
    mp_obj_t txmv = mp_obj_new_memoryview('B', nbBytes, pBufferTx);
    mp_obj_t rxmv = mp_obj_new_memoryview('B' | MP_OBJ_ARRAY_TYPECODE_FLAG_RW,
                                          nbBytes, pBufferRx);
    mp_obj_t dest[4];
    mp_load_method(s_spi, MP_QSTR_write_readinto, dest);
    dest[2] = txmv;
    dest[3] = rxmv;
    mp_call_function_1(s_cs, MP_OBJ_NEW_SMALL_INT(0));
    mp_call_method_n_kw(2, 0, dest);
    mp_call_function_1(s_cs, MP_OBJ_NEW_SMALL_INT(1));
    if (s_spi_cb) {
        s_spi_cb(s_spi_cb_param, 0, NULL);
    }
    return 0;
}

uint32_t HAL_SpiRegisterCallback(HAL_Callback_t const *spiCallback, void *hDevice)
{
    // The driver passes its callback function cast to HAL_Callback_t const*
    // (see MAC_Init); store it back as a callable pointer, bm_adin2111.c-style.
    s_spi_cb = (HAL_Callback_t)(void *)spiCallback;
    s_spi_cb_param = hDevice;
    return 0;
}

uint32_t HAL_RegisterCallback(HAL_Callback_t const *intCallback, void *hDevice)
{
    s_int_cb = (HAL_Callback_t)(void *)intCallback;
    s_int_cb_param = hDevice;
    return 0;
}

// Polled spike: no ADIN IRQ line in use, critical sections are no-ops
// (single MicroPython thread, no interrupt context touches driver state).
uint32_t HAL_EnterCriticalSection(void) { return 0; }
uint32_t HAL_ExitCriticalSection(void)  { return 0; }
uint32_t HAL_EnableIrq(void)            { return 0; }
uint32_t HAL_DisableIrq(void)           { return 0; }
uint32_t HAL_GetEnableIrq(void)         { return 0; }
uint32_t HAL_SetPendingIrq(void)        { return 0; }
uint32_t HAL_GetPendingIrq(void)        { return 0; }
uint32_t HAL_Init_Hook(void)            { return 0; }
uint32_t HAL_UnInit_Hook(void)          { return 0; }

#endif // !CORE_M55_HE
