"""Zone target temperature and deadband."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .controllers import ZoneController
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController):
        async_add_entities([TargetNumber(ctrl), DeadbandNumber(ctrl)])


class _ZoneNumber(AirconEntity, NumberEntity, RestoreEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    attr_name = ""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable"):
            try:
                setattr(self.ctrl, self.attr_name, float(last.state))
            except ValueError:
                pass

    @property
    def native_value(self) -> float:
        return getattr(self.ctrl, self.attr_name)


class TargetNumber(_ZoneNumber):
    _attr_native_min_value = 10
    _attr_native_max_value = 32
    _attr_native_step = 0.5
    attr_name = "target"

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "number", "target")

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_target(value)


class DeadbandNumber(_ZoneNumber):
    _attr_native_min_value = 0.2
    _attr_native_max_value = 4
    _attr_native_step = 0.1
    _attr_entity_category = EntityCategory.CONFIG
    attr_name = "deadband"

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "number", "deadband")

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_deadband(value)
