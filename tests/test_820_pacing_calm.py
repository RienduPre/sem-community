"""#820 follow-up (02.10.2026) — the pacer must be calm on a real inverter.

@ArneGollin1987 ran 2.2.0-beta.6 with the register read-back fix. His
Sungrow, through the mkaiser Modbus package, exposes the charge limit as a
TEMPLATE number (min 10, step 100). Its set_value writes register 33046 with
value/10, and its state is read back from a Modbus sensor a scan later. The
inverter also applies its own value: SEM wanted 1550 W, it reads 1449 W.

Three faults, each a test class below:

1. **Step grid.** The clamp anchored the grid at min=10, so with step 100
   SEM wrote 1410 W and with step 10 it wrote 1151 W. Steps of 100 W mean
   1400 W.
2. **Confirmation.** "Not exactly the cap" was judged refused. A register
   that moved toward the cap, or sits within a step or 10 % of it, TOOK the
   write. Refused means it did not move at all.
3. **Hammering.** The cap was rewritten every few seconds, 1150↔1158. A
   rewrite needs a real difference (more than one step, 100 W or 5 %) and at
   most one every 5 minutes. The release to full power below the buffer
   still goes at once.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solar_energy_management.coordinator.charge_pacing import (
    ChargePacingWriter,
)
from custom_components.solar_energy_management.coordinator.power_control import (
    clamp_to_entity_range,
)
from custom_components.solar_energy_management.utils.log_gate import (
    reset_log_gate,
)

ENTITY = "number.battery_max_charge_power_inv_1"


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class TemplateNumber:
    """Arne's template number: min 10, step 100 (or 10), a write lands on
    the register only on the NEXT read (the Modbus scan), and the inverter
    may apply its own value instead of the one sent."""

    def __init__(self, value, *, lo=10.0, hi=5000.0, step=100.0,
                 applies=None, refuse_all=False):
        self.state = SimpleNamespace(
            state=str(value),
            attributes={"min": lo, "max": hi, "step": step,
                        "unit_of_measurement": "W"})
        self.applies = applies  # value -> what the inverter really takes
        self.refuse_all = refuse_all
        self.pending: float | None = None
        self.writes: list[float] = []

    async def call(self, domain, service, data, blocking=False):
        value = float(data["value"])
        self.writes.append(value)
        if self.refuse_all:
            return
        self.pending = self.applies(value) if self.applies else value

    def scan(self) -> None:
        """The Modbus sensor's next read."""
        if self.pending is not None:
            self.state.state = str(self.pending)
            self.pending = None

    def hass(self):
        return SimpleNamespace(
            states=SimpleNamespace(get=lambda _eid: self.state),
            services=SimpleNamespace(async_call=AsyncMock(side_effect=self.call)),
        )


def _run(coro):
    return asyncio.run(coro)


def _writer(clock):
    w = ChargePacingWriter()
    w._clock = clock
    return w


@pytest.fixture(autouse=True)
def _fresh_log_gate():
    reset_log_gate()
    yield
    reset_log_gate()


class TestTheStepGridStartsAtZero:
    @pytest.mark.parametrize("cap, step, want", [
        (1458.0, 100.0, 1400.0),   # Arne with step 100: SEM wrote 1410
        (1158.0, 10.0, 1150.0),    # Arne with step 10: SEM wrote 1151
        (1550.0, 100.0, 1500.0),
    ])
    def test_a_min_off_the_grid_does_not_move_the_grid(self, cap, step, want):
        attrs = {"min": 10, "max": 5000, "step": step}
        got = clamp_to_entity_range(attrs, cap, 1.0, round_down_to_step=True)
        assert got == pytest.approx(want)

    def test_a_min_on_the_grid_keeps_its_own_grid(self):
        """min 50, step 25: 50 is on the grid of 25, so the grid is the
        same either way — and never a value off the user's step."""
        attrs = {"min": 50, "max": 5000, "step": 25}
        assert clamp_to_entity_range(attrs, 1160.0, 1.0,
                                     round_down_to_step=True) == 1150.0

    def test_a_grid_anchored_at_its_min_when_the_min_is_on_it(self):
        """min 5, step 10 in a kW-scaled entity is not Arne's case; a
        range that STARTS on its own grid (min 100, step 100) is the same
        grid as zero."""
        attrs = {"min": 100, "max": 5000, "step": 100}
        assert clamp_to_entity_range(attrs, 1458.0, 1.0,
                                     round_down_to_step=True) == 1400.0

    def test_a_cap_below_the_first_step_writes_the_entitys_minimum(self):
        """min 10, step 100: nothing on the grid is below 100 but the entity
        takes its min, so a 60 W cap becomes 10 W — the smallest cap it has."""
        attrs = {"min": 10, "max": 5000, "step": 100}
        assert clamp_to_entity_range(attrs, 60.0, 1.0,
                                     round_down_to_step=True) == 10.0

    def test_the_max_is_floored_on_the_zero_grid(self):
        attrs = {"min": 10, "max": 4555, "step": 100}
        assert clamp_to_entity_range(attrs, 9000.0, 1.0,
                                     round_down_to_step=True) == 4500.0

    def test_the_writer_sends_arnes_register_a_value_on_its_step(self):
        clock = Clock()
        reg = TemplateNumber(1560.0)
        w = _writer(clock)
        _run(w.apply(reg.hass(), ENTITY, 1458.0, observer=False))
        assert reg.writes == [1400.0]


