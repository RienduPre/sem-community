"""#984 / #985 — two Wallbox units behind the community MQTT bridge.

RienduPre's install (18.09.2026): ``wallbox_links_*`` and ``wallbox_rechts_*``
(the second unit with ``_2`` suffixes), plus a long tail of diagnostics the
bridge publishes without a unit prefix. The bridge rides the generic ``mqtt``
platform, so — like JuiceBox (#816) — every rule requires the wallbox naming,
and identity requires power AND an energy counter AND the current control.
The bridge also publishes per-phase power, power-boost power, and nine
``*_status`` sensors: the rules need a "not" as much as a "names".
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


def _links():
    """#984 — verbatim from the issue, device classes from its parentheses."""
    d = "wb-links"
    m = "mqtt"
    return [
        _entry("number.wallbox_links_max_charging_current", m, d, "current"),
        _entry("switch.wallbox_links_charging_enable", m, d),
        _entry("binary_sensor.wallbox_links_cable_connected", m, d, "plug"),
        _entry("sensor.wallbox_links_cumulative_added_energy", m, d, "energy"),
        _entry("sensor.wallbox_links_added_range", m, d, "distance"),
        _entry("number.wallbox_links_halo_brightness", m, d),
        _entry("sensor.wallbox_links_charging_power_l3", m, d, "power"),
        _entry("sensor.wallbox_links_charging_current_l3", m, d, "current"),
        _entry("sensor.wallbox_links_charging_current_l1", m, d, "current"),
        _entry("sensor.wallbox_links_charging_power_l2", m, d, "power"),
        _entry("sensor.wallbox_links_charging_current_l2", m, d, "current"),
        _entry("sensor.wallbox_links_charging_power_l1", m, d, "power"),
        _entry("sensor.wallbox_links_temperature_line_3", m, d, "temperature"),
        _entry("sensor.wallbox_links_added_energy", m, d, "energy"),
        _entry("sensor.wallbox_links_charging_power", m, d, "power"),
        _entry("sensor.wallbox_links_status", m, d),
        _entry("sensor.wallbox_s2_open", m, d),
        _entry("sensor.wallbox_m2w_status", m, d),
        _entry("sensor.wallbox_power_boost_cumulative_added_energy", m, d, "energy"),
        _entry("sensor.wallbox_state_machine", m, d),
        _entry("sensor.wallbox_control_pilot", m, d),
        _entry("sensor.wallbox_power_boost_current_l3", m, d, "current"),
        _entry("sensor.wallbox_power_boost_l1", m, d, "power"),
        _entry("sensor.wallbox_power_boost_l3", m, d, "power"),
        _entry("sensor.wallbox_power_boost_l2", m, d, "power"),
        _entry("sensor.wallbox_ecosmart_total_energy", m, d, "energy"),
        _entry("binary_sensor.wallbox_welding_detection", m, d, "problem"),
        _entry("sensor.wallbox_dca_current_l2", m, d, "current"),
        _entry("sensor.wallbox_control_pilot_state", m, d),
        _entry("binary_sensor.wallbox_charging_enable_status", m, d),
        _entry("sensor.wallbox_ecosmart_current_proposal", m, d, "current"),
        _entry("binary_sensor.wallbox_ocpp_connected", m, d, "connectivity"),
        _entry("binary_sensor.wallbox_firmware_error", m, d, "problem"),
        _entry("sensor.wallbox_mid_status", m, d),
        _entry("sensor.wallbox_max_available_current", m, d, "current"),
        _entry("sensor.wallbox_ecosmart_green_energy", m, d, "energy"),
        _entry("sensor.wallbox_powerboost_status", m, d),
        _entry("sensor.wallbox_connectivity_status", m, d),
        _entry("sensor.wallbox_ecosmart_mode", m, d),
        _entry("binary_sensor.wallbox_ocpp_mismatch", m, d, "problem"),
        _entry("binary_sensor.wallbox_ocpp_enabled", m, d, "power"),
        _entry("button.wallbox_restart_wallbox", m, d),
        _entry("sensor.wallbox_dynamic_power_sharing_max_current", m, d, "current"),
        _entry("sensor.wallbox_schedule_current_proposal", m, d, "current"),
        _entry("sensor.wallbox_ocpp_status", m, d),
        _entry("sensor.wallbox_internal_meter_energy", m, d, "energy"),
        _entry("sensor.wallbox_schedule_status", m, d),
        _entry("sensor.wallbox_external_meter_status", m, d),
        _entry("sensor.wallbox_control_mode", m, d),
        _entry("sensor.wallbox_icp_max_current", m, d, "current"),
        _entry("sensor.wallbox_max_charging_current_sensor", m, d, "current"),
        _entry("sensor.wallbox_control_pilot_status_raw", m, d),
    ]


