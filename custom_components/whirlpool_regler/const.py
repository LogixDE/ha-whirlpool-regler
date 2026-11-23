"""Constants of the Whirlpool-Regler."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "whirlpool_regler"

CONF_CLIMATE = "climate_entity"
CONF_WATERCARE = "watercare_entity"
CONF_WATER_SENSOR = "water_sensor"
CONF_HYSTERESIS_MODES = "hysteresis_modes"

DEFAULT_HYSTERESIS_MODES = ["Away"]

# Watercare options that cannot be selected as a target mode.
NON_SELECTABLE_MODES = frozenset({"Other"})

HEATER_ALLOWED = "erlaubt"
HEATER_LOCKED = "gesperrt"
HEATER_OPTIONS = [HEATER_ALLOWED, HEATER_LOCKED]

STORAGE_VERSION = 1

# Periodic re-evaluation. The logic works with settle and stability times,
# which need evaluations even when no state changes.
EVAL_INTERVAL_SECONDS = 15

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
