"""Dropout (problem) sensor for each zone and tank guard."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DropoutSensor(hass.data[DOMAIN][entry.entry_id])])


class DropoutSensor(AirconEntity, BinarySensorEntity):
    """On while any sensor, aircon unit, switch or damper this entry depends on
    has dropped out (unavailable, silent past its limit, or not obeying)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, ctrl) -> None:
        super().__init__(ctrl, "binary_sensor", "dropout")

    @property
    def is_on(self) -> bool:
        return bool(self.ctrl.problems)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "count": len(self.ctrl.problems),
            "problems": [
                {
                    "entity": p["entity"],
                    "name": p["name"],
                    "role": p["role"],
                    "kind": p["kind"],
                    "detail": p.get("detail"),
                    "since": datetime.fromtimestamp(p["since"], tz=timezone.utc).isoformat(),
                }
                for p in self.ctrl.problems
            ],
        }
