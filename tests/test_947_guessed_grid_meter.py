"""#947 — a name-matched grid meter must earn its place.

The reporter's `sensor.sem_grid_import_power` was a square wave: ~852 W for
one coordinator cycle, then 0, while every real meter in the house read ~0.
The Energy Dashboard gave SEM grid COUNTERS and no grid power entity, so SEM
matched meters by a SUBSTRING of the entity id — and `power_production`, the
DSMR/P1 feed-in meter's name, matched `sensor.power_production_now`, a SOLAR
FORECAST entity, which SEM then read as the grid export meter.

#911 answered that instance by excluding forecasts. A blacklist over every
sensor in a house cannot be completed: the import patterns reach
`power_consumption`, which this repo's own startup-race fixture uses as a
HEAT PUMP. The root cause is that a name-matched pick was stored
indistinguishably from a configured one and trusted forever.

So the pick is a CANDIDATE until it agrees with the grid energy counters —
the thing SEM already has and did not guess at.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator import sensor_reader as sr_mod
from custom_components.solar_energy_management.coordinator.sensor_reader import SensorReader


class _Clock:
    """A monotonic clock the test drives, in place of the module's ``time``."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _state(value, unit="W", device_class=None, state_class="measurement"):
    s = MagicMock()
    s.state = str(value)
    s.attributes = {"unit_of_measurement": unit}
    if device_class:
        s.attributes["device_class"] = device_class
    if state_class:
        s.attributes["state_class"] = state_class
    return s


def _ed(import_energy="sensor.grid_import_total", export_energy="sensor.grid_export_total"):
    ed = MagicMock()
    ed.solar_power = None
    ed.solar_power_list = []
    ed.grid_import_power = None
    ed.grid_power_list = []
    ed.grid_power_from = None
    ed.grid_power_to = None
    ed.grid_import_energy = import_energy
    ed.grid_export_energy = export_energy
    ed.grid_import_energy_list = [import_energy] if import_energy else []
    ed.grid_export_energy_list = [export_energy] if export_energy else []
    ed.battery_power = None
    ed.battery_power_list = []
    ed.battery_power_from = None
    ed.battery_power_to = None
    ed.battery_power_inverted = False
    ed.battery_power_pairs = []
    ed.battery_soc = None
    ed.battery_soc_list = []
    ed.battery_charge_energy = None
    ed.battery_discharge_energy = None
    ed.ev_power = None
    ed.has_solar = False
    ed.has_grid = True
    ed.has_battery = False
    ed.has_ev = False
    return ed


class _Rig:
    """A reader over a mutable state table, with the clock in the test's hand."""

    def __init__(self, monkeypatch, states, ed=None, device_of=None):
        self.states = states
        self.clock = _Clock()
        monkeypatch.setattr(sr_mod, "time", self.clock)
        hass = MagicMock()
        hass.states.get = lambda eid: self.states.get(eid)

        def _all(domain=None):
            out = []
            for eid, st in self.states.items():
                s = MagicMock()
                s.entity_id = eid
                s.state = st.state
                s.attributes = st.attributes
                if domain is None or eid.startswith(f"{domain}."):
                    out.append(s)
            return out

        hass.states.async_all = _all
        self.reader = SensorReader(hass, {"update_interval": 10})
        self.reader._sign_vote_warmup = 0
        self.reader._energy_dashboard_config = ed or _ed()
        if device_of is not None:
            self.reader._get_device_for_entity = lambda eid: device_of.get(eid)
        else:
            self.reader._get_device_for_entity = lambda eid: None

    def set(self, eid, value, **kw):
        self.states[eid] = _state(value, **kw)

    def read(self):
        with patch.object(sr_mod, "_ri", MagicMock()):
            return self.reader.read_power()

    @property
    def repairs(self):
        return self._repairs

    def read_watching_repairs(self):
        self._repairs = MagicMock()
        with patch.object(sr_mod, "_ri", self._repairs):
            return self.reader.read_power()


# The reporter's shape: a guessed import meter and a solar-forecast entity
# matched as the export meter, against counters that never move.
def _guessed_states(import_w=852.0, export_w=0.0, import_kwh=100.0, export_kwh=50.0):
    return {
        "sensor.heat_pump_power_consumption": _state(import_w, device_class="power"),
        "sensor.power_production": _state(export_w, device_class="power"),
        "sensor.grid_import_total": _state(import_kwh, "kWh"),
        "sensor.grid_export_total": _state(export_kwh, "kWh"),
    }


