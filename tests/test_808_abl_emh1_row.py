"""#808 — ABL eMH1 is detected through matfroh/ABL_emh1_modbus.

The integration (domain ``ev_charger_modbus``, source read 24.09.2026) names
its entities in plain English with the user's device name in front:
"Charging Current" (number), "<device> Charging Enable" (switch), "Power
Consumption" (sensor, device_class power), "State" (sensor), "Current L1/L2/L3"
(sensors, device_class current). The rules match the tail the source writes,
never the head the user chose. Nobody has the hardware on a bench yet: the
row ships ``implemented``, the first owner makes it ``tested-live``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.solar_energy_management.hardware_detection import (
    discover_all_ev_chargers_from_registry,
)


def _entry(entity_id, platform, device_id, device_class=None):
    return SimpleNamespace(
        entity_id=entity_id, platform=platform, device_id=device_id,
        original_device_class=device_class, disabled_by=None,
        unique_id=entity_id.split(".", 1)[1],
    )


def _discover(entries):
    registry = MagicMock()
    registry.entities.values.return_value = entries
    with patch(
        "custom_components.solar_energy_management.hardware_detection."
        "entity_registry.async_get", return_value=registry,
    ):
        return discover_all_ev_chargers_from_registry(MagicMock())


def _emh1():
    d = "abl-1"
    p = "ev_charger_modbus"
    return [
        _entry("number.garage_charging_current", p, d, "current"),
        _entry("switch.garage_charging_enable", p, d),
        _entry("sensor.garage_power_consumption", p, d, "power"),
        _entry("sensor.garage_state", p, d),
        _entry("sensor.garage_current_l1", p, d, "current"),
        _entry("sensor.garage_current_l2", p, d, "current"),
        _entry("sensor.garage_duty_cycle", p, d, "power_factor"),
    ]


def test_emh1_binds_the_four_things_sem_needs():
    chargers = _discover(_emh1())
    assert len(chargers) == 1
    c = chargers[0]
    assert c["_platform"] == "ev_charger_modbus"
    assert c["ev_current_control_entity"] == "number.garage_charging_current"
    assert c["ev_start_stop_entity"] == "switch.garage_charging_enable"
    assert c["ev_charging_power_sensor"] == "sensor.garage_power_consumption"
    assert c["ev_charging_sensor"] == "sensor.garage_state"


def test_a_phase_current_sensor_is_never_the_control():
    c = _discover(_emh1())[0]
    assert "current_l" not in c["ev_current_control_entity"]


def test_a_lone_state_sensor_is_not_a_charger():
    only = [_entry("sensor.garage_state", "ev_charger_modbus", "abl-2")]
    assert _discover(only) == []
