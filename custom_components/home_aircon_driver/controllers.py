"""Zone and Guard controllers.

A ZoneController owns one area's thermostat loop: it reads the chosen
(accurate) temperature sensors, decides heat/cool/idle with hysteresis and
drives climate entities and/or switches. A GuardController watches tank
water temperature and pushes its level into the zones it protects; a zone
under guard overrides its mode and target and cools hard.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_CLIMATES,
    CONF_COOLERS,
    CONF_CRITICAL,
    CONF_CRITICAL_SETPOINT,
    CONF_DAMPERS,
    CONF_HEATERS,
    CONF_HYSTERESIS,
    CONF_NOTIFY,
    CONF_REFRESH,
    CONF_SENSOR_MAX_AGE,
    CONF_SENSOR_TIMEOUTS,
    CONF_SLUG,
    CONF_STALE_MIN,
    CONF_TANK_SENSORS,
    CONF_TEMP_SENSORS,
    CONF_WARN,
    CONF_WARN_SETPOINT,
    CONF_ZONES,
    DEFAULT_CRITICAL,
    DEFAULT_CRITICAL_SETPOINT,
    DEFAULT_DEADBAND,
    DEFAULT_HYSTERESIS,
    DEFAULT_SENSOR_MAX_AGE,
    DEFAULT_STALE_MIN,
    DEFAULT_TARGET,
    DEFAULT_WARN,
    DEFAULT_WARN_SETPOINT,
    DOMAIN,
    TICK_SECONDS,
)
from .logic import (
    ACT_COOL,
    ACT_HEAT,
    ACT_IDLE,
    LEVEL_CRITICAL,
    LEVEL_OK,
    LEVEL_WARN,
    GuardEvaluator,
    GuardResult,
    average_fresh,
    corrected_setpoint,
    decide,
    override_reached,
    pick_program,
    programs_from_options,
)

_LOGGER = logging.getLogger(__name__)
RESEND_AFTER_S = 300
REFRESH_EVERY_S = 600
SimpleState = type("SimpleState", (), {"state": "off"})()
DEFAULT_OVERRIDE_MINUTES = 180


def cfg_of(entry: ConfigEntry) -> dict:
    """Entry data with options layered on top (options rewire hardware)."""
    return {**entry.data, **entry.options}


def read_sensors(
    hass: HomeAssistant,
    entity_ids: list[str],
    limits_s: dict[str, float] | None = None,
) -> list[tuple]:
    """(value, last_seen, own_limit_seconds) per sensor."""
    out: list[tuple] = []
    for eid in entity_ids:
        limit = (limits_s or {}).get(eid)
        st = hass.states.get(eid)
        if st is None:
            out.append((None, 0.0, limit))
            continue
        try:
            val: float | None = float(st.state)
        except (TypeError, ValueError):
            val = None
        # last_reported moves on every report, even an unchanged value;
        # last_updated does not (a steady room would look "stale").
        seen = getattr(st, "last_reported", None) or st.last_updated
        out.append((val, seen.timestamp(), limit))
    return out


class _Base:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.cfg = cfg_of(entry)
        self.slug: str = self.cfg[CONF_SLUG]
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._last_refresh: dict[str, float] = {}

    def _limits_s(self, ids: list[str], default_min: float) -> dict[str, float]:
        """Per-sensor 'treat as silent after' (seconds): the sensor's own
        override if set, else the entry default."""
        own = self.cfg.get(CONF_SENSOR_TIMEOUTS) or {}
        return {e: float(own.get(e) or default_min) * 60 for e in ids}

    async def _readings(self, ids: list[str], default_min: float) -> list[tuple]:
        """Readings with per-sensor limits. A sensor that is past half its
        limit is asked to refresh (rate-limited), which helps integrations
        that poll or need a nudge; sleepy Zigbee devices simply ignore it."""
        limits = self._limits_s(ids, default_min)
        readings = read_sensors(self.hass, ids, limits)
        if self.cfg.get(CONF_REFRESH, True):
            now = time.time()
            for eid, (val, seen, limit) in zip(ids, readings):
                st = self.hass.states.get(eid)
                if st is None or st.state == "unavailable":
                    continue
                if now - seen > 0.5 * limit and now - self._last_refresh.get(eid, 0) > REFRESH_EVERY_S:
                    self._last_refresh[eid] = now
                    await self.hass.services.async_call(
                        "homeassistant", "update_entity", {"entity_id": eid}, blocking=False
                    )
        return readings

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()


class ZoneController(_Base):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry)
        self.mode = "off"
        self.programs_enabled = True
        self.last_error: str | None = None
        self.active_program: dict | None = None
        self.target = DEFAULT_TARGET
        self.deadband = DEFAULT_DEADBAND
        self.action = ACT_IDLE
        self.status = "off"
        self.temperature: float | None = None
        self.temp_source = "none"
        # guard entry_id -> (level, warn_setpoint, critical_setpoint)
        self._guards: dict[str, tuple[int, float, float]] = {}
        self._last_cmd: dict[str, tuple[str, float, float]] = {}
        # Active one-shot "cool to X" / "heat to X": kind, target, until (epoch s)
        self.override: dict | None = None
        # Ducted systems (e.g. AirTouch): zone-damper fans + their on/off
        # switches. fan entity_id -> enabled by the user (default open).
        self.damper_enabled: dict[str, bool] = {}
        self.damper_switches: dict[str, str] = {}

    @property
    def programs(self) -> list[dict]:
        return programs_from_options(self.cfg)

    async def async_set_programs_enabled(self, on: bool) -> None:
        self.programs_enabled = on
        await self.async_evaluate()

    def _current_program(self) -> dict | None:
        if not self.programs_enabled:
            return None
        on = {p["schedule"]: (self.hass.states.get(p["schedule"]) or SimpleState).state == "on" for p in self.programs}
        return pick_program(self.programs, on, datetime.now().month)

    @property
    def dampers(self) -> list[str]:
        return list(self.cfg.get(CONF_DAMPERS, []))

    @property
    def sensors(self) -> list[str]:
        return list(self.cfg.get(CONF_TEMP_SENSORS, []))

    @property
    def climates(self) -> list[str]:
        return list(self.cfg.get(CONF_CLIMATES, []))

    @property
    def heaters(self) -> list[str]:
        return list(self.cfg.get(CONF_HEATERS, []))

    @property
    def coolers(self) -> list[str]:
        return list(self.cfg.get(CONF_COOLERS, []))

    @property
    def guard_level(self) -> int:
        return max((g[0] for g in self._guards.values()), default=LEVEL_OK)

    @callback
    def async_setup(self) -> None:
        watch = self.sensors + self.climates + self.dampers + [p["schedule"] for p in self.programs]
        if watch:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, watch, lambda _e: self.hass.async_create_task(self.async_evaluate())
                )
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                lambda _n: self.hass.async_create_task(self.async_evaluate()),
                timedelta(seconds=TICK_SECONDS),
            )
        )
        self.hass.async_create_task(self.async_evaluate())

    # ---- user settings (called by select/number entities) ----
    async def async_set_mode(self, mode: str) -> None:
        self.mode = mode
        await self.async_evaluate()

    async def async_set_target(self, value: float) -> None:
        self.target = value
        await self.async_evaluate()

    async def async_set_deadband(self, value: float) -> None:
        self.deadband = value
        await self.async_evaluate()

    async def async_set_damper(self, fan_id: str, enabled: bool) -> None:
        self.damper_enabled[fan_id] = enabled
        await self.async_evaluate()

    # ---- one-shot override ("cool to X" / "heat to X") ----
    async def async_set_override(self, kind: str, target: float, minutes: float | None = None) -> None:
        """Run until the zone reaches `target`, then drop back to normal.
        `minutes` caps the run so a stuck sensor can never run it forever."""
        if kind not in (ACT_COOL, ACT_HEAT):
            raise ValueError("override kind must be 'cool' or 'heat'")
        cap = DEFAULT_OVERRIDE_MINUTES if not minutes else float(minutes)
        self.override = {"kind": kind, "target": float(target), "until": time.time() + cap * 60}
        self._last_cmd.clear()
        await self.async_evaluate()

    async def async_clear_override(self) -> None:
        if self.override is not None:
            self.override = None
            self._last_cmd.clear()
            await self.async_evaluate()

    def restore_override(self, data: dict | None) -> None:
        """Called on startup with the saved override attributes."""
        if data and data.get("until", 0) > time.time():
            self.override = {"kind": data["kind"], "target": float(data["target"]), "until": float(data["until"])}

    # ---- guard hooks ----
    async def async_set_guard(self, guard_id: str, level: int, warn_sp: float, crit_sp: float) -> None:
        prev = self.guard_level
        if level == LEVEL_OK:
            self._guards.pop(guard_id, None)
        else:
            self._guards[guard_id] = (level, warn_sp, crit_sp)
        if self.guard_level != prev or level != LEVEL_OK:
            await self.async_evaluate()

    async def async_clear_guard(self, guard_id: str) -> None:
        if self._guards.pop(guard_id, None) is not None:
            await self.async_evaluate()

    # ---- the loop ----
    async def _zone_temperature(self, now: float) -> tuple[float | None, str]:
        default_min = float(self.cfg.get(CONF_SENSOR_MAX_AGE, DEFAULT_SENSOR_MAX_AGE))
        max_age = default_min * 60
        t = average_fresh(await self._readings(self.sensors, default_min), now, max_age)
        if t is not None:
            return t, "sensors"
        # Fallback: the units' own readings (less accurate, but not blind).
        units = []
        for eid in self.climates:
            st = self.hass.states.get(eid)
            try:
                units.append((float(st.attributes.get("current_temperature")), now))  # type: ignore[union-attr, arg-type]
            except (TypeError, ValueError, AttributeError):
                pass
        t = average_fresh(units, now, max_age)
        return (t, "unit_fallback") if t is not None else (None, "none")

    async def async_evaluate(self) -> None:
        """One decision pass. Never raises: an error is logged and kept in
        `last_error` (shown on the Status sensor), and the entities are
        always refreshed so the reading can never freeze behind a failure."""
        try:
            await self._evaluate()
        except Exception as err:  # noqa: BLE001
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Zone %s: evaluation failed", self.slug)
        self._notify()

    async def _evaluate(self) -> None:
        now = time.time()
        temp, source = await self._zone_temperature(now)
        self.temperature, self.temp_source = temp, source

        level = self.guard_level
        # A scheduled program (schedule helper "on" + season match) replaces
        # the manual mode/target while it is active.
        self.active_program = self._current_program()
        mode = self.active_program["mode"] if self.active_program else self.mode
        target = self.active_program["target"] if self.active_program else self.target
        if level >= LEVEL_WARN:
            sp = min((g[2] if g[0] == LEVEL_CRITICAL else g[1]) for g in self._guards.values())
            target = min(target, sp)
            self.action = ACT_COOL  # guard forces cooling regardless of mode/temp
            self.status = "guard_critical" if level == LEVEL_CRITICAL else "guard_warn"
        elif self._override_active(temp):
            ov = self.override
            target = ov["target"]  # type: ignore[index]
            self.action = ov["kind"]  # type: ignore[index]
            self.status = "override_cool" if self.action == ACT_COOL else "override_heat"
        elif temp is None:
            self.action = ACT_IDLE
            self.status = "sensor_lost" if mode != "off" else "off"
        else:
            self.action = decide(temp, target, self.deadband, mode, self.action)
            self.status = {ACT_HEAT: "heating", ACT_COOL: "cooling"}.get(
                self.action, "off" if mode == "off" else "idle"
            )

        self.last_error = None
        await self._apply(target, temp, level)

    def _override_active(self, temp: float | None) -> bool:
        """True while an override should keep running; clears it when it is
        done (target reached) or timed out."""
        ov = self.override
        if ov is None:
            return False
        if time.time() >= ov["until"] or override_reached(ov["kind"], temp, ov["target"]):
            self.override = None
            self._last_cmd.clear()
            return False
        return True

    async def _apply(self, target: float, temp: float | None, level: int) -> None:
        # Each output is isolated: one failing command must not stop the
        # others (or the entity refresh) and is reported in last_error.
        jobs = (
            [(eid, self._set_switch(eid, self.action == ACT_HEAT)) for eid in self.heaters]
            + [(eid, self._set_switch(eid, self.action == ACT_COOL)) for eid in self.coolers]
            + [(eid, self._set_climate(eid, target, temp, level)) for eid in self.climates]
            + [("dampers", self._apply_dampers(level))]
        )
        for name, job in jobs:
            try:
                await job
            except Exception as err:  # noqa: BLE001
                self.last_error = f"{name}: {type(err).__name__}: {err}"
                _LOGGER.exception("Zone %s: command to %s failed", self.slug, name)

    async def _apply_dampers(self, level: int) -> None:
        """Open the dampers of enabled zones while the system is running.

        Tank guard (warn/critical) opens every damper. At least one damper
        is always kept open while running: a ducted unit must never push
        air into fully closed ducts. When the zone is idle/off the dampers
        are left alone.
        """
        if not self.dampers or (self.action == ACT_IDLE and level < LEVEL_WARN):
            return
        if level >= LEVEL_WARN:
            want = set(self.dampers)
        else:
            want = {d for d in self.dampers if self.damper_enabled.get(d, True)}
            if not want:
                want = {self.dampers[0]}
        for eid in self.dampers:
            st = self.hass.states.get(eid)
            if st is None or st.state in ("unavailable", "unknown"):
                continue
            is_on = st.state == "on"
            if eid in want and not is_on:
                await self.hass.services.async_call("fan", "turn_on", {"entity_id": eid}, blocking=False)
            elif eid not in want and is_on:
                await self.hass.services.async_call("fan", "turn_off", {"entity_id": eid}, blocking=False)

    async def _set_switch(self, eid: str, on: bool) -> None:
        st = self.hass.states.get(eid)
        if st is None or st.state in ("unavailable", "unknown"):
            return
        if (st.state == "on") == on:
            return
        await self.hass.services.async_call(
            "homeassistant", "turn_on" if on else "turn_off", {"entity_id": eid}, blocking=False
        )

    async def _set_climate(self, eid: str, target: float, temp: float | None, level: int) -> None:
        st = self.hass.states.get(eid)
        if st is None or st.state in ("unavailable", "unknown"):
            return
        want = {ACT_HEAT: "heat", ACT_COOL: "cool"}.get(self.action, "off")
        modes = st.attributes.get("hvac_modes") or []
        if want != "off" and want not in modes:
            _LOGGER.warning("%s does not support hvac mode %s", eid, want)
            return
        if want == "off" and "off" not in modes:
            return

        setpoint = corrected_setpoint(target, temp, st.attributes.get("current_temperature"))
        step = float(st.attributes.get("target_temp_step") or 0.5)
        setpoint = round(setpoint / step) * step
        lo, hi = st.attributes.get("min_temp"), st.attributes.get("max_temp")
        if lo is not None:
            setpoint = max(float(lo), setpoint)
        if hi is not None:
            setpoint = min(float(hi), setpoint)

        cur_sp = st.attributes.get("temperature")
        in_sync = st.state == want and (
            want == "off" or (cur_sp is not None and abs(float(cur_sp) - setpoint) < 0.1)
        )
        last = self._last_cmd.get(eid)
        now = time.time()
        if in_sync:
            self._last_cmd[eid] = (want, setpoint, now)
            return
        if last and last[0] == want and abs(last[1] - setpoint) < 0.1 and now - last[2] < RESEND_AFTER_S:
            return  # already asked recently; give the unit time to respond
        self._last_cmd[eid] = (want, setpoint, now)

        if st.state != want:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": eid, "hvac_mode": want}, blocking=False
            )
        if want != "off":
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": eid, "temperature": setpoint}, blocking=False
            )
            if level == LEVEL_CRITICAL:
                for fan in ("high", "turbo", "max", "strong"):
                    if fan in (st.attributes.get("fan_modes") or []):
                        await self.hass.services.async_call(
                            "climate", "set_fan_mode", {"entity_id": eid, "fan_mode": fan}, blocking=False
                        )
                        break


class GuardController(_Base):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry)
        c = self.cfg
        self.warn = float(c.get(CONF_WARN, DEFAULT_WARN))
        self.critical = float(c.get(CONF_CRITICAL, DEFAULT_CRITICAL))
        self.warn_sp = float(c.get(CONF_WARN_SETPOINT, DEFAULT_WARN_SETPOINT))
        self.crit_sp = float(c.get(CONF_CRITICAL_SETPOINT, DEFAULT_CRITICAL_SETPOINT))
        self.evaluator = GuardEvaluator(
            warn=self.warn,
            critical=self.critical,
            hysteresis=float(c.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS)),
            stale_after_s=float(c.get(CONF_STALE_MIN, DEFAULT_STALE_MIN)) * 60,
        )
        self.result = GuardResult(LEVEL_OK, None, None, "ok")
        self._notified_level = LEVEL_OK
        self.last_error: str | None = None

    @property
    def tank_sensors(self) -> list[str]:
        return list(self.cfg.get(CONF_TANK_SENSORS, []))

    @property
    def zone_ids(self) -> list[str]:
        return list(self.cfg.get(CONF_ZONES, []))

    @callback
    def async_setup(self) -> None:
        if self.tank_sensors:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, self.tank_sensors, lambda _e: self.hass.async_create_task(self.async_evaluate())
                )
            )
        # The periodic tick is what notices a sensor going silent.
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                lambda _n: self.hass.async_create_task(self.async_evaluate()),
                timedelta(seconds=TICK_SECONDS),
            )
        )
        self.hass.async_create_task(self.async_evaluate())

    def _zones(self) -> list[ZoneController]:
        found = []
        for zid in self.zone_ids:
            z = self.hass.data.get(DOMAIN, {}).get(zid)
            if isinstance(z, ZoneController):
                found.append(z)
        return found

    async def async_evaluate(self) -> None:
        try:
            await self._evaluate()
        except Exception as err:  # noqa: BLE001
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Guard %s: evaluation failed", self.slug)
        self._notify()

    async def _evaluate(self) -> None:
        default_min = float(self.cfg.get(CONF_STALE_MIN, DEFAULT_STALE_MIN))
        self.result = self.evaluator.update(time.time(), await self._readings(self.tank_sensors, default_min))
        self.last_error = None
        # Re-pushed every tick (idempotent) so zones that load later or
        # reload still pick the level up. One zone failing never blocks the rest.
        for z in self._zones():
            try:
                await z.async_set_guard(self.entry.entry_id, self.result.level, self.warn_sp, self.crit_sp)
            except Exception as err:  # noqa: BLE001
                self.last_error = f"zone {z.slug}: {type(err).__name__}: {err}"
                _LOGGER.exception("Guard %s: pushing level to %s failed", self.slug, z.slug)
        await self._maybe_notify()

    async def _maybe_notify(self) -> None:
        level = self.result.level
        if level == self._notified_level:
            return
        prev, self._notified_level = self._notified_level, level
        svc = (self.cfg.get(CONF_NOTIFY) or "").strip()
        if not svc or "." not in svc:
            return
        domain, name = svc.split(".", 1)
        r = self.result
        if level > prev:
            msg = (
                f"No fresh tank temperature data - treating as CRITICAL."
                if r.reason == "no_data"
                else f"Tank water {r.hottest:.1f}C ({r.reason}); cooling the house."
            )
            title = "TANK TEMPERATURE CRITICAL" if level == LEVEL_CRITICAL else "Tank temperature warning"
        else:
            msg = f"Tank water back to {r.hottest:.1f}C; level now {r.name}." if r.hottest is not None else "Recovered."
            title = "Tank temperature recovering"
        try:
            await self.hass.services.async_call(domain, name, {"title": title, "message": msg}, blocking=False)
        except Exception:  # noqa: BLE001 - a bad notify target must never break protection
            _LOGGER.exception("Guard notification via %s failed", svc)

    @callback
    def async_shutdown(self) -> None:
        super().async_shutdown()
        for z in self._zones():
            self.hass.async_create_task(z.async_clear_guard(self.entry.entry_id))
