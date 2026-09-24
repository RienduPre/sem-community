"""#887 — a GM vehicle over OnStar2MQTT is a vehicle, not unknown hardware.

Azlinon's cars came up under SEM's auto-detection as "entities present, no
role matched — please report". The bridge's own EV vocabulary (src/mqtt.js:
EV_BATTERY_LEVEL, EV_RANGE, EV_CHARGE_STATE, EV_PLUG_STATE) names the car; a
2019 Traverse (ICE) carries none of it; the "Command Status Monitor" devices
are the poller, not a car. Matched by entity-id tail, never by make.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management import hardware_detection as hd


def _ent(entity_id, device_id, device_class=None):
    return SimpleNamespace(
        entity_id=entity_id, platform="mqtt", device_id=device_id,
        original_device_class=device_class, disabled_by=None,
        unique_id=entity_id.split(".", 1)[1], translation_key=None,
    )


def _blazer():
    d = "onstar-blazer"
    return [
        _ent("sensor.2024_chevrolet_blazer_ev_ev_battery_level", d, "battery"),
        _ent("sensor.2024_chevrolet_blazer_ev_ev_charging_battery_level", d, "battery"),
        _ent("sensor.2024_chevrolet_blazer_ev_ev_range", d, "distance"),
        _ent("sensor.2024_chevrolet_blazer_ev_charge_state", d),
        _ent("binary_sensor.2024_chevrolet_blazer_ev_ev_plug_state", d, "plug"),
        _ent("binary_sensor.2024_chevrolet_blazer_ev_ev_charge_state", d, "battery_charging"),
        _ent("sensor.2024_chevrolet_blazer_ev_odometer", d, "distance"),
        _ent("sensor.2024_chevrolet_blazer_ev_tire_pressure_lf", d, "pressure"),
    ]


def _traverse():
    d = "onstar-traverse"
    return [
        _ent("sensor.2019_chevrolet_traverse_fuel_level", d),
        _ent("sensor.2019_chevrolet_traverse_odometer", d, "distance"),
        _ent("sensor.2019_chevrolet_traverse_oil_life", d),
    ]


def _poller():
    d = "onstar-poller"
    return [
        _ent("sensor.2024_chevrolet_blazer_ev_command_status_monitor_sensors_last_run", d),
        _ent("binary_sensor.2024_chevrolet_blazer_ev_command_status_monitor_sensors_ok", d),
    ]


def _registry(entries):
    reg = SimpleNamespace()
    reg.entities = {e.entity_id: e for e in entries}
    return reg


class TestTheCarIsNamedFromTheBridgesOwnWords:
    def test_the_blazer_is_a_vehicle_with_its_four_sources(self):
        v = hd.vehicle_from_device(_blazer())
        assert v["vehicle_soc_entity"] == "sensor.2024_chevrolet_blazer_ev_ev_battery_level"
        assert v["vehicle_range_entity"] == "sensor.2024_chevrolet_blazer_ev_ev_range"
        assert v["ev_connected_sensor"] == "binary_sensor.2024_chevrolet_blazer_ev_ev_plug_state"
        assert v["ev_charging_sensor"] == "binary_sensor.2024_chevrolet_blazer_ev_ev_charge_state"
        assert v["name"] == "2024 Chevrolet Blazer Ev"

    def test_the_plain_element_is_preferred_over_the_metrics_twin(self):
        v = hd.vehicle_from_device(_blazer())
        assert "charging_battery_level" not in v["vehicle_soc_entity"]

    def test_the_metrics_twin_serves_when_the_plain_one_is_absent(self):
        ents = [e for e in _blazer() if not e.entity_id.endswith("_ev_ev_battery_level")]
        v = hd.vehicle_from_device(ents)
        assert v["vehicle_soc_entity"].endswith("_ev_charging_battery_level")

    def test_an_ice_car_is_not_a_vehicle_source(self):
        assert hd.vehicle_from_device(_traverse()) == {}

    def test_the_poller_is_not_a_car(self):
        assert hd.vehicle_from_device(_poller()) == {}

    def test_a_range_alone_still_names_a_car(self):
        only = [_ent("sensor.2024_chevrolet_blazer_ev_ev_range", "d", "distance")]
        assert hd.vehicle_from_device(only)["name"] == "2024 Chevrolet Blazer Ev"


@pytest.mark.unit
class TestTheReportSaysVehicleNotPleaseReport:
    def test_the_blazer_leaves_the_near_misses(self):
        rep = hd.build_detection_report(registry=_registry(_blazer() + _traverse()))
        assert [v["note"] for v in rep["vehicles"]] == ["vehicle"]
        assert rep["vehicles"][0]["name"] == "2024 Chevrolet Blazer Ev"
        assert rep["vehicles"][0]["vehicle_soc_entity"].endswith("_ev_battery_level")
        assert not any(m["device_id"] == "onstar-blazer" for m in rep["near_misses"])

    def test_the_key_exists_even_with_no_car(self):
        rep = hd.build_detection_report(registry=_registry(_traverse()))
        assert rep["vehicles"] == []
