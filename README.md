# Home Aircon Driver

Home Assistant integration (HACS) that drives house heating and cooling from
**your own temperature sensors** — no YAML — and protects fish tanks from
heat soak by cooling the house when tank water gets hot.

Same approach as [ha-feedandwater-cards](https://github.com/markosharknz1/ha-feedandwater-cards):
config-flow entity pickers, one config entry per thing, rewire later via Options.

## Two kinds of entry

**Zone** — a room or area.
- Pick one or more temperature sensors (e.g. Zigbee). They are averaged; stale or
  unavailable ones are ignored.
- Pick outputs: climate units, heater switches, cooler/fan switches.
- The unit's own reading is only a fallback if every sensor goes quiet.
- Offset correction: if the unit reads 2° cold versus your sensor, the setpoint
  sent to it is shifted so the *room* reaches the target.
- Entities: Mode (off/heat/cool/auto), Target, Deadband, Temperature, Status.

**Tank guard** — protects the tanks.
- Pick tank water sensors and the zones to cool.
- Warn (default 26 °C) and Critical (27 °C); a rising-fast tank near warn also warns early.
- Warn/critical force the chosen zones to cool at the guard's cooling target
  (defaults 22 / 18 °C), overriding mode and schedule; critical also sets high fan.
- Release needs the water to fall a margin below the threshold (hysteresis).
- **No fresh tank data = critical.** A silent sensor is never treated as fine.
- Optional notify service (e.g. `notify.mobile_app_phone`).

## Install

HACS → Custom repositories → this repo (Integration) → install → restart →
Settings → Devices & services → Add integration → *Home Aircon Driver*.
Add your zones first, then the tank guard (it picks from existing zones).

## Roadmap

Dashboard card, weather-forecast pre-cooling (Met.no) and outdoor-temperature
triggers (Weathercloud), schedules, solar/tariff awareness, pick-sensors-by-area.

## Status

v0.1.0 scaffold: core logic and controllers unit-tested with a fake hass;
**not yet verified against a live Home Assistant** — do that before relying on
it for the tanks.
