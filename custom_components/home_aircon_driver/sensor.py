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
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .controllers import GuardController, ZoneController
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController):
        async_add_entities([ZoneTemperature(ctrl), ZoneStatus(ctrl), ZoneOverride(ctrl)])
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
    _attr_options = ["off", "idle", "heating", "cooling", "guard_warn", "guard_critical", "sensor_lost", "override_cool", "override_heat"]

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "sensor", "status")

    @property
    def native_value(self) -> str:
        return self.ctrl.status

    @property
    def extra_state_attributes(self) -> dict:
        p = self.ctrl.active_program
        attrs: dict = {"damper_switches": list(self.ctrl.damper_switches.values())}
        if self.ctrl.last_error:
            attrs["last_error"] = self.ctrl.last_error
        if p:
            attrs["program"] = {"mode": p["mode"], "target": p["target"], "schedule": p["schedule"]}
            st = self.ctrl.hass.states.get(p["schedule"])
            if st is not None:
                attrs["program"]["next_event"] = st.attributes.get("next_event")
        return attrs


class ZoneOverride(AirconEntity, SensorEntity, RestoreEntity):
    """none | cool | heat, with target/until attributes. Restored on restart
    so a running override survives a reboot (until its own deadline)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["none", "cool", "heat"]

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "sensor", "override")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("cool", "heat"):
            a = last.attributes
            try:
                self.ctrl.restore_override(
                    {"kind": last.state, "target": a.get("target"), "until": float(a.get("until_ts", 0))}
                )
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self) -> str:
        ov = self.ctrl.override
        return ov["kind"] if ov else "none"

    @property
    def extra_state_attributes(self) -> dict:
        ov = self.ctrl.override
        if not ov:
            return {}
        from datetime import datetime, timezone

        return {
            "target": ov["target"],
            "until_ts": ov["until"],
            "until": datetime.fromtimestamp(ov["until"], tz=timezone.utc).isoformat(),
        }


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
            "last_error": self.ctrl.last_error,
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
