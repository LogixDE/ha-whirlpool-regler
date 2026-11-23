"""Heater (Heizstab) release of the Whirlpool-Regler."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .const import HEATER_ALLOWED, HEATER_LOCKED, HEATER_OPTIONS
from .entity import RegelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HeaterSelect(entry.runtime_data)])


class HeaterSelect(RegelEntity, SelectEntity):
    """erlaubt = Gecko may use the heater, gesperrt = keep the gap small."""

    _attr_options = HEATER_OPTIONS

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "heizstab")

    @property
    def current_option(self) -> str:
        return HEATER_LOCKED if self.runtime.regler.settings.heater_locked else HEATER_ALLOWED

    async def async_select_option(self, option: str) -> None:
        await self.runtime.async_set_setting("heater_locked", option == HEATER_LOCKED)
