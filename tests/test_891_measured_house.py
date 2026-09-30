"""#891 — show the house as the inverter measures it, beside SEM's own.

@SandmanNCL, Fronius Symo GEN24 + BYD HVS. His inverter publishes the house
load directly; SEM works it out from four other readings. On a hybrid the
battery runs through the inverter's DC side, so the conversion losses are
never measured and land in that arithmetic. His two numbers differ by
30-60 W.

What he actually complained about, in his words:

    "This also means the SEM dashboard and my Home Assistant power-flow
    dashboard show different house-consumption values even though both are
    based on the same Fronius installation."

Two dashboards disagreeing. That is a display problem, and this is the
display fix: publish what his inverter says, and publish the difference.

**SEM's own house figure is not touched.** A first build substituted the
measured value into ``home_consumption_power``, and a review found it broke
the energy balance for eleven consumers — the worst handed the car watts
the sun was not producing. And the gain would have been nothing: 30-60 W
against a 1200 W surplus gate flips no decision SEM makes. So the measured
number is published, not used.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.house_meter import (
    house_gap_w,
    read_house_meter,
)


def _state(value, unit="W"):
    return SimpleNamespace(state=str(value),
                           attributes={"unit_of_measurement": unit})


def _hass(state):
    h = MagicMock()
    h.states.get = MagicMock(return_value=state)
    return h


@pytest.mark.unit
class TestReadingHisMeter:

    def test_it_reads_watts(self):
        assert read_house_meter(_hass(_state(370)), "sensor.x") == 370.0

    def test_kilowatts_are_converted(self):
        assert read_house_meter(_hass(_state(0.37, "kW")), "sensor.x") == 370.0

    @pytest.mark.parametrize("bad", ["unavailable", "unknown", "", "nonsense"])
    def test_an_unreadable_sensor_is_no_number(self, bad):
        assert read_house_meter(_hass(_state(bad)), "sensor.x") is None

    def test_a_missing_entity_is_no_number(self):
        assert read_house_meter(_hass(None), "sensor.x") is None

    def test_no_sensor_configured_is_no_number(self):
        assert read_house_meter(_hass(_state(370)), None) is None
        assert read_house_meter(_hass(_state(370)), "") is None

    def test_a_negative_reading_is_refused(self):
        """A house does not generate. A minus sign means a sign convention
        nobody declared, and guessing at one broke every Huawei install
        once (00e449c)."""
        assert read_house_meter(_hass(_state(-370)), "sensor.x") is None


@pytest.mark.unit
class TestTheDifference:
    """His 30-60 W, named. On a hybrid it is the inverter's conversion
    loss, which SEM could not see while the house figure absorbed it."""

    def test_his_numbers(self):
        assert house_gap_w(420.0, 370.0) == 50.0

    def test_a_meter_reading_higher_is_reported_too(self):
        """Signed on purpose. The first build clamped at zero and was blind
        to an over-reading sensor — the exact case worth seeing, because it
        means a wrong scale or a sensor that also counts the car."""
        assert house_gap_w(370.0, 420.0) == -50.0

    def test_no_meter_is_no_difference(self):
        assert house_gap_w(420.0, None) is None

    def test_agreement_reads_zero(self):
        assert house_gap_w(420.0, 420.0) == 0.0


@pytest.mark.unit
class TestTheSensorsFollowTheSetting:
    """They depend on a setting, not on hardware, so the module table
    cannot answer for them. And the same list feeds the stale sweep, so
    un-naming a sensor takes the entities away again."""

    def _keys(self, configured):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "sensor.py").read_text(encoding="utf-8")
        block = src[src.index("async def async_setup_entry"):][:2000]
        assert 'house_power_sensor' in block, (
            "the two sensors are no longer gated on the setting — a house "
            "with no sensor named would publish two empty entities"
        )
        assert '"house_meter_power"' in block and '"house_meter_gap"' in block
        return True

    def test_they_are_gated_on_the_setting(self):
        assert self._keys(True)

    def test_they_are_declared(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "sensor.py").read_text(encoding="utf-8")
        for key in ("house_meter_power", "house_meter_gap"):
            assert f'key="{key}"' in src, key

    def test_sems_own_house_figure_is_untouched(self):
        """The whole point. A first build substituted the measured value
        into it and broke the energy balance for eleven consumers."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "coordinator"
               / "types.py").read_text(encoding="utf-8")
        derived = src[src.index("# Home consumption from energy balance"):][:600]
        assert "measured" not in derived, (
            "calculate_derived reads the meter again — it must not. The "
            "meter is published beside SEM's figure, never into it."
        )
