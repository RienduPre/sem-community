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


# ── what the ruflo review refuted ─────────────────────────────────────

def _run_full(coord, sem=None, power=None):
    coord.time_manager = MagicMock()
    coord.time_manager.is_night_mode.return_value = False
    coord._curtailment_last = None
    t = _Trace()
    coord._trace_ev(t, sem or _sem_data(), power or _power())
    return t.subs["ev"]


def test_a_stalled_charger_is_not_absorbed_by_a_healthy_one():
    """REFUTED: the fleet SUM was checked against ONE phase/voltage pair —
    necessarily the primary's. A 1-phase charger drawing correctly and a
    3-phase charger stalled at 0 W summed to a threshold the healthy one
    cleared alone, so the stall read OK. That is the flap this check exists
    to catch."""
    from custom_components.solar_energy_management.coordinator.cycle_trace import (
        LayerStatus,
    )
    a = SimpleNamespace(_current_setpoint=10, phases=1, voltage=230)   # drawing
    b = SimpleNamespace(_current_setpoint=6, phases=3, voltage=230)    # stalled
    coord = _coord(devices={"A": a, "B": b})
    coord.config = {"ev_phases": 1, "ev_voltage": 230}                 # primary is 1φ
    power = SimpleNamespace(battery_soc=50.0, ev_connected=True, ev_power=2300,
                            ev_power_per_charger={"A": 2300.0, "B": 0.0})
    ev = _run_full(coord, power=power)
    assert ev.integration.data["match"] is False, "B is stalled and must show"
    assert ev.integration.status is LayerStatus.DEGRADED
    assert "B" in ev.integration.detail
    assert ev.integration.data["per_charger_match"] == {"A": True, "B": False}


def test_a_healthy_fleet_still_reads_ok():
    from custom_components.solar_energy_management.coordinator.cycle_trace import (
        LayerStatus,
    )
    a = SimpleNamespace(_current_setpoint=10, phases=1, voltage=230)
    b = SimpleNamespace(_current_setpoint=6, phases=3, voltage=230)
    coord = _coord(devices={"A": a, "B": b})
    power = SimpleNamespace(battery_soc=50.0, ev_connected=True, ev_power=6440,
                            ev_power_per_charger={"A": 2300.0, "B": 4140.0})
    ev = _run_full(coord, power=power)
    assert ev.integration.data["match"] is True
    assert ev.integration.status is LayerStatus.OK


def test_observer_mode_still_says_whether_sem_would_charge():
    """REFUTED: observer mode zeroes every setpoint by design, so
    commanded_amps is honestly 0 — but p_status then read IDLE always,
    destroying the 'would charge' signal on the rig this project verifies on."""
    from custom_components.solar_energy_management.coordinator.cycle_trace import (
        LayerStatus,
    )
    coord = _coord(devices={"ev_charger": SimpleNamespace(_current_setpoint=0)},
                   observer=True)
    ev = _run_full(coord)
    assert ev.process.data["commanded_amps"] == 0, "observer commands nothing"
    assert ev.process.data["budget_amps"] == 7
    assert ev.process.status is LayerStatus.OK, "SEM would charge — say so"


def test_a_truly_idle_install_still_reads_idle():
    from custom_components.solar_energy_management.coordinator.cycle_trace import (
        LayerStatus,
    )
    coord = _coord(devices={"ev_charger": SimpleNamespace(_current_setpoint=0)})
    ev = _run_full(coord, sem=_sem_data(calculated_current=0))
    assert ev.process.status is LayerStatus.IDLE


def test_the_fleet_roster_makes_absence_checkable():
    """REFUTED (c): a charger dropped for an unreadable setpoint was
    indistinguishable from one that is idle, because nothing said who the
    fleet was."""
    coord = _coord(devices={"a": SimpleNamespace(_current_setpoint="nope"),
                            "b": SimpleNamespace(_current_setpoint=8)})
    data = _run(coord)
    assert data["fleet_charger_ids"] == ["a", "b"]
    assert data["per_charger_amps"] == {"b": 8}
