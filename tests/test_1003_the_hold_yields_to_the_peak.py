"""#1003 — waiting for a cheap hour must not break the peak limit.

The house sink (#879) tells the battery to stop covering the house in a cheap
hour, so the grid pays and the pack is kept for a dear hour later. It wrote a
discharge limit of 0 W and asked nothing: while the hold stands the house buys
everything, and a capacity tariff bills the whole month on one bad 15-minute
slot. Waiting for a cheaper hour saves cents; going over the limit costs far
more.

Two halves, and the second is why the first could not have worked anyway:

* the hold now gives way to the limit — the pack covers at least what the
  meter may not buy, bounded by the house load it is allowed to serve
  (``home_consumption_w`` excludes the car) and split across the fleet;
* the battery pipeline builds its OWN ``FleetContext``, and the peak numbers
  had never been threaded through it. ``peak_slot_allowed_w`` rode the
  chargers' context from #864 on; the battery decider read the dataclass
  default (``None``) on every cycle and could not have seen a ceiling if it
  had asked. Bug class 93, the same shape #955 was found in.

The same floor is swept onto the two sibling clamps that also hand part of the
house's draw to the meter on purpose (the EV protection clamp and the #620
grid-funded clamp), because "the grid funds this to save cents" loses to the
limit for exactly the reason the hold does.
"""
from __future__ import annotations

import pytest

from custom_components.solar_energy_management.coordinator.charger_types import (
    BatteryIntent, BatteryRuntime, BatteryView, FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide_battery import (
    decide_battery,
)
from custom_components.solar_energy_management.coordinator.peak_guard import (
    cover_for_peak_w,
)
from custom_components.solar_energy_management.coordinator.sink_verdicts import (
    HELD, SinkVerdict,
)


def _view(*, allowed_w=None, grid_import_w=0.0, discharge_w=0.0, home_w=6000.0,
          import_known=True, batteries=1, soc=80.0, held=True,
          grid_funded_w=0.0, ev_connected=False, solar_w=0.0):
    cfg = {"battery_max_discharge_power": 6000, "battery_mode": "auto"}
    fleet = FleetContext(
        solar_w=solar_w, home_w=home_w, battery_soc=soc, battery_soc_known=True,
        battery_count=batteries, peak_slot_allowed_w=allowed_w,
        grid_import_w=grid_import_w, grid_import_known=import_known,
        battery_discharge_w=discharge_w,
    )
    return BatteryView(
        runtime=BatteryRuntime(battery_id="b1", last_known_soc=soc), config=cfg,
        fleet=fleet, charging_state="idle", ev_charging=ev_connected,
        ev_connected=ev_connected, home_consumption_w=home_w,
        scheduler_decision=None, grid_funded_load_w=grid_funded_w,
        sink_verdicts=({"house": SinkVerdict("house", HELD, "cheap hour")}
                       if held else {}),
    )


class TestTheReportedCase:
    """A 6 kW ceiling, a cheap hour, and a house drawing 8 kW."""

    def test_before_the_fix_the_hold_bought_the_whole_house(self):
        """The shape #1003 reports: no ceiling reaches the decider → 0 W.

        Pinned so the second half of the fix cannot be undone quietly: if the
        peak numbers stop arriving on the battery's fleet context, THIS is
        what the decider does, and every assertion below goes green on a
        default that means "nobody asked"."""
        d = decide_battery(_view(allowed_w=None, grid_import_w=8000.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == 0.0

    def test_the_pack_covers_what_the_meter_may_not_buy(self):
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=8000.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == pytest.approx(2000.0)
        assert "6000 W" in d.reason and "covers 2000 W" in d.reason

    def test_an_import_that_fits_still_holds_the_whole_pack(self):
        """The saving is real whenever the limit is not in danger."""
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=4000.0))
        assert d.discharge_limit_w == 0.0
        assert "covers" not in d.reason

    def test_a_spent_slot_covers_everything_the_house_draws(self):
        """Allowance 0 W: the meter may buy nothing more this quarter hour."""
        d = decide_battery(_view(allowed_w=0.0, grid_import_w=3000.0,
                                 home_w=3000.0))
        assert d.discharge_limit_w == pytest.approx(3000.0)

    def test_the_pack_it_is_already_covering_counts_toward_the_import(self):
        """First cycle of a hold: the meter reads low BECAUSE the pack is
        covering. Without the add-back the hold stops a 3 kW cover, import
        jumps to 8 kW and the slot is lost before the next cycle sees it."""
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=5000.0,
                                 discharge_w=3000.0))
        assert d.discharge_limit_w == pytest.approx(2000.0)


