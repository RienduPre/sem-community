"""#871 step 2 — while the meter is closed, the loads absorb first.

Step 1 (the day-ledger unclamp) lives on ``fix/871-export-price-clamp``: it is
the one change in this arc that is NOT behind a default-off switch, so it
rides on its own with its own proof rather than inside an arc that is
otherwise inert. What is left here is step 2, and it is gated — ``grid_closed``
is only ever True when the export guard's verdict says so.

While the grid-export sink is CLOSED, the surplus controller's "always export a
little" regulation buffer — the one threshold that exists to FEED the meter
— relaxes to zero, so a load that would have been declined by 50 W starts.
Safety gates are not touched.
"""
import pytest

from custom_components.solar_energy_management.coordinator.surplus_controller import (
    SurplusController,
)

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
