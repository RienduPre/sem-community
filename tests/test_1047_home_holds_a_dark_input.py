"""#1047 — home power jumps when the battery reading drops out.

HA-PROD, 04.10.2026 (Huawei, 2.2 beta), 10 s cycles:

    15:25:24  solar 4596   battery +3916 (charging)   grid +6    home  674
    15:25:49  solar unavailable
    15:26:01  battery unavailable
    15:26:03  grid unavailable
    15:26:24  solar 4676   battery dark               grid dark  home 4676  <- 20 s
    15:26:44  solar 4676   battery +4094              grid +38   home  544

``sensor.sem_battery_power`` kept 3916 W through the gap — the entity's
dark-read grace. The home balance took the reader's 0.0 fallback instead,
so the whole solar output landed on the house. The 2-cycle spike guard did
not catch it: the dip hold had already spent its count on the solar-dark
cycles before.

The rule pinned here: an input whose every read is dark enters the HOME
figure as the value its entity still shows (last published, same grace),
never as 0. The input fields keep the 0.0 fallback — #818's "nothing
substitutes a steering value" still stands — and ``inputs_degraded`` still
says the cycle cannot see.
"""
from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from custom_components.solar_energy_management.consts.core import (
    SENSOR_DARK_READ_GRACE_S,
)
from custom_components.solar_energy_management.coordinator.health_check import (
    HealthCheck,
)
from custom_components.solar_energy_management.coordinator.sensor_reader import (
    _DEGRADABLE_POWER_INPUTS,
    SensorReader,
)
from custom_components.solar_energy_management.coordinator.types import (
    PowerReadings,
    SEMData,
)

GRACE = SENSOR_DARK_READ_GRACE_S


def _state(value):
    import homeassistant.util.dt as dt_util
    s = Mock()
    s.state = str(value)
    s.attributes = {"unit_of_measurement": "W"}
    s.last_updated = s.last_reported = dt_util.utcnow()
    return s


class _House:
    """Legacy-config reader over three power sensors the test can move."""

    def __init__(self, extra_config=None):
        self.values = {"sensor.pv": "0", "sensor.grid": "0", "sensor.bat": "0"}
        self.clock = [1000.0]
        hass = MagicMock()
        hass.states = MagicMock()
        hass.states.get = lambda eid: (
            _state(self.values[eid]) if eid in self.values else None)
        cfg = {
            "solar_production_sensor": "sensor.pv",
            "grid_power_sensor": "sensor.grid",
            "battery_power_sensor": "sensor.bat",
        }
        cfg.update(extra_config or {})
        self.reader = SensorReader(hass, cfg)
        self.reader._energy_dashboard_config = None
        self.reader._now_monotonic = lambda: self.clock[0]

    def read(self, at, *, pv, grid, bat) -> PowerReadings:
        self.clock[0] = at
        self.values.update(
            {"sensor.pv": pv, "sensor.grid": grid, "sensor.bat": bat})
        return self.reader.read_power()


@pytest.mark.unit
class TestTheProdAfternoon:
    def _replay(self):
        h = _House()
        out = {}
        out["15:25:24"] = h.read(1000, pv=4596, grid=6, bat=3916)
        out["15:25:54"] = h.read(1030, pv="unavailable", grid=6, bat=3916)
        out["15:26:04"] = h.read(1040, pv="unavailable", grid="unavailable",
                                 bat="unavailable")
        out["15:26:24"] = h.read(1060, pv=4676, grid="unavailable",
                                 bat="unavailable")
        out["15:26:44"] = h.read(1080, pv=4676, grid=38, bat=4094)
        return out

    def test_home_is_the_house_not_the_solar_output(self):
        p = self._replay()["15:26:24"]
        assert p.home_consumption_power == pytest.approx(4676 - 3916 - 6), (
            f"home {p.home_consumption_power:.0f} W — the dark battery and "
            f"grid entered the balance as 0 W")

    def test_every_dark_cycle_keeps_the_house(self):
        """Solar dark, then all three dark: the house never moved, so home
        does not either — and no clamp fires to need the dip hold."""
        out = self._replay()
        for t in ("15:25:24", "15:25:54", "15:26:04"):
            assert out[t].home_consumption_power == pytest.approx(674), t
            assert out[t].home_residual_clamped_w == 0.0, t
        assert out["15:26:44"].home_consumption_power == pytest.approx(544)

    def test_the_steering_inputs_still_say_dark(self):
        """#818: the input fields keep the fallback; the cycle cannot see."""
        p = self._replay()["15:26:24"]
        assert p.battery_power == 0.0
        assert p.grid_power == 0.0
        assert p.battery_charge_power == 0.0
        assert p.battery_power_unavailable is True
        assert p.battery_power_all_unavailable is True
        assert p.grid_power_unavailable is True
        assert p.inputs_degraded is True
        assert set(p.dark_inputs) >= {"battery", "grid"}
        assert p.balance_held_w == {"battery": 3916.0, "grid": 6.0}

    def test_a_live_cycle_holds_nothing(self):
        p = self._replay()["15:26:44"]
        assert p.balance_held_w == {}


