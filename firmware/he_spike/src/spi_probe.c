// spi_probe.c -- verdict C: the HE core claims SPI0 + its IRQ and proves
// the controller's full data path. No ADIN, no external wiring; the rig
// stays dismantled.
//
// Register recipe: verbatim from our S9-proven HP HAL
// (firmware/bm_spike/src/bm_spike_hal_alif.c, machine_spi.c's spi_init
// order) using Alif's own driver helpers (lib/alif/drivers). Pin facts:
// AE3 P0/P1/P2 = P5_1/P5_0/P5_3 = SPI0 MOSI(AF4)/MISO(AF4)/SCLK(AF3),
// P3 = P5_2 (left GPIO/AF0) -- verified in openmv.git @ 7d4dbf7ab2
// (S9 bite-2 nibble 1).
//
// RX data-path notes (both measured 2026-08-12):
//   * The DW internal loopback (CTRLR0 bit 13, SRL) is NOT implemented
//     on this SPI0 instance -- the bit reads back 0 immediately after
//     writing 1 with the controller disabled.
//   * The pad-pull fallback (MISO via PADCTRL pulls: up -> 0xFF, down ->
//     0x00) does not work on this pad: the line reads 0xFF under BOTH
//     pulls with the pin verifiably unconnected (bench-checked by Nick
//     2026-08-12) and the pinconf writes verifiably landing (readback
//     ok) -- PADCTRL_DRIVER_DISABLED pulls do not steer an AF-mode
//     input here. The pull test therefore REPORTS but does not gate;
//     the real RX-data proof is the first PHY-ID read when replacement
//     ADIN hardware arrives.
// Verdict C gates on: pinmux write + READBACK, controller init, and the
// SPI0 IRQ observed on the HE NVIC.
//
// IRQ: SPI0_IRQ_IRQn = 137 on the HE NVIC (M55_HE.h:295); RX-full
// (IMR.RXFIM) unmasked during the first transfer -> SPI_T_IRQ_SEEN.

#include <stdint.h>
#include "he_spike.h"

#include "spi.h"        // Alif DFP: SPI_Type + CTRLR0/IMR/SR bit names
#include "pinconf.h"    // Alif DFP: pinconf_set() + PADCTRL_*

#define SPI0_BASE_ADDR 0x48103000u   // global_map.h:74
#define SPI0_IRQN      137

#define CTRLR0_SRL     (1u << 13)    // DW SRL position; tied off here (above)

#define NVIC_ISER(n)   (*(volatile uint32_t *)(0xE000E100u + 4u * (n)))
#define NVIC_ICER(n)   (*(volatile uint32_t *)(0xE000E180u + 4u * (n)))
#define NVIC_IPR(irq)  (*(volatile uint8_t *)(0xE000E400u + (irq)))

static volatile uint32_t s_spi_irqs;

void he_spi0_irq(void) {
    SPI_Type *spi = (SPI_Type *)SPI0_BASE_ADDR;
    s_spi_irqs++;
    spi_mask_interrupts(spi);            // one-shot: count, quiesce
}

// One polled 16-byte full-duplex transfer; returns rx bytes via out.
static void xfer16(SPI_Type *spi, uint8_t *out) {
    for (uint32_t i = 0; i < 16; i++) {
        spi->SPI_DR[0] = (uint8_t)(0xA0u + i * 7u);
    }
    for (uint32_t i = 0, spins = 0; i < 16 && spins < 1000000u;) {
        if (spi->SPI_SR & SPI_SR_RFNE) {
            out[i++] = (uint8_t)spi->SPI_DR[0];
        } else {
            spins++;
        }
    }
}

static uint32_t all_bytes(const uint8_t *p, uint8_t v) {
    for (uint32_t i = 0; i < 16; i++) {
        if (p[i] != v) {
            return 0;
        }
    }
    return 1;
}

