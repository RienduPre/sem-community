"""#899 round 2 — the meter check sat on the door almost nobody uses.

Round 1 closed the forecast redirect: a commanded pack keeps its watts, and
three cycles of grid import with a redirect in the budget drop the redirect
for the rest of the plug-in. Ten months of that check ran and it never once
struck on koen71's install, because the watts reach the car by a second
route and round 1 could not see it.

``solar_only`` builds its budget from ``self_consumption_surplus_w``, which
subtracts the pack's charge power — *unless* the #576 position rule says the
car outranks the pack. Then it simply stops subtracting, and those same
watts land in ``bare``. A stock install takes that route: a charger seeds at
priority 3 (5 from the config flow), the pack at 100, so every pack above
the reserve floor is outranked. On that route round 1 recorded
``redirect_w=0``, the strike rule needs ``redirect_w > 0``, and the check
could not arm — the car took the pack's 2700 W and the meter bought them,
for ever, with no strike and no veto.

koen71's own numbers, on develop before this fix:

    solar_only: surplus=2700W (bare=2700W + redirect=0W) → 11A
                (solar=3800W, home=1100W, batt_chg=2700W)
    ten cycles of 2700 W import → strikes 0, vetoed False

Bug class 29: the guard was written into one branch of the split and the
other branch handed the caller's watts straight through. The gate moves to
``_ev_reclaims`` — above the split — so no route to the budget skips it, and
the decision now declares the pack watts it spent whichever door they came
through.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.solar_energy_management.consts.core import (
    DEFAULT_BATTERY_SURPLUS_PRIORITY,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerEnergy,
    ChargerIntent,
    ChargerPower,
    ChargerView,
    FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide import (
    SolarOnlyMode,
    _ev_reclaims,
    decide,
    reclaimed_pack_w,
    self_consumption_surplus_w,
)
from custom_components.solar_energy_management.coordinator.energy_reclaim import (
    REDIRECT_VETO_STRIKES,
)
from custom_components.solar_energy_management.coordinator.per_charger_context import (
    PerChargerState,
    note_redirect_outcome,
)

#: koen71's cycle: a 3.8 kW sun, 1.1 kW of house, and a pack eating 2.7 kW.
SOLAR_W = 3800.0
HOME_W = 1100.0
PACK_W = 2700.0


def _view(*, redirect_allowed=True, ev_priority=3,
          battery_priority=DEFAULT_BATTERY_SURPLUS_PRIORITY,
          battery_soc=60.0, battery_commanded=False,
          solar_w=SOLAR_W, home_w=HOME_W, battery_charge_w=PACK_W,
          charging=True, grid_import_w=PACK_W):
    """A charger on ``solar_only`` with the stock priority seeds — the
    arrangement #576 gives every install with a readable pack."""
    fleet = FleetContext(
        solar_w=solar_w, home_w=home_w, battery_charge_w=battery_charge_w,
        battery_soc=battery_soc, auto_start_soc=90.0, priority_soc=30.0,
        battery_capacity_kwh=15.0, forecast_remaining_kwh=30.0,
        battery_priority=battery_priority, battery_commanded=battery_commanded,
        min_solar_w=200.0, grid_import_w=grid_import_w, grid_import_known=True,
    )
    return ChargerView(
        power=ChargerPower(charger_id="terra", power_w=2500.0,
                           connected=True, charging=charging),
        energy=ChargerEnergy(charger_id="terra", day_kwh=0.0),
        mode="solar_only",
        config={"ev_min_current": 6, "ev_phases": 1, "ev_voltage": 230,
                "ev_max_current": 16},
        fleet=fleet, ev_priority=ev_priority,
        redirect_allowed=redirect_allowed,
    )


