"""Config flow of the Whirlpool-Regler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE,
    CONF_HYSTERESIS_MODES,
    CONF_WATER_SENSOR,
    CONF_WATERCARE,
    DEFAULT_HYSTERESIS_MODES,
    DOMAIN,
    NON_SELECTABLE_MODES,
)

DEFAULT_TITLE = "Whirlpool-Regler"


def _mode_options(hass, watercare_id: str | None) -> list[str]:
    state = hass.states.get(watercare_id) if watercare_id else None
    options = state.attributes.get("options", []) if state else []
    modes = [o for o in options if o not in NON_SELECTABLE_MODES]
    return modes or list(DEFAULT_HYSTERESIS_MODES)


def _modes_selector(modes: list[str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=modes,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


class WhirlpoolReglerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE])
            self._abort_if_unique_id_configured()
            watercare = self.hass.states.get(user_input[CONF_WATERCARE])
            if watercare is None or not watercare.attributes.get("options"):
                errors[CONF_WATERCARE] = "no_modes"
            else:
                modes = _mode_options(self.hass, user_input[CONF_WATERCARE])
                default_modes = [m for m in DEFAULT_HYSTERESIS_MODES if m in modes]
                return self.async_create_entry(
                    title=DEFAULT_TITLE,
                    data=user_input,
                    options={CONF_HYSTERESIS_MODES: default_modes},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN)
                ),
                vol.Required(CONF_WATERCARE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=SELECT_DOMAIN)
                ),
                vol.Optional(CONF_WATER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.TEMPERATURE,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WhirlpoolReglerOptionsFlow()


class WhirlpoolReglerOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        modes = _mode_options(self.hass, self.config_entry.data.get(CONF_WATERCARE))
        current = self.config_entry.options.get(
            CONF_HYSTERESIS_MODES, DEFAULT_HYSTERESIS_MODES
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HYSTERESIS_MODES,
                    default=[m for m in current if m in modes],
                ): _modes_selector(modes),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
