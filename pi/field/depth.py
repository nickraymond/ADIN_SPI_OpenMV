#!/usr/bin/env python3
"""Depth sensor integration point: BlueRobotics Bar30 (MS5837-30BA).

STATUS 2026-09-09: the sensor is NOT INSTALLED on any rig. This module exists
so the recorder, the metadata log and the recipes can be written and shipped
against a stable interface now, and so plugging the sensor in later is a
config change rather than a code change two days before a boat leaves.

DESIGN RULE, and the reason this file is so defensive: a missing depth sensor
must NEVER cost a dive. Every read path degrades to ``None`` with a stated
reason; nothing here raises into a recording loop, and nothing here ever
invents a depth. A frame with ``depth_m: null`` is honest and still carries
its in-frame reference card; a frame with a guessed depth silently corrupts
the dataset the whole trip exists to collect.

WHAT IS VERIFIED vs WHAT IS OPEN
--------------------------------
Verified: the Pi+ power board sits at I2C ``0x43`` on bus 1, so a Bar30 at its
factory ``0x76`` does not collide. (Read off nereus002, 2026-09-09.)

OPEN -- and deliberately NOT guessed here (SPEC Open questions):
  * WHICH second bus, and on which pins. Nick specified "2nd I2C bus"; on a
    Pi Zero 2 W that means an ``i2c-gpio`` overlay on a chosen GPIO pair. The
    pin choice is a wiring decision, so ``BUS`` is configuration, not a
    constant baked into this file.
  * The conversion driver. The MS5837 compensation polynomial is datasheet
    material and this repo does not write register maths from memory
    (CLAUDE.md rule 3) -- so the actual conversion is delegated to
    BlueRobotics' own ``ms5837`` module. If it is not importable, this module
    reports that rather than approximating it.
"""

import os
import time

#: Factory address of the MS5837-30BA. Verified not to collide with the Pi+
#: at 0x43; both would still need to be on different buses only if the wiring
#: demands it, which is why the bus is configurable.
BAR30_ADDR = 0x76

#: Which I2C bus the Bar30 is wired to. Unset means "no sensor" -- the safe
#: default, because that is the true state of every rig today.
ENV_BUS = "FIELD_DEPTH_I2C_BUS"

#: Surface pressure used to turn absolute pressure into depth. A real dive
#: should seed this from the sensor at the surface, not from a constant.
DEFAULT_SURFACE_MBAR = 1013.25


class DepthReading(dict):
    """A reading, or an explained absence. Always safe to serialise."""

    @property
    def ok(self):
        return self.get("depth_m") is not None


def _absent(reason):
    return DepthReading(depth_m=None, pressure_mbar=None, temp_c=None,
                        ts_utc=_now_iso(), source="bar30", available=False,
                        reason=reason)


def _now_iso():
    """ISO-8601 UTC. Nick's standing rule: files carry UTC, never local."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def configured_bus():
    """The bus number from config, or None when no sensor is declared."""
    raw = os.environ.get(ENV_BUS, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def bus_present(bus):
    return bus is not None and os.path.exists("/dev/i2c-%d" % bus)


class DepthSensor:
    """Thin wrapper over BlueRobotics' ms5837 driver.

    Construction never raises and never blocks a recording start: if anything
    is missing the object simply reports ``available == False`` forever, and
    ``read()`` returns an absence carrying the reason.
    """

    def __init__(self, bus=None, surface_mbar=DEFAULT_SURFACE_MBAR):
        self.bus = configured_bus() if bus is None else bus
        self.surface_mbar = surface_mbar
        self._sensor = None
        self._reason = None
        self._init()

    def _init(self):
        if self.bus is None:
            self._reason = ("no depth sensor configured (set %s to the bus "
                            "number once the Bar30 is wired)" % ENV_BUS)
            return
        if not bus_present(self.bus):
            self._reason = ("/dev/i2c-%d does not exist -- enable the second "
                            "bus (i2c-gpio overlay) before expecting depth"
                            % self.bus)
            return
        try:
            import ms5837
        except ImportError:
            self._reason = ("ms5837 driver not installed -- BlueRobotics' "
                            "module provides the datasheet compensation; this "
                            "repo does not reimplement it")
            return
        try:
            sensor = ms5837.MS5837_30BA(bus=self.bus)
            if not sensor.init():
                self._reason = ("MS5837 init() failed on bus %d addr 0x%02X "
                                "-- check wiring and pull-ups"
                                % (self.bus, BAR30_ADDR))
                return
            self._sensor = sensor
        except Exception as exc:                  # driver/I2C errors, loudly
            self._reason = ("MS5837 open failed on bus %d addr 0x%02X: %s: %s"
                            % (self.bus, BAR30_ADDR, type(exc).__name__, exc))

    @property
    def available(self):
        return self._sensor is not None

    @property
    def reason(self):
        return self._reason

    def zero_at_surface(self):
        """Seed surface pressure from the sensor. Call topside, before a dive."""
        r = self.read()
        if r.ok and r.get("pressure_mbar"):
            self.surface_mbar = r["pressure_mbar"]
            return self.surface_mbar
        return None

    def read(self):
        if not self.available:
            return _absent(self._reason or "unavailable")
        try:
            if not self._sensor.read():
                return _absent("MS5837 read() returned false")
            pressure_mbar = self._sensor.pressure()      # mbar
            temp_c = self._sensor.temperature()
            depth_m = self._sensor.depth()               # driver's own model
            return DepthReading(depth_m=round(float(depth_m), 3),
                                pressure_mbar=round(float(pressure_mbar), 2),
                                temp_c=round(float(temp_c), 2),
                                surface_mbar=round(float(self.surface_mbar), 2),
                                ts_utc=_now_iso(), source="bar30",
                                available=True, reason=None)
        except Exception as exc:
            return _absent("MS5837 read failed: %s: %s"
                           % (type(exc).__name__, exc))


def open_sensor(bus=None):
    """Factory used by the recorder. Always returns a usable object."""
    return DepthSensor(bus=bus)


if __name__ == "__main__":
    s = open_sensor()
    print("bus:       ", s.bus)
    print("available: ", s.available)
    if not s.available:
        print("reason:    ", s.reason)
    print("read:      ", dict(s.read()))