@pytest.mark.unit
class TestEachInputHoldsAlone:
    """battery: + charge.  grid: + export.  Each one dark on its own."""

    @pytest.mark.parametrize("dark", ["pv", "grid", "bat"])
    def test_the_dark_input_enters_at_its_held_value(self, dark):
        h = _House()
        before = h.read(1000, pv=3000, grid=500, bat=2000)
        assert before.home_consumption_power == pytest.approx(500)
        live = {"pv": 3000, "grid": 500, "bat": 2000}
        live[dark] = "unavailable"
        p = h.read(1010, **live)
        assert p.home_consumption_power == pytest.approx(500), (
            f"{dark} dark: home {p.home_consumption_power:.0f} W")

    def test_a_discharging_battery_holds_as_a_source(self):
        """The other sign: a dark discharge must not drop the house."""
        h = _House()
        h.read(1000, pv=0, grid=-300, bat=-1500)        # night, import 300
        p = h.read(1010, pv=0, grid=-300, bat="unavailable")
        assert p.home_consumption_power == pytest.approx(1800)


@pytest.mark.unit
class TestTheHoldEndsWhereTheEntityLetsGo:
    def test_the_grace_boundary_is_the_entity_boundary(self):
        """Same ``age <= grace`` as ``SEMSolarSensor``: held at the edge,
        released one second past it — the entity blanks there too."""
        h = _House()
        h.read(1000, pv=4000, grid=0, bat=3000)
        p = h.read(1000 + GRACE, pv=4000, grid=0, bat="unavailable")
        assert p.home_consumption_power == pytest.approx(1000)
        p = h.read(1000 + GRACE + 1, pv=4000, grid=0, bat="unavailable")
        assert p.balance_held_w == {}
        assert p.home_consumption_power == pytest.approx(4000)

    def test_a_value_never_read_is_never_held(self):
        """A restart into a dropout: nothing was published, nothing is
        invented (#875)."""
        h = _House()
        p = h.read(1000, pv=4000, grid=0, bat="unavailable")
        assert p.balance_held_w == {}
        assert p.home_consumption_power == pytest.approx(4000)

    def test_a_live_zero_is_a_reading(self):
        """The battery really stopped: 0 W read live is believed at once."""
        h = _House()
        h.read(1000, pv=4000, grid=0, bat=3000)
        p = h.read(1010, pv=4000, grid=0, bat=0)
        assert p.balance_held_w == {}
        assert p.home_consumption_power == pytest.approx(4000)

    def test_the_held_value_is_the_last_one_published(self):
        h = _House()
        h.read(1000, pv=4000, grid=0, bat=3000)
        h.read(1010, pv=4000, grid=0, bat=2500)
        p = h.read(1020, pv=4000, grid=0, bat="unavailable")
        assert p.balance_held_w["battery"] == pytest.approx(2500)


@pytest.mark.unit
class TestTheHeldValueIsInSemConvention:
    def test_a_user_flipped_battery_holds_the_corrected_sign(self):
        """The sensor says −3000 and the user's flip makes it +3000 charge.
        The hold must carry +3000: a held raw value would be re-flipped,
        and a raw 0 cannot be."""
        h = _House({"battery_sign_user_flip": True})
        p = h.read(1000, pv=4000, grid=0, bat=-3000)
        assert p.battery_power == pytest.approx(3000)
        assert p.home_consumption_power == pytest.approx(1000)
        p = h.read(1010, pv=4000, grid=0, bat="unavailable")
        assert p.balance_held_w["battery"] == pytest.approx(3000)
        assert p.home_consumption_power == pytest.approx(1000)

    def test_a_manually_inverted_grid_holds_the_corrected_sign(self):
        h = _House({"grid_sign_invert": True})
        p = h.read(1000, pv=0, grid=800, bat=0)      # sensor + = import here
        assert p.grid_power == pytest.approx(-800)
        assert p.home_consumption_power == pytest.approx(800)
        p = h.read(1010, pv=0, grid="unavailable", bat=0)
        assert p.home_consumption_power == pytest.approx(800)


