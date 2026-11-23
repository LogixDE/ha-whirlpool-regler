"""Control logic of the Whirlpool-Regler, free of Home Assistant imports.

The Regler is the only writer of the Gecko setpoint. It keeps the real target
temperature per watercare mode and derives the setpoint the spa should get:

* Hysteresis: once the water has reached the target, the setpoint is lowered
  to target - span - 0.5 K so the heat pump does not short-cycle. When the
  water has cooled down by the span, the target is restored and the heat pump
  runs through.
* Heater lock: with Smart Heat, Gecko switches the electric heater on when the
  gap between setpoint and water is large. While the heater is locked, the
  setpoint is kept at water + gap (rounded up to the setpoint step) and
  tracked upwards as the water warms, never beyond the target.

Gecko quirks the logic filters:

* After a write Gecko briefly reports the old value again (echo).
* Around midnight the setpoint jumps to another value for 1-2 s.
* Every watercare mode has its own setpoint, so a mode change changes the
  reported setpoint without anyone touching it.
* A setpoint that is changed by someone else (app, control panel) and stays
  stable is taken over as the new target of that mode.

All times are monotonic seconds supplied by the caller, which keeps the logic
deterministic and easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Seconds the water must stay at or above the target before lowering.
REACHED_DELAY = 30.0
# Seconds a watercare mode must be stable before the Regler writes.
MODE_SETTLE = 15.0
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

EPS = 0.01

PHASE_WAITING = "wartet"
PHASE_PAUSED = "pausiert"
PHASE_HEATING = "heizen"
PHASE_HEATING_NO_HEATER = "heizen_ohne_heizstab"
PHASE_HOLDING = "ziel_erreicht"
PHASE_LOWERED = "abgesenkt"

PHASES = [
    PHASE_WAITING,
    PHASE_PAUSED,
    PHASE_HEATING,
    PHASE_HEATING_NO_HEATER,
    PHASE_HOLDING,
    PHASE_LOWERED,
]


@dataclass
class Settings:
    """User settings, changed through the Regler's entities."""

    active: bool = True
    hysteresis_enabled: bool = True
    hysteresis_modes: frozenset[str] = frozenset({"Away"})
    span: float = 1.0
    heater_locked: bool = False
    heater_gap: float = 1.0
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


@dataclass
class Decision:
    """Result of one evaluation."""

    phase: str
    target: float | None = None
    desired: float | None = None
    write: float | None = None
    adopted_external: bool = False


@dataclass
class _ModeState:
    target: float | None = None
    lowered: bool = False
    cap: float | None = None
    commanded: float | None = None


