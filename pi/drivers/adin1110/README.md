# Vendored ADIN1110 kernel drivers (out-of-tree build)

The Raspberry Pi OS kernel ships with `CONFIG_ADIN1110` disabled, so these
two **unmodified** mainline driver files are vendored here and built as
out-of-tree modules against the installed kernel headers (minutes, stock
kernel untouched) instead of SG's full-kernel-rebuild procedure.

## Provenance (do not edit these files — re-vendor to update)

| File | Source | Commit |
|---|---|---|
| `adin1110.c` | `raspberrypi/linux` `drivers/net/ethernet/adi/adin1110.c` | `rpi-6.18.y` @ `222a4b4132760c52d6067a2f99c430142b7800a6` (2026-08-07) |
| `adin1100.c` | `raspberrypi/linux` `drivers/net/phy/adin1100.c` | same |

Built/verified against kernel `6.18.34+rpt-rpi-2712` (Debian 13 trixie,
Pi 5). `adin1110.ko` is the SPI MAC driver; `adin1100.ko` is the phylib
driver for the internal 10BASE-T1L PHY (ID `0x0283BC91`) that the MAC
driver discovers on its own internal MDIO bus.

Kernel-side requirements (all satisfied by the stock rpt kernel, checked
2026-08-09): `CONFIG_NET_SWITCHDEV=y`, `CONFIG_PHYLIB=y`, `CONFIG_CRC8=m`,
`CONFIG_MODULE_SIG` off.

## Caveat

An apt kernel upgrade orphans the modules — re-run `pi/build_adin1110.sh`
after any kernel update (it checks for this and fails loudly).
