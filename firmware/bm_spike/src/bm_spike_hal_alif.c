// bm_spike_hal_alif.c -- S9 bite 2: adi_hal.h implemented against the Alif
// silicon directly. Replaces bm_spike_hal_mp.c's MicroPython-object path
// (machine.SPI.write_readinto per call) with a bare-metal SPI0 engine that
// actually uses the controller's 16-deep FIFOs. Exactly one of the two HAL
// files is staged per build (build_spike.sh --hal mp|alif).
//
// Hardware facts (verified in openmv.git @ 7d4dbf7ab2, the fixture rev):
//   AE3 P0 = P5_1 = SPI0_MOSI  AF4   (ports/alif/mcu/ensemble_pin_alt.csv)
//   AE3 P1 = P5_0 = SPI0_MISO  AF4   (input: pad READ_ENABLE)
//   AE3 P2 = P5_3 = SPI0_SCLK  AF3   (note: AF3, unlike the AF4 siblings)
//   AE3 P3 = P5_2 = CS, manual GPIO out (D2; silicon SS0 exists, unused)
//   AE3 P4 = P0_5 = ADIN RESET -- runner-owned machine.Pin (not in adi_hal.h)
//   AE3 P5 = P0_4 = ADIN INT_N in, needs pull-up (D14); NVIC GPIO0_IRQ4_IRQn
//   SPI0_BASE/GPIO5_BASE per lib/alif/Device/common/include/global_map.h;
//   SPI0 is clocked from GetSystemAHBClock(), always-on (no enable call
//   exists for non-LP SPI; proven by machine.SPI(0) since S4).
//
// SPI init recipe mirrors machine_spi.c's spi_init() 1:1 (S4-proven
// parameters), including spi_control_ss(SER) -- the DesignWare controller
// only clocks with a slave enabled, even though the SS0 pad is muxed away
// to our GPIO CS -- and SSTE off.
//
// Transfer model: same synchronous call + inline SPI-callback pattern bite 1
// proved re-entrant with adi_spi_oa's state machine; only the engine under
// it changes (FIFO-burst full duplex, <=16 frames in flight, vs the
// per-word lock-step of both machine_spi.c and Alif's spi_transfer_blocking
// -- the D8 ceiling).
//
// IRQ model: delivery is NVIC -> GPIO0_IRQ4Handler (machine_pin.c owns that
// symbol; vector table is const in MRAM, so we ride its dispatch) ->
// mp_irq_handler(hard) -> bm_spike.irq_trampoline (mod file) ->
// bm_spike_hal_alif_irq_fire() -> the driver callback registered via
// HAL_RegisterCallback. Registration is done by the runner through
// Pin("P5").irq(..., hard=True) -- sanctioned MicroPython plumbing, no fork,
// no vector-table fight. HAL_EnableIrq/DisableIrq gate the NVIC line
// directly. Edge-triggered (falling) for this bite; level-low conversion is
// a bite-3 concern if the data path needs it.
//
// NOT compiled on the host -- host tests link hal_mock.c instead.

#if !defined(CORE_M55_HE)   // spike is HP-only (REPL + P0-P5 live there; see D24)

#include "py/mphal.h"       // -> mphalport.h -> irq.h -> ALIF_CMSIS_H (NVIC, IRQn)

#include "spi.h"
#include "gpio.h"
#include "pinconf.h"
#include "clk.h"
#include "global_map.h"

#include "adi_hal.h"

// ---- fixed hardware bindings (see facts block above) ----
static SPI_Type *const s_spi = (SPI_Type *)SPI0_BASE;
static GPIO_Type *const s_cs_gpio = (GPIO_Type *)GPIO5_BASE;
#define CS_PIN            2U                    // P5_2 = AE3 P3
#define ADIN_IRQN         GPIO0_IRQ4_IRQn       // P0_4 = AE3 P5

// A stalled FIFO flag means broken wiring/clocking, not a slow ADIN: at the
// slowest supported rate (5 MHz) one byte is 1.6 us, so ~2M spins is
// seconds -- loud failure without hanging the REPL forever.
#define SPI_STALL_SPINS   (2u * 1000u * 1000u)

static bool s_ready = false;

static HAL_Callback_t s_spi_cb = NULL;
static void *s_spi_cb_param = NULL;
static HAL_Callback_t s_int_cb = NULL;
static void *s_int_cb_param = NULL;

// Debug counters, exposed via bm_spike.stats() -- trust artifacts: a bench
// number without a moved counter is not a measurement.
static volatile uint32_t s_stat_xfers = 0;
static volatile uint32_t s_stat_bytes = 0;
static volatile uint32_t s_stat_stalls = 0;
static volatile uint32_t s_stat_irqs = 0;

// ---- setup / teardown -------------------------------------------------

int bm_spike_hal_alif_setup(uint32_t hz)
{
    // CS first so it is deasserted before SCLK/MOSI start switching.
    pinconf_set(PORT_5, PIN_2, PINMUX_ALTERNATE_FUNCTION_0, 0);
    gpio_set_value_high(s_cs_gpio, CS_PIN);
    gpio_set_direction_output(s_cs_gpio, CS_PIN);

    pinconf_set(PORT_5, PIN_1, PINMUX_ALTERNATE_FUNCTION_4, 0);                   // MOSI
    pinconf_set(PORT_5, PIN_0, PINMUX_ALTERNATE_FUNCTION_4, PADCTRL_READ_ENABLE); // MISO
    pinconf_set(PORT_5, PIN_3, PINMUX_ALTERNATE_FUNCTION_3, 0);                   // SCLK

    // machine_spi.c spi_init() recipe, verbatim order.
    spi_disable(s_spi);
    spi_mask_interrupts(s_spi);
    spi_set_bus_speed(s_spi, hz, GetSystemAHBClock());
    spi_set_tx_threshold(s_spi, 0);
    spi_set_rx_threshold(s_spi, 0);
    spi_set_rx_sample_delay(s_spi, 0);
    spi_set_tx_fifo_start_level(s_spi, 0);
    spi_set_mode(s_spi, SPI_MODE_0);
    spi_set_protocol(s_spi, SPI_PROTO_SPI);
    spi_mode_master(s_spi);
    spi_set_dfs(s_spi, 8);
    spi_control_ss(s_spi, 0, SPI_SS_STATE_ENABLE);  // SER: DW won't clock without it
    spi_set_sste(s_spi, false);
    (void)s_spi->SPI_ICR;
    spi_enable(s_spi);

    s_ready = true;
    return 0;
}

