"""#1047 — home power jumps when the battery reading drops out.

HA-PROD, 04.10.2026 (Huawei), 10 s cycles:

    15:25:24  solar 4596   battery +3916 (charging)   grid +6    home  674
    15:25:49  solar unavailable
    15:26:01  battery unavailable
    15:26:03  grid unavailable
    15:26:24  solar 4676   battery dark               grid dark  home 4676  <- 20 s
    15:26:44  solar 4676   battery +4094              grid +38   home  544

``sensor.sem_battery_power`` kept 3916 W through the gap (the entity's
dark-read grace). Home was shown straight from the reader's 0 W fallback,
so the whole solar output landed on the house. The #237/#444 hold catches
a dip only; its 2-cycle spike guard had been spent by the dip hold on the
solar-dark cycles before.

Pinned here: the SHOWN home (entity, ``coordinator.data``, the cards'
snapshot) keeps its last value while any input of it reads dark, for the
same grace as the input entities. The STEERING home is untouched: it
steers beside the raw battery/grid fields, and a held home next to a 0 W
battery charge is a surplus that is not there (the first cut of this fix
held the battery inside the sum and the review measured a 3.9 kW phantom
EV surplus from exactly that).
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock, Mock

import pytest

from custom_components.solar_energy_management.consts.core import (
    SENSOR_DARK_READ_GRACE_S,
)
from custom_components.solar_energy_management.coordinator.coordinator import (
    SEMCoordinator,
)
from custom_components.solar_energy_management.coordinator.sensor_reader import (
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
    """A real reader over three sensors the test can move, and the
    coordinator steps that turn its reading into what SEM shows."""

    def __init__(self):
        self.values = {"sensor.pv": "0", "sensor.grid": "0", "sensor.bat": "0"}
        self.clock = [1000.0]
        hass = MagicMock()
        hass.states = MagicMock()
        hass.states.get = lambda eid: (
            _state(self.values[eid]) if eid in self.values else None)
        self.reader = SensorReader(hass, {
            "solar_production_sensor": "sensor.pv",
            "grid_power_sensor": "sensor.grid",
            "battery_power_sensor": "sensor.bat",
        })
        self.reader._energy_dashboard_config = None
        self.coord = SEMCoordinator.__new__(SEMCoordinator)
        self.coord._shown_home_clock = lambda: self.clock[0]

    def cycle(self, at, *, pv, grid, bat):
        self.clock[0] = at
        self.values.update(
            {"sensor.pv": pv, "sensor.grid": grid, "sensor.bat": bat})
        p = self.reader.read_power()
        self.coord._smooth_home_consumption(p)
        self.coord._hold_shown_home(p)
        return p, SEMData(power=p).to_dict(), self.coord._build_power_snapshot(p)


def _replay():
    h = _House()
    out = {}
    out["15:25:24"] = h.cycle(1000, pv=4596, grid=6, bat=3916)
    out["15:25:54"] = h.cycle(1030, pv="unavailable", grid=6, bat=3916)
    out["15:26:04"] = h.cycle(1040, pv="unavailable", grid="unavailable",
                              bat="unavailable")
    out["15:26:14"] = h.cycle(1050, pv="unavailable", grid="unavailable",
                              bat="unavailable")
    out["15:26:24"] = h.cycle(1060, pv=4676, grid="unavailable",
                              bat="unavailable")
    out["15:26:34"] = h.cycle(1070, pv=4676, grid="unavailable",
                              bat="unavailable")
    out["15:26:44"] = h.cycle(1080, pv=4676, grid=38, bat=4094)
    return out


@pytest.mark.unit
class TestTheProdAfternoon:
    def test_the_shown_home_never_jumps_to_the_solar_output(self):
        out = _replay()
        for t in ("15:25:54", "15:26:04", "15:26:14", "15:26:24", "15:26:34"):
            _p, data, _snap = out[t]
            assert data["home_consumption_power"] == 674, (
                f"{t}: shown home {data['home_consumption_power']} W — a dark "
                f"input's 0 W was shown as house load")
        assert out["15:26:44"][1]["home_consumption_power"] == 544

    def test_the_bug_is_reproduced_without_the_hold(self):
        """Vacuity: the same replay, shown straight from the steering
        figure, is the PROD jump."""
        p, _data, _snap = _replay()["15:26:24"]
        assert p.home_consumption_power == pytest.approx(4676)

    def test_the_cards_keep_the_last_set_that_added_up(self):
        out = _replay()
        _p, _d, snap = out["15:26:24"]
        assert snap["held"] is True
        assert snap["home_w"] == pytest.approx(674)
        assert snap["battery_w"] == pytest.approx(3916), (
            "the cards showed a 0 W battery beside a 4.7 kW house")
        assert out["15:26:44"][2]["held"] is False

    def test_the_input_entities_and_their_halves_go_quiet(self):
        _p, data, _snap = _replay()["15:26:24"]
        for k in ("battery_power", "battery_charge_power",
                  "battery_discharge_power", "grid_power",
                  "grid_import_power", "grid_export_power"):
            assert data[k] is None, k
        assert data["solar_power"] == 4676


@pytest.mark.unit
class TestSteeringIsNotTouched:
    def test_the_hold_never_writes_the_steering_figure(self):
        c = SEMCoordinator.__new__(SEMCoordinator)
        c._shown_home_clock = lambda: 1000.0
        p = PowerReadings()
        p.home_consumption_power = 674.0
        c._hold_shown_home(p)
        p.home_consumption_power = 4676.0
        p.battery_power_all_unavailable = True
        c._hold_shown_home(p)
        assert p.home_shown_w == pytest.approx(674)
        assert p.home_consumption_power == pytest.approx(4676)

    def test_no_phantom_ev_surplus_from_a_dark_battery(self):
        """solar − home − battery charge, the EV budget's own sum, on the
        dark cycle: the raw set says the meter's answer (≈ 0), never the
        3.9 kW a held home beside a 0 W battery charge would invent."""
        p, _d, _s = _replay()["15:26:24"]
        surplus = max(0.0, p.solar_power - p.home_consumption_power
                      - p.battery_charge_power)
        assert surplus < 100.0, f"phantom surplus {surplus:.0f} W"
        assert p.inputs_degraded is True


@pytest.mark.unit
class TestTheHoldEndsWhereTheEntityLetsGo:
    def _coord(self, clock):
        c = SEMCoordinator.__new__(SEMCoordinator)
        c._shown_home_clock = lambda: clock[0]
        return c

    def _p(self, home, **flags):
        p = PowerReadings()
        p.home_consumption_power = float(home)
        for k, v in flags.items():
            setattr(p, k, v)
        return p

    @pytest.mark.parametrize("flag", [
        "solar_power_unavailable", "grid_power_unavailable",
        "battery_power_all_unavailable"])
    def test_each_dark_input_holds_the_shown_home(self, flag):
        clock = [1000.0]
        c = self._coord(clock)
        c._hold_shown_home(self._p(674))
        clock[0] = 1010.0
        p = self._p(4676, **{flag: True})
        c._hold_shown_home(p)
        assert SEMData(power=p).to_dict()["home_consumption_power"] == 674

    def test_the_grace_boundary_is_the_entity_boundary(self):
        """Same ``age <= grace`` as ``SEMSolarSensor``: held at the edge,
        released one second past it — the input entities blank there too,
        and home shows the computed value again (never unknown)."""
        clock = [1000.0]
        c = self._coord(clock)
        c._hold_shown_home(self._p(674))
        clock[0] = 1000.0 + GRACE
        p = self._p(4676, battery_power_all_unavailable=True)
        c._hold_shown_home(p)
        assert p.home_shown_w == pytest.approx(674)
        clock[0] = 1000.0 + GRACE + 1
        p = self._p(4676, battery_power_all_unavailable=True)
        c._hold_shown_home(p)
        assert p.home_shown_w is None
        assert SEMData(power=p).to_dict()["home_consumption_power"] == 4676

    def test_nothing_shown_yet_is_never_held(self):
        """A restart into a dropout: there is no last value to keep."""
        c = self._coord([1000.0])
        p = self._p(4676, battery_power_all_unavailable=True)
        c._hold_shown_home(p)
        assert p.home_shown_w is None
        assert SEMData(power=p).to_dict()["home_consumption_power"] == 4676

    def test_the_held_value_is_the_last_one_shown(self):
        clock = [1000.0]
        c = self._coord(clock)
        c._hold_shown_home(self._p(674))
        clock[0] = 1010.0
        c._hold_shown_home(self._p(710))
        clock[0] = 1020.0
        p = self._p(4676, grid_power_unavailable=True)
        c._hold_shown_home(p)
        assert p.home_shown_w == pytest.approx(710)

    def test_a_live_cycle_shows_its_own_figure(self):
        clock = [1000.0]
        c = self._coord(clock)
        c._hold_shown_home(self._p(674))
        p = self._p(2500)
        c._hold_shown_home(p)
        assert p.home_shown_w is None
        assert SEMData(power=p).to_dict()["home_consumption_power"] == 2500


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
        assert d["battery_charge_power"] is None
        assert d["battery_discharge_power"] is None
        assert d["grid_export_power"] == 6, "only the dark input goes quiet"

    def test_a_dark_grid_blanks_its_halves(self):
        d = self._data(grid_power_unavailable=True)
        assert d["grid_import_power"] is None
        assert d["grid_export_power"] is None
        assert d["battery_charge_power"] == 3916


def _getattr_flags(fn) -> set:
    """Every ``getattr(<x>, "<name>_unavailable", …)`` in a function's code."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {
        n.args[1].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "getattr"
        and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
        and isinstance(n.args[1].value, str)
        and n.args[1].value.endswith("_unavailable")
    }


@pytest.mark.unit
class TestTheHoldListensToEveryFlagThatBlanksAnInput:
    def test_every_input_the_entity_blanks_holds_the_shown_home(self):
        """Home is built from every input ``to_dict`` can blank. A flag
        added there and not here is this bug again for that input."""
        blanks = _getattr_flags(SEMData.to_dict)
        held_on = _getattr_flags(SEMCoordinator._hold_shown_home)
        assert blanks, "the parse found no blanking flags — the pin is blind"
        assert blanks <= held_on, sorted(blanks - held_on)
