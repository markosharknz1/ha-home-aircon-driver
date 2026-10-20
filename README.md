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

## Ducted systems (AirTouch) and room units

A ducted unit with zone dampers (e.g. the AirTouch 2+ integration: one `climate`
for the unit, one `fan` per zone) is **one Zone entry**: the unit as the climate
output, a single sensor (e.g. by the air return) as the thermostat input, and the
zone fans as *Zone dampers*. Each damper gets an on/off switch ("open this room
when the system runs"); at least one damper always stays open while running, and a
tank-guard trip opens every damper. Room split units (e.g. Mitsubishi ECHONET Lite
`climate` entities) are separate Zone entries with their own sensors. The card shows
them all.

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