class TestTheStockInstallTakesThisDoor:
    """The route round 1 could not see is not an edge case — it is the
    default. If this ever stops being true the rest of the file is still
    right, but the urgency behind it was real."""

    def test_the_pack_is_outranked_by_a_seeded_charger(self):
        # 3 is the coordinator's fallback seed, 5 the config-flow default
        # for ``ev_surplus_priority``; the pack seeds at 100.
        assert DEFAULT_BATTERY_SURPLUS_PRIORITY > 5

    def test_a_readable_pack_above_the_floor_hands_its_watts_over(self):
        assert _ev_reclaims(_view()) is True
        # …and the surplus is inflated by exactly the pack's charge power.
        assert self_consumption_surplus_w(_view()) == pytest.approx(PACK_W)
        assert reclaimed_pack_w(_view()) == pytest.approx(PACK_W)


class TestTheMeterNowSeesBothDoors:
    def test_the_decision_declares_the_watts_it_took_from_the_pack(self):
        d = SolarOnlyMode().decide(_view())
        assert d.intent is ChargerIntent.CHARGE_AT_AMPS, d.reason
        # pre-fix this was 0.0 while the car drew 11 A of the pack's watts
        assert d.redirect_w == pytest.approx(PACK_W)

    def test_three_importing_cycles_veto_the_reclaim(self):
        """End to end on koen71's shape: decide → record → decide again."""
        st = PerChargerState()
        view = _view()
        for _ in range(REDIRECT_VETO_STRIKES):
            d = SolarOnlyMode().decide(
                replace(view, redirect_allowed=not st.redirect_vetoed))
            assert d.intent is ChargerIntent.CHARGE_AT_AMPS, d.reason
            note_redirect_outcome(
                st, redirect_w=d.redirect_w, grid_import_w=PACK_W,
                charging=True, grid_import_known=True,
            )
        assert st.redirect_vetoed is True
        after = SolarOnlyMode().decide(replace(view, redirect_allowed=False))
        assert after.intent is ChargerIntent.IDLE, after.reason
        assert after.budget_w == pytest.approx(0.0)

    def test_the_veto_shuts_the_reclaim_not_only_the_redirect(self):
        assert _ev_reclaims(_view(redirect_allowed=False)) is False
        assert self_consumption_surplus_w(
            _view(redirect_allowed=False)) == pytest.approx(0.0)

    def test_a_vetoed_idle_says_why_the_sun_stopped_counting(self):
        d = SolarOnlyMode().decide(_view(redirect_allowed=False))
        assert d.intent is ChargerIntent.IDLE
        assert "battery kept its charge" in d.reason, d.reason

    def test_a_pack_that_yields_is_never_vetoed(self):
        """The legitimate case still charges: the meter agrees, so no
        strike ever lands and the reclaim survives the whole session."""
        st = PerChargerState()
        for _ in range(REDIRECT_VETO_STRIKES * 3):
            d = SolarOnlyMode().decide(
                replace(_view(), redirect_allowed=not st.redirect_vetoed))
            # without this the test passes on the broken code too: a
            # declared 0 W can never strike, so "never vetoed" would be
            # true for the wrong reason
            assert d.redirect_w > 0, d.reason
            note_redirect_outcome(
                st, redirect_w=d.redirect_w, grid_import_w=0.0,
                charging=True, grid_import_known=True,
            )
        assert st.redirect_vetoed is False
        assert st.redirect_strikes == 0

    def test_an_unread_pack_is_credited_nothing_by_either_door(self):
        """(#875, and the other branch of the same split.) An unread SOC
        turns the position reclaim off — which used to send the cycle to
        the forecast redirect, where ``battery_soc`` is the reader's 0.0
        fallback and 1350 W of an unread pack got credited anyway."""
        blind = replace(
            _view(), fleet=replace(_view().fleet, battery_soc_known=False))
        assert _ev_reclaims(blind) is False
        d = SolarOnlyMode().decide(blind)
        assert d.redirect_w == pytest.approx(0.0), d.reason
        assert d.intent is ChargerIntent.IDLE, d.reason


_MODES = ["solar_only", "min_plus_solar", "solar_plus_battery",
          "solar_plus_cheap"]

