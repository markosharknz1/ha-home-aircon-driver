"""Home Aircon Driver — heating/cooling orchestration without YAML.

Two kinds of config entry: a Zone (room sensors + climate/switch outputs +
a thermostat loop) and a Tank Guard (tank-water temperature protection that
forces cooling in the zones it protects).
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_KIND, DOMAIN, KIND_GUARD
from .controllers import GuardController, ZoneController

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    is_guard = {**entry.data, **entry.options}.get(CONF_KIND) == KIND_GUARD
    ctrl = GuardController(hass, entry) if is_guard else ZoneController(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ctrl

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start the loop after entities have restored their saved settings.
    ctrl.async_setup()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id).async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
