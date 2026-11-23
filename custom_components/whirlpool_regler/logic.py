"""Control logic of the Whirlpool-Regler, free of Home Assistant imports.

The spa always runs in one watercare mode (Standard). The Regler is the only
writer of the Gecko setpoint and implements its own eco mode with three
profiles and two targets:

* eco: keep the water at the eco temperature, heat pump only.
* baden: heat to the bathing temperature, heat pump only.
* baden_plus: heat to the bathing temperature, electric heater allowed.

The heater is also allowed while the PV release is on (set by a PV automation
as long as the energy balance stays positive).

On top of the active target it derives the setpoint the spa gets:

* Hysteresis (eco only): once the water has reached the eco target, the
  setpoint is lowered to target - span - 0.5 K so the heat pump does not
  short-cycle. When the water has cooled down by the span, the target is
  restored and the heat pump runs through.
* Heater lock: with Smart Heat, Gecko switches the electric heater on when
  the gap between setpoint and water is large. While the heater is locked
  (every profile except baden_plus, unless the PV release is on),
  the setpoint is kept at water + gap (rounded up to the setpoint step) and
  tracked upwards as the water warms, never beyond the target.
* Heater shedding: Gecko keeps a running heater on even when the gap gets
  small. If it runs although it is locked, the setpoint is dropped below the
  water until the heater is off.

Gecko quirks the logic filters:

* After a write Gecko briefly reports the old value again (echo).
* Around midnight the setpoint jumps to another value for 1-2 s.
* After a watercare mode change Gecko reports the previous mode's setpoint
  for a few seconds.
* A setpoint that is changed by someone else (app, control panel) and stays
  stable is taken over as the new target of the active profile.

All times are monotonic seconds supplied by the caller, which keeps the logic
deterministic and easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

PROFILE_ECO = "eco"
PROFILE_BATH = "baden"
PROFILE_BATH_PLUS = "baden_plus"
PROFILES = [PROFILE_ECO, PROFILE_BATH, PROFILE_BATH_PLUS]

# Targets: baden and baden_plus share the bathing temperature.
TARGET_KEYS = {
    PROFILE_ECO: PROFILE_ECO,
    PROFILE_BATH: PROFILE_BATH,
    PROFILE_BATH_PLUS: PROFILE_BATH,
}
DEFAULT_TARGETS = {PROFILE_ECO: 25.0, PROFILE_BATH: 37.0}

# Seconds the water must stay at or above the target before lowering.
REACHED_DELAY = 30.0
# Seconds a watercare mode must be stable before the Regler writes.
MODE_SETTLE = 15.0
# Seconds the spa may be in another watercare mode before it is switched
# back to the work mode, and the minimum time between two such switches.
MODE_ENFORCE_DELAY = 60.0
# Seconds the reported setpoint must be stable before the Regler writes
# (filters the midnight blip).
SETPOINT_SETTLE = 10.0
# Seconds after an own write during which deviating reports are echoes.
ECHO_WINDOW = 90.0
# Seconds a deviating setpoint must be stable to count as an external change.
EXTERNAL_STABLE = 60.0
# Seconds after a mode change during which setpoint changes are not external.
MODE_GRACE = 90.0
# How often a write that Gecko did not apply is repeated.
MAX_RETRIES = 3
# Seconds the heater may keep running while it is locked before it is shed.
HEATER_SHED_DELAY = 60.0
# Seconds the heater must be off before the capped setpoint is restored.
HEATER_OFF_HOLD = 30.0

EPS = 0.01

PHASE_WAITING = "wartet"
PHASE_PAUSED = "pausiert"
PHASE_WRONG_MODE = "falscher_modus"
PHASE_HEATING = "heizen"
PHASE_HEATING_NO_HEATER = "heizen_ohne_heizstab"
PHASE_HOLDING = "ziel_erreicht"
PHASE_LOWERED = "abgesenkt"
PHASE_SHEDDING = "heizstab_abwurf"

PHASES = [
    PHASE_WAITING,
    PHASE_PAUSED,
    PHASE_WRONG_MODE,
    PHASE_HEATING,
    PHASE_HEATING_NO_HEATER,
    PHASE_HOLDING,
    PHASE_LOWERED,
    PHASE_SHEDDING,
]


@dataclass
class Settings:
    """User settings, changed through the Regler's entities."""

    active: bool = True
    hysteresis_enabled: bool = True
    span: float = 1.0
    heater_pv_release: bool = False
    heater_gap: float = 1.0
    work_mode: str = "Standard"
    min_temp: float = 8.0
    max_temp: float = 40.0
    step: float = 0.5


@dataclass
class Inputs:
    """What the spa reports right now."""

    now: float
    mode: str | None
    water: float | None
    setpoint: float | None
    available: bool = True
    heater_running: bool | None = None


