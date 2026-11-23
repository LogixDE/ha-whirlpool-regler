"""Hysteresis switch of the Whirlpool-Regler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .entity import RegelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HysteresisSwitch(entry.runtime_data)])


class HysteresisSwitch(RegelEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "hysterese_aktiv")

    @property
    def is_on(self) -> bool:
        return self.runtime.regler.settings.hysteresis_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.async_set_setting("hysteresis_enabled", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_set_setting("hysteresis_enabled", False)
