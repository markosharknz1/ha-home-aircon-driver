"""Base entity: one HA device per config entry, slug-prefixed entity_ids."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controllers import GuardController, ZoneController


class AirconEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, ctrl: ZoneController | GuardController, domain: str, suffix: str) -> None:
        self.ctrl = ctrl
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{suffix}"
        # Pre-assigned entity_id works on old and new HA cores (see the
        # reef pack: 2026.7 removed _attr_suggested_object_id).
        self.entity_id = f"{domain}.{ctrl.slug}_{suffix}"
        self._attr_translation_key = suffix
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ctrl.entry.entry_id)},
            name=ctrl.entry.title,
            manufacturer="Home Aircon Driver",
            model="Tank guard" if isinstance(ctrl, GuardController) else "Zone",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.ctrl.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
