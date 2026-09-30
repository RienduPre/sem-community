"""(#1017, 2.1 stable audit) Warnings that describe a lasting state are
logged once per episode, not once per 30 s cycle.

Two sites in the main read path warned every cycle for as long as the
condition held: the physics defence against a lying plug sensor (#285+1),
and every rejection in ``power_control`` for a misconfigured battery
control entity. A healthy install must not fill its log with the same
line; a person reading it must see the transition, not the duration.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from custom_components.solar_energy_management.coordinator import power_control
from custom_components.solar_energy_management.coordinator.sensor_reader import (
    SensorConfig,
    SensorReader,
)
from custom_components.solar_energy_management.coordinator.types import (
    PowerReadings,
)


def _reader() -> SensorReader:
    r = SensorReader.__new__(SensorReader)
    r.config = SensorConfig(
        ev_power_sensor="sensor.evp",
        ev_plug_sensor="binary_sensor.plug",
        ev_charging_sensor="binary_sensor.chg",
    )
    return r


def _warnings(caplog) -> list[str]:
    return [rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.WARNING]


class TestPlugSensorPhysicsWarnsOncePerEpisode:
    def test_fleet_warning_once_while_the_plug_keeps_lying(self, caplog):
        caplog.set_level(logging.DEBUG)
        r = _reader()
        for _ in range(5):
            readings = PowerReadings()
            readings.ev_connected = False
            readings.ev_power = 4000.0
            r._infer_fleet_connection_from_physics(readings)
            assert readings.ev_connected is True
        assert len(_warnings(caplog)) == 1

    def test_fleet_warning_again_after_the_plug_agreed_in_between(self, caplog):
        caplog.set_level(logging.DEBUG)
        r = _reader()
        def lying() -> PowerReadings:
            rd = PowerReadings()
            rd.ev_connected = False
            rd.ev_power = 4000.0
            return rd

        honest = PowerReadings()
        honest.ev_connected = True
        honest.ev_power = 4000.0
        # the call flips the reading it is given, so each lie is a fresh one
        r._infer_fleet_connection_from_physics(lying())
        r._infer_fleet_connection_from_physics(honest)
        r._infer_fleet_connection_from_physics(lying())
        assert len(_warnings(caplog)) == 2

    def test_per_charger_warning_once_per_charger(self, caplog):
        caplog.set_level(logging.DEBUG)
        r = _reader()
        for _ in range(4):
            readings = PowerReadings()
            readings.ev_connected_per_charger = {"a": False, "b": False}
            readings.ev_power_per_charger = {"a": 3000.0, "b": 3000.0}
            readings.ev_charging_per_charger = {"a": False, "b": False}
            r._infer_per_charger_connection_from_physics(readings)
            assert readings.ev_connected_per_charger == {"a": True, "b": True}
        assert len(_warnings(caplog)) == 2


class TestPowerControlRejectsOncePerEntity:
    def test_a_wrong_unit_is_reported_once(self, caplog):
        caplog.set_level(logging.DEBUG)
        hass = MagicMock()
        state = MagicMock()
        state.state = "5"
        state.attributes = {"unit_of_measurement": "A"}
        hass.states.get.return_value = state
        for _ in range(6):
            assert power_control.prepare_power_setpoint(
                hass, "number.battery_limit", 1500.0,
            ) is None
        assert len(_warnings(caplog)) == 1
        assert "rejected" in _warnings(caplog)[0]
