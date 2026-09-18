"""#975 — a plug-in has an interruption BUDGET, not just a stop rate.

@hoyte (Zaptec Go 2, 2.1.0-beta.20): "SEM sends too many start-stop commands
… apparently there is some limit in the device in how many it can take.
Resulting in an error and not charging at all anymore." Measured on SEM's own
loop against a flickering cloudy-day surplus: 6-8 interruptions an hour, and
~30 on a charger driven by a current number alone.

Every guard SEM had bounds how FAST it may stop — the rolling median, the
2 A deadband, the 30 s cadence, the 180 s disable delay, the post-stop
settle. None bounded how OFTEN, and that is exactly the budget the Go 2
spends: the charge point counts session interruptions itself and locks out.

So the hysteresis grows with the churn it has already caused: past
``SESSION_STOP_BUDGET`` stops in one plug-in, each further stop widens the
start delay and the transient bridge, to a ceiling. Pinned here, including
the two things that must NOT move — the structural stop (#461: when the sun
is gone there is nothing to bridge to) and a fresh plug-in's budget.
"""
from __future__ import annotations


import pytest

from custom_components.solar_energy_management.coordinator.charge_stability import (
    ChargeStability,
    DEFAULT_DISABLE_DELAY_S,
    DEFAULT_ENABLE_DELAY_S,
    SESSION_CHURN_MAX,
    SESSION_CHURN_PER_STOP,
    SESSION_STOP_BUDGET,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerDecision,
    ChargerEnergy,
    ChargerIntent,
    ChargerPower,
    ChargerView,
    FleetContext,
)

CID = "zaptec"


class _Adapter:
    last_intent = None
    min_current_a = 6
    max_current_a = 16

    def actual_charging(self, power):
        return power.power_w > 500.0


def _view(*, power_w=4000.0, connected=True, solar_w=3000.0, mode="solar_only"):
    return ChargerView(
        power=ChargerPower(charger_id=CID, power_w=power_w, connected=connected,
                           charging=power_w > 500),
        energy=ChargerEnergy(charger_id=CID),
        mode=mode,
        config={"ev_min_current": 6, "ev_phases": 1, "ev_voltage": 230,
                "ev_max_current": 16},
        fleet=FleetContext(is_night=False, solar_w=solar_w, min_solar_w=200.0,
                           tariff_level=None),
    )


def _charge(amps=8):
    return ChargerDecision(charger_id=CID, mode="solar_only",
                           intent=ChargerIntent.CHARGE_AT_AMPS,
                           commanded_amps=amps, reason="solar")


def _idle(bridgeable=True):
    return ChargerDecision(charger_id=CID, mode="solar_only",
                           intent=ChargerIntent.IDLE, commanded_amps=0,
                           reason="deficit", bridgeable=bridgeable)


def _own(cs, t, span=1200.0):
    """Let SEM take ownership of the session (#552: the bridge only protects
    a charge SEM started). Returns the time it did."""
    t0 = t
    while t - t0 <= span:
        cs.filter(_charge(), _view(power_w=4000.0), _Adapter(), now_ts=t)
        if CID in cs._sem_session:
            return t
        t += 10.0
    raise AssertionError("SEM never took the session")


def _run_stop(cs, t, *, bridgeable=True, power_w=4000.0, span=4000.0):
    """Hold a deficit until SEM actually spends an interruption; return when.

    The stop is read from the budget counter itself, not guessed from the
    reason text — that is the thing under test and the thing the charge point
    counts."""
    t0, before = t, cs._session_stops.get(CID, 0)
    while t - t0 <= span:
        cs.filter(_idle(bridgeable),
                  _view(power_w=power_w, solar_w=3000.0 if bridgeable else 0.0),
                  _Adapter(), now_ts=t)
        if cs._session_stops.get(CID, 0) > before:
            return t
        t += 10.0
    raise AssertionError("the bridge never stopped")


@pytest.mark.unit
class TestTheBudgetItself:
    def test_the_first_stops_are_free(self):
        cs = ChargeStability()
        assert cs.session_churn_factor(CID) == 1.0
        cs._session_stops[CID] = SESSION_STOP_BUDGET
        assert cs.session_churn_factor(CID) == 1.0

    def test_each_further_stop_widens_the_delays(self):
        cs = ChargeStability()
        cs._session_stops[CID] = SESSION_STOP_BUDGET + 1
        assert cs.session_churn_factor(CID) == 1.0 + SESSION_CHURN_PER_STOP
        cs._session_stops[CID] = SESSION_STOP_BUDGET + 2
        assert cs.session_churn_factor(CID) == 1.0 + 2 * SESSION_CHURN_PER_STOP

    def test_it_is_capped(self):
        cs = ChargeStability()
        cs._session_stops[CID] = 500
        assert cs.session_churn_factor(CID) == SESSION_CHURN_MAX


