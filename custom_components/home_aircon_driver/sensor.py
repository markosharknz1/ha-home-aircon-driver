"""Zone temperature/status sensors and Tank Guard level/hottest sensors."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controllers import GuardController, ZoneController
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController):
        async_add_entities([ZoneTemperature(ctrl), ZoneStatus(ctrl)])
    elif isinstance(ctrl, GuardController):
        async_add_entities([GuardLevel(ctrl), GuardHottest(ctrl)])


class ZoneTemperature(AirconEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "sensor", "temperature")

    @property
    def native_value(self) -> float | None:
        t = self.ctrl.temperature
        return None if t is None else round(t, 2)

    @property
    def extra_state_attributes(self) -> dict:
        return {"source": self.ctrl.temp_source}


class ZoneStatus(AirconEntity, SensorEntity):
    """off | idle | heating | cooling | guard_warn | guard_critical | sensor_lost"""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "idle", "heating", "cooling", "guard_warn", "guard_critical", "sensor_lost"]

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "sensor", "status")

    @property
    def native_value(self) -> str:
        return self.ctrl.status


class GuardLevel(AirconEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "warn", "critical"]

    def __init__(self, ctrl: GuardController) -> None:
        super().__init__(ctrl, "sensor", "level")

    @property
    def native_value(self) -> str:
        return self.ctrl.result.name

    @property
    def extra_state_attributes(self) -> dict:
        r = self.ctrl.result
        return {
            "reason": r.reason,
            "hottest": r.hottest,
            "rise_per_hour": None if r.rise_per_hour is None else round(r.rise_per_hour, 2),
            "warn_temp": self.ctrl.warn,
            "critical_temp": self.ctrl.critical,
            "protecting": self.ctrl.zone_ids,
        }


class GuardHottest(AirconEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, ctrl: GuardController) -> None:
        super().__init__(ctrl, "sensor", "hottest")

    @property
    def native_value(self) -> float | None:
        h = self.ctrl.result.hottest
        return None if h is None else round(h, 2)
