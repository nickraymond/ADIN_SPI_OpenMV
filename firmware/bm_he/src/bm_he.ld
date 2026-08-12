/* bm_he.ld -- S10 INTERIM 2: bm_core stack app for the AE3's M55_HE core.
 *
 * Same region + rationale as the bite-1 spike's he_spike.ld (SRAM9_B upper
 * half, 0x60080000, 256 KB -- global address writable by the HP loader,
 * provably untouched by the flashed images per the D24 build maps).
 *
 * Deltas from he_spike.ld:
 *   - TWO reserved pages at the top:
 *       0x600BFE00..0x600BFEFF  bm status page   (bm_he.h)
 *       0x600BFF00..0x600BFFFF  he_spike status page + guard (he_spike.h)
 *   - .noinit section: NOT zeroed by Reset_Handler -- bcmp's dfu_core
 *     keeps its reboot-info struct across resets there.
 * MSP grows down from the bm status page.
 */

ENTRY(_vectors)

MEMORY
{
    APP (rwx) : ORIGIN = 0x60080000, LENGTH = 0x0003FE00
}

_bm_status_page = 0x600BFE00;
_estack         = _bm_status_page;

SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.vectors))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > APP

    .data : ALIGN(4)
    {
        *(.data*)
        . = ALIGN(4);
    } > APP

    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start__ = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
    } > APP

    .noinit (NOLOAD) : ALIGN(4)
    {
        *(.noinit*)
        . = ALIGN(4);
    } > APP

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
