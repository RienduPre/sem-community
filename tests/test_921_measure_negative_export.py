"""#871 step 0 (arc #921) — measure what a negative export price costs before acting.

Nothing today records "export was negative for two hours and SEM pushed 6 kWh
into it". Curtailment (#955) DESTROYS energy, so the decision to build it
should rest on a measured number, not a guess. This counter is that number.
It stays at zero on every fixed-tariff install, which is all of them today.
"""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator.energy_calculator import (
    EnergyCalculator,
)
from custom_components.solar_energy_management.coordinator.types import PowerReadings

_NOW = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
_DT = "custom_components.solar_energy_management.coordinator.energy_calculator.dt_util"


def _calc(export_rate):
    """A real calculator; the FIRST integration step uses ``update_interval``
    seconds, so 3600 makes one call integrate exactly one hour."""
    config = {
        "electricity_import_rate": 0.30,
        "electricity_export_rate": export_rate,
        "system_investment_cost": 10000,
        "update_interval": 3600,
    }
    tm = MagicMock()
    tm.get_current_meter_day_sunrise_based.return_value = date(2026, 9, 15)
    return EnergyCalculator(config, tm)


def _export(watts):
    p = PowerReadings(solar_power=watts, grid_import_power=0.0,
                      grid_export_power=watts, home_consumption_power=0.0)
    return p


@patch(_DT)
class TestNegativeExportIsCounted:
    def test_a_negative_rate_accrues_kwh_and_cost(self, mock_dt):
        mock_dt.now.return_value = _NOW
        energy = _calc(-0.05).calculate_energy(_export(2000.0))
        assert energy.daily_grid_export_negative == pytest.approx(2.0)
        assert energy.daily_grid_export_negative_cost == pytest.approx(0.10, abs=0.005)

    def test_a_positive_rate_accrues_nothing(self, mock_dt):
        mock_dt.now.return_value = _NOW
        energy = _calc(0.075).calculate_energy(_export(2000.0))
        assert energy.daily_grid_export_negative == 0.0
        assert energy.daily_grid_export_negative_cost == 0.0

    def test_zero_is_worthless_not_costly(self, mock_dt):
        """Zero and negative must not be conflated in EITHER direction."""
        mock_dt.now.return_value = _NOW
        energy = _calc(0.0).calculate_energy(_export(2000.0))
        assert energy.daily_grid_export_negative == 0.0

    def test_the_plain_export_total_is_untouched(self, mock_dt):
        """A separate key, never a sign on the export total a user reads."""
        mock_dt.now.return_value = _NOW
        energy = _calc(-0.05).calculate_energy(_export(2000.0))
        assert energy.daily_grid_export == pytest.approx(2.0)

    def test_the_two_new_keys_are_in_to_dict(self, mock_dt):
        mock_dt.now.return_value = _NOW
        from custom_components.solar_energy_management.coordinator.types import EnergyTotals
        e = EnergyTotals()
        assert hasattr(e, "daily_grid_export_negative")
        assert hasattr(e, "daily_grid_export_negative_cost")
