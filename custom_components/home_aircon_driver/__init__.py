"""Home Aircon Driver — heating/cooling orchestration without YAML.

Two kinds of config entry: a Zone (room sensors + climate/switch outputs +
a thermostat loop) and a Tank Guard (tank-water temperature protection that
forces cooling in the zones it protects).
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import CONF_KIND, DOMAIN, KIND_GUARD
from .controllers import GuardController, ZoneController
from .frontend import async_register_card

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    is_guard = {**entry.data, **entry.options}.get(CONF_KIND) == KIND_GUARD
    ctrl = GuardController(hass, entry) if is_guard else ZoneController(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ctrl

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start the loop after entities have restored their saved settings.
    ctrl.async_setup()
    await async_register_card(hass)
    _async_register_services(hass)
    return True


def _find_zone(hass: HomeAssistant, slug: str) -> ZoneController:
    for ctrl in hass.data.get(DOMAIN, {}).values():
        if isinstance(ctrl, ZoneController) and ctrl.slug == slug:
            return ctrl
    raise ValueError(f"No Home Aircon Driver zone with slug '{slug}'")


def _async_register_services(hass: HomeAssistant) -> None:
    """Domain-wide services used by the card's "cool to / heat to" override
    (registered once, idempotent)."""
    if hass.data.get("home_aircon_driver_services_registered"):
        return
    hass.data["home_aircon_driver_services_registered"] = True

    async def _set_override(call) -> None:
        zone = _find_zone(hass, call.data["zone"])
        await zone.async_set_override(
            call.data["mode"], call.data["target"], call.data.get("max_minutes")
        )

    async def _clear_override(call) -> None:
        await _find_zone(hass, call.data["zone"]).async_clear_override()

    hass.services.async_register(
        DOMAIN,
        "set_override",
        _set_override,
        schema=vol.Schema(
            {
                vol.Required("zone"): cv.string,
                vol.Required("mode"): vol.In(["cool", "heat"]),
                vol.Required("target"): vol.All(vol.Coerce(float), vol.Range(min=10, max=32)),
                vol.Optional("max_minutes"): vol.All(vol.Coerce(float), vol.Range(min=5, max=720)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, "clear_override", _clear_override, schema=vol.Schema({vol.Required("zone"): cv.string})
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id).async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