class TestAGuessDoesNotSteer:
    def test_the_first_read_reports_no_grid_power(self, monkeypatch):
        """852 W of claimed import, and SEM reports 0 — because nothing has
        corroborated the sensor it just matched by name."""
        rig = _Rig(monkeypatch, _guessed_states())
        power = rig.read()
        assert rig.reader._split_grid_discovery["import"] == "sensor.heat_pump_power_consumption"
        assert rig.reader._split_grid_discovery["confidence"] == "any-device"
        assert power.grid_power == 0.0
        power.calculate_derived()
        assert power.grid_import_power == 0.0

    def test_the_reporters_phantom_is_rejected_by_the_counters(self, monkeypatch):
        """15 minutes of 852 W against counters that never move. That is not
        a meter, and SEM says so instead of reporting the number."""
        rig = _Rig(monkeypatch, _guessed_states())
        rig.read()
        for _ in range(15):
            rig.clock.advance(60)
            power = rig.read()
        assert rig.reader._split_grid_proof["verdict"] is False
        assert power.grid_power == 0.0

    def test_the_rejection_raises_a_repair_naming_both_sensors(self, monkeypatch):
        rig = _Rig(monkeypatch, _guessed_states())
        rig.read()
        for _ in range(14):
            rig.clock.advance(60)
            rig.read()
        rig.clock.advance(60)
        rig.read_watching_repairs()
        call = rig.repairs.raise_split_grid_rejected.call_args
        assert call is not None, "a disproved guess must surface in the UI"
        assert call.kwargs["import_entity"] == "sensor.heat_pump_power_consumption"


class TestAGuessThatTracksTheCountersIsPromoted:
    def test_it_steers_once_the_energy_agrees(self, monkeypatch):
        """A real meter: 2 kW of import for 15 min moves the import counter
        by 0.5 kWh. SEM adopts it and reads the grid again."""
        rig = _Rig(monkeypatch, _guessed_states(import_w=2000.0))
        rig.read()
        kwh = 100.0
        for _ in range(15):
            rig.clock.advance(60)
            kwh += 2000.0 / 1000.0 / 60.0          # 2 kW for one minute
            rig.set("sensor.grid_import_total", round(kwh, 6), unit="kWh")
            power = rig.read()
        assert rig.reader._split_grid_proof["verdict"] is True
        assert power.grid_power == -2000.0
        power.calculate_derived()
        assert power.grid_import_power == 2000.0

    def test_the_verdict_survives_into_the_next_window(self, monkeypatch):
        """A proven meter must not blink off for a cycle when the window
        rolls over — the window is bookkeeping, not the verdict."""
        rig = _Rig(monkeypatch, _guessed_states(import_w=2000.0))
        rig.read()
        kwh = 100.0
        for _ in range(16):
            rig.clock.advance(60)
            kwh += 2000.0 / 1000.0 / 60.0
            rig.set("sensor.grid_import_total", round(kwh, 6), unit="kWh")
            power = rig.read()
        assert power.grid_power == -2000.0, "the fresh window un-steered a proven meter"


class TestEvidenceBeatsNames:
    def test_a_same_device_pair_never_waits(self, monkeypatch):
        """Growatt, DSMR, E3DC, Senec — the brands these patterns were
        written for — have their power sensors on the counter's own device.
        That is evidence, not a guess, and it steers on the first read."""
        states = _guessed_states(import_w=852.0)
        device_of = {
            "sensor.grid_import_total": "meter",
            "sensor.heat_pump_power_consumption": "meter",
            "sensor.power_production": "meter",
        }
        rig = _Rig(monkeypatch, states, device_of=device_of)
        power = rig.read()
        assert rig.reader._split_grid_discovery["confidence"] == "same-device"
        assert power.grid_power == -852.0


class TestSilenceIsNotAVerdict:
    def test_a_quiet_window_convicts_nobody(self, monkeypatch):
        """No import, no export, counters still: the candidate is unproven,
        NOT disproved. "I could not tell" is its own value (#925)."""
        rig = _Rig(monkeypatch, _guessed_states(import_w=0.0))
        rig.read()
        for _ in range(20):
            rig.clock.advance(60)
            rig.read()
        assert rig.reader._split_grid_proof["verdict"] is None

    def test_a_quiet_window_is_restarted_rather_than_left_open(self, monkeypatch):
        """After two hours of nothing the window re-baselines, so a house
        that wakes up at noon is judged on the afternoon, not on the night."""
        rig = _Rig(monkeypatch, _guessed_states(import_w=0.0))
        rig.read()
        rig.clock.advance(3600)
        rig.read()
        started_before = rig.reader._split_grid_proof["started"]
        rig.clock.advance(3600)
        rig.read()
        assert rig.reader._split_grid_proof["started"] != started_before

    def test_a_counter_reset_does_not_decide(self, monkeypatch):
        """Growatt resets its daily counters at midnight. A negative delta
        means the comparison is garbage, not that the meter is wrong."""
        rig = _Rig(monkeypatch, _guessed_states(import_w=2000.0))
        rig.read()
        for _ in range(14):
            rig.clock.advance(60)
            rig.read()
        rig.set("sensor.grid_import_total", 0.0, unit="kWh")   # midnight reset
        rig.clock.advance(60)
        rig.read()
        assert rig.reader._split_grid_proof["verdict"] is None


