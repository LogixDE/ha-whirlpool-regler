"""Tests of the pure control logic, scenario by scenario."""

from __future__ import annotations

import pytest

from custom_components.whirlpool_regler.logic import (
    ECHO_WINDOW,
    EXTERNAL_STABLE,
    MODE_GRACE,
    MODE_SETTLE,
    PHASE_HEATING,
    PHASE_HEATING_NO_HEATER,
    PHASE_HOLDING,
    PHASE_LOWERED,
    PHASE_PAUSED,
    PHASE_WAITING,
    REACHED_DELAY,
    Inputs,
    Regler,
    Settings,
)


class Spa:
    """Minimal spa model: one setpoint per mode, writes land in the current mode."""

    def __init__(self, regler: Regler, *, mode="Away", water=30.0, setpoints=None):
        self.r = regler
        self.now = 1000.0
        self.mode = mode
        self.water = water
        self.setpoints = dict(setpoints or {"Away": 30.0, "Standard": 36.0})
        self.available = True
        self.writes: list[tuple[str, float]] = []
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
            ),
            force=force,
        )
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


def make(**settings) -> Regler:
    return Regler(Settings(**settings))


def test_first_contact_adopts_current_setpoint_without_writing():
    spa = Spa(make(), water=25.0)
    d = spa.step()
    assert d.target == 30.0
    assert d.phase == PHASE_HEATING
    spa.run(120)
    assert spa.writes == []


def test_unavailable_waits_and_does_not_write():
    spa = Spa(make())
    spa.available = False
    d = spa.step()
    assert d.phase == PHASE_WAITING
    assert d.write is None


def test_hysteresis_lowers_after_delay_and_restores_after_cooling():
    spa = Spa(make(span=1.0), water=29.0)
    spa.run(120)
    spa.water = 30.0
    d = spa.step(15)
    assert d.phase == PHASE_HOLDING
    d = spa.run(REACHED_DELAY + 15)
    assert d.phase == PHASE_LOWERED
    assert spa.setpoints["Away"] == 28.5  # 30 - 1 - 0.5
    # Cools down, but not by the full span yet.
    spa.water = 29.5
    assert spa.run(60).phase == PHASE_LOWERED
    spa.water = 29.0
    d = spa.step(15)
    assert d.phase == PHASE_HEATING
    assert spa.setpoints["Away"] == 30.0
    # Target is never lost: no drift over several cycles.
    for _ in range(3):
        spa.water = 30.0
        spa.run(REACHED_DELAY + 30)
        spa.water = 29.0
        spa.run(30)
    assert spa.r.target("Away") == 30.0
    assert spa.setpoints["Away"] == 30.0


def test_hysteresis_only_in_configured_modes():
    spa = Spa(make(), mode="Standard", water=36.0)
    spa.run(300)
    assert spa.writes == []
    assert spa.step().phase == PHASE_HOLDING


def test_mode_change_never_writes_away_target_into_standard():
    spa = Spa(make(), water=30.0)
    spa.run(120)
    assert spa.setpoints["Away"] == 28.5
    spa.mode = "Standard"
    spa.run(600)
    assert spa.setpoints["Standard"] == 36.0
    assert all(mode == "Away" for mode, _ in spa.writes)
    assert spa.r.target("Away") == 30.0
    # Back in Away: still lowered, restores once the water has cooled.
    spa.mode = "Away"
    spa.water = 28.5
    spa.run(60)
    assert spa.setpoints["Away"] == 30.0


def test_no_write_until_mode_has_settled():
    spa = Spa(make(), water=25.0)
    spa.step()
    spa.mode = "Standard"
    spa.step()
    spa.r.set_target("Standard", 38.0)
    d = spa.step(1, force=True)
    assert d.write is None
    spa.run(MODE_SETTLE + 15)
    assert spa.setpoints["Standard"] == 38.0


def test_echo_of_old_value_is_ignored():
    spa = Spa(make(), mode="Standard", water=30.0)
    spa.run(60)
    spa.r.set_target("Standard", 37.0)
    spa.step(1, force=True)
    assert spa.setpoints["Standard"] == 37.0
    # Gecko briefly reports the old value again.
    spa.setpoints["Standard"] = 36.0
    spa.step(5)
    spa.setpoints["Standard"] = 37.0
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.r.target("Standard") == 37.0