@pytest.mark.unit
class TestTheHomeFormula:
    def test_held_values_reach_home_and_nothing_else(self):
        p = PowerReadings()
        p.solar_power, p.grid_power, p.battery_power = 4676.0, 0.0, 0.0
        p.balance_held_w = {"battery": 3916.0, "grid": 6.0}
        p.calculate_derived()
        assert p.home_consumption_power == pytest.approx(754)
        assert p.battery_charge_power == 0.0
        assert p.grid_export_power == 0.0

    def test_no_held_value_is_the_old_formula(self):
        p = PowerReadings()
        p.solar_power, p.grid_power, p.battery_power = 4676.0, 0.0, 0.0
        p.calculate_derived()
        assert p.home_consumption_power == pytest.approx(4676)


@pytest.mark.unit
class TestTheHealthCheckKnowsTheGap:
    def _cycle(self, held):
        p = PowerReadings()
        p.solar_power, p.grid_power, p.battery_power = 4676.0, 0.0, 0.0
        p.balance_held_w = held
        p.calculate_derived()
        return HealthCheck().check_power_balance(p)

    def test_a_held_input_is_not_a_violation(self):
        assert self._cycle({"battery": 3916.0, "grid": 6.0}) == []

    def test_the_same_gap_without_a_hold_still_is(self):
        """Vacuity: the gap itself is visible to the check."""
        p = PowerReadings()
        p.solar_power, p.grid_power, p.battery_power = 4676.0, 0.0, 0.0
        p.calculate_derived()
        p.home_consumption_power = 754.0          # a substitute nobody declared
        assert HealthCheck().check_power_balance(p) != []


@pytest.mark.unit
class TestTheSplitFiguresFollowTheirInput:
    def _data(self, **flags):
        p = PowerReadings()
        p.solar_power, p.grid_power, p.battery_power = 4676.0, 6.0, 3916.0
        p.calculate_derived()
        for k, v in flags.items():
            setattr(p, k, v)
        return SEMData(power=p).to_dict()

    def test_healthy_cycle_publishes_numbers(self):
        d = self._data()
        assert d["battery_charge_power"] == 3916
        assert d["battery_discharge_power"] == 0
        assert d["grid_export_power"] == 6
        assert d["grid_import_power"] == 0

    def test_a_dark_battery_blanks_its_halves(self):
        d = self._data(battery_power_all_unavailable=True)
        assert d["battery_power"] is None
        assert d["battery_charge_power"] is None
        assert d["battery_discharge_power"] is None
        assert d["grid_export_power"] == 6, "only the dark input goes quiet"

    def test_a_dark_grid_blanks_its_halves(self):
        d = self._data(grid_power_unavailable=True)
        assert d["grid_import_power"] is None
        assert d["grid_export_power"] is None
        assert d["battery_charge_power"] == 3916


@pytest.mark.unit
class TestEveryDarkInputIsCovered:
    def test_every_degradable_input_rolls_up_into_a_held_one(self):
        """A new steering input added to the dark tally must also get a
        home hold, or its dropout is this bug again."""
        held = {name for name, _flag, _field in SensorReader._BALANCE_HOLD_INPUTS}
        for name in _DEGRADABLE_POWER_INPUTS:
            assert name.split("_")[0] in held, name

    @pytest.mark.parametrize(
        "name, flag, field", SensorReader._BALANCE_HOLD_INPUTS)
    def test_the_hold_flag_is_the_flag_that_blanks_the_entity(
            self, name, flag, field):
        """Home holds exactly when the input's entity starts holding —
        the two must answer to one flag."""
        p = PowerReadings()
        setattr(p, field, 1234.0)
        assert SEMData(power=p).to_dict()[field] == 1234
        setattr(p, flag, True)
        assert SEMData(power=p).to_dict()[field] is None, name
