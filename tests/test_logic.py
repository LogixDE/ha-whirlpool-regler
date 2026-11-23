"""Tests of the pure control logic, scenario by scenario."""

from __future__ import annotations

import pytest

from custom_components.whirlpool_regler.logic import (
    ECHO_WINDOW,
    EXTERNAL_STABLE,
    HEATER_OFF_HOLD,
    HEATER_SHED_DELAY,
    MODE_ENFORCE_DELAY,
    PHASE_HEATING,
    PHASE_HEATING_NO_HEATER,
    PHASE_HOLDING,
    PHASE_LOWERED,
    PHASE_PAUSED,
    PHASE_SHEDDING,
    PHASE_WAITING,
    PHASE_WRONG_MODE,
    REACHED_DELAY,
    SETPOINT_SETTLE,
    Inputs,
    Regler,
    Settings,
)


class Spa:
    """Minimal spa model: one setpoint per watercare mode, writes land in the
    current mode, mode switches requested by the Regler are applied."""

    def __init__(
        self,
        regler: Regler,
        *,
        mode="Standard",
        water=30.0,
        setpoints=None,
    ):
        self.r = regler
        self.now = 1000.0
        self.mode = mode
        self.water = water
        self.setpoints = dict(setpoints or {"Away": 22.0, "Standard": 30.0})
        self.available = True
        self.heater = False
        self.writes: list[tuple[str, float]] = []
        self.mode_switches: list[str] = []
        self.drop_writes = False

    @property
    def setpoint(self):
        return self.setpoints[self.mode]

    def step(self, seconds: float = 0.0, *, force: bool = False):
        self.now += seconds
        d = self.r.update(
            Inputs(
                now=self.now,
                mode=self.mode,
                water=self.water,
                setpoint=self.setpoint,
                available=self.available,
                heater_running=self.heater,
            ),
            force=force,
        )
        if d.switch_mode is not None:
            self.mode_switches.append(d.switch_mode)
            self.mode = d.switch_mode
        if d.write is not None:
            self.writes.append((self.mode, d.write))
            if not self.drop_writes:
                self.setpoints[self.mode] = d.write
        return d

    def run(self, seconds: float, tick: float = 15.0):
        d = None
        for _ in range(int(seconds / tick)):
            d = self.step(tick)
        return d


def make(eco=30.0, bath=37.0, profile="eco", **settings) -> Regler:
    r = Regler(Settings(**settings))
    r.targets = {"eco": eco, "baden": bath}
    r.profile = profile
    return r


def test_startup_waits_then_writes_active_target():
    spa = Spa(make(eco=25.0, hysteresis_enabled=False), water=20.0,
              setpoints={"Standard": 34.0})
    assert spa.step().write is None
    spa.run(60)
    assert spa.setpoints["Standard"] == 25.0
    assert spa.writes == [("Standard", 25.0)]


def test_unavailable_waits_and_does_not_write():
    spa = Spa(make())
    spa.available = False
    d = spa.step()
    assert d.phase == PHASE_WAITING
    assert d.write is None


def test_profile_switch_writes_other_target():
    spa = Spa(make(eco=25.0, bath=37.0), water=24.0, setpoints={"Standard": 25.0})
    spa.run(60)
    assert spa.writes == []
    spa.r.set_profile("baden")
    d = spa.step(1, force=True)
    assert d.phase == PHASE_HEATING
    assert spa.setpoints["Standard"] == 37.0
    spa.r.set_profile("eco")
    spa.step(1, force=True)
    assert spa.setpoints["Standard"] == 25.0


def test_hysteresis_in_eco_lowers_and_restores_without_drift():
    spa = Spa(make(eco=30.0, span=1.0), water=29.0)
    spa.run(60)
    spa.water = 30.0
    assert spa.step(15).phase == PHASE_HOLDING
    d = spa.run(REACHED_DELAY + 15)
    assert d.phase == PHASE_LOWERED
    assert spa.setpoints["Standard"] == 28.5
    spa.water = 29.5
    assert spa.run(60).phase == PHASE_LOWERED
    spa.water = 29.0
    assert spa.step(15).phase == PHASE_HEATING
    assert spa.setpoints["Standard"] == 30.0
    for _ in range(3):
        spa.water = 30.0
        spa.run(REACHED_DELAY + 30)
        spa.water = 29.0
        spa.run(30)
    assert spa.r.targets["eco"] == 30.0
    assert spa.setpoints["Standard"] == 30.0


