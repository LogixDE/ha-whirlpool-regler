"""End-to-end tests against a real Home Assistant with fake Gecko entities."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.whirlpool_regler.const import (
    CONF_CLIMATE,
    CONF_HYSTERESIS_MODES,
    CONF_WATERCARE,
    DOMAIN,
)

CLIMATE = "climate.whirlpool_whirlpool_thermostat_1"
WATERCARE = "select.whirlpool_whirlpool_watercare_mode"
MODES = ["Away", "Standard", "Savings", "Super Savings", "Weekender", "Other"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def set_spa(hass, *, mode="Standard", setpoint=34.5, water=31.5, heat_source="heat_pump"):
    hass.states.async_set(
        CLIMATE,
        "heat",
        {
            "temperature": setpoint,
            "current_temperature": water,
            "min_temp": 8,
            "max_temp": 40,
            "target_temp_step": 0.5,
            "hvac_action": "heating",
            "heat_source": heat_source,
        },
    )
    hass.states.async_set(WATERCARE, mode, {"options": MODES})


async def tick(hass, freezer, seconds: float, *, step: float = 15):
    """Let time pass so the periodic evaluation runs."""
    for _ in range(int(seconds / step)):
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


@pytest.fixture
def calls(hass, monkeypatch):
    """Record setpoint writes to the Gecko thermostat instead of calling it."""
    from custom_components.whirlpool_regler.runtime import WhirlpoolRuntime

    writes: list[float] = []

    async def fake_write(self, temperature):
        writes.append(temperature)

    monkeypatch.setattr(WhirlpoolRuntime, "_async_write", fake_write)
    return writes


async def setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Whirlpool-Regler",
        data={CONF_CLIMATE: CLIMATE, CONF_WATERCARE: WATERCARE},
        options={CONF_HYSTERESIS_MODES: ["Away"]},
        unique_id=CLIMATE,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity(hass, domain, key):
    reg = er.async_get(hass)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    entity_id = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id, f"{domain} {key} missing"
    return entity_id


async def test_config_flow_creates_entry(hass: HomeAssistant):
    set_spa(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CLIMATE: CLIMATE, CONF_WATERCARE: WATERCARE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_HYSTERESIS_MODES: ["Away"]}


async def test_options_flow_sets_modes(hass: HomeAssistant, calls):
    set_spa(hass)
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HYSTERESIS_MODES: ["Away", "Savings"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.regler.settings.hysteresis_modes == {"Away", "Savings"}


async def test_entities_and_target_write(hass: HomeAssistant, calls):
    set_spa(hass)
    await setup_entry(hass)

    climate_id = entity(hass, "climate", "regler")
    state = hass.states.get(climate_id)
    assert state.attributes["temperature"] == 34.5
    assert state.attributes["preset_mode"] == "Standard"
    assert "Other" not in state.attributes["preset_modes"]
    assert hass.states.get(entity(hass, "sensor", "regelphase")).state == "heizen"
    assert hass.states.get(entity(hass, "binary_sensor", "heizstab_aktiv")).state == "off"
    assert hass.states.get(entity(hass, "select", "heizstab")).state == "erlaubt"

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "temperature": 36},
        blocking=True,
    )
    assert calls == [36.0]


async def test_heater_lock_caps_gecko_setpoint(hass: HomeAssistant, calls):
    set_spa(hass, setpoint=34.5, water=31.5)
    await setup_entry(hass)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity(hass, "select", "heizstab"), "option": "gesperrt"},
        blocking=True,
    )
    assert calls == [32.5]
    assert (
        hass.states.get(entity(hass, "sensor", "regelphase")).state
        == "heizen_ohne_heizstab"
    )
    # The thermostat still shows the real target.
    assert hass.states.get(entity(hass, "climate", "regler")).attributes[
        "temperature"
    ] == 34.5


async def test_hysteresis_in_away(hass: HomeAssistant, calls, freezer):
    set_spa(hass, mode="Away", setpoint=23.5, water=23.0)
    await setup_entry(hass)
    set_spa(hass, mode="Away", setpoint=23.5, water=23.5)
    await tick(hass, freezer, 60)
    assert calls == [22.0]


async def test_preset_switches_watercare(hass: HomeAssistant, calls):
    set_spa(hass)
    await setup_entry(hass)
    # Mock after setup: the Regler's own select platform registers the real
    # select services.
    select_calls = async_mock_service(hass, "select", "select_option")
    climate_id = entity(hass, "climate", "regler")
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": climate_id, "preset_mode": "Away"},
        blocking=True,
    )
    assert [c.data for c in select_calls] == [{"entity_id": WATERCARE, "option": "Away"}]


async def test_settings_survive_reload(hass: HomeAssistant, calls):
    set_spa(hass)
    entry = await setup_entry(hass)
    await entry.runtime_data.async_set_setting("heater_gap", 1.5)
    await entry.runtime_data.async_set_setting("hysteresis_enabled", False)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    s = entry.runtime_data.regler.settings
    assert s.heater_gap == 1.5
    assert s.hysteresis_enabled is False


async def test_unavailable_spa(hass: HomeAssistant, calls, freezer):
    set_spa(hass)
    await setup_entry(hass)
    hass.states.async_set(CLIMATE, "unavailable", {})
    await tick(hass, freezer, 60)
    assert hass.states.get(entity(hass, "climate", "regler")).state == "unavailable"
    assert hass.states.get(entity(hass, "sensor", "regelphase")).state == "wartet"
    assert calls == []
