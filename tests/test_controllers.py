"""Zone/Guard controller behaviour against a minimal fake hass."""
import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.home_aircon_driver.controllers import GuardController, ZoneController
from custom_components.home_aircon_driver.logic import LEVEL_CRITICAL, LEVEL_OK


class FakeStates:
    def __init__(self):
        self.d = {}

    def set(self, eid, state, attrs=None, age_s=0):
        ts = datetime.fromtimestamp(time.time() - age_s, tz=timezone.utc)
        self.d[eid] = SimpleNamespace(state=str(state), attributes=attrs or {}, last_updated=ts)

    def get(self, eid):
        return self.d.get(eid)


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


def make_hass():
    h = SimpleNamespace(states=FakeStates(), services=FakeServices(), data={})
    h.async_create_task = lambda coro: asyncio.ensure_future(coro)
    return h


def entry(**opts):
    return SimpleNamespace(
        entry_id="e1", title="Lounge", data={"kind": "zone", "slug": "lounge"}, options=opts
    )


AC = {
    "hvac_modes": ["off", "heat", "cool"],
    "fan_modes": ["low", "high"],
    "current_temperature": 23.0,
    "temperature": 24.0,
    "min_temp": 16,
    "max_temp": 30,
    "target_temp_step": 1,
}


def zone(hass, **opts):
    opts.setdefault("temperature_sensors", ["sensor.room"])
    opts.setdefault("climates", ["climate.ac"])
    z = ZoneController(hass, entry(**opts))
    return z


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_cool_uses_sensor_with_offset_correction():
    h = make_hass()
    h.states.set("sensor.room", 25.0)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    z.mode, z.target = "cool", 22.0
    run(z.async_evaluate())
    assert z.status == "cooling"
    calls = h.services.calls
    assert ("climate", "set_hvac_mode", {"entity_id": "climate.ac", "hvac_mode": "cool"}) in calls
    # unit reads 23, sensor 25 -> aim 2 lower: 20
    assert ("climate", "set_temperature", {"entity_id": "climate.ac", "temperature": 20.0}) in calls


def test_guard_critical_overrides_off_mode_and_sets_fan():
    h = make_hass()
    h.states.set("sensor.room", 21.0)  # room already cold; tank is the problem
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    z.mode = "off"
    run(z.async_set_guard("g1", LEVEL_CRITICAL, 22.0, 18.0))
    assert z.status == "guard_critical"
    calls = h.services.calls
    assert any(c[1] == "set_hvac_mode" and c[2]["hvac_mode"] == "cool" for c in calls)
    assert any(c[1] == "set_temperature" and c[2]["temperature"] <= 18.0 + 3 for c in calls)
    assert ("climate", "set_fan_mode", {"entity_id": "climate.ac", "fan_mode": "high"}) in calls


def test_guard_release_returns_to_normal():
    h = make_hass()
    h.states.set("sensor.room", 22.0)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    z.mode = "off"
    run(z.async_set_guard("g1", LEVEL_CRITICAL, 22.0, 18.0))
    run(z.async_set_guard("g1", LEVEL_OK, 22.0, 18.0))
    assert z.guard_level == LEVEL_OK
    assert z.status == "off"


def test_sensor_lost_idles_unless_guarded():
    h = make_hass()
    h.states.set("sensor.room", 25.0, age_s=99999)  # stale
    h.states.set("climate.ac", "cool", {**AC, "current_temperature": None})
    z = zone(h)
    z.mode = "cool"
    run(z.async_evaluate())
    assert z.status == "sensor_lost"
    run(z.async_set_guard("g1", LEVEL_CRITICAL, 22.0, 18.0))
    assert z.status == "guard_critical"  # guard cools even when blind


def test_unit_fallback_when_sensors_silent():
    h = make_hass()
    h.states.set("sensor.room", 25.0, age_s=99999)
    h.states.set("climate.ac", "off", {**AC, "current_temperature": 26.0})
    z = zone(h)
    z.mode, z.target = "cool", 22.0
    run(z.async_evaluate())
    assert z.temp_source == "unit_fallback" and z.status == "cooling"


def test_guard_controller_pushes_level_and_goes_critical_on_silence():
    h = make_hass()
    h.states.set("sensor.room", 22.0)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    h.data["home_aircon_driver"] = {"e1": z}
    g = GuardController(
        h,
        SimpleNamespace(
            entry_id="g1",
            title="Reef",
            data={"kind": "guard", "slug": "reef"},
            options={"tank_sensors": ["sensor.tank"], "zones": ["e1"]},
        ),
    )
    h.states.set("sensor.tank", 24.0)
    run(g.async_evaluate())
    assert g.result.name == "ok" and z.guard_level == LEVEL_OK
    h.states.set("sensor.tank", 27.4)
    run(g.async_evaluate())
    assert g.result.name == "critical" and z.guard_level == LEVEL_CRITICAL
    # sensor falls silent -> still critical
    h.states.set("sensor.tank", 24.0, age_s=99999)
    run(g.async_evaluate())
    assert g.result.reason == "no_data" and z.guard_level == LEVEL_CRITICAL