@dataclass
class Decision:
    """Result of one evaluation."""

    phase: str
    target: float | None = None
    desired: float | None = None
    write: float | None = None
    switch_mode: str | None = None
    adopted_external: bool = False


@dataclass
class Regler:
    """Stateful controller. Feed it with update(); it tells what to do."""

    settings: Settings = field(default_factory=Settings)
    profile: str = PROFILE_ECO
    targets: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    lowered: bool = False

    # Runtime bookkeeping (not persisted).
    _cap: float | None = None
    _commanded: float | None = None
    _mode: str | None = None
    _mode_since: float = -math.inf
    _last_mode_switch: float = -math.inf
    _seen_setpoint: float | None = None
    _seen_since: float = -math.inf
    _reached_since: float | None = None
    _last_write: float = -math.inf
    _pre_write_setpoint: float | None = None
    _retries: int = 0
    _heater_since: float | None = None
    _heater_off_since: float | None = None
    _shedding: bool = False

    # --- persistence --------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "targets": dict(self.targets),
            "lowered": self.lowered,
        }

    def load(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        if data.get("profile") in PROFILES:
            self.profile = data["profile"]
        for name, value in (data.get("targets") or {}).items():
            if name in DEFAULT_TARGETS and value is not None:
                self.targets[name] = float(value)
        self.lowered = bool(data.get("lowered", False))

    # --- queries ------------------------------------------------------------

    @property
    def target(self) -> float:
        return self.targets[TARGET_KEYS[self.profile]]

    @property
    def heater_locked(self) -> bool:
        return self.profile != PROFILE_BATH_PLUS and not self.settings.heater_pv_release

    # --- commands -----------------------------------------------------------

    def set_target(self, value: float, profile: str | None = None) -> None:
        """Set the target of a profile (default: the active one)."""
        key = TARGET_KEYS[profile or self.profile]
        self.targets[key] = self._clamp(value)
        if key == TARGET_KEYS[self.profile]:
            self._reset_control()

    def set_profile(self, profile: str) -> None:
        if profile not in PROFILES:
            raise ValueError(profile)
        if profile != self.profile:
            self.profile = profile
            self._reset_control()

    def pause_restore(self, inp: Inputs) -> float | None:
        """Setpoint to write once when the Regler is paused.

        Leaves the spa on the real target instead of a lowered, capped or
        shed value. Returns None when nothing needs to be written.
        """
        self._reset_control()
        if (
            not inp.available
            or inp.mode != self.settings.work_mode
            or inp.setpoint is None
            or abs(inp.setpoint - self.target) <= EPS
        ):
            return None
        self._pre_write_setpoint = inp.setpoint
        self._last_write = inp.now
        return self.target

    # --- evaluation ---------------------------------------------------------

    def update(self, inp: Inputs, *, force: bool = False) -> Decision:
        """Evaluate the current situation.

        force=True skips the setpoint settle time (used right after a user
        command so the spa reacts at once). The mode settle time still
        applies, because Gecko reports stale values right after a switch.
        """
        s = self.settings

        if inp.mode != self._mode:
            if self._mode is not None and inp.mode is not None:
                # A real mode switch (not startup, not a reconnect).
                self._mode_since = inp.now
            self._mode = inp.mode
            self._reached_since = None
            self._retries = 0

        if inp.setpoint is None or self._seen_setpoint is None or abs(
            inp.setpoint - self._seen_setpoint
        ) > EPS:
            self._seen_setpoint = inp.setpoint
            self._seen_since = inp.now

        if (
            not inp.available
            or inp.mode is None
            or inp.water is None
            or inp.setpoint is None
        ):
            return Decision(phase=PHASE_WAITING, target=self.target)

        if not s.active:
            # Paused: Gecko is in charge. A stable setpoint is the target, so
            # the Regler continues from there when it is switched back on.
            self._reset_control()
            if (
                inp.mode == s.work_mode
                and inp.now - self._last_write >= ECHO_WINDOW
                and inp.now - self._seen_since >= EXTERNAL_STABLE
            ):
                self.targets[TARGET_KEYS[self.profile]] = inp.setpoint
            self._commanded = None
            return Decision(phase=PHASE_PAUSED, target=self.target)

        if inp.mode != s.work_mode:
            # Someone switched the watercare mode. Its setpoint is not ours;
            # switch back after a while.
            self._commanded = None
            switch = None
            if (
                inp.now - self._mode_since >= MODE_ENFORCE_DELAY
                and inp.now - self._last_mode_switch >= MODE_ENFORCE_DELAY
            ):
                switch = s.work_mode
                self._last_mode_switch = inp.now
            return Decision(
                phase=PHASE_WRONG_MODE, target=self.target, switch_mode=switch
            )

        adopted = self._check_external(inp)

        target = self.target
        water = inp.water
        hysteresis = s.hysteresis_enabled and self.profile == PROFILE_ECO

        if not hysteresis:
            self.lowered = False
        if self.lowered and water <= target - s.span + EPS:
            self.lowered = False
            self._reached_since = None
        if hysteresis and not self.lowered:
            if water >= target - EPS:
                if self._reached_since is None:
                    self._reached_since = inp.now
                if inp.now - self._reached_since >= REACHED_DELAY:
                    self.lowered = True
            else:
                self._reached_since = None

        if self.lowered:
            desired = self._round(target - s.span - 0.5)
            phase = PHASE_LOWERED
            self._cap = None
        elif self.heater_locked and water + s.heater_gap < target - EPS:
            cap = self._ceil(water + s.heater_gap)
            if self._cap is not None:
                cap = max(cap, self._cap)
            cap = min(cap, target)
            self._cap = cap
            desired = cap
            phase = PHASE_HEATING_NO_HEATER
        else:
            self._cap = None
            desired = self._round(target)
            phase = PHASE_HEATING if water < target - EPS else PHASE_HOLDING

        if self._update_shedding(inp):
            desired = min(desired, self._floor(water) - s.step)
            phase = PHASE_SHEDDING

        desired = self._clamp(desired)
        write = self._maybe_write(inp, desired, force=force)
        return Decision(
            phase=phase,
            target=target,
            desired=desired,
            write=write,
            adopted_external=adopted,
        )

    # --- internals ----------------------------------------------------------

    def _reset_control(self) -> None:
        self.lowered = False
        self._cap = None
        self._reached_since = None
        self._shedding = False
        self._heater_since = None
        self._heater_off_since = None

    def _check_external(self, inp: Inputs) -> bool:
        """Take over a setpoint someone else wrote; retry lost own writes."""
        sp = inp.setpoint
        if self._commanded is None:
            # Nothing sent yet (startup, back in the work mode): the reported
            # value is the baseline once it has settled.
            if inp.now - self._seen_since >= SETPOINT_SETTLE and (
                inp.now - self._mode_since >= MODE_SETTLE
            ):
                self._commanded = sp
            return False
        if abs(sp - self._commanded) <= EPS:
            self._retries = 0
            return False
        if inp.now - self._last_write < ECHO_WINDOW:
            return False
        if inp.now - self._mode_since < MODE_GRACE:
            return False
        if inp.now - self._seen_since < EXTERNAL_STABLE:
            return False
        if (
            self._pre_write_setpoint is not None
            and abs(sp - self._pre_write_setpoint) <= EPS
            and self._retries < MAX_RETRIES
        ):
            # Our last write did not arrive; send it again.
            self._retries += 1
            self._commanded = None
            self._seen_since = -math.inf
            return False
        # Someone else set a new value: it becomes the active target.
        self.targets[TARGET_KEYS[self.profile]] = self._clamp(sp)
        self._reset_control()
        self._commanded = sp
        self._pre_write_setpoint = None
        self._retries = 0
        return True

    def _update_shedding(self, inp: Inputs) -> bool:
        """Track whether a locked heater has to be forced off."""
        if not self.heater_locked or self.lowered:
            self._shedding = False
            self._heater_since = None
            self._heater_off_since = None
            return False
        if inp.heater_running:
            self._heater_off_since = None
            if self._heater_since is None:
                self._heater_since = inp.now
            if inp.now - self._heater_since >= HEATER_SHED_DELAY:
                self._shedding = True
        else:
            self._heater_since = None
            if self._shedding:
                if self._heater_off_since is None:
                    self._heater_off_since = inp.now
                if inp.now - self._heater_off_since >= HEATER_OFF_HOLD:
                    self._shedding = False
                    self._heater_off_since = None
        return self._shedding

    def _maybe_write(self, inp: Inputs, desired: float, *, force: bool) -> float | None:
        if abs(inp.setpoint - desired) <= EPS:
            self._commanded = desired
            return None
        if self._commanded is not None and abs(self._commanded - desired) <= EPS:
            # Already sent; waiting for Gecko to report it.
            return None
        if inp.now - self._mode_since < MODE_SETTLE:
            return None
        if not force and inp.now - self._seen_since < SETPOINT_SETTLE:
            return None
        self._pre_write_setpoint = inp.setpoint
        self._last_write = inp.now
        self._commanded = desired
        return desired

    def _clamp(self, value: float) -> float:
        s = self.settings
        return min(max(value, s.min_temp), s.max_temp)

    def _round(self, value: float) -> float:
        step = self.settings.step
        return round(round(value / step) * step, 2)

    def _floor(self, value: float) -> float:
        step = self.settings.step
        return round(math.floor(value / step + 1e-6) * step, 2)

    def _ceil(self, value: float) -> float:
        step = self.settings.step
        return round(math.ceil(value / step - 1e-6) * step, 2)