def test_no_hysteresis_when_bathing():
    spa = Spa(make(bath=37.0, profile="baden"), water=37.0, setpoints={"Standard": 37.0})
    spa.run(600)
    assert spa.writes == []
    assert spa.step().phase == PHASE_HOLDING


def test_switching_to_bath_while_lowered_ends_lowering():
    spa = Spa(make(eco=30.0), water=30.0)
    spa.run(120)
    assert spa.r.lowered
    spa.r.set_profile("baden")
    spa.step(1, force=True)
    assert not spa.r.lowered
    assert spa.setpoints["Standard"] == 37.0


def test_wrong_watercare_mode_is_switched_back_and_never_written():
    spa = Spa(make(eco=30.0), water=29.0)
    spa.run(60)
    spa.mode = "Away"
    d = spa.step(1)
    assert d.phase == PHASE_WRONG_MODE
    assert d.switch_mode is None
    spa.run(MODE_ENFORCE_DELAY + 15)
    assert spa.mode_switches == ["Standard"]
    assert spa.mode == "Standard"
    assert all(mode == "Standard" for mode, _ in spa.writes)
    assert spa.setpoints["Away"] == 22.0


def test_stale_setpoint_after_returning_to_work_mode_is_not_adopted():
    spa = Spa(make(eco=30.0, hysteresis_enabled=False), water=29.0)
    spa.run(60)
    spa.mode = "Away"
    spa.step(1)
    spa.mode = "Standard"
    # Gecko reports the Away setpoint for a few seconds.
    real = spa.setpoints["Standard"]
    spa.setpoints["Standard"] = 22.0
    spa.step(1)
    spa.step(6)
    spa.setpoints["Standard"] = real
    spa.run(300)
    assert spa.r.targets["eco"] == 30.0
    assert spa.setpoints["Standard"] == 30.0


def test_echo_of_old_value_is_ignored():
    spa = Spa(make(eco=30.0, hysteresis_enabled=False), water=25.0)
    spa.run(60)
    spa.r.set_target(31.0)
    spa.step(1, force=True)
    assert spa.setpoints["Standard"] == 31.0
    spa.setpoints["Standard"] = 30.0
    spa.step(5)
    spa.setpoints["Standard"] = 31.0
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.r.targets["eco"] == 31.0


def test_midnight_blip_is_ignored():
    spa = Spa(make(eco=30.0, hysteresis_enabled=False), water=25.0)
    spa.run(60)
    spa.setpoints["Standard"] = 39.0
    spa.step(1)
    spa.setpoints["Standard"] = 30.0
    spa.step(1)
    spa.run(300)
    assert spa.r.targets["eco"] == 30.0
    assert spa.writes == []


def test_stable_external_change_becomes_target_of_active_profile():
    spa = Spa(make(eco=30.0, bath=37.0, profile="baden"), water=30.0,
              setpoints={"Standard": 37.0})
    spa.run(60)
    spa.setpoints["Standard"] = 38.5  # changed in the app
    adopted = False
    for _ in range(10):
        adopted = spa.step(15).adopted_external or adopted
    assert adopted
    assert spa.r.targets == {"eco": 30.0, "baden": 38.5}
    assert spa.writes == []


def test_lost_write_is_retried_not_adopted():
    spa = Spa(make(eco=30.0, hysteresis_enabled=False), water=25.0)
    spa.run(60)
    spa.drop_writes = True
    spa.r.set_profile("baden")
    spa.step(1, force=True)
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.r.targets["baden"] == 37.0
    assert len(spa.writes) >= 2
    spa.drop_writes = False
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.setpoints["Standard"] == 37.0


