"""#996 — the coordinator feeds the two registry answers into the verdict.

A forecast integration and the inverter's export-limit entity are found in
the entity registry, not in SEM's options. The coordinator asks its own
readers and passes True / False / None. None — "not asked" — is UNKNOWN
and keeps every row (#925). Two things are "not asked":

* any read while Home Assistant is still starting: the integration may
  simply load after SEM (the restart race the review found);
* a miss the reader itself took before HA was running.

ABSENT for a runtime capability only ever comes from a read taken while
HA is running."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.core import CoreState

from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
from custom_components.solar_energy_management.coordinator.forecast_reader import ForecastReader
from custom_components.solar_energy_management.coordinator.install_modules import (
    Module, Presence,
)

RUNNING = SimpleNamespace(state=CoreState.running)
STARTING = SimpleNamespace(state=CoreState.starting)


def _stub(forecast_path=None, forecast_source=None, export_cache="unset",
          hass=RUNNING, read_running=True):
    reader = MagicMock()
    reader._last_source_detection_path = forecast_path
    reader._source = forecast_source
    reader._none_read_while_running = read_running
    reader.detection_answer = lambda: ForecastReader.detection_answer(reader)
    sensors = MagicMock()
    if export_cache == "unset":
        sensors.export_limit_answer = lambda *_: None
    else:
        sensors.export_limit_answer = lambda *_: export_cache is not None
    return SimpleNamespace(
        hass=hass, config={"solar_production_sensor": "sensor.pv"},
        _ed_raw_config=None, _ed_answered=True,
        _forecast_reader=reader, _sensor_reader=sensors,
    )


class TestForecastAnswer:

    def test_never_detected_is_none(self):
        assert ForecastReader.detection_answer(
            SimpleNamespace(_last_source_detection_path="uninitialized", _source=None)) is None

    def test_a_found_source_is_true(self):
        assert ForecastReader.detection_answer(
            SimpleNamespace(_last_source_detection_path="solcast", _source="solcast")) is True

    def test_none_available_while_running_is_false(self):
        assert ForecastReader.detection_answer(SimpleNamespace(
            _last_source_detection_path="none_available", _source=None,
            _none_read_while_running=True)) is False

    def test_none_available_while_starting_is_not_an_answer(self):
        assert ForecastReader.detection_answer(SimpleNamespace(
            _last_source_detection_path="none_available", _source=None,
            _none_read_while_running=False)) is None


class TestTheVerdict:

    def test_no_answers_keep_both_unknown(self):
        p = SEMCoordinator.install_presence(_stub(forecast_path="uninitialized"))
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN

    def test_found_answers_are_present(self):
        p = SEMCoordinator.install_presence(
            _stub(forecast_path="solcast", forecast_source="solcast",
                  export_cache="number.inverter_export_limit"))
        assert p[Module.SOLAR_FORECAST] is Presence.PRESENT
        assert p[Module.EXPORT_LIMIT] is Presence.PRESENT

    def test_asked_while_running_and_nothing_is_absent(self):
        p = SEMCoordinator.install_presence(
            _stub(forecast_path="none_available", export_cache=None))
        assert p[Module.SOLAR_FORECAST] is Presence.ABSENT
        assert p[Module.EXPORT_LIMIT] is Presence.ABSENT

    def test_while_ha_is_starting_nothing_is_absent(self):
        p = SEMCoordinator.install_presence(
            _stub(forecast_path="none_available", export_cache=None, hass=STARTING))
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN

    def test_a_miss_read_while_starting_stays_unknown_after_start(self):
        # Running now, but the reader's last miss was taken during start-up.
        p = SEMCoordinator.install_presence(
            _stub(forecast_path="none_available", read_running=False))
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN

    def test_a_reader_that_raises_is_not_an_answer(self):
        stub = _stub(forecast_path="none_available", export_cache=None)
        stub._forecast_reader.detection_answer = MagicMock(side_effect=RuntimeError("boom"))
        stub._sensor_reader.export_limit_answer = MagicMock(side_effect=RuntimeError("boom"))
        p = SEMCoordinator.install_presence(stub)
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN

    def test_a_coordinator_without_hass_or_readers_is_unknown(self):
        stub = SimpleNamespace(config={}, _ed_raw_config=None, _ed_answered=True)
        p = SEMCoordinator.install_presence(stub)
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN
        assert p[Module.DYNAMIC_TARIFF] is Presence.ABSENT
