"""Whirlpool-Regler: control layer on top of the Gecko spa integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .runtime import WhirlpoolRuntime

type WhirlpoolConfigEntry = ConfigEntry[WhirlpoolRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: WhirlpoolConfigEntry) -> bool:
    runtime = WhirlpoolRuntime(hass, entry)
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WhirlpoolConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: WhirlpoolConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