def test_override_cools_to_target_then_reverts():
    h = make_hass()
    h.states.set("sensor.room", 25.0)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    z.mode, z.target = "off", 22.0
    run(z.async_set_override("cool", 21.0, 60))
    assert z.status == "override_cool" and z.override is not None
    # room reaches the boost target -> override clears, zone returns to mode off
    h.states.set("sensor.room", 20.9)
    run(z.async_evaluate())
    assert z.override is None and z.status == "off"


def test_override_times_out_and_guard_beats_it():
    h = make_hass()
    h.states.set("sensor.room", 25.0)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    run(z.async_set_override("heat", 28.0, 60))
    assert z.status == "override_heat"
    run(z.async_set_guard("g1", LEVEL_CRITICAL, 22.0, 18.0))
    assert z.status == "guard_critical"  # tank protection wins over a heat boost
    z.override["until"] = time.time() - 1
    run(z.async_set_guard("g1", LEVEL_OK, 22.0, 18.0))
    assert z.override is None


def _ducted(h, **extra):
    h.states.set("sensor.room", 25.0)
    h.states.set("climate.ac", "off", dict(AC))
    for n in ("lounge", "bed1"):
        h.states.set(f"fan.{n}", "off")
    return zone(h, dampers=["fan.lounge", "fan.bed1"], **extra)


def _fan_calls(h):
    return [(c[1], c[2]["entity_id"]) for c in h.services.calls if c[0] == "fan"]


def test_dampers_follow_enabled_zones_when_running():
    h = make_hass()
    z = _ducted(h)
    z.mode, z.target = "cool", 22.0
    z.damper_enabled["fan.bed1"] = False
    run(z.async_evaluate())
    calls = _fan_calls(h)
    assert ("turn_on", "fan.lounge") in calls
    assert ("turn_on", "fan.bed1") not in calls


def test_at_least_one_damper_stays_open():
    h = make_hass()
    z = _ducted(h)
    z.mode, z.target = "cool", 22.0
    z.damper_enabled.update({"fan.lounge": False, "fan.bed1": False})
    run(z.async_evaluate())
    assert ("turn_on", "fan.lounge") in _fan_calls(h)


def test_guard_opens_every_damper():
    h = make_hass()
    z = _ducted(h)
    z.mode = "off"
    z.damper_enabled["fan.bed1"] = False
    run(z.async_set_guard("g1", LEVEL_CRITICAL, 22.0, 18.0))
    calls = _fan_calls(h)
    assert ("turn_on", "fan.lounge") in calls and ("turn_on", "fan.bed1") in calls


def test_idle_leaves_dampers_alone():
    h = make_hass()
    z = _ducted(h)
    z.mode = "off"
    run(z.async_evaluate())
    assert _fan_calls(h) == []


def test_program_replaces_mode_and_target_while_schedule_on():
    from datetime import datetime
    h = make_hass()
    h.states.set("sensor.room", 18.0)
    h.states.set("climate.ac", "off", dict(AC))
    h.states.set("schedule.morning", "on", {"next_event": "2026-06-01T08:00:00+10:00"})
    m = datetime.now().month
    z = zone(h, program1_schedule="schedule.morning", program1_mode="heat",
             program1_target=21, program1_months=[str(m)])
    z.mode = "off"  # manual mode off; program should still heat
    run(z.async_evaluate())
    assert z.status == "heating" and z.active_program["target"] == 21.0
    # programs switched off -> back to manual (off)
    run(z.async_set_programs_enabled(False))
    assert z.status == "off" and z.active_program is None
    # schedule off -> manual as well
    run(z.async_set_programs_enabled(True))
    h.states.set("schedule.morning", "off")
    run(z.async_evaluate())
    assert z.active_program is None and z.status == "off"


def test_steady_sensor_that_keeps_reporting_is_not_stale():
    """last_updated stops moving when the value is unchanged; last_reported
    keeps moving. The driver must trust last_reported."""
    h = make_hass()
    h.states.set("sensor.room", 24.1, age_s=99999)  # value unchanged for ages
    h.states.d["sensor.room"].last_reported = datetime.fromtimestamp(time.time() - 60, tz=timezone.utc)
    h.states.set("climate.ac", "off", dict(AC))
    z = zone(h)
    z.mode, z.target = "cool", 22.0
    run(z.async_evaluate())
    assert z.temp_source == "sensors"