class TestWhatTheCoverMayNotDo:
    def test_it_never_reaches_past_the_house_into_the_car(self):
        """home_consumption_w excludes the car. A 9 kW car under a 6 kW
        ceiling with a 400 W house must not drain the pack into it — the
        charger's own peak clamp is what answers for the car."""
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=9400.0,
                                 home_w=400.0))
        assert d.discharge_limit_w == pytest.approx(400.0)

    def test_two_batteries_cover_the_excess_once(self):
        """#531/#691: a house quantity splits across the fleet, or N packs
        each inject the whole of it."""
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=8000.0,
                                 batteries=2))
        assert d.discharge_limit_w == pytest.approx(1000.0)

    def test_a_dark_meter_does_not_hold_the_pack(self):
        """#906/#925: the reader's 0.0 is not "the house is buying nothing".
        A hold that cannot see the meter cannot show the slot is safe, so it
        does not hold — the pack covers the house as it did before #879."""
        d = decide_battery(_view(allowed_w=6000.0, grid_import_w=0.0,
                                 import_known=False))
        assert d.intent is BatteryIntent.NORMAL
        assert "house sink held" not in d.reason

    def test_a_dark_meter_with_no_ceiling_still_holds(self):
        """No ceiling is nothing to defend, dark meter or not — an install
        with no peak limit keeps the whole saving."""
        d = decide_battery(_view(allowed_w=None, grid_import_w=0.0,
                                 import_known=False))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == 0.0


class TestTheSiblingClamps:
    """The same shape lives wherever a limit hands house draw to the meter."""

    def test_the_grid_funded_clamp_takes_the_floor(self):
        """#620 lets the grid fund the cheap-hours loads. It still may not
        take the meter over: house 6000 of which 4000 is funded → limit 2000,
        the pack is covering that 2000 and the meter is buying 4000 against a
        3000 allowance, so the pack has to cover 3000 instead."""
        d = decide_battery(_view(held=False, allowed_w=3000.0,
                                 grid_import_w=4000.0, discharge_w=2000.0,
                                 home_w=6000.0, grid_funded_w=4000.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert "grid-funded" in d.reason
        assert d.discharge_limit_w == pytest.approx(3000.0)

    def test_the_grid_funded_clamp_is_untouched_under_the_limit(self):
        d = decide_battery(_view(held=False, allowed_w=6000.0,
                                 grid_import_w=4000.0, home_w=6000.0,
                                 grid_funded_w=4000.0))
        assert d.discharge_limit_w == pytest.approx(2000.0)

    def test_the_ev_clamp_takes_the_floor_without_feeding_the_car(self):
        """The EV protection clamp caps the pack at the house so grid+solar
        fund the car. Floored by the peak, it still never exceeds the house."""
        d = decide_battery(_view(held=False, ev_connected=True, soc=40.0,
                                 allowed_w=6000.0, grid_import_w=11000.0,
                                 home_w=2000.0, grid_funded_w=1500.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert "ev plugged in" in d.reason
        assert d.discharge_limit_w == pytest.approx(2000.0)

    def test_the_ev_clamp_is_untouched_under_the_limit(self):
        d = decide_battery(_view(held=False, ev_connected=True, soc=40.0,
                                 allowed_w=6000.0, grid_import_w=3000.0,
                                 home_w=2000.0, grid_funded_w=1500.0))
        assert d.discharge_limit_w == pytest.approx(500.0)


class TestTheMetersArithmetic:
    def test_no_ceiling_asks_for_nothing(self):
        assert cover_for_peak_w(None, 9000.0, 0.0) == 0.0

    def test_an_import_under_the_allowance_asks_for_nothing(self):
        assert cover_for_peak_w(6000.0, 4000.0) == 0.0

    def test_export_is_never_a_negative_import(self):
        assert cover_for_peak_w(6000.0, -3000.0) == 0.0

    def test_it_is_the_mirror_of_the_import_clamp(self):
        """Both read one slot budget. What a command may ADD and what a hold
        must GIVE BACK add up to the allowance."""
        from custom_components.solar_energy_management.coordinator.peak_guard import (
            clamp_import_command,
        )
        fits, _ = clamp_import_command(10_000.0, 6000.0, 4000.0)
        assert fits == 2000.0                       # may still be bought
        assert cover_for_peak_w(6000.0, 4000.0) == 0.0
        assert cover_for_peak_w(6000.0, 8000.0) == 2000.0   # already over


class TestBothProducersCarryThePeakAxis:
    """Class 93 — a field threaded through one producer of a context.

    ``build_view.build_charger_view`` and ``_run_battery_pipeline`` each build
    a ``FleetContext``. The peak numbers reached the first and not the second
    for a year, and no per-function pin could ask."""

    REQUIRED = ("peak_slot_allowed_w", "grid_import_w", "grid_import_known",
                "battery_discharge_w")

    def test_every_production_site_threads_them(self):
        from .ast_contracts import call_sites
        sites = call_sites("FleetContext")
        assert len(sites) >= 2, sites
        for path, line, kw in sites:
            for f in self.REQUIRED:
                assert f in kw, (path, line, f, kw)

    def test_the_decider_reads_the_allowance_off_the_fleet(self):
        """Not off the config: the Control-tab slider writes through the load
        manager and skips the entry reload, so the config sits stale."""
        from .ast_contracts import reads_attribute
        from custom_components.solar_energy_management.coordinator import (
            decide_battery as db,
        )
        assert reads_attribute(db.peak_cover_floor_w, "view", "fleet")

    def test_the_absent_ceiling_is_none_and_not_zero(self):
        assert FleetContext().peak_slot_allowed_w is None
