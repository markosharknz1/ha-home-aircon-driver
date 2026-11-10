"""A real thermostat entity for each zone.

It shows the zone's own temperature (your accurate sensors) and the driver's
target, so Mushroom/thermostat cards, Google Home and Alexa see the zone like
any other thermostat. The aircon unit keeps its own entity, which shows the
offset-corrected setpoint the driver sends it.
"""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controllers import ZoneController
from .entity import AirconEntity

# driver mode <-> HA hvac mode ("auto" = heat and cool as needed)
TO_HVAC = {"off": HVACMode.OFF, "heat": HVACMode.HEAT, "cool": HVACMode.COOL, "auto": HVACMode.HEAT_COOL}
FROM_HVAC = {v: k for k, v in TO_HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController):
        async_add_entities([ZoneThermostat(ctrl)])


class ZoneThermostat(AirconEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 10
    _attr_max_temp = 32
    _attr_target_temperature_step = 1
    _attr_precision = 0.1
    _enable_turn_on_off_backwards_compat = False

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "climate", "thermostat")

    # What the zone is really doing: boost > guard > program > manual settings.
    @property
    def hvac_mode(self) -> HVACMode:
        return TO_HVAC.get(self.ctrl.eff_mode, HVACMode.OFF)

    @property
    def current_temperature(self) -> float | None:
        t = self.ctrl.temperature
        return None if t is None else round(t, 1)

    @property
    def target_temperature(self) -> float:
        return float(round(self.ctrl.eff_target))

    @property
    def hvac_action(self) -> HVACAction:
        if self.ctrl.action == "heat":
            return HVACAction.HEATING
        if self.ctrl.action == "cool":
            return HVACAction.COOLING
        return HVACAction.OFF if self.hvac_mode == HVACMode.OFF else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "status": self.ctrl.status,
            "manual_mode": self.ctrl.mode,
            "manual_target": self.ctrl.target,
            "deadband": self.ctrl.deadband,
            "program_active": self.ctrl.active_program is not None,
            "boost_active": self.ctrl.override is not None,
            "temperature_source": self.ctrl.temp_source,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.ctrl.async_set_mode(FROM_HVAC[hvac_mode])

    async def async_set_temperature(self, **kwargs) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.ctrl.async_set_target(float(round(temp)))
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.ctrl.async_set_mode(FROM_HVAC[mode])

    async def async_turn_on(self) -> None:
        # "On" restores a working mode: auto if the zone was off.
        if self.ctrl.mode == "off":
            await self.ctrl.async_set_mode("auto")

    async def async_turn_off(self) -> None:
        await self.ctrl.async_set_mode("off")
