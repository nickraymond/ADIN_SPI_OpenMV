// spi_probe.c -- verdict C: the HE core claims SPI0 + its IRQ and proves
// the controller works via the DesignWare internal loopback. No ADIN, no
// external wiring; the rig stays dismantled.
//
// Register recipe: verbatim from our S9-proven HP HAL
// (firmware/bm_spike/src/bm_spike_hal_alif.c, machine_spi.c's spi_init
// order) using Alif's own driver helpers (lib/alif/drivers). Pin facts:
// AE3 P0/P1/P2 = P5_1/P5_0/P5_3 = SPI0 MOSI(AF4)/MISO(AF4)/SCLK(AF3),
// P3 = P5_2 (left GPIO/AF0) -- verified in openmv.git @ 7d4dbf7ab2
// (S9 bite-2 nibble 1).
//
// Loopback bit: DW apb_ssi CTRLR0 bit 13 (SRL). Alif's spi.h names bits
// 12 (SLV_OE) and 14 (SSTE) and leaves 13 unnamed -- the classic DW SSI
// layout puts SRL between them (DW databook). The test is self-verifying:
// if bit 13 were not SRL, the RX pattern comparison fails and verdict C
// reports it honestly (SPI_T_LOOP_MATCH stays 0).
//
// IRQ: SPI0_IRQ_IRQn = 137 on the HE NVIC (M55_HE.h:295); we unmask RX-
// full (IMR.RXFIM) during the transfer and count arrivals -> SPI_T_IRQ_SEEN.

#include <stdint.h>
#include "he_spike.h"

#include "spi.h"        // Alif DFP: SPI_Type + CTRLR0/IMR/SR bit names
#include "pinconf.h"    // Alif DFP: pinconf_set()

#define SPI0_BASE_ADDR 0x48103000u   // global_map.h:74
#define SPI0_IRQN      137

#define CTRLR0_SRL     (1u << 13)    // see header note

#define NVIC_ISER(n)   (*(volatile uint32_t *)(0xE000E100u + 4u * (n)))
#define NVIC_ICER(n)   (*(volatile uint32_t *)(0xE000E180u + 4u * (n)))
#define NVIC_IPR(irq)  (*(volatile uint8_t *)(0xE000E400u + (irq)))

static volatile uint32_t s_spi_irqs;

void he_spi0_irq(void) {
    SPI_Type *spi = (SPI_Type *)SPI0_BASE_ADDR;
    s_spi_irqs++;
    spi_mask_interrupts(spi);            // one-shot: count, quiesce
}

// result[0]=flags, [1]=tx crc, [2]=rx crc, [3]=irq count, [4]=CTRLR0
void he_spi_test(uint32_t result[5]) {
    SPI_Type *spi = (SPI_Type *)SPI0_BASE_ADDR;
    uint8_t tx[16], rx[16];
    uint32_t flags = 0;

    for (uint32_t i = 0; i < sizeof(tx); i++) {
        tx[i] = (uint8_t)(0xA0u + i * 7u);
        rx[i] = 0;
    }

    // Pin claim -- same calls, same pins as the proven HP HAL.
    pinconf_set(PORT_5, PIN_1, PINMUX_ALTERNATE_FUNCTION_4, 0);
    pinconf_set(PORT_5, PIN_0, PINMUX_ALTERNATE_FUNCTION_4,
                PADCTRL_READ_ENABLE);
    pinconf_set(PORT_5, PIN_3, PINMUX_ALTERNATE_FUNCTION_3, 0);
    flags |= SPI_T_PINMUX_OK;

    // Controller init, machine_spi.c order; clock divider fixed (internal
    // loopback is rate-agnostic).
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
    spi->SPI_CTRLR0 |= CTRLR0_SRL;                // internal loopback
    (void)spi->SPI_ICR;
    spi_enable(spi);
    flags |= SPI_T_INIT_OK;

    // IRQ path: unmask RX-full, enable NVIC 137 on THIS core.
    s_spi_irqs = 0;
    NVIC_IPR(SPI0_IRQN) = 0xC0u;
    NVIC_ISER(SPI0_IRQN / 32u) = 1u << (SPI0_IRQN % 32u);
    spi->SPI_IMR = SPI_IMR_RX_FIFO_FULL_INTERRUPT_MASK;

    // Polled full-duplex transfer (16 B fits both FIFOs).
    for (uint32_t i = 0; i < sizeof(tx); i++) {
        spi->SPI_DR[0] = tx[i];
    }
    for (uint32_t i = 0, spins = 0; i < sizeof(rx) && spins < 1000000u;) {
        if (spi->SPI_SR & SPI_SR_RFNE) {
            rx[i++] = (uint8_t)spi->SPI_DR[0];
        } else {
            spins++;
        }
    }

    NVIC_ICER(SPI0_IRQN / 32u) = 1u << (SPI0_IRQN % 32u);
    spi_mask_interrupts(spi);
    spi_disable(spi);

    result[1] = he_crc32(tx, sizeof(tx));
    result[2] = he_crc32(rx, sizeof(rx));
    if (result[1] == result[2]) {
        flags |= SPI_T_LOOP_MATCH;
    }
    if (s_spi_irqs) {
        flags |= SPI_T_IRQ_SEEN;
    }
    result[0] = flags;
    result[3] = s_spi_irqs;
    result[4] = spi->SPI_CTRLR0;
}