@pytest.mark.unit
class TestItActuallyReducesTheInterruptions:
    def test_a_stop_spends_one_interruption(self):
        cs = ChargeStability()
        t = _own(cs, 1000.0)
        _run_stop(cs, t)
        assert cs._session_stops[CID] == 1

    def test_the_bridge_gets_longer_as_the_session_churns(self):
        """The reporter's afternoon: the same flicker, stop after stop."""
        cs = ChargeStability()
        t = _own(cs, 1000.0)
        spans = []
        for _ in range(SESSION_STOP_BUDGET + 3):
            start = t
            stopped = _run_stop(cs, t)
            spans.append(stopped - start)
            t = stopped + 600.0          # settle, then the car draws again
            t = _own(cs, t)
        # the first stops are the plain disable delay; the later ones are
        # multiples of it, and the count is what bought the slack
        assert spans[0] == pytest.approx(DEFAULT_DISABLE_DELAY_S, abs=15)
        assert spans[-1] > spans[0] * 1.4, spans
        assert spans[-1] <= DEFAULT_DISABLE_DELAY_S * SESSION_CHURN_MAX + 15

    def test_the_start_delay_widens_too(self):
        """Fewer stops is half the answer; the restarts are the other half."""
        cs = ChargeStability()
        cs._session_stops[CID] = SESSION_STOP_BUDGET + 2
        t = 5000.0
        held = None
        while t < 5000.0 + DEFAULT_ENABLE_DELAY_S * 3:
            d = cs.filter(_charge(), _view(power_w=0.0), _Adapter(), now_ts=t)
            if d.intent is ChargerIntent.CHARGE_AT_AMPS:
                held = t - 5000.0
                break
            t += 10.0
        assert held is not None and held > DEFAULT_ENABLE_DELAY_S, held


@pytest.mark.unit
class TestWhatMustNotMove:
    def test_a_structural_idle_still_stops_promptly(self):
        """#461: with the sun gone there is nothing to bridge TO, and holding
        the contactor closed imports grid. Churn must never buy slack here."""
        cs = ChargeStability()
        cs._session_stops[CID] = 500          # maximum churn
        t = _own(cs, 9000.0)
        stopped = _run_stop(cs, t, bridgeable=False, power_w=4000.0)
        assert stopped - t <= 120.0, stopped - t

    def test_a_new_plug_in_starts_a_fresh_budget(self):
        cs = ChargeStability()
        cs._session_stops[CID] = 9
        cs.filter(_charge(), _view(connected=False), _Adapter(), now_ts=100.0)
        assert CID not in cs._session_stops
        assert cs.session_churn_factor(CID) == 1.0

    def test_a_mode_change_does_not_refund_the_budget(self):
        """The charge point's own counter does not reset for a mode change,
        so neither does ours — otherwise the budget is refundable by flapping
        the mode, which is what a churning install does anyway."""
        cs = ChargeStability()
        cs._session_stops[CID] = 9
        cs.filter(_charge(), _view(mode="always_max"), _Adapter(), now_ts=100.0)
        assert cs._session_stops[CID] == 9

    def test_another_charger_keeps_its_own_budget(self):
        cs = ChargeStability()
        cs._session_stops[CID] = 9
        assert cs.session_churn_factor("other") == 1.0


@pytest.mark.unit
class TestItSurvivesARestart:
    def test_the_budget_round_trips(self):
        """A restart is not a new plug-in — and the charge point's counter
        did not reset either."""
        cs = ChargeStability()
        cs._session_stops[CID] = SESSION_STOP_BUDGET + 2
        saved = cs.snapshot_timers(1000.0)
        assert saved["session_stops"][CID] == SESSION_STOP_BUDGET + 2
        fresh = ChargeStability()
        fresh.restore_timers(saved, 50.0)
        assert fresh.session_churn_factor(CID) == cs.session_churn_factor(CID)

    def test_a_corrupt_blob_is_ignored(self):
        fresh = ChargeStability()
        fresh.restore_timers({"session_stops": {CID: "many", "x": -3}}, 10.0)
        assert fresh._session_stops == {}
