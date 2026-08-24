# Bench power rig — INA3221 on nereus000 (S8 bite D)

The rig measures each board's 5 V USB supply through an Adafruit INA3221
(triple-channel, 0.05 Ω shunts, I2C 0x40) so latency loops and power draw
are captured on one clock. Replaces the FTDI USB-stick meter (removed
2026-08-23; the inline stick was the prime suspect in an AE3 enumeration
failure).

## One-time setup (Nick — wiring + sudo)

1. **Wire the breakout to the Pi header** (3.3 V logic, STEMMA QT or pins):
   - VCC → pin 1 (3V3) · GND → pin 9 · SDA → pin 3 (GPIO2) · SCL → pin 5 (GPIO3)
2. **Route each board's VBUS through a channel shunt** (VIN+ from the Pi
   side, VIN− to the board side, current flows +→−):
   - CH1 = AE3 supply · CH2 = N6 supply · CH3 = spare
   D+/D− data lines stay on the normal cable — the INA3221 carries ONLY
   the 5 V rail, so USB signal integrity is untouched (the lesson from
   the inline-stick meter).
3. **Enable I2C** (one-time, needs sudo + reboot):

   ```bash
   sudo raspi-config nonint do_i2c 0 && sudo reboot
   ```

4. Install the one dependency: `pip install --user smbus2`

## Every measurement session (agent-runnable routine)

```bash
# 1. Is the chip there?  (expects "manuf=0x5449 ... INA3221 CONFIRMED")
python3 ~/workbench/power_log.py --probe

# 2. Start logging both rails at 10 Hz
python3 ~/workbench/power_log.py --ch 1=AE3 --ch 2=N6 &
# rows land in ~/bench_logs/power/power_<ts>.jsonl

# 3. Run the timing probe on the board(s) — the probes print
#    "PWR_MARK <label>_start/<label>_end <ticks_ms>" markers.

# 4. Stop with SIGTERM/Ctrl-C (clean line-buffered JSONL either way).
```

**mJ/inference** = mean mW inside the probe's marked window × window
seconds ÷ inferences, minus the idle baseline measured in the 30 s
before the loop. Idle-vs-load must be visible in the trace before any
number is trusted — a flat trace means the shunt is not in the path.

## Verification ladder (trust artifacts)

- `--probe` must print `manuf=0x5449` (TI) before anything else is believed.
- The logger prints its config write AND read-back (`0x7527` = 3 ch,
  16-sample avg, 1.1 ms conversions, continuous) — a mismatch is a wiring
  or chip problem, stop there.
- Sanity anchors from S24 (order of magnitude only, different models):
  AE3 ~0.2 W, N6 ~1.0 W under load. A channel reading 0 mA under load
  means VBUS is not routed through that shunt.
