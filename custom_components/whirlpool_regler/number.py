"""Tuning values of the Whirlpool-Regler."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .entity import RegelEntity


@dataclass(frozen=True)
class _Spec:
    key: str
    setting: str
    minimum: float
    maximum: float
    step: float


SPECS = (
    # The span stays at most 2 K: a larger gap makes Smart Heat use the heater.
    _Spec("hysterese", "span", 0.5, 2.0, 0.5),
    _Spec("heizstab_abstand", "heater_gap", 0.5, 2.0, 0.5),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(RegelNumber(entry.runtime_data, spec) for spec in SPECS)


class RegelNumber(RegelEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTemperature.KELVIN
    _attr_mode = NumberMode.BOX

    def __init__(self, runtime, spec: _Spec) -> None:
        super().__init__(runtime, spec.key)
        self._spec = spec
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step

    @property
    def native_value(self) -> float:
        return getattr(self.runtime.regler.settings, self._spec.setting)

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_set_setting(self._spec.setting, float(value))
