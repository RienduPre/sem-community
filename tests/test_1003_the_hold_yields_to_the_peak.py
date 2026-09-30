"""#1003 — waiting for a cheap hour must not break the peak limit.

The house sink (#879) tells the battery to stop covering the house in a cheap
hour, so the grid pays and the pack is kept for a dear hour later. It wrote a
discharge limit of 0 W and asked nothing: while the hold stands the house buys
everything, and a capacity tariff bills the whole month on one bad 15-minute
slot. Waiting for a cheaper hour saves cents; going over the limit costs far
more.

Two halves, and the second is why the first could not have worked anyway:

* the hold now gives way to the limit — the pack covers what the meter may not
  buy FOR THE HOUSE. The trigger is the house's own import (``home − solar``),
  never the meter's total: the car and the pack's own charging each answer for
  themselves against this same allowance, and a cover sized from the total
  would make the pack pay for a car's breach — after which the car's clamp
  reads the lowered meter as room and takes the freed watts (the #545 shape);
* the battery pipeline builds its OWN ``FleetContext``, and the peak numbers
  had never been threaded through it. ``peak_slot_allowed_w`` rode the
  chargers' context from #864 on; the battery decider read the dataclass
  default (``None``) on every cycle and could not have seen a limit if it had
  asked. Bug class 93, the same shape #955 was found in.

The same floor is swept onto the two sibling clamps that also hand part of the
house's draw to the meter on purpose (the EV protection clamp and the #620
grid-funded clamp), because "the grid funds this to save cents" loses to the
limit for exactly the reason the hold does.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.actuate_battery import (
    actuate_battery,
)
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


def _view(*, allowed_w=None, home_w=8000.0, solar_w=0.0, degraded=False,
          clamped_w=0.0, batteries=1, soc=80.0, held=True, grid_funded_w=0.0,
          ev_connected=False):
    cfg = {"battery_max_discharge_power": 9000, "battery_mode": "auto"}
    fleet = FleetContext(
        solar_w=solar_w, home_w=home_w, battery_soc=soc, battery_soc_known=True,
        battery_count=batteries, peak_slot_allowed_w=allowed_w,
        inputs_degraded=degraded, dark_inputs=(("grid",) if degraded else ()),
        home_residual_clamped_w=clamped_w,
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
    """A 6 kW limit, a cheap hour, and a house drawing 8 kW."""

    def test_before_the_fix_the_hold_bought_the_whole_house(self):
        """The shape #1003 reports: no limit reaches the decider → 0 W.

        Pinned so the second half of the fix cannot be undone quietly. If the
        peak numbers stop arriving on the battery's fleet context, THIS is
        what the decider does — and every assertion below would go green on a
        default that means "nobody asked"."""
        d = decide_battery(_view(allowed_w=None))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == 0.0

    def test_the_pack_covers_what_the_meter_may_not_buy(self):
        d = decide_battery(_view(allowed_w=6000.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == pytest.approx(2000.0)
        assert "6000 W" in d.reason and "covers 2000 W" in d.reason

    def test_a_house_that_fits_still_holds_the_whole_pack(self):
        """The saving is real whenever the limit is not in danger."""
        d = decide_battery(_view(allowed_w=6000.0, home_w=4000.0))
        assert d.discharge_limit_w == 0.0
        assert "covers" not in d.reason

    def test_a_spent_slot_covers_everything_the_house_draws(self):
        """Allowance 0 W: the meter may buy nothing more this quarter hour."""
        d = decide_battery(_view(allowed_w=0.0, home_w=3000.0))
        assert d.discharge_limit_w == pytest.approx(3000.0)

    def test_the_sun_is_counted_before_the_pack_is_asked(self):
        """The house is served from the sun first, as everywhere else in SEM.
        9000 W house under 2000 W of sun imports 7000, so 1000 is over."""
        d = decide_battery(_view(allowed_w=6000.0, home_w=9000.0,
                                 solar_w=2000.0))
        assert d.discharge_limit_w == pytest.approx(1000.0)

    def test_sun_that_covers_the_house_asks_for_nothing(self):
        d = decide_battery(_view(allowed_w=1000.0, home_w=3000.0,
                                 solar_w=4000.0))
        assert d.discharge_limit_w == 0.0


class TestWhatTheCoverMayNotDo:
    def test_a_car_that_breaks_the_limit_alone_never_moves_the_pack(self):
        """The one the first cut got wrong. A 9 kW car and a 400 W house under
        a 6 kW limit: the meter reads 9.4 kW, but the HOUSE is 15× under the
        limit. Sized from the meter total the pack would have been told to
        cover its whole house load — and the charger's clamp, which reads the
        lowered meter as room, would have handed those watts straight to the
        car. The pack holds; the car's own clamp answers for the car."""
        d = decide_battery(_view(allowed_w=6000.0, home_w=400.0))
        assert d.discharge_limit_w == 0.0
        assert "covers" not in d.reason

    def test_a_spent_slot_covers_the_house_and_no_more(self):
        """The bound is redundant today (the cover is derived FROM the house,
        so it cannot exceed it) and written anyway — this pins the ceiling it
        guarantees, not the ``min``."""
        d = decide_battery(_view(allowed_w=0.0, home_w=2500.0))
        assert d.discharge_limit_w == pytest.approx(2500.0)

    def test_two_batteries_cover_the_excess_once(self):
        """#531/#691: a house quantity splits across the fleet, or N packs
        each inject the whole of it."""
        d = decide_battery(_view(allowed_w=6000.0, batteries=2))
        assert d.discharge_limit_w == pytest.approx(1000.0)


