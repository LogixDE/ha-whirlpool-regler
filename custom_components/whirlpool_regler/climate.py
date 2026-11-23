"""Thermostat of the Whirlpool-Regler: the real target per watercare mode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WhirlpoolConfigEntry
from .entity import RegelEntity
from .logic import PROFILES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RegelClimate(entry.runtime_data)])


class RegelClimate(RegelEntity, ClimateEntity):
    """HEAT = Regler active, OFF = Regler paused (Gecko keeps heating).

    Presets are the Regler's own profiles (eco, baden); the target is the one
    of the active profile.
    """

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = PROFILES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "regler")

    @property
    def available(self) -> bool:
        return self.runtime.available

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.runtime.regler.settings.active else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        action = self.runtime.gecko_hvac_action
        if action is None:
            return None
        try:
            return HVACAction(action)
        except ValueError:
            return None

    @property
    def current_temperature(self) -> float | None:
        return self.runtime.water

    @property
    def target_temperature(self) -> float | None:
        return self.runtime.regler.target

    @property
    def min_temp(self) -> float:
        return self.runtime.regler.settings.min_temp

    @property
    def max_temp(self) -> float:
        return self.runtime.regler.settings.max_temp

    @property
    def target_temperature_step(self) -> float:
        return self.runtime.regler.settings.step

    @property
    def preset_mode(self) -> str:
        return self.runtime.regler.profile

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.runtime.decision
        return {
            "regelphase": d.phase,
            "gecko_sollwert": self.runtime.gecko_setpoint,
            "geplanter_sollwert": d.desired,
            "abgesenkt": self.runtime.regler.lowered,
            "eco_temperatur": self.runtime.regler.targets["eco"],
            "badetemperatur": self.runtime.regler.targets["baden"],
            "heizstab_erlaubt": not self.runtime.regler.heater_locked,
            "watercare_modus": self.runtime.mode,
            "heat_source": self.runtime.heat_source,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.runtime.async_set_target(float(temperature))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.runtime.async_set_profile(preset_mode)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.runtime.async_set_setting("active", hvac_mode != HVACMode.OFF)
