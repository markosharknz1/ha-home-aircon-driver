"""Per-zone damper switches for ducted systems (e.g. AirTouch zones).

Each switch says "open this zone's damper when the system runs". The
controller does the actual fan commands; the guard overrides them all open.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .controllers import ZoneController
from .entity import AirconEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = hass.data[DOMAIN][entry.entry_id]
    if isinstance(ctrl, ZoneController) and ctrl.dampers:
        async_add_entities([DamperSwitch(ctrl, fan_id) for fan_id in ctrl.dampers])


class DamperSwitch(AirconEntity, SwitchEntity, RestoreEntity):
    def __init__(self, ctrl: ZoneController, fan_id: str) -> None:
        suffix = "damper_" + slugify(fan_id.split(".", 1)[1])
        super().__init__(ctrl, "switch", suffix)
        self.fan_id = fan_id
        self._attr_translation_key = None
        ctrl.damper_switches[fan_id] = self.entity_id

    @property
    def name(self) -> str:
        st = self.ctrl.hass.states.get(self.fan_id)
        fan_name = st.attributes.get("friendly_name") if st else None
        return f"{fan_name or self.fan_id} damper"

    @property
    def is_on(self) -> bool:
        return self.ctrl.damper_enabled.get(self.fan_id, True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.ctrl.damper_enabled[self.fan_id] = last.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        await self.ctrl.async_set_damper(self.fan_id, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.ctrl.async_set_damper(self.fan_id, False)