_SHAPES = [
    ("position reclaim (stock seeds)", {}),
    ("forecast redirect (no pack in the list)", {"battery_priority": None}),
    ("forecast redirect (car ranked below the pack)", {"ev_priority": 200}),
    ("pack under the reserve floor", {"battery_soc": 10.0}),
    ("commanded pack", {"battery_commanded": True}),
    ("pack idle, plain surplus", {"battery_charge_w": 0.0, "home_w": 500.0}),
    ("fat sun, both routes live", {"solar_w": 9000.0}),
    ("pack high enough for battery assist",
     {"battery_soc": 75.0, "solar_w": 6000.0, "home_w": 1000.0}),
]

_CASES = [(m, n, k) for m in _MODES for n, k in _SHAPES]


class TestEveryDoorDeclaresItself:
    """The oracle that makes the class unrepresentable.

    Asks the decision a question no new door can dodge: run the same cycle
    twice, once as it is and once with the pack's charge power moved into
    the house (the pack keeps every watt, nothing to reclaim, nothing to
    redirect). The budget a decision gained over that counterfactual IS the
    pack's contribution — and it has to equal what the decision declared,
    or the meter is judging the wrong number again.

    Over MODES as well as shapes, because the first round of this fix
    passed a shape-only version of this test while ``min_plus_solar`` and
    ``solar_plus_battery`` still spent 2700 W of the pack and declared 0.
    They reach the same reclaim through ``_battery_assist_split``.

    Not in scope: a decision that BUYS grid on purpose (the Min floor, the
    night top-up, a cheap-hour top-up). Import proves nothing about the
    pack there, so those branches declare 0 deliberately —
    ``TestTheFloorBuysGridOnPurpose`` pins that.
    """

    MODES = _MODES
    SHAPES = _SHAPES

    @pytest.mark.parametrize(
        "mode,name,kwargs", _CASES,
        ids=[f"{m}-{n}" for m, n, _k in _CASES])
    def test_the_budget_gained_from_the_pack_is_the_budget_declared(
            self, mode, name, kwargs):
        view = replace(_view(**kwargs), mode=mode)
        pack_w = float(view.fleet.battery_charge_w)
        # the same cycle with the pack's draw counted as house load
        untouchable = replace(
            view,
            fleet=replace(view.fleet, battery_charge_w=0.0,
                          home_w=view.fleet.home_w + pack_w),
        )
        got = decide(view)
        if got.intent is not ChargerIntent.CHARGE_AT_AMPS:
            pytest.skip(f"{mode}/{name}: no charge to judge")
        base = decide(untouchable)
        gained = float(got.budget_w) - float(base.budget_w)
        assert got.redirect_w == pytest.approx(gained, abs=1.0), (
            f"{mode}/{name}: the budget gained {gained:.0f}W from the pack "
            f"and declared {got.redirect_w:.0f}W — the meter check judges "
            f"the declared number, so the difference is watts nothing audits"
        )

    def test_the_oracle_actually_judged_something(self):
        """The skip above is a real exit, so prove the matrix is not one
        big skip: at least one case per mode has to reach a CHARGE."""
        for mode in self.MODES:
            charged = [
                n for n, k in self.SHAPES
                if decide(replace(_view(**k), mode=mode)).intent
                is ChargerIntent.CHARGE_AT_AMPS
            ]
            assert charged, f"{mode}: every shape skipped — the oracle is blind"


class TestTheFloorBuysGridOnPurpose:
    """The one branch that spends pack watts and declares none, on purpose.

    ``min_plus_solar``'s Min floor imports by design — the "At least"
    guarantee outranks the sun once the night window can no longer deliver
    it. A meter reading there says nothing about whether the pack yielded,
    so declaring pack watts would strike the mode for doing its job, three
    cycles at a time, until the reclaim was vetoed for the plug-in.
    """

    def test_the_min_floor_declares_nothing(self):
        view = replace(
            _view(battery_soc=75.0, solar_w=1500.0, home_w=1000.0),
            mode="min_plus_solar", target_kwh=8.0, night_deliverable_kwh=1.0,
        )
        d = decide(view)
        assert d.intent is ChargerIntent.CHARGE_AT_AMPS, d.reason
        assert "Min floor engaged" in d.reason, d.reason
        assert d.redirect_w == pytest.approx(0.0), d.reason
