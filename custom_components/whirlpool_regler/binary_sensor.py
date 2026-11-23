"""Shows whether the spa's electric heater is running."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .entity import RegelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HeaterRunning(entry.runtime_data)])


class HeaterRunning(RegelEntity, BinarySensorEntity):
    """On while the Gecko climate reports a heat source with 'electric'."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "heizstab_aktiv")

    @property
    def available(self) -> bool:
        return self.runtime.heat_source is not None

    @property
    def is_on(self) -> bool | None:
        source = self.runtime.heat_source
        return None if source is None else "electric" in str(source)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"heat_source": self.runtime.heat_source}