class TestTheHalfPairIsNamed:
    def test_an_export_only_discovery_says_which_silence_it_is(self, monkeypatch, caplog):
        """Export-only used to fall through to "no grid power sensor found",
        which is a different fact. SEM still reads 0 — export-minus-zero
        would make a house that imports read as one that never does — but it
        says which of the two silences this is."""
        states = {
            "sensor.power_production": _state(1500, device_class="power"),
            "sensor.grid_import_total": _state(100, "kWh"),
            "sensor.grid_export_total": _state(50, "kWh"),
        }
        rig = _Rig(monkeypatch, states)
        with caplog.at_level("WARNING"):
            power = rig.read()
        assert power.grid_power == 0.0
        assert "export" in caplog.text.lower()


# ── tier 1: what the integrations DECLARE (#947, folded from the roster audit)

class TestDeclaredBeatsGuessed:
    """The #915 roster reads each integration's own repository. A declared
    role is the integration author's semantic label; a substring of an
    entity_id is a guess about a house. The audit that produced this tier
    found 98 grid-meter-shaped declared keys across 46 domains that the
    lexicon did not match — word order being the recurring miss, so Fronius
    (``power_grid``) and Tibber (``power_flow_from_grid``) contributed no
    grid role at all despite ~10k installs each."""

    def _registry(self, entries):
        reg = MagicMock()
        reg.entities = {e.entity_id: e for e in entries}
        return reg

    def _entry(self, entity_id, platform, translation_key=None, unique_id=None,
               device_id=None):
        e = MagicMock()
        e.entity_id = entity_id
        e.platform = platform
        e.translation_key = translation_key
        e.unique_id = unique_id or ""
        e.device_id = device_id
        return e

    def test_a_declared_pair_steers_on_the_first_read(self, monkeypatch):
        """Fronius declares power_grid_import / power_grid_export. That is
        evidence, so it needs no corroboration window."""
        states = {
            "sensor.fronius_import": _state(1200, device_class="power"),
            "sensor.fronius_export": _state(0, device_class="power"),
            "sensor.grid_import_total": _state(100, "kWh"),
            "sensor.grid_export_total": _state(50, "kWh"),
        }
        rig = _Rig(monkeypatch, states)
        reg = self._registry([
            self._entry("sensor.fronius_import", "fronius",
                        translation_key="power_grid_import"),
            self._entry("sensor.fronius_export", "fronius",
                        translation_key="power_grid_export"),
        ])
        with patch.object(sr_mod.er, "async_get", return_value=reg):
            power = rig.read()
        assert rig.reader._split_grid_discovery["confidence"] == "declared"
        assert power.grid_power == -1200.0

    def test_a_forecast_can_never_win_the_declared_tier(self, monkeypatch):
        """#947's exact collision. `forecast_solar` declares
        `power_production_next_12hours` and no grid role whatsoever, so on
        this tier the forecast is not a candidate at all — structurally,
        not by a blacklist that has to be kept complete."""
        states = {
            "sensor.power_production_now": _state(3000, device_class="power"),
            "sensor.grid_import_total": _state(100, "kWh"),
            "sensor.grid_export_total": _state(50, "kWh"),
        }
        rig = _Rig(monkeypatch, states)
        reg = self._registry([
            self._entry("sensor.power_production_now", "forecast_solar",
                        translation_key="power_production_now"),
        ])
        with patch.object(sr_mod.er, "async_get", return_value=reg):
            imp, exp = rig.reader._declared_split_grid_power(
                rig.reader._energy_dashboard_config)
        assert (imp, exp) == (None, None)

    def test_the_lexicon_knows_the_brands_sems_own_patterns_were_written_for(self):
        """The runtime IMPORT/EXPORT patterns name Growatt, Senec, GivEnergy,
        E3DC and DSMR. The roster is what turns those names into evidence, so
        the split-grid brands it CAN answer for must not silently shrink."""
        from custom_components.solar_energy_management.consts import (
            integration_roster as roster,
        )
        vocab = getattr(roster, "ROLE_VOCAB", {})
        pairs = {d for d, v in vocab.items()
                 if "grid_import_power" in v and "grid_export_power" in v}
        assert {"growatt_modbus", "senec", "fronius", "tibber"} <= pairs, (
            f"declared split-grid pairs shrank: {sorted(pairs)}")
