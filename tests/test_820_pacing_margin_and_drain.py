"""#820 — the pace lands short in the evening: no margin, and a blind spot.

@ArneGollin1987 ran a paced day on a 2 × 10 kW install (16.09): pacing
works, but the evening comes in under the model — actual solar below
forecast, the house or the EV taking more than modelled — and "the pacing
watts cannot be reached". He asked whether an intraday discharge is even
accounted for.

Two things in ``paced_charge_cap_w``, both against its own docstring
("lands the pack at its target by sunset − margin"):

* **No margin by construction.** ``end_margin_slots`` defaulted to 1 and no
  caller passed it, so the bisection picked the smallest cap that lands
  full in the LAST remaining slot. Any negative model error late in the day
  strands the pack, and the per-cycle re-solve then raises a cap the
  remaining sun cannot deliver.
* **Deficit hours counted as zero, never as a drain.** ``_fill_kwh`` summed
  surplus only, while the SOC curve the user sees (``provisional_soc_curve``)
  models the house drawing the pack DOWN in those hours. The curve knew; the
  cap did not. An afternoon cloud, a midday EV session — the pack's need
  grew by exactly that and the pace never saw it.

The fix keeps both inside the solver, with no new knob: the #830 option
surface only shrinks, so the headroom Arne asked for is a fixed 10 % — the
same number he proposed — and the deficit hours before sunset are added to
the need the cap must cover. Everything he could read on the sensor
(``need_kwh``, ``fill_kwh_at_max``, the per-slot model) gains ``drain_kwh``
and ``headroom_pct``, so a short evening can be argued with.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management.coordinator.charge_pacing import (
    PACING_HEADROOM_PCT, paced_charge_cap_w,
)

T0 = datetime(2026, 9, 16, 9, 0)


def _slot(i, solar_w, home_w=800.0, surplus=True):
    """One ledger slot the way ``build_day_slots`` shapes it: a surplus
    slot carries the surplus as ``cap_override_w`` and ``home_w=0``; a
    DEFICIT slot carries no cap and the net house draw as ``home_w``."""
    return SimpleNamespace(
        start=T0 + timedelta(hours=i), end=T0 + timedelta(hours=i + 1),
        hours=1.0, soc_kwh=6.0, home_batt_kwh=0.0, solar_w=float(solar_w),
        cap_override_w=(max(0.0, solar_w - home_w) if surplus else None),
        home_w=(0.0 if surplus else max(0.0, home_w - solar_w)),
        grid_committed_w=0.0,
    )


def _day(solar, deficit_at=()):
    return [_slot(i, w, surplus=(i not in deficit_at)) for i, w in enumerate(solar)]


BASE = dict(capacity_kwh=21.0, soc_pct=40.0, target_soc_pct=100.0,
            floor_soc_pct=35.0, forecast_trusted=True,
            inverter_ac_limit_w=20000.0, hw_max_charge_w=10000.0)
NEED = 21.0 * 0.60      # 12.6 kWh


def _fill(ledger, cap_w):
    return sum(min(s.cap_override_w or 0.0, cap_w) * s.hours / 1000.0 for s in ledger)


class TestTheMarginExists:
    def test_the_cap_carries_a_ten_percent_headroom(self):
        """Solve the pace for the bare need, then see it opened by the margin."""
        led = _day([6800.0] * 8)
        d = paced_charge_cap_w(ledger=led, **BASE)
        assert d.headroom_pct == PACING_HEADROOM_PCT == 10.0
        bare = NEED / 8.0 * 1000.0                 # the flat cap with no margin
        assert d.cap_w == pytest.approx(bare * 1.10, rel=0.02), d

    def test_an_evening_ten_percent_under_forecast_still_lands_full(self):
        """The reporter's day: a bright morning, a modest evening the cap was
        solved against (2.5 kW solar → 1.7 kW surplus, just above the pace),
        and the evening comes in 10 % short — its surplus drops BELOW the
        pace, which is exactly when a shortfall bites. Without the margin the
        day strands ~0.4 kWh, the number he saw; with it the brighter hours
        delivered more, and the pack still lands full."""
        modelled = _day([6800.0] * 5 + [2500.0] * 3)
        d = paced_charge_cap_w(ledger=modelled, **BASE)
        actual = _day([6800.0] * 5 + [2500.0 * 0.90] * 3)
        bare = NEED / 8.0 * 1000.0                    # 1575 W, lands full exactly
        assert _fill(modelled, bare) >= NEED - 1e-6   # the modelled day: fine
        assert _fill(actual, bare) < NEED - 0.3       # the real evening: short
        assert _fill(actual, d.cap_w) >= NEED - 1e-6  # with the margin: full

    def test_the_margin_never_pushes_past_the_hardware(self):
        led = _day([6800.0] * 3)                   # 12.6 kWh over 3 h wants 4.2 kW
        d = paced_charge_cap_w(ledger=led, **{**BASE, "hw_max_charge_w": 4500.0})
        assert d.cap_w == 4500.0, d                # 4.2 kW + 10 % = 4.62 → clamped

    def test_the_sunset_contract_still_holds(self):
        """#820's original promise: full near the END, not by 11:30."""
        led = _day([6800.0] * 8)
        d = paced_charge_cap_w(ledger=led, **BASE)
        filled, full_at = 0.0, None
        for i, s in enumerate(led):
            filled += min(s.cap_override_w, d.cap_w) / 1000.0
            if filled >= NEED - 1e-9 and full_at is None:
                full_at = i
        assert full_at is not None and full_at >= len(led) - 2, full_at


