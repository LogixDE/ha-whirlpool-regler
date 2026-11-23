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
    CONF_WATERCARE,
    CONF_WORK_MODE,
    DOMAIN,
)

CLIMATE = "climate.whirlpool_whirlpool_thermostat_1"
WATERCARE = "select.whirlpool_whirlpool_watercare_mode"
MODES = ["Away", "Standard", "Savings", "Super Savings", "Weekender", "Other"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def set_spa(hass, *, mode="Standard", setpoint=25.0, water=24.5, heat_source="heat_pump"):
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
    """Record setpoint writes and apply them to the fake Gecko thermostat."""
    from custom_components.whirlpool_regler.runtime import WhirlpoolRuntime

    writes: list[float] = []

    async def fake_write(self, temperature):
        writes.append(temperature)
        state = hass.states.get(CLIMATE)
        hass.states.async_set(
            CLIMATE, state.state, {**state.attributes, "temperature": temperature}
        )

    monkeypatch.setattr(WhirlpoolRuntime, "_async_write", fake_write)
    return writes


async def setup_entry(hass, freezer) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Whirlpool-Regler",
        data={CONF_CLIMATE: CLIMATE, CONF_WATERCARE: WATERCARE},
        options={CONF_WORK_MODE: "Standard"},
        unique_id=CLIMATE,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Let the reported setpoint settle.
    await tick(hass, freezer, 30)
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
    assert result["options"] == {CONF_WORK_MODE: "Standard"}


async def test_options_flow_sets_work_mode(hass: HomeAssistant, calls, freezer):
    set_spa(hass)
    entry = await setup_entry(hass, freezer)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WORK_MODE: "Weekender"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.regler.settings.work_mode == "Weekender"


async def test_entities_profiles_and_targets(hass: HomeAssistant, calls, freezer):
    set_spa(hass, setpoint=25.0, water=24.5)
    await setup_entry(hass, freezer)

    climate_id = entity(hass, "climate", "regler")
    state = hass.states.get(climate_id)
    assert state.attributes["preset_modes"] == ["eco", "baden"]
    assert state.attributes["preset_mode"] == "eco"
    assert state.attributes["temperature"] == 25.0
    assert hass.states.get(entity(hass, "number", "eco_temperatur")).state == "25.0"
    assert hass.states.get(entity(hass, "number", "badetemperatur")).state == "37.0"
    assert hass.states.get(entity(hass, "binary_sensor", "heizstab_aktiv")).state == "off"
    assert calls == []

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity(hass, "number", "badetemperatur"), "value": 38},
        blocking=True,
    )
    assert calls == []  # not the active profile

    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": climate_id, "preset_mode": "baden"},
        blocking=True,
    )
    assert calls == [38.0]
    assert hass.states.get(climate_id).attributes["temperature"] == 38.0

    # Setting the thermostat changes the active profile's target.
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "temperature": 37.5},
        blocking=True,
    )
    assert calls == [38.0, 37.5]
    assert hass.states.get(entity(hass, "number", "badetemperatur")).state == "37.5"


async def test_heater_lock_caps_gecko_setpoint(hass: HomeAssistant, calls, freezer):
    set_spa(hass, setpoint=37.0, water=31.5)
    entry = await setup_entry(hass, freezer)
    await entry.runtime_data.async_set_profile("baden")
    calls.clear()
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
    assert hass.states.get(entity(hass, "climate", "regler")).attributes[
        "temperature"
    ] == 37.0


async def test_wrong_mode_is_switched_back(hass: HomeAssistant, calls, freezer):
    set_spa(hass)
    await setup_entry(hass, freezer)
    select_calls = async_mock_service(hass, "select", "select_option")
    set_spa(hass, mode="Away", setpoint=22.0)
    await tick(hass, freezer, 90)
    assert [c.data for c in select_calls] == [
        {"entity_id": WATERCARE, "option": "Standard"}
    ]
    assert hass.states.get(entity(hass, "sensor", "regelphase")).state == "falscher_modus"
    assert calls == []


async def test_settings_and_targets_survive_reload(hass: HomeAssistant, calls, freezer):
    set_spa(hass)
    entry = await setup_entry(hass, freezer)
    rt = entry.runtime_data
    await rt.async_set_setting("heater_gap", 1.5)
    await rt.async_set_setting("hysteresis_enabled", False)
    await rt.async_set_target(23.5, "eco")
    await rt.async_set_profile("baden")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert rt.regler.settings.heater_gap == 1.5
    assert rt.regler.settings.hysteresis_enabled is False
    assert rt.regler.targets["eco"] == 23.5
    assert rt.regler.profile == "baden"


async def test_unavailable_spa(hass: HomeAssistant, calls, freezer):
    set_spa(hass)
    await setup_entry(hass, freezer)
    hass.states.async_set(CLIMATE, "unavailable", {})
    await tick(hass, freezer, 60)
    assert hass.states.get(entity(hass, "climate", "regler")).state == "unavailable"
    assert hass.states.get(entity(hass, "sensor", "regelphase")).state == "wartet"
    assert calls == []


async def test_migrates_targets_from_version_0_1(
    hass: HomeAssistant, calls, freezer, hass_storage
):
    set_spa(hass, mode="Standard", setpoint=38.0, water=33.0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Whirlpool-Regler",
        data={CONF_CLIMATE: CLIMATE, CONF_WATERCARE: WATERCARE},
        options={"hysteresis_modes": ["Away"]},
        unique_id=CLIMATE,
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "modes": {
                "Away": {"target": 23.5, "lowered": True},
                "Standard": {"target": 38.0, "lowered": False},
            },
            "settings": {"heater_locked": True, "heater_gap": 1.0},
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert rt.regler.targets == {"eco": 23.5, "baden": 38.0}
    assert rt.regler.profile == "baden"
    assert rt.regler.settings.heater_locked is True
    assert rt.regler.settings.work_mode == "Standard"
