"""Base entity of the Whirlpool-Regler."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .runtime import WhirlpoolRuntime


class RegelEntity(Entity):
    """Entity that follows the runtime's evaluations."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: WhirlpoolRuntime, key: str) -> None:
        self.runtime = runtime
        entry = runtime.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="LogixDE",
            model="Whirlpool-Regler",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.runtime.add_listener(self.async_write_ha_state))
