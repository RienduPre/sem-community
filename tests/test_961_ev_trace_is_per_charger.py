"""#961 (from discussion #958) — the EV trace called a fleet budget "commanded_amps".

RienduPre reported his commanded current hunting 0 → 6 → 12 → 8 → 6 → 0 several
times a minute on two Wallbox Pulsars. The trace in his diagnostics showed
exactly that shape, under `commanded_amps`, beside a per-charger mode reason
reading "always_max mode — charge at hardware maximum".

They are not the same quantity. `commanded_amps` was
`ChargingContext.calculated_current` — the fleet canonical budget, one number
for the house from the primary charger's config — while the reason came from
that charger's own mode decision. A budget following the sun is SUPPOSED to
move; printing it under the name of a command, next to a mode that promises
hardware maximum, is what turned normal behaviour into a bug report.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _coord(devices=None, calculated_current=7, observer=False):
    from custom_components.solar_energy_management.coordinator.coordinator import (
        SEMCoordinator,
    )
    c = object.__new__(SEMCoordinator)
    c._ev_devices = devices or {}
    c._observer_mode = observer
    c.config = {"ev_phases": 3, "ev_voltage": 230}
    return c


class _Trace:
    def __init__(self):
        self.subs = {}

    def subsystem(self, name):
        self.subs.setdefault(name, SimpleNamespace(management=None, process=None,
                                                   integration=None))
        return self.subs[name]


def _sem_data(calculated_current=7, reason="always_max mode — charge at hardware maximum"):
    return SimpleNamespace(
        calculated_current=calculated_current,
        charging_strategy_reason=reason,
        available_power=4200.0,
    )


def _power(ev_power=11000):
    return SimpleNamespace(battery_soc=74.5, ev_connected=True, ev_power=ev_power)


def _run(coord):
    coord.time_manager = MagicMock()
    coord.time_manager.is_night_mode.return_value = False
    coord._curtailment_last = None
    t = _Trace()
    coord._trace_ev(t, _sem_data(), _power())
    return t.subs["ev"].process.data


def test_the_fleet_budget_keeps_its_own_name():
    """The number that follows the sun is a BUDGET and says so."""
    coord = _coord(devices={}, calculated_current=7)
    data = _run(coord)
    assert data["budget_amps"] == 7


def test_commanded_amps_is_what_sem_asked_the_chargers_for():
    """Two chargers, each with its own setpoint — the commanded figure is
    theirs, not the house budget's."""
    left = SimpleNamespace(_current_setpoint=16)
    right = SimpleNamespace(_current_setpoint=0)
    coord = _coord(devices={"ev_charger": left, "ev_charger_1": right})
    data = _run(coord)
    assert data["commanded_amps"] == 16, "the budget (7) must not stand in for the command"
    assert data["budget_amps"] == 7


def test_a_fleet_gets_its_breakdown():
    """RienduPre's shape: the per-charger split is what makes a two-charger
    trace readable at all."""
    coord = _coord(devices={"ev_charger": SimpleNamespace(_current_setpoint=16),
                            "ev_charger_1": SimpleNamespace(_current_setpoint=6)})
    data = _run(coord)
    assert data["per_charger_amps"] == {"ev_charger": 16, "ev_charger_1": 6}
    assert data["commanded_amps"] == 22


def test_one_charger_gets_no_noisy_breakdown():
    coord = _coord(devices={"ev_charger": SimpleNamespace(_current_setpoint=10)})
    data = _run(coord)
    assert "per_charger_amps" not in data
    assert data["commanded_amps"] == 10


def test_no_chargers_falls_back_to_the_budget():
    """An install with no charger registered still has a budget to show, and
    the old behaviour is what it had."""
    coord = _coord(devices={})
    data = _run(coord)
    assert data["commanded_amps"] == data["budget_amps"] == 7


def test_a_setpoint_that_is_not_a_number_is_skipped_not_fatal():
    coord = _coord(devices={"a": SimpleNamespace(_current_setpoint="nope"),
                            "b": SimpleNamespace(_current_setpoint=8)})
    data = _run(coord)
    assert data["per_charger_amps"] == {"b": 8}


def test_the_off_mode_case_from_the_report_reads_zero():
    """His 16:08 window: off mode, hands-off, 10.9 kW still flowing. The
    command is 0 and must not borrow the budget's number."""
    coord = _coord(devices={"ev_charger": SimpleNamespace(_current_setpoint=0)})
    coord.time_manager = MagicMock()
    coord.time_manager.is_night_mode.return_value = False
    coord._curtailment_last = None
    t = _Trace()
    coord._trace_ev(
        t,
        _sem_data(calculated_current=5,
                  reason="off mode — hands-off, SEM sends nothing to this charger (#898)"),
        _power(ev_power=10960),
    )
    data = t.subs["ev"].process.data
    assert data["commanded_amps"] == 0
    assert data["budget_amps"] == 5