// result[0]=flags, [1]=rx word (MISO pulled up), [2]=rx word (pulled
// down), [3]=irq count, [4]=CTRLR0 diag (low16 after SRL write | end<<16)
void he_spi_test(uint32_t result[5]) {
    SPI_Type *spi = (SPI_Type *)SPI0_BASE_ADDR;
    uint8_t rx_up[16], rx_dn[16];
    uint32_t flags = 0;

    // Pin claim -- same calls, same pins as the proven HP HAL. MISO gets
    // pull-DOWN first: a floating pad tends to read high, so 0x00 in
    // phase 1 is the decisive (non-accidental) outcome.
    pinconf_set(PORT_5, PIN_1, PINMUX_ALTERNATE_FUNCTION_4, 0);
    pinconf_set(PORT_5, PIN_0, PINMUX_ALTERNATE_FUNCTION_4,
                PADCTRL_READ_ENABLE | PADCTRL_DRIVER_DISABLED_PULL_DOWN);
    pinconf_set(PORT_5, PIN_3, PINMUX_ALTERNATE_FUNCTION_3, 0);
    flags |= SPI_T_PINMUX_OK;

    // Controller init, machine_spi.c order; clock divider fixed (the
    // pull-based RX test is rate-agnostic).
    spi_disable(spi);
    spi_mask_interrupts(spi);
    spi->SPI_BAUDR = 32u;
    spi_set_tx_threshold(spi, 0);
    spi_set_rx_threshold(spi, 0);
    spi_set_mode(spi, SPI_MODE_0);
    spi_set_protocol(spi, SPI_PROTO_SPI);
    spi_mode_master(spi);
    spi_set_dfs(spi, 8);
    spi_control_ss(spi, 0, SPI_SS_STATE_ENABLE);  // SER: DW won't clock w/o it
    spi_set_sste(spi, false);
    spi->SPI_CTRLR0 |= CTRLR0_SRL;                // diagnostic only (tied off)
    uint32_t ctrlr0_after_write = spi->SPI_CTRLR0;
    (void)spi->SPI_ICR;
    spi_enable(spi);
    flags |= SPI_T_INIT_OK;

    // IRQ path: unmask RX-full, enable NVIC 137 on THIS core.
    s_spi_irqs = 0;
    NVIC_IPR(SPI0_IRQN) = 0xC0u;
    NVIC_ISER(SPI0_IRQN / 32u) = 1u << (SPI0_IRQN % 32u);
    spi->SPI_IMR = SPI_IMR_RX_FIFO_FULL_INTERRUPT_MASK;

    // Phase 1: MISO pulled down -> expect 0x00.
    xfer16(spi, rx_dn);

    // Phase 2: repad MISO pulled up -> expect 0xFF. Repad with the
    // controller disabled + settle delay (~us), in case the pad register
    // ignores writes mid-transfer.
    spi_disable(spi);
    pinconf_set(PORT_5, PIN_0, PINMUX_ALTERNATE_FUNCTION_4,
                PADCTRL_READ_ENABLE | PADCTRL_DRIVER_DISABLED_PULL_UP);
    for (volatile uint32_t i = 0; i < 10000; i++) {
    }
    spi_enable(spi);
    xfer16(spi, rx_up);

    NVIC_ICER(SPI0_IRQN / 32u) = 1u << (SPI0_IRQN % 32u);
    spi_mask_interrupts(spi);
    uint32_t ctrlr0_end = spi->SPI_CTRLR0;
    spi_disable(spi);

    if (all_bytes(rx_up, 0xFFu) && all_bytes(rx_dn, 0x00u)) {
        flags |= SPI_T_LOOP_MATCH;       // rx data path deterministic
    }
    if (s_spi_irqs) {
        flags |= SPI_T_IRQ_SEEN;
    }
    // Pad-config readback: proves the HE core's pinconf writes land
    // (measured 2026-08-12: af=4, pad as written, rc=0 -- pin ownership
    // from HE is real). Gate PINMUX on it.
    uint8_t rb_af = 0xEE, rb_pad = 0xEE;
    int32_t rc = pinconf_get(PORT_5, PIN_0, &rb_af, &rb_pad);
    if (rc != 0 || rb_af != PINMUX_ALTERNATE_FUNCTION_4) {
        flags &= ~SPI_T_PINMUX_OK;
    }
    result[0] = flags;
    result[1] = rx_up[0] | ((uint32_t)rx_up[15] << 8)
        | ((uint32_t)rb_af << 16) | ((uint32_t)rb_pad << 24);
    result[2] = rx_dn[0] | ((uint32_t)rx_dn[15] << 8)
        | (((uint32_t)(rc & 0xFF)) << 16);
    result[3] = s_spi_irqs;
    result[4] = (ctrlr0_after_write & 0xFFFFu) | ((ctrlr0_end & 0xFFFFu) << 16);
}
