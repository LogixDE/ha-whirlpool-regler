"""Glue between Home Assistant and the control logic."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SERVICE_SELECT_OPTION
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_OPTION,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_HYSTERESIS_MODES,
    CONF_WATER_SENSOR,
    CONF_WATERCARE,
    DEFAULT_HYSTERESIS_MODES,
    DOMAIN,
    EVAL_INTERVAL_SECONDS,
    NON_SELECTABLE_MODES,
    STORAGE_VERSION,
)
from .logic import Decision, Inputs, PHASE_WAITING, Regler, Settings

_LOGGER = logging.getLogger(__name__)

_BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)

_SETTING_KEYS = ("active", "hysteresis_enabled", "span", "heater_locked", "heater_gap")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class WhirlpoolRuntime:
    """One Regler instance per config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        conf = {**entry.data, **entry.options}
        self.climate_id: str = conf[CONF_CLIMATE]
        self.watercare_id: str = conf[CONF_WATERCARE]
        self.water_sensor_id: str | None = conf.get(CONF_WATER_SENSOR) or None
        modes = conf.get(CONF_HYSTERESIS_MODES, DEFAULT_HYSTERESIS_MODES)
        self.regler = Regler(Settings(hysteresis_modes=frozenset(modes)))
        self.decision = Decision(phase=PHASE_WAITING)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()

    # --- lifecycle ----------------------------------------------------------

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.regler.load(data)
        s = self.regler.settings
        for key in _SETTING_KEYS:
            if key in data.get("settings", {}):
                setattr(s, key, type(getattr(s, key))(data["settings"][key]))

        watched = [self.climate_id, self.watercare_id]
        if self.water_sensor_id:
            watched.append(self.water_sensor_id)
        self._unsubs.append(
            async_track_state_change_event(self.hass, watched, self._on_state_change)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._on_interval,
                timedelta(seconds=EVAL_INTERVAL_SECONDS),
            )
        )
        await self.async_evaluate()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data())

    # --- listeners ----------------------------------------------------------

    @callback
    def add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def _on_state_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    async def _on_interval(self, _now: Any) -> None:
        await self.async_evaluate()

    # --- reading the spa ----------------------------------------------------

    @property
    def climate_state(self) -> State | None:
        return self.hass.states.get(self.climate_id)

    @property
    def mode(self) -> str | None:
        state = self.hass.states.get(self.watercare_id)
        if state is None or state.state in _BAD_STATES:
            return None
        return state.state

    @property
    def mode_options(self) -> list[str]:
        state = self.hass.states.get(self.watercare_id)
        options = state.attributes.get("options", []) if state else []
        return [o for o in options if o not in NON_SELECTABLE_MODES]

    @property
    def water(self) -> float | None:
        if self.water_sensor_id:
            state = self.hass.states.get(self.water_sensor_id)
            if state is not None and state.state not in _BAD_STATES:
                value = _float(state.state)
                if value is not None:
                    return value
        climate = self.climate_state
        if climate is None or climate.state in _BAD_STATES:
            return None
        return _float(climate.attributes.get(ATTR_CURRENT_TEMPERATURE))

    def climate_attr(self, key: str) -> Any:
        climate = self.climate_state
        if climate is None or climate.state in _BAD_STATES:
            return None
        return climate.attributes.get(key)

    @property
    def gecko_setpoint(self) -> float | None:
        return _float(self.climate_attr(ATTR_TEMPERATURE))

    @property
    def gecko_hvac_action(self) -> str | None:
        return self.climate_attr(ATTR_HVAC_ACTION)

    @property
    def heat_source(self) -> str | None:
        return self.climate_attr("heat_source")

    @property
    def available(self) -> bool:
        climate = self.climate_state
        return (
            climate is not None
            and climate.state not in _BAD_STATES
            and self.mode is not None
        )

    def _inputs(self) -> Inputs:
        climate = self.climate_state
        if climate is not None and climate.state not in _BAD_STATES:
            s = self.regler.settings
            s.min_temp = _float(climate.attributes.get(ATTR_MIN_TEMP)) or s.min_temp
            s.max_temp = _float(climate.attributes.get(ATTR_MAX_TEMP)) or s.max_temp
            s.step = _float(climate.attributes.get(ATTR_TARGET_TEMP_STEP)) or s.step
        return Inputs(
            now=dt_util.utcnow().timestamp(),
            mode=self.mode,
            water=self.water,
            setpoint=self.gecko_setpoint,
            available=self.available,
        )

    # --- evaluation ---------------------------------------------------------

    async def async_evaluate(self, *, force: bool = False) -> None:
        async with self._lock:
            await self._async_evaluate(force=force)

    async def _async_evaluate(self, *, force: bool) -> None:
        before = self.regler.as_dict()
        self.decision = self.regler.update(self._inputs(), force=force)
        if self.decision.adopted_external:
            _LOGGER.info(
                "Setpoint %s of mode %s was changed outside the Regler; "
                "taken over as the new target",
                self.decision.target,
                self.mode,
            )
        if self.decision.write is not None:
            await self._async_write(self.decision.write)
        if self.regler.as_dict() != before:
            self._schedule_save()
        self._notify()

    async def _async_write(self, temperature: float) -> None:
        _LOGGER.debug(
            "Writing setpoint %s (mode %s, phase %s)",
            temperature,
            self.mode,
            self.decision.phase,
        )
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: self.climate_id, ATTR_TEMPERATURE: temperature},
                blocking=True,
            )
        except HomeAssistantError as err:
            # The logic retries when the spa keeps reporting the old value.
            _LOGGER.warning("Could not write setpoint %s: %s", temperature, err)

    # --- commands from the entities -----------------------------------------

    async def async_set_target(self, temperature: float) -> None:
        mode = self.mode
        if mode is None:
            raise HomeAssistantError("Watercare-Modus ist gerade nicht verfügbar")
        self.regler.set_target(mode, temperature)
        self._schedule_save()
        await self.async_evaluate(force=True)

    async def async_set_mode(self, mode: str) -> None:
        await self.hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: self.watercare_id, ATTR_OPTION: mode},
            blocking=True,
        )

    async def async_set_setting(self, key: str, value: Any) -> None:
        s = self.regler.settings
        was_active = s.active
        setattr(s, key, value)
        self._schedule_save()
        if key == "active" and was_active and not value:
            restore = self.regler.pause_restore(self._inputs())
            if restore is not None:
                await self._async_write(restore)
        await self.async_evaluate(force=True)

    # --- persistence --------------------------------------------------------

    def _data(self) -> dict[str, Any]:
        s = self.regler.settings
        return {
            **self.regler.as_dict(),
            "settings": {key: getattr(s, key) for key in _SETTING_KEYS},
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 5)