def test_heater_lock_keeps_gap_and_tracks_water():
    spa = Spa(make(bath=36.0, profile="baden", heater_locked=True), water=31.5,
              setpoints={"Standard": 36.0})
    d = spa.run(60)
    assert d.phase == PHASE_HEATING_NO_HEATER
    assert spa.setpoints["Standard"] == 32.5
    spa.water = 32.0
    spa.run(30)
    assert spa.setpoints["Standard"] == 33.0
    spa.water = 31.5
    spa.run(30)
    assert spa.setpoints["Standard"] == 33.0
    spa.water = 35.5
    d = spa.run(30)
    assert spa.setpoints["Standard"] == 36.0
    assert d.phase == PHASE_HEATING
    assert spa.r.targets["baden"] == 36.0


def test_capped_setpoint_is_not_taken_as_external():
    spa = Spa(make(profile="baden", heater_locked=True), water=30.0)
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 120)
    assert spa.r.targets["baden"] == 37.0


def test_unlocking_heater_restores_target():
    spa = Spa(make(profile="baden", heater_locked=True), water=30.0)
    spa.run(60)
    assert spa.setpoints["Standard"] == 31.0
    spa.r.settings.heater_locked = False
    spa.step(1, force=True)
    assert spa.setpoints["Standard"] == 37.0


@pytest.mark.parametrize(("water", "expected"), [(31.2, 32.5), (31.5, 32.5), (31.6, 33.0)])
def test_cap_is_rounded_up_to_step(water, expected):
    spa = Spa(make(profile="baden", heater_locked=True), water=water)
    spa.run(60)
    assert spa.setpoints["Standard"] == expected


def test_locked_heater_that_keeps_running_is_shed():
    spa = Spa(make(bath=38.0, profile="baden", heater_locked=True), water=32.0)
    spa.run(60)
    assert spa.setpoints["Standard"] == 33.0
    spa.heater = True  # Gecko keeps the heater latched at 1 K gap
    d = spa.run(HEATER_SHED_DELAY + 30)
    assert d.phase == PHASE_SHEDDING
    assert spa.setpoints["Standard"] == 31.5
    spa.heater = False
    d = spa.run(HEATER_OFF_HOLD + 30)
    assert d.phase == PHASE_HEATING_NO_HEATER
    assert spa.setpoints["Standard"] == 33.0
    spa.run(300)
    assert spa.r.targets["baden"] == 38.0


def test_running_heater_is_not_shed_when_allowed():
    spa = Spa(make(profile="baden"), water=32.0, setpoints={"Standard": 37.0})
    spa.heater = True
    spa.run(600)
    assert spa.writes == []


def test_pause_writes_nothing_and_adopts_changes_made_meanwhile():
    spa = Spa(make(eco=30.0), water=30.0)
    spa.run(120)
    assert spa.setpoints["Standard"] == 28.5
    restore = spa.r.pause_restore(
        Inputs(now=spa.now, mode="Standard", water=30.0, setpoint=spa.setpoint)
    )
    assert restore == 30.0
    spa.r.settings.active = False
    spa.setpoints["Standard"] = restore
    assert spa.run(300).phase == PHASE_PAUSED
    spa.setpoints["Standard"] = 31.0
    spa.run(120)
    assert spa.r.targets["eco"] == 31.0
    spa.r.settings.active = True
    spa.water = 29.0
    spa.run(120)
    assert spa.setpoints["Standard"] == 31.0


def test_paused_regler_does_not_enforce_work_mode():
    spa = Spa(make(), mode="Away")
    spa.r.settings.active = False
    spa.run(300)
    assert spa.mode_switches == []


def test_state_survives_restart():
    spa = Spa(make(eco=30.0), water=30.0)
    spa.run(120)
    saved = spa.r.as_dict()
    fresh = Regler()
    fresh.load(saved)
    spa.r = fresh
    spa.run(300)
    assert fresh.targets["eco"] == 30.0
    assert fresh.lowered
    spa.water = 29.0
    spa.run(30)
    assert spa.setpoints["Standard"] == 30.0


def test_first_value_settles_before_writing():
    spa = Spa(make(eco=25.0, hysteresis_enabled=False), water=20.0,
              setpoints={"Standard": 34.0})
    spa.step()
    spa.step(SETPOINT_SETTLE / 2)
    assert spa.writes == []