class TestTheDrainIsSeen:
    def test_a_deficit_afternoon_hour_raises_the_cap(self):
        """The same day with one clouded hour in the afternoon: the house
        draws ~0.8 kWh from the pack then, and the pace must earn it back."""
        clear = _day([6800.0] * 8)
        cloud = _day([6800.0] * 5 + [300.0] + [6800.0] * 2, deficit_at=(5,))
        d_clear = paced_charge_cap_w(ledger=clear, **BASE)
        d_cloud = paced_charge_cap_w(ledger=cloud, **BASE)
        assert d_cloud.drain_kwh == pytest.approx(0.5, abs=0.01)   # 800 − 300 W for 1 h
        assert d_clear.drain_kwh == 0.0
        assert d_cloud.cap_w > d_clear.cap_w
        # and the fill the cap is solved for covers need + drain
        assert _fill(cloud, d_cloud.cap_w) >= NEED + d_cloud.drain_kwh - 1e-6

    def test_the_need_the_sensor_shows_includes_the_drain(self):
        cloud = _day([6800.0] * 5 + [300.0] + [6800.0] * 2, deficit_at=(5,))
        d = paced_charge_cap_w(ledger=cloud, **BASE)
        assert d.need_kwh == pytest.approx(NEED + 0.5, abs=0.02)

    def test_a_day_that_cannot_cover_need_plus_drain_steps_aside(self):
        """Feasibility is judged on the whole bill: a weak day plus a heavy
        afternoon drain is a weak day, and a cap would only make it worse."""
        weak = _day([2400.0] * 6 + [300.0] * 2, deficit_at=(6, 7))
        d = paced_charge_cap_w(ledger=weak, **BASE)
        assert d.cap_w is None and d.code == "weak_day"
        assert d.drain_kwh == pytest.approx(1.0, abs=0.01)

    def test_a_ledger_without_home_w_is_the_old_ledger(self):
        """Fixtures and older ledgers carry no ``home_w`` — they read as a
        day with no drain, never as a crash."""
        led = [SimpleNamespace(start=T0, end=T0 + timedelta(hours=1), hours=1.0,
                               soc_kwh=6.0, home_batt_kwh=0.0, solar_w=6800.0,
                               cap_override_w=6000.0, grid_committed_w=0.0)
               for _ in range(8)]
        d = paced_charge_cap_w(ledger=led, **BASE)
        assert d.drain_kwh == 0.0 and d.cap_w is not None


class TestItSaysSo:
    def test_the_reason_names_the_margin(self):
        d = paced_charge_cap_w(ledger=_day([6800.0] * 8), **BASE)
        assert "10 %" in d.reason or "10%" in d.reason