class TestATakenWriteIsNotARefusal:
    def test_the_inverter_applies_its_own_value(self):
        """SEM wanted 1550 (sent 1500), the inverter reads 1449."""
        clock = Clock()
        reg = TemplateNumber(2500.0, applies=lambda v: 1449.0)
        w = _writer(clock)
        h = reg.hass()
        assert _run(w.apply(h, ENTITY, 1550.0, observer=False)) == "wrote"
        actions = []
        for _ in range(10):
            reg.scan()
            clock.advance(10)
            actions.append(_run(w.apply(h, ENTITY, 1550.0, observer=False)))
        assert "write_refused" not in actions, actions
        assert len(reg.writes) == 1

    def test_a_drift_of_the_devices_own_is_not_a_taken_write(self):
        """Review: SEM writes 1500, the device drops it, the register drifts
        2000 → 1800 for its own reasons. Moved toward, never in the band:
        pending through the window, then refused — and no fabricated "took
        the write as 1800 W"."""
        clock = Clock()
        reg = TemplateNumber(2000.0, refuse_all=True)
        w = _writer(clock)
        h = reg.hass()
        assert _run(w.apply(h, ENTITY, 1500.0, observer=False)) == "wrote"
        reg.state.state = "1800.0"
        actions = []
        for _ in range(12):
            clock.advance(10)
            actions.append(_run(w.apply(h, ENTITY, 1500.0, observer=False)))
        assert actions[0] == "held" and actions[-1] == "write_refused", actions
        assert w.applied_differs is None
        assert len(reg.writes) == 1

    def test_a_late_entry_into_the_band_is_taken(self):
        clock = Clock()
        reg = TemplateNumber(5000.0, refuse_all=True)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1500.0, observer=False))
        for _ in range(12):
            clock.advance(10)
            out = _run(w.apply(h, ENTITY, 1500.0, observer=False))
        assert out == "write_refused"
        reg.state.state = "1500.0"
        clock.advance(10)
        assert _run(w.apply(h, ENTITY, 1500.0, observer=False)) == "held"

    def test_a_read_back_one_scan_late_is_not_a_refusal(self):
        clock = Clock()
        reg = TemplateNumber(5000.0)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1500.0, observer=False))
        clock.advance(10)
        assert _run(w.apply(h, ENTITY, 1500.0, observer=False)) == "held"
        reg.scan()
        clock.advance(10)
        assert _run(w.apply(h, ENTITY, 1500.0, observer=False)) == "held"

    def test_a_register_that_never_moves_is_refused(self):
        clock = Clock()
        reg = TemplateNumber(1560.0, refuse_all=True)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1000.0, observer=False))
        out = None
        for _ in range(20):
            reg.scan()
            clock.advance(10)
            out = _run(w.apply(h, ENTITY, 1000.0, observer=False))
        assert out == "write_refused"


