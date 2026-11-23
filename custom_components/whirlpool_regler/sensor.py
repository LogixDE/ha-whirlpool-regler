"""Control phase of the Whirlpool-Regler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .entity import RegelEntity
from .logic import PHASES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PhaseSensor(entry.runtime_data)])


class PhaseSensor(RegelEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PHASES

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "regelphase")

    @property
    def native_value(self) -> str:
        return self.runtime.decision.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.runtime.decision
        return {
            "zieltemperatur": d.target,
            "geplanter_sollwert": d.desired,
            "gecko_sollwert": self.runtime.gecko_setpoint,
            "watercare_modus": self.runtime.mode,
        }