def _rechts():
    """#985 — the second unit; the bridge suffixed its entities ``_2``."""
    d = "wb-rechts"
    m = "mqtt"
    return [
        _entry("sensor.wallbox_rechts_charging_power_2", m, d, "power"),
        _entry("sensor.wallbox_rechts_charging_power_l1", m, d, "power"),
        _entry("sensor.wallbox_rechts_charging_current_l2", m, d, "current"),
        _entry("number.wallbox_rechts_halo_brightness", m, d),
        _entry("sensor.wallbox_rechts_status_2", m, d),
        _entry("sensor.wallbox_rechts_added_energy_2", m, d, "energy"),
        _entry("sensor.wallbox_rechts_charging_power_l2", m, d, "power"),
        _entry("sensor.wallbox_rechts_charging_power_l3", m, d, "power"),
        _entry("binary_sensor.wallbox_rechts_cable_connected_2", m, d, "plug"),
        _entry("switch.wallbox_rechts_charging_enable_2", m, d),
        _entry("sensor.wallbox_rechts_cumulative_added_energy_2", m, d, "energy"),
        _entry("number.wallbox_rechts_max_charging_current_2", m, d, "current"),
        _entry("sensor.wallbox_rechts_state_machine", m, d),
        _entry("sensor.wallbox_rechts_power_boost_l2", m, d, "power"),
        _entry("sensor.wallbox_rechts_power_boost_cumulative_added_energy", m, d, "energy"),
        _entry("sensor.wallbox_rechts_ecosmart_green_energy", m, d, "energy"),
        _entry("sensor.wallbox_rechts_ocpp_status", m, d),
        _entry("sensor.wallbox_rechts_control_pilot_status_raw", m, d),
        _entry("sensor.wallbox_rechts_ecosmart_mode", m, d),
        _entry("sensor.wallbox_rechts_connectivity_status", m, d),
        _entry("sensor.wallbox_rechts_internal_meter_energy", m, d, "energy"),
        _entry("sensor.wallbox_rechts_max_charging_current_sensor", m, d, "current"),
        _entry("binary_sensor.wallbox_rechts_ocpp_enabled", m, d, "power"),
        _entry("sensor.wallbox_rechts_powerboost_status", m, d),
        _entry("sensor.wallbox_rechts_schedule_status", m, d),
        _entry("sensor.wallbox_rechts_mid_status", m, d),
        _entry("sensor.wallbox_rechts_external_meter_status", m, d),
        _entry("sensor.wallbox_rechts_ecosmart_status", m, d),
    ]


def _juicebox():
    """The #816 shape, so the two mqtt brands are proven not to cross-claim."""
    d = "jb-1"
    return [
        _entry("sensor.juicebox_abc123_power", "mqtt", d, "power"),
        _entry("sensor.juicebox_abc123_energy_lifetime", "mqtt", d, "energy"),
        _entry("sensor.juicebox_abc123_energy_session", "mqtt", d, "energy"),
        _entry("sensor.juicebox_abc123_status", "mqtt", d),
        _entry("number.juicebox_abc123_max_current", "mqtt", d, "current"),
    ]


def _by_control(chargers):
    return {c["ev_current_control_entity"]: c for c in chargers}


def test_two_bridged_wallboxes_become_two_chargers():
    by = _by_control(_discover(_links() + _rechts()))
    assert set(by) == {
        "number.wallbox_links_max_charging_current",
        "number.wallbox_rechts_max_charging_current_2",
    }
    links = by["number.wallbox_links_max_charging_current"]
    rechts = by["number.wallbox_rechts_max_charging_current_2"]
    assert links["ev_start_stop_entity"] == "switch.wallbox_links_charging_enable"
    assert rechts["ev_start_stop_entity"] == "switch.wallbox_rechts_charging_enable_2"
    assert links["ev_connected_sensor"] == "binary_sensor.wallbox_links_cable_connected"
    assert rechts["ev_connected_sensor"] == "binary_sensor.wallbox_rechts_cable_connected_2"


def test_the_power_is_the_unit_total_not_a_phase_or_the_boost():
    by = _by_control(_discover(_links() + _rechts()))
    assert by["number.wallbox_links_max_charging_current"]["ev_charging_power_sensor"] \
        == "sensor.wallbox_links_charging_power"
    assert by["number.wallbox_rechts_max_charging_current_2"]["ev_charging_power_sensor"] \
        == "sensor.wallbox_rechts_charging_power_2"


def test_the_two_energies_are_the_units_own_counters():
    by = _by_control(_discover(_links() + _rechts()))
    links = by["number.wallbox_links_max_charging_current"]
    assert links["ev_total_energy_sensor"] == "sensor.wallbox_links_cumulative_added_energy"
    assert links["ev_session_energy_sensor"] == "sensor.wallbox_links_added_energy"
    rechts = by["number.wallbox_rechts_max_charging_current_2"]
    assert rechts["ev_total_energy_sensor"] == "sensor.wallbox_rechts_cumulative_added_energy_2"
    assert rechts["ev_session_energy_sensor"] == "sensor.wallbox_rechts_added_energy_2"


def test_the_status_is_the_charger_status_not_one_of_the_nine_others():
    by = _by_control(_discover(_links() + _rechts()))
    assert by["number.wallbox_links_max_charging_current"]["ev_charging_sensor"] \
        == "sensor.wallbox_links_status"
    assert by["number.wallbox_rechts_max_charging_current_2"]["ev_charging_sensor"] \
        == "sensor.wallbox_rechts_status_2"


def test_a_shelly_plug_over_mqtt_is_not_a_wallbox():
    shelly = [
        _entry("sensor.shelly_plug_power", "mqtt", "sh-1", "power"),
        _entry("sensor.shelly_plug_energy", "mqtt", "sh-1", "energy"),
        _entry("number.shelly_plug_max_current", "mqtt", "sh-1", "current"),
    ]
    assert _discover(shelly) == []


def test_a_wallbox_without_its_current_control_is_not_claimed():
    """Power and energy alone are a meter, not a charger SEM can drive."""
    meter_only = [e for e in _links() if not e.entity_id.startswith("number.")]
    assert _discover(meter_only) == []


def test_juicebox_and_wallbox_do_not_cross_claim():
    chargers = _discover(_juicebox() + _links())
    assert {c["ev_current_control_entity"] for c in chargers} == {
        "number.juicebox_abc123_max_current",
        "number.wallbox_links_max_charging_current",
    }
