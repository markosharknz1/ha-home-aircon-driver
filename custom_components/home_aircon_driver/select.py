"""Zone mode selector (off / heat / cool / auto)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MODES
from .controllers import ZoneController
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController):
        async_add_entities([ZoneModeSelect(ctrl)])


class ZoneModeSelect(AirconEntity, SelectEntity, RestoreEntity):
    _attr_options = MODES

    def __init__(self, ctrl: ZoneController) -> None:
        super().__init__(ctrl, "select", "mode")

    @property
    def current_option(self) -> str:
        return self.ctrl.mode

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in MODES:
            self.ctrl.mode = last.state

    async def async_select_option(self, option: str) -> None:
        await self.ctrl.async_set_mode(option)