def test_midnight_blip_is_ignored():
    spa = Spa(make(), mode="Standard", water=30.0)
    spa.run(60)
    spa.setpoints["Standard"] = 39.0
    spa.step(1)
    spa.setpoints["Standard"] = 36.0
    spa.step(1)
    spa.run(300)
    assert spa.r.target("Standard") == 36.0
    assert spa.writes == []


def test_stable_external_change_becomes_new_target():
    spa = Spa(make(), mode="Standard", water=30.0)
    spa.run(60)
    spa.setpoints["Standard"] = 37.5  # changed in the app
    d = None
    adopted = False
    for _ in range(10):
        d = spa.step(15)
        adopted = adopted or d.adopted_external
    assert adopted
    assert spa.r.target("Standard") == 37.5
    assert spa.writes == []


def test_external_change_while_lowered_ends_lowering():
    spa = Spa(make(), water=30.0)
    spa.run(120)
    assert spa.r.lowered("Away")
    spa.setpoints["Away"] = 32.0
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.r.target("Away") == 32.0
    assert not spa.r.lowered("Away")


def test_lost_write_is_retried_not_adopted():
    spa = Spa(make(), mode="Standard", water=30.0)
    spa.run(60)
    spa.drop_writes = True
    spa.r.set_target("Standard", 38.0)
    spa.step(1, force=True)
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.r.target("Standard") == 38.0
    assert len(spa.writes) >= 2
    spa.drop_writes = False
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 30)
    assert spa.setpoints["Standard"] == 38.0


def test_heater_lock_keeps_gap_and_tracks_water():
    spa = Spa(make(heater_locked=True, heater_gap=1.0), mode="Standard", water=31.5)
    spa.step()
    spa.r.set_target("Standard", 36.0)
    d = spa.run(60)
    assert d.phase == PHASE_HEATING_NO_HEATER
    assert spa.setpoints["Standard"] == 32.5
    assert spa.r.target("Standard") == 36.0
    spa.water = 32.0
    spa.run(30)
    assert spa.setpoints["Standard"] == 33.0
    # A lower reading never lowers the cap again.
    spa.water = 31.5
    spa.run(30)
    assert spa.setpoints["Standard"] == 33.0
    # Close to the target the real target is used.
    spa.water = 35.5
    d = spa.run(30)
    assert spa.setpoints["Standard"] == 36.0
    assert d.phase == PHASE_HEATING


def test_capped_setpoint_is_not_taken_as_external():
    spa = Spa(make(heater_locked=True), mode="Standard", water=30.0)
    spa.run(60)
    spa.run(ECHO_WINDOW + EXTERNAL_STABLE + 60)
    assert spa.r.target("Standard") == 36.0


def test_unlocking_heater_restores_target():
    spa = Spa(make(heater_locked=True), mode="Standard", water=30.0)
    spa.run(60)
    assert spa.setpoints["Standard"] == 31.0
    spa.r.settings.heater_locked = False
    spa.step(1, force=True)
    assert spa.setpoints["Standard"] == 36.0


def test_pause_writes_nothing_and_restore_gives_target():
    spa = Spa(make(), water=30.0)
    spa.run(120)
    assert spa.setpoints["Away"] == 28.5
    restore = spa.r.pause_restore(
        Inputs(now=spa.now, mode="Away", water=30.0, setpoint=spa.setpoint)
    )
    assert restore == 30.0
    spa.r.settings.active = False
    spa.setpoints["Away"] = restore
    d = spa.run(300)
    assert d.phase == PHASE_PAUSED
    assert spa.writes == [("Away", 28.5)]
    # Changes made while paused are adopted, not overwritten, on resume.
    spa.setpoints["Away"] = 31.0
    spa.run(30)
    spa.r.settings.active = True
    spa.water = 29.0
    spa.run(MODE_GRACE + EXTERNAL_STABLE)
    assert spa.setpoints["Away"] == 31.0


def test_state_survives_restart():
    spa = Spa(make(), water=30.0)
    spa.run(120)
    saved = spa.r.as_dict()
    fresh = make()
    fresh.load(saved)
    spa.r = fresh
    spa.run(300)
    assert fresh.target("Away") == 30.0
    assert fresh.lowered("Away")
    spa.water = 29.0
    spa.run(30)
    assert spa.setpoints["Away"] == 30.0


@pytest.mark.parametrize(("water", "expected"), [(31.2, 32.5), (31.5, 32.5), (31.6, 33.0)])
def test_cap_is_rounded_up_to_step(water, expected):
    spa = Spa(make(heater_locked=True), mode="Standard", water=water)
    spa.run(60)
    assert spa.setpoints["Standard"] == expected
