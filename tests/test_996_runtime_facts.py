"""#996 — the coordinator feeds the two registry answers into the verdict.

A forecast integration and the inverter's export-limit entity are found in
the entity registry, not in SEM's options. The coordinator asks its own
readers and passes True / False / None; a reader that has not looked yet,
or that cannot be asked, is None — UNKNOWN keeps the rows (#925)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
from custom_components.solar_energy_management.coordinator.forecast_reader import ForecastReader
from custom_components.solar_energy_management.coordinator.install_modules import (
    Module, Presence,
)


def _stub(forecast_path=None, forecast_source=None, export_cache="unset", config=None):
    reader = MagicMock()
    reader._last_source_detection_path = forecast_path
    reader._source = forecast_source
    reader.detection_answer = lambda: ForecastReader.detection_answer(reader)
    sensors = MagicMock()
    if export_cache == "unset":
        sensors.detect_export_limit_entity.return_value = None
        sensors.export_limit_answer = lambda *_: None
    else:
        sensors.detect_export_limit_entity.return_value = export_cache
        sensors.export_limit_answer = lambda *_: export_cache is not None
    return SimpleNamespace(
        config=config or {"solar_production_sensor": "sensor.pv"},
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

    def test_none_available_is_false(self):
        assert ForecastReader.detection_answer(
            SimpleNamespace(_last_source_detection_path="none_available", _source=None)) is False


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

    def test_asked_and_nothing_is_absent(self):
        p = SEMCoordinator.install_presence(
            _stub(forecast_path="none_available", export_cache=None))
        assert p[Module.SOLAR_FORECAST] is Presence.ABSENT
        assert p[Module.EXPORT_LIMIT] is Presence.ABSENT

    def test_a_reader_that_raises_is_not_an_answer(self):
        stub = _stub(forecast_path="none_available", export_cache=None)
        stub._forecast_reader.detection_answer = MagicMock(side_effect=RuntimeError("boom"))
        stub._sensor_reader.export_limit_answer = MagicMock(side_effect=RuntimeError("boom"))
        p = SEMCoordinator.install_presence(stub)
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN

    def test_a_coordinator_without_readers_is_unknown(self):
        stub = SimpleNamespace(config={}, _ed_raw_config=None, _ed_answered=True)
        p = SEMCoordinator.install_presence(stub)
        assert p[Module.SOLAR_FORECAST] is Presence.UNKNOWN
        assert p[Module.EXPORT_LIMIT] is Presence.UNKNOWN
        assert p[Module.DYNAMIC_TARIFF] is Presence.ABSENT