class TestNoHammering:
    def test_jitter_1150_1158_over_a_minute_is_one_write(self):
        clock = Clock()
        reg = TemplateNumber(5000.0, step=10.0)
        w = _writer(clock)
        h = reg.hass()
        for i in range(12):
            _run(w.apply(h, ENTITY, 1150.0 if i % 2 else 1158.0,
                         observer=False))
            reg.scan()
            clock.advance(5)
        assert len(reg.writes) == 1, reg.writes

    def test_a_real_change_waits_for_the_interval(self):
        clock = Clock()
        reg = TemplateNumber(5000.0)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1500.0, observer=False))
        reg.scan()
        clock.advance(60)
        _run(w.apply(h, ENTITY, 2500.0, observer=False))
        assert reg.writes == [1500.0], "a second write after one minute"
        clock.advance(300)
        assert _run(w.apply(h, ENTITY, 2500.0, observer=False)) == "wrote"
        assert reg.writes == [1500.0, 2500.0]

    def test_a_small_change_is_never_written(self):
        """Inside the deadband: max(step 100, 100 W, 5 %)."""
        clock = Clock()
        reg = TemplateNumber(5000.0)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 2000.0, observer=False))
        reg.scan()
        clock.advance(3600)
        _run(w.apply(h, ENTITY, 2090.0, observer=False))
        assert reg.writes == [2000.0]

    def test_a_refused_cap_is_not_retried_with_a_slightly_different_value(self):
        clock = Clock()
        reg = TemplateNumber(1560.0, refuse_all=True)
        w = _writer(clock)
        h = reg.hass()
        for i in range(60):
            _run(w.apply(h, ENTITY, 1000.0 + (i % 3) * 10, observer=False))
            reg.scan()
            clock.advance(10)
        assert len(reg.writes) == 1, reg.writes

    def test_the_release_below_the_buffer_goes_at_once(self):
        clock = Clock()
        reg = TemplateNumber(1560.0)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1200.0, observer=False, hw_max_w=5000.0))
        reg.scan()
        clock.advance(5)
        assert _run(w.apply(h, ENTITY, None, observer=False,
                            hw_max_w=5000.0)) == "restored"
        assert reg.writes == [1200.0, 5000.0]


class TestTheRegisterTookADifferentValue:
    """Arne: SEM wrote 1550 W, the register reads 1449 W. Register 33047 is
    in 0.01 kW and the package divides by 10, and the inverter has its own
    limit — so a value can land as a different number than written. The
    write is TAKEN, and the difference is said, once."""

    def _taken_at(self, wrote, reads):
        clock = Clock()
        reg = TemplateNumber(2500.0, step=10.0, applies=lambda v: reads)
        w = _writer(clock)
        h = reg.hass()
        assert _run(w.apply(h, ENTITY, wrote, observer=False)) == "wrote"
        reg.scan()
        for _ in range(2):  # settled = the same value on two reads
            clock.advance(10)
            assert _run(w.apply(h, ENTITY, wrote, observer=False)) == "held"
        return w, h, reg, clock

    def test_the_difference_is_in_the_writer_state(self):
        w, *_ = self._taken_at(1550.0, 1449.0)
        assert w.applied_differs == (1449.0, 1550.0)

    def test_one_in_band_read_is_not_yet_a_settled_difference(self):
        clock = Clock()
        reg = TemplateNumber(2500.0, step=10.0, applies=lambda v: 1449.0)
        w = _writer(clock)
        h = reg.hass()
        _run(w.apply(h, ENTITY, 1550.0, observer=False))
        reg.scan()
        clock.advance(10)
        assert _run(w.apply(h, ENTITY, 1550.0, observer=False)) == "held"
        assert w.applied_differs is None, "one read is not settled"

    def test_a_value_within_a_step_is_not_a_difference(self):
        w, *_ = self._taken_at(1550.0, 1545.0)
        assert w.applied_differs is None

    def test_the_difference_is_said_once(self, caplog):
        with caplog.at_level(logging.WARNING):
            w, h, reg, clock = self._taken_at(1550.0, 1449.0)
            for _ in range(10):
                clock.advance(10)
                _run(w.apply(h, ENTITY, 1550.0, observer=False))
        lines = [r.getMessage() for r in caplog.records
                 if "took the write as" in r.getMessage()]
        assert len(lines) == 1, lines
        assert "1449 W, not 1550 W" in lines[0]
        assert "unit" in lines[0] and "own limit" in lines[0]

    def test_the_diagnose_button_shows_it(self):
        from custom_components.solar_energy_management.coordinator.battery_diag import (
            pacing_actuation_diag,
        )
        w, h, reg, clock = self._taken_at(1550.0, 1449.0)
        coordinator = SimpleNamespace(
            config={"battery_charge_power_limit_entity": ENTITY},
            _charge_pacing_writer=w, _charge_pacing_state={})
        out = _run(pacing_actuation_diag(h, coordinator))
        assert out["writer"]["applied_differs"] == {
            "register_w": 1449.0, "written_w": 1550.0}

    def test_a_new_write_clears_it(self):
        w, h, reg, clock = self._taken_at(1550.0, 1449.0)
        reg.applies = None
        clock.advance(400)
        assert _run(w.apply(h, ENTITY, 2500.0, observer=False)) == "wrote"
        assert w.applied_differs is None
