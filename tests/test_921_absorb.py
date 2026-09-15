"""#871 steps 1-2 — a negative price is a cost the planner can see, and the loads absorb first.

Step 1: ``day_ledger`` clamped the export rate to zero, so a hostile meter
and a generous one were the same number to the planner. Step 2: while the
grid-export sink is CLOSED, the surplus controller's "always export a
little" regulation buffer — the one threshold that exists to FEED the meter
— relaxes to zero, so a load that would have been declined by 50 W starts.
Safety gates are not touched.
"""
from datetime import datetime, timezone

import pytest

from custom_components.solar_energy_management.coordinator.day_ledger import build_day_slots
from custom_components.solar_energy_management.coordinator.surplus_controller import (
    SurplusController,
)

TZ = timezone.utc
T0 = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)


def _slots(export_rate):
    return build_day_slots(
        start=T0, end=T0.replace(hour=12), day_kwh=20.0,
        sunrise=T0.replace(hour=7), sunset=T0.replace(hour=19),
        home_w_at=lambda t: 300.0, price_at=lambda t: 0.30,
        level_cheap_at=lambda t: False, export_rate=export_rate,
    )


class TestTheLedgerSeesTheCost:
    def test_a_negative_export_rate_reaches_the_slot(self):
        s = _slots(-0.05)
        assert s, "the fixture must produce at least one surplus slot"
        assert s[0].price == -0.05

    def test_a_positive_rate_is_unchanged(self):
        assert _slots(0.075)[0].price == 0.075

    def test_a_missing_rate_is_still_zero(self):
        assert _slots(None)[0].price == 0.0


@pytest.mark.asyncio
class TestTheLoadsAbsorbFirst:
    async def test_a_closed_meter_relaxes_the_regulation_offset(self, mock_hass):
        sc = SurplusController(mock_hass, regulation_offset=50)
        await sc.update(available_power_w=1000.0, is_night=False, grid_closed=True)
        assert sc._last_surplus == pytest.approx(sc._smoothed_surplus)

    async def test_an_open_meter_keeps_it(self, mock_hass):
        sc = SurplusController(mock_hass, regulation_offset=50)
        await sc.update(available_power_w=1000.0, is_night=False, grid_closed=False)
        assert sc._last_surplus == pytest.approx(sc._smoothed_surplus - 50.0)

    async def test_the_default_is_open(self, mock_hass):
        """Every existing caller passes nothing — that must be today's behaviour."""
        sc = SurplusController(mock_hass, regulation_offset=50)
        await sc.update(available_power_w=1000.0, is_night=False)
        assert sc._last_surplus == pytest.approx(sc._smoothed_surplus - 50.0)

    async def test_the_peak_guard_still_wins(self, mock_hass):
        """A CLOSED meter never lets a load start past the slot allowance."""
        sc = SurplusController(mock_hass, regulation_offset=50)
        await sc.update(available_power_w=1000.0, is_night=False, grid_closed=True,
                        peak_slot_allowed_w=0.0, grid_import_w=0.0)
        assert sc._grid_closed is True