class TestACycleThatCannotSee:
    """``home_consumption_w`` is the energy balance's residual, not a sensor.
    A hold that cannot read the house cannot show the slot is safe."""

    def _dark_house_w(self):
        """What the reader really produces on a dark grid read during a hold.

        Not hand-fed: the first cut released to ``home_consumption_w``, and in
        this exact state — the term that went dark IS an input to the balance,
        the pack is held so its term is zero, and a cheap hour is usually dark
        — that figure is 0. The release wrote the hold and said it had not."""
        from custom_components.solar_energy_management.coordinator.types import (
            PowerReadings,
        )
        p = PowerReadings()
        p.solar_power = 0.0      # night
        p.grid_power = 0.0       # the reader's fallback — the dark read
        p.battery_power = 0.0    # the hold: the pack covers nothing
        p.ev_power = 0.0
        p.calculate_derived()
        return p.home_consumption_power

    def test_the_house_figure_really_does_collapse(self):
        assert self._dark_house_w() == 0.0

    def test_the_release_is_the_packs_max_not_the_dark_house_figure(self):
        d = decide_battery(_view(allowed_w=6000.0, home_w=self._dark_house_w(),
                                 degraded=True))
        assert d.discharge_limit_w == pytest.approx(9000.0)
        assert "not held" in d.reason and "grid" in d.reason

    def test_the_release_keeps_the_intent_so_it_reaches_the_wire(self):
        """The first cut released as NORMAL. ``actuate_battery`` refuses a
        FLIP between NORMAL and LIMIT_DISCHARGE on a degraded cycle (#818), so
        that release was never written and the 0 W hold stood through exactly
        the blindness that released it. NORMAL at the wire is this same write:
        ``command_normal`` applies the pack's own max discharge."""
        d = decide_battery(_view(allowed_w=6000.0, home_w=self._dark_house_w(),
                                 degraded=True))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE

    @pytest.mark.asyncio
    async def test_the_release_actually_lands_on_the_adapter(self):
        adapter = MagicMock()
        adapter.last_intent = BatteryIntent.LIMIT_DISCHARGE   # today's hold
        adapter.last_discharge_limit_w = 250.0                # a real last write
        adapter._limit_lower_streak = 0
        adapter.command_limit_discharge = AsyncMock()
        d = decide_battery(_view(allowed_w=6000.0, home_w=self._dark_house_w(),
                                 degraded=True))
        await actuate_battery(d, adapter, inputs_degraded=True)
        adapter.command_limit_discharge.assert_awaited_once()
        assert adapter.command_limit_discharge.await_args[0][0] >= 9000.0

    def test_a_balance_that_did_not_close_releases_too(self):
        """#660: a grid sign the autodetect got wrong is READABLE, so
        ``inputs_degraded`` says nothing — the house clamps to 0 and the hold
        would sit through the breach it was meant to stop."""
        d = decide_battery(_view(allowed_w=1000.0, home_w=0.0,
                                 clamped_w=4000.0))
        assert d.discharge_limit_w == pytest.approx(9000.0)
        assert "clamped balance" in d.reason

    def test_a_dark_read_with_no_limit_still_holds(self):
        """No limit is nothing to defend, dark read or not — an install with
        no peak limit keeps the whole saving."""
        d = decide_battery(_view(allowed_w=None, degraded=True))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == 0.0

    def test_a_clamped_balance_never_floors_a_sibling_clamp(self):
        d = decide_battery(_view(held=False, allowed_w=1000.0, home_w=0.0,
                                 clamped_w=4000.0, grid_funded_w=200.0))
        assert d.discharge_limit_w == 0.0


