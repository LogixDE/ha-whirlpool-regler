"""Constants of the Whirlpool-Regler."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "whirlpool_regler"

CONF_CLIMATE = "climate_entity"
CONF_WATERCARE = "watercare_entity"
CONF_WATER_SENSOR = "water_sensor"
CONF_WORK_MODE = "work_mode"

# The watercare mode the spa always runs in. The Regler implements eco itself.
DEFAULT_WORK_MODE = "Standard"

# Watercare options that cannot be selected as the work mode.
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