uint32_t bm_spike_hal_alif_actual_hz(void)
{
    return spi_get_bus_speed(s_spi, GetSystemAHBClock());
}

bool bm_spike_hal_alif_ready(void)
{
    return s_ready;
}

// ---- IRQ trampoline target (called from the mod's builtin fun, which the
// runner registers as the hard Pin.irq handler for P5) ----

void bm_spike_hal_alif_irq_fire(void)
{
    s_stat_irqs++;
    if (s_int_cb) {
        s_int_cb(s_int_cb_param, 0, NULL);
    }
}

void bm_spike_hal_alif_stats(uint32_t out[4])
{
    out[0] = s_stat_xfers;
    out[1] = s_stat_bytes;
    out[2] = s_stat_stalls;
    out[3] = s_stat_irqs;
}

void bm_spike_hal_alif_stats_clear(void)
{
    s_stat_xfers = s_stat_bytes = s_stat_stalls = s_stat_irqs = 0;
}

// ---- adi_hal.h implementation -----------------------------------------

uint32_t HAL_SpiReadWrite(uint8_t *pBufferTx, uint8_t *pBufferRx, uint32_t nbBytes, bool useDma)
{
    (void)useDma;   // DMA deferred to S10 (Nick, 2026-08-11); SPI_DMACR hooks exist

    if (!s_ready) {
        return ADI_HAL_ERROR;
    }

    uint32_t txi = 0, rxi = 0, spins = 0;

    gpio_set_value_low(s_cs_gpio, CS_PIN);

    while (rxi < nbBytes) {
        bool moved = false;
        // Keep the TX FIFO fed, but never more than the RX FIFO depth in
        // flight -- every clocked frame lands a byte we must have room for.
        while (txi < nbBytes && (txi - rxi) < SPI_RX_FIFO_DEPTH
               && (s_spi->SPI_SR & SPI_SR_TFNF)) {
            s_spi->SPI_DR[0] = pBufferTx ? pBufferTx[txi] : 0xFFu;
            txi++;
            moved = true;
        }
        while (rxi < txi && (s_spi->SPI_SR & SPI_SR_RFNE)) {
            uint32_t d = s_spi->SPI_DR[0];
            if (pBufferRx) {
                pBufferRx[rxi] = (uint8_t)d;
            }
            rxi++;
            moved = true;
        }
        if (moved) {
            spins = 0;
        } else if (++spins > SPI_STALL_SPINS) {
            s_stat_stalls++;
            gpio_set_value_high(s_cs_gpio, CS_PIN);
            return ADI_HAL_ERROR;
        }
    }

    // Drain: SR.BUSY covers the last frame still shifting after RX count
    // says done (should not happen with TMOD_TX_AND_RX, cheap insurance).
    while (s_spi->SPI_SR & SPI_SR_BUSY) {
        if (++spins > SPI_STALL_SPINS) {
            s_stat_stalls++;
            break;
        }
    }

    gpio_set_value_high(s_cs_gpio, CS_PIN);

    s_stat_xfers++;
    s_stat_bytes += nbBytes;

    if (s_spi_cb) {
        s_spi_cb(s_spi_cb_param, 0, NULL);
    }
    return ADI_HAL_SUCCESS;
}

uint32_t HAL_SpiRegisterCallback(HAL_Callback_t const *spiCallback, void *hDevice)
{
    // Driver passes its callback cast to HAL_Callback_t const* (MAC_Init);
    // store it back as a callable pointer, bm_adin2111.c-style.
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

// Real critical sections this time: a hard GPIO IRQ can now touch driver
// state mid-transfer. PRIMASK save/restore with a nesting count.
static uint32_t s_cs_primask;
static uint32_t s_cs_depth;

uint32_t HAL_EnterCriticalSection(void)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    if (s_cs_depth++ == 0) {
        s_cs_primask = pm;
    }
    return 0;
}

uint32_t HAL_ExitCriticalSection(void)
{
    if (s_cs_depth != 0 && --s_cs_depth == 0 && s_cs_primask == 0) {
        __enable_irq();
    }
    return 0;
}

uint32_t HAL_EnableIrq(void)
{
    NVIC_EnableIRQ(ADIN_IRQN);
    return 0;
}

uint32_t HAL_DisableIrq(void)
{
    NVIC_DisableIRQ(ADIN_IRQN);
    return 0;
}

uint32_t HAL_GetEnableIrq(void)
{
    return NVIC_GetEnableIRQ(ADIN_IRQN);
}

uint32_t HAL_SetPendingIrq(void)
{
    NVIC_SetPendingIRQ(ADIN_IRQN);
    return 0;
}

uint32_t HAL_GetPendingIrq(void)
{
    return NVIC_GetPendingIRQ(ADIN_IRQN);
}

uint32_t HAL_Init_Hook(void)   { return 0; }
uint32_t HAL_UnInit_Hook(void) { return 0; }

#endif // !CORE_M55_HE
