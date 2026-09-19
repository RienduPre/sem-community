"""#988 — a solar zero the balance refutes is a dark read, not a number.

The Huawei feed on the reference install drops constantly, in two shapes.
Measured over 24 h on PROD (2.1.0-beta.31):

* ``sensor.inverter_eingangsleistung`` — **273** ``unavailable`` episodes,
  151 min blind (10.5 % of the day), p50 29 s — and **102** rows reading a
  hard ``0``;
* ``sensor.sem_solar_power`` — **1** episode, 2 min.

So the grace that bridges ``unavailable`` works, and it is the only thing
that does. A dropout that arrives as ``0 W`` looks exactly like night and is
spent as a measurement. PROD, 19.09 08:24:

    residual clamped by 1274W — solar=0W grid_import=0W battery_discharge=0W
                              | ev=0W grid_export=7W battery_charge=1267W

A house does not charge its battery from nothing. SEM repaired the symptom
(clamp the residual) and kept the false zero in the inputs every decision is
built on — including ``decide()``'s structural-idle branch, where a 29-second
zero can end an EV charge that should have continued (#461).

Pinned here: the refutation is physics, it only ever refuses a zero (never
invents a number), night and grid-charging are untouched, and the refusal
travels the same road an unavailable read does (#902/#818).
"""
from __future__ import annotations

import logging

import pytest

from custom_components.solar_energy_management.consts.core import (
    SOLAR_ZERO_REFUTED_W,
)
from custom_components.solar_energy_management.coordinator.sensor_reader import (
    SensorReader,
)
from custom_components.solar_energy_management.coordinator.types import (
    PowerReadings,
)


def _reader():
    # ``log_on_change`` dedups on the DIGIT-STRIPPED message in a module-level
    # cache, so a second test asserting the same warning would see silence.
    from custom_components.solar_energy_management.utils import log_gate
    log_gate.reset_log_gate() if hasattr(log_gate, "reset_log_gate") else log_gate._LAST.clear()
    r = SensorReader.__new__(SensorReader)      # no hass needed for the gate
    r._input_dark = {}
    r._input_reads = {"solar": 1}
    r._solar_zero_refuted = False
    return r


def _readings(*, solar=0.0, battery=0.0, grid=0.0, ev=0.0) -> PowerReadings:
    """battery: + charge / − discharge.   grid: + export / − import."""
    p = PowerReadings()
    p.solar_power, p.battery_power, p.grid_power, p.ev_power = solar, battery, grid, ev
    return p


def _gate(r, readings):
    SensorReader._gate_solar_power(r, readings)
    return r._input_dark.get("solar", 0)


@pytest.mark.unit
class TestTheReporterCase:
    def test_the_prod_morning_zero_is_refused(self, caplog):
        """solar=0, import=0, discharge=0, charge=1267 → 1267 W from nowhere."""
        r = _reader()
        with caplog.at_level(logging.WARNING):
            dark = _gate(r, _readings(solar=0.0, battery=1267.0, grid=7.0))
        assert dark == 1
        assert r._input_reads["solar"] == 0
        said = " ".join(caplog.messages)
        assert "dark read" in said and "1274" in said        # the unexplained watts
        assert "1267" in said                                 # named term: charge

    def test_it_says_so_once_and_recovers_out_loud(self, caplog):
        r = _reader()
        with caplog.at_level(logging.DEBUG):
            for _ in range(4):
                _gate(r, _readings(solar=0.0, battery=1267.0))
            assert len([m for m in caplog.messages if "dark read" in m]) == 1
            _gate(r, _readings(solar=2600.0, battery=1267.0))
        assert any("number again" in m for m in caplog.messages)
        assert r._solar_zero_refuted is False


@pytest.mark.unit
class TestItOnlyEverRefusesAZero:
    def test_a_real_zero_at_night_is_left_alone(self):
        """Nothing leaving, nothing arriving — the honest zero."""
        assert _gate(_reader(), _readings(solar=0.0)) == 0

    def test_charging_the_battery_from_the_grid_at_night_is_explained(self):
        """2 kW in, 2 kW into the pack: the sun is not needed to explain it."""
        assert _gate(_reader(), _readings(solar=0.0, battery=2000.0, grid=-2000.0)) == 0

    def test_exporting_from_the_battery_at_night_is_explained(self):
        """#533 arbitrage: discharge 1 kW, export 1 kW, solar genuinely 0."""
        assert _gate(_reader(), _readings(solar=0.0, battery=-1000.0, grid=1000.0)) == 0

    def test_a_nonzero_solar_reading_is_never_touched(self):
        """Understatement is not detectable here and must not be guessed at."""
        r = _reader()
        assert _gate(r, _readings(solar=50.0, battery=3000.0)) == 0
        assert r._input_dark == {}

    def test_the_margin_keeps_rounding_and_meter_skew_out(self):
        r = _reader()
        assert _gate(r, _readings(solar=0.0, battery=SOLAR_ZERO_REFUTED_W - 1)) == 0
        assert _gate(r, _readings(solar=0.0, battery=SOLAR_ZERO_REFUTED_W + 50)) == 1

    def test_the_value_is_not_replaced_by_a_guess(self):
        """It refuses a reading; it never invents one (that would be a second
        producer of what solar was during the gap)."""
        r, p = _reader(), _readings(solar=0.0, battery=1267.0)
        _gate(r, p)
        assert p.solar_power == 0.0


@pytest.mark.unit
class TestTheRefusalTravelsTheUsualRoad:
    def test_export_alone_refutes_it_too(self):
        """Exporting 1.5 kW with an idle battery and no import: same fault."""
        assert _gate(_reader(), _readings(solar=0.0, grid=1500.0)) == 1

    def test_a_refused_zero_degrades_the_cycle_like_an_unavailable_read(self):
        """#818: any dark input means this cycle must not steer. The reader
        computes that from the same map the gate writes to."""
        r = _reader()
        _gate(r, _readings(solar=0.0, battery=1267.0))
        assert any(r._input_dark.values())

    def test_the_gate_runs_in_the_read_pipeline(self):
        """Structural (AST, not source text): ``_read_all`` calls it, next to
        the battery gate it mirrors — a gate nothing calls is a comment."""
        import ast
        import inspect
        from pathlib import Path
        tree = ast.parse(Path(inspect.getfile(SensorReader)).read_text(encoding="utf-8"))
        callers = [
            fn.name for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(c, ast.Call)
                    and getattr(c.func, "attr", None) == "_gate_solar_power"
                    for c in ast.walk(fn))
        ]
        assert callers, "nothing calls _gate_solar_power"
        for name in callers:
            fn = next(f for f in ast.walk(tree)
                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and f.name == name)
            assert any(isinstance(c, ast.Call)
                       and getattr(c.func, "attr", None) == "_gate_battery_power"
                       for c in ast.walk(fn)), (
                f"{name}() gates solar but not battery — the two belong together")