@dataclass
class Regler:
    """Stateful controller. Feed it with update(); it tells what to write."""

    settings: Settings = field(default_factory=Settings)
    modes: dict[str, _ModeState] = field(default_factory=dict)

    # Runtime bookkeeping (not persisted).
    _mode: str | None = None
    _mode_since: float = -math.inf
    _seen_setpoint: float | None = None
    _seen_since: float = -math.inf
    _reached_since: float | None = None
    _last_write: float = -math.inf
    _pre_write_setpoint: float | None = None
    _retries: int = 0

    # --- persistence --------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Persistent part of the state."""
        return {
            "modes": {
                name: {"target": st.target, "lowered": st.lowered}
                for name, st in self.modes.items()
            }
        }

    def load(self, data: dict[str, Any] | None) -> None:
        """Restore state saved by as_dict()."""
        for name, raw in ((data or {}).get("modes") or {}).items():
            st = self._state(name)
            target = raw.get("target")
            st.target = float(target) if target is not None else None
            st.lowered = bool(raw.get("lowered", False))

    # --- queries ------------------------------------------------------------

    def target(self, mode: str | None) -> float | None:
        """Real target temperature of a mode."""
        if mode is None or mode not in self.modes:
            return None
        return self.modes[mode].target

    def lowered(self, mode: str | None) -> bool:
        return mode is not None and mode in self.modes and self.modes[mode].lowered

    # --- commands -----------------------------------------------------------

    def set_target(self, mode: str, target: float) -> None:
        """The user sets a new real target for a mode."""
        st = self._state(mode)
        st.target = self._clamp(target)
        st.lowered = False
        st.cap = None
        if mode == self._mode:
            self._reached_since = None

    # --- evaluation ---------------------------------------------------------

    def update(self, inp: Inputs, *, force: bool = False) -> Decision:
        """Evaluate the current situation.

        force=True skips the setpoint settle time (used right after a user
        command so the spa reacts at once). The mode settle time still
        applies, because a write always lands in the mode the spa is in.
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
            return Decision(phase=PHASE_WAITING, target=self.target(inp.mode))

        st = self._state(inp.mode)
        if st.target is None:
            # First time in this mode: whatever the spa has is the target.
            st.target = inp.setpoint
            st.commanded = inp.setpoint

        if not s.active:
            # Paused: Gecko is in charge. A stable setpoint is the target, so
            # the Regler continues from there when it is switched back on.
            st.cap = None
            st.lowered = False
            self._reached_since = None
            if (
                inp.now - self._last_write >= ECHO_WINDOW
                and inp.now - self._seen_since >= EXTERNAL_STABLE
            ):
                st.target = inp.setpoint
            st.commanded = st.target
            return Decision(phase=PHASE_PAUSED, target=st.target)

        adopted = self._check_external(inp, st)

        target = st.target
        water = inp.water
        hysteresis = s.hysteresis_enabled and inp.mode in s.hysteresis_modes

        if not hysteresis:
            st.lowered = False
        if st.lowered and water <= target - s.span + EPS:
            st.lowered = False
            self._reached_since = None
        if hysteresis and not st.lowered:
            if water >= target - EPS:
                if self._reached_since is None:
                    self._reached_since = inp.now
                if inp.now - self._reached_since >= REACHED_DELAY:
                    st.lowered = True
                    st.cap = None
            else:
                self._reached_since = None

        if st.lowered:
            desired = self._round(target - s.span - 0.5)
            phase = PHASE_LOWERED
            st.cap = None
        elif s.heater_locked and water + s.heater_gap < target - EPS:
            cap = self._ceil(water + s.heater_gap)
            if st.cap is not None:
                cap = max(cap, st.cap)
            cap = min(cap, target)
            st.cap = cap
            desired = cap
            phase = PHASE_HEATING_NO_HEATER
        else:
            st.cap = None
            desired = self._round(target)
            phase = PHASE_HEATING if water < target - EPS else PHASE_HOLDING

        desired = self._clamp(desired)
        write = self._maybe_write(inp, st, desired, force=force)
        return Decision(
            phase=phase,
            target=target,
            desired=desired,
            write=write,
            adopted_external=adopted,
        )

    def pause_restore(self, inp: Inputs) -> float | None:
        """Setpoint to write once when the Regler is paused.

        Leaves the spa on the real target instead of a lowered or capped
        value. Returns None when nothing needs to be written.
        """
        if not inp.available or inp.mode is None or inp.setpoint is None:
            return None
        st = self.modes.get(inp.mode)
        if st is None or st.target is None:
            return None
        st.lowered = False
        st.cap = None
        if abs(inp.setpoint - st.target) <= EPS:
            return None
        self._pre_write_setpoint = inp.setpoint
        self._last_write = inp.now
        return st.target

    # --- internals ----------------------------------------------------------

    def _check_external(self, inp: Inputs, st: _ModeState) -> bool:
        """Take over a setpoint someone else wrote; retry lost own writes."""
        if st.commanded is None:
            st.commanded = inp.setpoint
            return False
        sp = inp.setpoint
        if abs(sp - st.commanded) <= EPS:
            self._retries = 0
            return False
        if inp.now - self._last_write < ECHO_WINDOW:
            return False
        if inp.now - self._mode_since < MODE_GRACE:
            # After a mode change the spa reports that mode's own setpoint.
            # Treat it as the new baseline instead of an external change.
            if inp.now - self._seen_since >= SETPOINT_SETTLE:
                st.commanded = sp
            return False
        if inp.now - self._seen_since < EXTERNAL_STABLE:
            return False
        if (
            self._pre_write_setpoint is not None
            and abs(sp - self._pre_write_setpoint) <= EPS
            and self._retries < MAX_RETRIES
        ):
            # Our last write did not arrive; leave commanded as is so that
            # _maybe_write sends it again.
            self._retries += 1
            st.commanded = None
            return False
        # Someone else set a new value: it becomes the target of this mode,
        # unless it is just our own lowered/capped value coming back.
        st.target = sp
        st.lowered = False
        st.cap = None
        st.commanded = sp
        self._reached_since = None
        self._pre_write_setpoint = None
        self._retries = 0
        return True

    def _maybe_write(
        self, inp: Inputs, st: _ModeState, desired: float, *, force: bool
    ) -> float | None:
        if abs(inp.setpoint - desired) <= EPS:
            st.commanded = desired
            return None
        if st.commanded is not None and abs(st.commanded - desired) <= EPS:
            # Already sent; waiting for Gecko to report it.
            return None
        if inp.now - self._mode_since < MODE_SETTLE:
            return None
        if not force and inp.now - self._seen_since < SETPOINT_SETTLE:
            return None
        self._pre_write_setpoint = inp.setpoint
        self._last_write = inp.now
        st.commanded = desired
        return desired

    def _state(self, mode: str) -> _ModeState:
        if mode not in self.modes:
            self.modes[mode] = _ModeState()
        return self.modes[mode]

    def _clamp(self, value: float) -> float:
        s = self.settings
        return min(max(value, s.min_temp), s.max_temp)

    def _round(self, value: float) -> float:
        step = self.settings.step
        return round(round(value / step) * step, 2)

    def _ceil(self, value: float) -> float:
        step = self.settings.step
        return round(math.ceil(value / step - 1e-6) * step, 2)
