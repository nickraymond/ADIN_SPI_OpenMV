# Why nereus002's N6 is in USB full speed — and how to change it

**Source-verified 2026-09-07 against the OpenMV tree at the PR #3247 base.
No hardware was touched to establish any of this.**

## It is not the hub

USB device speed is negotiated by the **device**, from a device-side
configuration. A USB 2.0 hub does not downgrade a high-speed device — it
passes HS through and only does transaction translation for FS/LS devices
below it. A hub can only force FS if the *hub itself* is USB 1.1.

The FS/HS in the `by-id` name is not cosmetic and not negotiated hardware
capability — it is MicroPython's USB **product string**, chosen from the
mode the device came up in:

```
lib/micropython/ports/stm32/mpconfigboard_common.h:320
  MICROPY_HW_USB_PRODUCT_HS_STRING  "Pyboard Virtual Comm Port in HS Mode"
  MICROPY_HW_USB_PRODUCT_FS_STRING  "Pyboard Virtual Comm Port in FS Mode"
```

## The N6 has an embedded HS PHY and needs no external part

On classic STM32 parts, true HS needs an external ULPI PHY (`usbd_conf.c`
configures 12 ULPI pins for it). **The STM32N6 does not** — the N6 branch
enables the on-chip PHY directly:

```c
// usbd_conf.c, STM32N6 branch
LL_AHB5_GRP1_EnableClock(LL_AHB5_GRP1_PERIPH_OTGPHY1);
MODIFY_REG(USB1_HS_PHYC->USBPHYC_CR, USB_USBPHYC_CR_FSEL, 2 << ...);  // 24 MHz ref
...
pcd_hs_handle.Init.phy_itface = USB_OTG_HS_EMBEDDED_PHY;   // STM32N6
```

## High speed is compiled in, and it is a RUNTIME switch

`boards/OPENMV_N6/mpconfigboard.h` sets `MICROPY_HW_USB_HS_IN_FS (1)`, which
on most parts would rule HS out. **The N6 is explicitly exempted:**

```c
// usbdev/class/inc/usbd_cdc_msc_hid.h:13
#if MICROPY_HW_USB_HS \
    && (!MICROPY_HW_USB_HS_IN_FS || defined(STM32F723xx) || defined(STM32F733xx) || defined(STM32N6))
#define USBD_SUPPORT_HS_MODE (1)
```

So `USBD_SUPPORT_HS_MODE == 1` on stock OPENMV_N6 firmware, and the speed is
picked at **runtime**, defaulting to full:

```c
// usb.c:475   { MP_QSTR_high_speed, MP_ARG_KW_ONLY | MP_ARG_BOOL, {.u_bool = false} },
// usbd_conf.c:633
if (high_speed) { pcd_hs_handle.Init.speed = PCD_SPEED_HIGH; }
else            { pcd_hs_handle.Init.speed = PCD_SPEED_HIGH_IN_FULL; }
```

**No firmware rebuild is needed.** The call is:

```python
import pyb
pyb.usb_mode("VCP+MSC", high_speed=True)   # re-enumerates immediately
```

To persist it, put that in `boot.py` on the board's `/flash`.

## What to expect when it is switched — READ BEFORE DOING IT

- **The `by-id` name CHANGES** to `..._in_HS_Mode_...`, and the interface
  index can change too (`-if00` → `-if01`). The S30/nereus000 session hit
  exactly this. Anything that globs `-if00` silently stops finding the board.
- **The device re-enumerates**, so any open port drops. Do it with nothing
  holding the board, and re-run discovery afterwards.
- **Keep MSC off.** `usb_mode()` re-creates the USB config; the S29
  `usb-storage` rule matches interface CLASS 08/06/50 under VID 37c5 so it
  should still catch it, but verify
  `ls /sys/bus/usb/drivers/usb-storage/ | grep -c ':'` is still 0 afterwards.
- **A wrong `usb_mode()` can make the board unreachable** until a power cycle,
  because it is applied immediately. `boot.py` is the risk: a bad line there
  runs on every boot. Test interactively first, persist second.

## Still UNVERIFIED — needs the rig

1. That it actually negotiates 480 Mbps on this board. Check
   `/sys/bus/usb/devices/*/speed` → `12` vs `480`.
2. **The hub's own speed class.** An unpowered Terminus hub sits between the
   N6 and the Pi Zero 2 W. Terminus parts are typically USB 2.0, but if this
   one enumerates at 12 Mbps then it, not the board, is the ceiling — and
   that WOULD be a hub problem.
3. Achievable payload rate through a Pi Zero 2 W's single DWC2 OTG port with
   two cameras and a hub contending for it. The theoretical 480 Mbps is not
   the number that matters; `bench/n6_h264/usb_throughput.sh` measures the
   one that does.
