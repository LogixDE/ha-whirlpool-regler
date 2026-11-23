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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE,
    CONF_WATER_SENSOR,
    CONF_WATERCARE,
    CONF_WORK_MODE,
    DEFAULT_WORK_MODE,
    DOMAIN,
    NON_SELECTABLE_MODES,
)

DEFAULT_TITLE = "Whirlpool-Regler"


def _mode_options(hass: HomeAssistant, watercare_id: str | None) -> list[str]:
    state = hass.states.get(watercare_id) if watercare_id else None
    options = state.attributes.get("options", []) if state else []
    return [o for o in options if o not in NON_SELECTABLE_MODES]


def _work_mode_schema(modes: list[str], current: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WORK_MODE, default=current): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=modes, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        }
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
            modes = _mode_options(self.hass, user_input[CONF_WATERCARE])
            if not modes:
                errors[CONF_WATERCARE] = "no_modes"
            else:
                work_mode = DEFAULT_WORK_MODE if DEFAULT_WORK_MODE in modes else modes[0]
                return self.async_create_entry(
                    title=DEFAULT_TITLE,
                    data=user_input,
                    options={CONF_WORK_MODE: work_mode},
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
        current = self.config_entry.options.get(CONF_WORK_MODE, DEFAULT_WORK_MODE)
        if current not in modes:
            modes = [current, *modes]
        return self.async_show_form(
            step_id="init", data_schema=_work_mode_schema(modes, current)
        )