class TestTheSiblingClamps:
    """The same shape lives wherever a limit hands house draw to the meter."""

    def test_the_grid_funded_clamp_takes_the_floor(self):
        """#620 lets the grid fund the cheap-hours loads. It still may not
        take the meter over: house 6000 of which 4000 is funded → limit 2000,
        but the house imports 6000 against a 3000 allowance."""
        d = decide_battery(_view(held=False, allowed_w=3000.0, home_w=6000.0,
                                 grid_funded_w=4000.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert d.discharge_limit_w == pytest.approx(3000.0)
        assert "may buy only 3000 W" in d.reason
        assert "so the grid feeds them" not in d.reason   # it no longer does

    def test_the_grid_funded_clamp_is_untouched_under_the_limit(self):
        d = decide_battery(_view(held=False, allowed_w=6000.0, home_w=6000.0,
                                 grid_funded_w=4000.0))
        assert d.discharge_limit_w == pytest.approx(2000.0)
        assert "so the grid feeds them" in d.reason

    def test_the_ev_clamp_takes_the_floor_without_feeding_the_car(self):
        """The EV protection clamp caps the pack at the house so grid+solar
        fund the car. Floored by the peak, it still never exceeds the house."""
        d = decide_battery(_view(held=False, ev_connected=True, soc=40.0,
                                 allowed_w=1000.0, home_w=2000.0,
                                 grid_funded_w=1500.0))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE
        assert "ev plugged in" in d.reason
        assert d.discharge_limit_w == pytest.approx(1000.0)
        assert "may buy only 1000 W" in d.reason
        assert "excl. 1500W" in d.reason       # how much was excluded, still

    def test_the_ev_clamp_is_untouched_under_the_limit(self):
        d = decide_battery(_view(held=False, ev_connected=True, soc=40.0,
                                 allowed_w=6000.0, home_w=2000.0,
                                 grid_funded_w=1500.0))
        assert d.discharge_limit_w == pytest.approx(500.0)
        assert "excl. 1500W grid-funded load" in d.reason

    def test_a_car_alone_over_the_limit_does_not_raise_the_ev_clamp(self):
        d = decide_battery(_view(held=False, ev_connected=True, soc=40.0,
                                 allowed_w=6000.0, home_w=400.0))
        assert d.discharge_limit_w == pytest.approx(400.0)


class TestTheMetersArithmetic:
    def test_no_limit_asks_for_nothing(self):
        assert cover_for_peak_w(None, 9000.0) == 0.0

    def test_a_house_under_the_allowance_asks_for_nothing(self):
        assert cover_for_peak_w(6000.0, 4000.0) == 0.0

    def test_the_sun_comes_off_the_house_first(self):
        assert cover_for_peak_w(6000.0, 9000.0, 2000.0) == 1000.0

    def test_more_sun_than_house_is_never_a_negative_import(self):
        assert cover_for_peak_w(6000.0, 1000.0, 4000.0) == 0.0

    def test_it_is_the_mirror_of_the_import_clamp(self):
        """Both read one slot budget: what a command may ADD, and what a hold
        must GIVE BACK."""
        from custom_components.solar_energy_management.coordinator.peak_guard import (
            clamp_import_command,
        )
        fits, _ = clamp_import_command(10_000.0, 6000.0, 4000.0)
        assert fits == 2000.0                          # may still be bought
        assert cover_for_peak_w(6000.0, 4000.0) == 0.0
        assert cover_for_peak_w(6000.0, 8000.0) == 2000.0      # already over


class TestBothProducersCarryThePeakAxis:
    """Class 93 — a field threaded through one producer of a context.

    ``build_view.build_charger_view`` and ``_run_battery_pipeline`` each build
    a ``FleetContext``. The peak numbers reached the first and not the second,
    and no per-function pin could ask."""

    REQUIRED = ("peak_slot_allowed_w", "inputs_degraded", "dark_inputs",
                "home_residual_clamped_w")

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

    def test_the_absent_limit_is_none_and_not_zero(self):
        assert FleetContext().peak_slot_allowed_w is None
