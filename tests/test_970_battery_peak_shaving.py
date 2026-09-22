"""#970 — the battery as the peak-shaving instrument.

@Hanzzzie85 (discussion #969), on a capacity tariff with a SolarEdge pack
and a heat-pumped outdoor pool, wrote the worked example:

    "house requires 5kw, i have a 2kw solar power at the moment and peak
    import can be 2.5kw, so lacking 0.5kw. battery can, in my case through
    automation, give the house 500w that it requires to limit import
    capacity at 2.5kw."

Read it carefully, because the obvious reading is the wrong one. A battery
in plain self-consumption already covers the whole 3 kW gap and the meter
reads zero — that is what every SEM install does today, and it is free.
What he wants is the OPPOSITE: let the grid fund everything up to the
ceiling, because import under the capacity limit costs nothing on the bill
he is billed on, and keep the kilowatt-hours for the evening.

So the feature can only ever discharge the pack LESS than today. It adds no
drain path — and it adds deliberate grid import on an install that imports
nothing, which is why it is opt-in.

    "When battery gets to a certain level, and next day yield is high
    enough, it goes to 0 grid to maximize battery offload untill the
    evening."

That is #778's budget, already computed: when tonight's forecast says the
pack has something spendable, the shave lifts.
"""
from __future__ import annotations

import pytest

from custom_components.solar_energy_management.coordinator.charger_types import (
    BatteryIntent,
    BatteryRuntime,
    BatteryView,
    FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide_battery import (
    decide_battery,
)
from custom_components.solar_energy_management.coordinator.peak_shave import (
    shave_discharge_limit_w,
    zero_grid_open,
)


@pytest.mark.unit
class TestTheArithmeticIsHis:

    def test_the_reporters_own_numbers(self):
        """5 kW house, 2 kW sun, 2.5 kW ceiling → the pack gives 500 W."""
        assert shave_discharge_limit_w(5000.0, 2000.0, 2500.0) == 500.0

    def test_under_the_ceiling_the_grid_funds_everything(self):
        """1 kW house, no sun, 2.5 kW ceiling: nothing needs the pack, so
        it keeps its charge and the meter carries the lot. This IS the
        feature, not an edge case — it is what saves the evening's energy."""
        assert shave_discharge_limit_w(1000.0, 0.0, 2500.0) == 0.0

    def test_no_ceiling_means_leave_the_battery_alone(self):
        """An unlimited install, or a slot the guard could not compute.
        Absence of a ceiling is not a ceiling of zero (#864, #925)."""
        assert shave_discharge_limit_w(5000.0, 2000.0, None) is None

    def test_two_packs_split_the_gap(self):
        """#531 — N packs each told to supply the whole gap supply it N×."""
        assert shave_discharge_limit_w(
            5000.0, 2000.0, 2500.0, battery_count=2) == 250.0

    def test_unreadable_inputs_decline_rather_than_guess(self):
        assert shave_discharge_limit_w("nonsense", 0.0, 2500.0) is None

    @pytest.mark.parametrize("budget,open_", [
        (3.4, True), (0.0, False), (-1.0, False), (None, False), ("", False),
    ])
    def test_zero_grid_opens_only_on_a_real_budget(self, budget, open_):
        assert zero_grid_open(budget) is open_


def _view(**kw):
    cfg = {"battery_mode": "auto",
           "battery_discharge_protection_enabled": True,
           "battery_reserve_soc": 10.0}
    cfg.update(kw.pop("config", {}))
    fleet = FleetContext(
        solar_w=kw.pop("solar_w", 2000.0),
        home_w=kw.pop("home_w", 5000.0),
        battery_soc=80.0, battery_soc_known=True, buffer_soc=70.0,
        battery_assist_min_surplus_w=1200.0,
        battery_count=kw.pop("battery_count", 1),
    )
    return BatteryView(
        runtime=BatteryRuntime(battery_id="b1", last_known_soc=80.0),
        config=cfg, fleet=fleet, charging_state="idle",
        ev_charging=False, ev_connected=kw.pop("ev_connected", False),
        home_consumption_w=fleet.home_w,
        peak_shaving_enabled=kw.pop("peak_shaving_enabled", True),
        peak_slot_allowed_w=kw.pop("peak_slot_allowed_w", 2500.0),
        **kw,
    )


@pytest.mark.unit
class TestTheDecision:

    def test_it_limits_the_pack_to_the_gap(self):
        d = decide_battery(_view())
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE, d.reason
        assert d.discharge_limit_w == 500.0
        assert "peak shaving" in d.reason

    def test_off_by_default_nothing_changes(self):
        """The switch is the whole difference: an install that imports
        nothing today must go on importing nothing until someone opts in."""
        d = decide_battery(_view(peak_shaving_enabled=False))
        assert d.intent is BatteryIntent.NORMAL, d.reason

    def test_no_ceiling_means_normal(self):
        d = decide_battery(_view(peak_slot_allowed_w=None))
        assert d.intent is BatteryIntent.NORMAL, d.reason

    def test_tomorrows_sun_lifts_the_shave(self):
        """Zero grid: the budget says the house can have the pack because
        the sun puts it back."""
        d = decide_battery(_view(battery_spendable_kwh=3.4))
        assert d.intent is BatteryIntent.NORMAL, d.reason
        assert "zero grid" in d.reason
        assert "3.4 kWh" in d.reason

    def test_an_empty_budget_keeps_shaving(self):
        d = decide_battery(_view(battery_spendable_kwh=0.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE, d.reason

    def test_the_reason_names_all_three_numbers(self):
        """A limit a reader cannot reconstruct is a number out of nowhere —
        the whole of bug class 99 in one habit."""
        d = decide_battery(_view())
        for token in ("5000W", "2000W", "2500W", "500 W"):
            assert token in d.reason, (token, d.reason)


@pytest.mark.unit
class TestSeniority:
    """Every branch above this one is a floor SEM owes someone. Shaving is
    an economics preference about the DEFAULT, so it goes last."""

    def test_the_ev_protection_still_wins(self):
        d = decide_battery(_view(
            ev_connected=True, solar_w=0.0,
            config={"battery_discharge_protection_enabled": True}))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert "ev plugged in" in d.reason, d.reason
        assert "peak shaving" not in d.reason

    def test_grid_funded_loads_still_win(self):
        d = decide_battery(_view(grid_funded_load_w=1500.0))
        assert "grid-funded" in d.reason, d.reason

    def test_hands_off_mode_is_untouched(self):
        d = decide_battery(_view(config={"battery_mode": "off"}))
        assert d.intent is BatteryIntent.OFF, d.reason


@pytest.mark.unit
class TestItNeverDrainsMoreThanToday:
    """The one property that makes this safe to ship: shaving is a CAP.
    Whatever the numbers, it can only hold the pack back."""

    @pytest.mark.parametrize("home,solar,allowed", [
        (5000.0, 2000.0, 2500.0),
        (500.0, 4000.0, 2500.0),     # exporting
        (9000.0, 0.0, 100.0),        # a nearly-closed ceiling
        (0.0, 0.0, 0.0),
    ])
    def test_the_limit_never_exceeds_the_house(self, home, solar, allowed):
        limit = shave_discharge_limit_w(home, solar, allowed)
        assert limit is not None
        assert limit <= max(0.0, home), (limit, home)
        assert limit >= 0.0
