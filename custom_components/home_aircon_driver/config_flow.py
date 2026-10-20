"""Config flow: one entry per Zone or Tank Guard, all via entity pickers.

Hardware picks live in entry.options (via the OptionsFlow) layered over
entry.data, so a sensor can be swapped later without recreating the entry.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CLIMATES,
    CONF_COOLERS,
    CONF_DAMPERS,
    CONF_CRITICAL,
    CONF_CRITICAL_SETPOINT,
    CONF_HEATERS,
    CONF_HYSTERESIS,
    CONF_KIND,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_SENSOR_MAX_AGE,
    CONF_SLUG,
    CONF_STALE_MIN,
    CONF_TANK_SENSORS,
    CONF_TEMP_SENSORS,
    CONF_WARN,
    CONF_WARN_SETPOINT,
    CONF_ZONES,
    DEFAULT_CRITICAL,
    DEFAULT_CRITICAL_SETPOINT,
    DEFAULT_HYSTERESIS,
    DEFAULT_SENSOR_MAX_AGE,
    DEFAULT_STALE_MIN,
    DEFAULT_WARN,
    DEFAULT_WARN_SETPOINT,
    DOMAIN,
    KIND_GUARD,
    KIND_ZONE,
)

TEMP_SENSORS = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=True)
)
CLIMATES = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate", multiple=True))
FANS = selector.EntitySelector(selector.EntitySelectorConfig(domain="fan", multiple=True))
SWITCHES = selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", multiple=True))


def _num(lo: float, hi: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX
        )
    )


def _opt(key: str, d: dict, default: Any = None) -> vol.Optional:
    """Optional field that pre-fills from the current value when present."""
    val = d.get(key, default)
    return vol.Optional(key, description={"suggested_value": val}) if val is not None else vol.Optional(key)


def _zone_schema(d: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_TEMP_SENSORS, default=d.get(CONF_TEMP_SENSORS, [])): TEMP_SENSORS,
            _opt(CONF_CLIMATES, d): CLIMATES,
            _opt(CONF_HEATERS, d): SWITCHES,
            _opt(CONF_COOLERS, d): SWITCHES,
            _opt(CONF_DAMPERS, d): FANS,
            vol.Required(
                CONF_SENSOR_MAX_AGE, default=d.get(CONF_SENSOR_MAX_AGE, DEFAULT_SENSOR_MAX_AGE)
            ): _num(5, 240, 5, "min"),
        }
    )


def _guard_schema(hass, d: dict) -> vol.Schema:
    zone_opts = [
        selector.SelectOptionDict(value=e.entry_id, label=e.title)
        for e in hass.config_entries.async_entries(DOMAIN)
        if {**e.data, **e.options}.get(CONF_KIND) == KIND_ZONE
    ]
    return vol.Schema(
        {
            vol.Required(CONF_TANK_SENSORS, default=d.get(CONF_TANK_SENSORS, [])): TEMP_SENSORS,
            vol.Required(CONF_ZONES, default=d.get(CONF_ZONES, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=zone_opts, multiple=True)
            ),
            vol.Required(CONF_WARN, default=d.get(CONF_WARN, DEFAULT_WARN)): _num(15, 35, 0.1, "°C"),
            vol.Required(CONF_CRITICAL, default=d.get(CONF_CRITICAL, DEFAULT_CRITICAL)): _num(15, 35, 0.1, "°C"),
            vol.Required(CONF_WARN_SETPOINT, default=d.get(CONF_WARN_SETPOINT, DEFAULT_WARN_SETPOINT)): _num(
                10, 30, 0.5, "°C"
            ),
            vol.Required(
                CONF_CRITICAL_SETPOINT, default=d.get(CONF_CRITICAL_SETPOINT, DEFAULT_CRITICAL_SETPOINT)
            ): _num(10, 30, 0.5, "°C"),
            vol.Required(CONF_HYSTERESIS, default=d.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS)): _num(
                0.1, 3, 0.1, "°C"
            ),
            vol.Required(CONF_STALE_MIN, default=d.get(CONF_STALE_MIN, DEFAULT_STALE_MIN)): _num(
                5, 120, 5, "min"
            ),
            _opt(CONF_NOTIFY, d): selector.TextSelector(),
        }
    )


def _guard_errors(user_input: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    if float(user_input[CONF_CRITICAL]) <= float(user_input[CONF_WARN]):
        errors[CONF_CRITICAL] = "critical_below_warn"
    if not user_input.get(CONF_TANK_SENSORS):
        errors[CONF_TANK_SENSORS] = "need_sensor"
    return errors


class HomeAirconDriverConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._name = ""
        self._slug = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["zone", "guard"])

    async def _name_step(self, step_id: str, kind: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = slugify(user_input[CONF_NAME])
            existing = {
                {**e.data, **e.options}.get(CONF_SLUG) for e in self._async_current_entries()
            }
            if not slug:
                errors[CONF_NAME] = "bad_name"
            elif slug in existing:
                errors[CONF_NAME] = "name_exists"
            else:
                self._name, self._slug = user_input[CONF_NAME], slug
                return await (self.async_step_zone_hw() if kind == KIND_ZONE else self.async_step_guard_hw())
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_NAME): str}),
            errors=errors,
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._name_step("zone", KIND_ZONE, user_input)

    async def async_step_guard(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._name_step("guard", KIND_GUARD, user_input)

    async def async_step_zone_hw(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_TEMP_SENSORS):
                errors[CONF_TEMP_SENSORS] = "need_sensor"
            elif not any(user_input.get(k) for k in (CONF_CLIMATES, CONF_HEATERS, CONF_COOLERS)):
                errors["base"] = "need_output"
            else:
                return self.async_create_entry(
                    title=self._name,
                    data={CONF_KIND: KIND_ZONE, CONF_NAME: self._name, CONF_SLUG: self._slug},
                    options=user_input,
                )
        return self.async_show_form(
            step_id="zone_hw", data_schema=_zone_schema(user_input or {}), errors=errors
        )

    async def async_step_guard_hw(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _guard_errors(user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._name,
                    data={CONF_KIND: KIND_GUARD, CONF_NAME: self._name, CONF_SLUG: self._slug},
                    options=user_input,
                )
        return self.async_show_form(
            step_id="guard_hw", data_schema=_guard_schema(self.hass, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HomeAirconDriverOptionsFlow()


class HomeAirconDriverOptionsFlow(OptionsFlow):
    """Rewire sensors/outputs/thresholds later (e.g. after replacing a sensor)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        is_guard = current.get(CONF_KIND) == KIND_GUARD
        errors: dict[str, str] = {}
        if user_input is not None:
            if is_guard:
                errors = _guard_errors(user_input)
            elif not user_input.get(CONF_TEMP_SENSORS):
                errors[CONF_TEMP_SENSORS] = "need_sensor"
            elif not any(user_input.get(k) for k in (CONF_CLIMATES, CONF_HEATERS, CONF_COOLERS)):
                errors["base"] = "need_output"
            if not errors:
                return self.async_create_entry(data=user_input)
        schema = _guard_schema(self.hass, current) if is_guard else _zone_schema(current)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
