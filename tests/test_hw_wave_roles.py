"""#1032 hardware wave — the crawler learns ROLES, proven on the real output
of the integrations that expose them (tests/integrations_rig).

No brand is named in the code under test; the brands are only the data.
"""
from __future__ import annotations

import json

import pytest

from .integrations_rig.rig import crawl, load_capture, replay


async def _offers(hass, *names):
    for n in names:
        await replay(hass, load_capture(n))
    rep = crawl(hass)
    return rep, rep.get("role_offers") or []


def _by_platform(offers, platform):
    return [o for o in offers if o["platform"] == platform]


# ── R3 charge control on the car ─────────────────────────────────────────

@pytest.mark.parametrize("name", ["tesla_fleet", "teslemetry", "tessie"])
async def test_r3_the_car_integration_offers_its_charge_control(hass, name):
    _, offers = await _offers(hass, name)
    cars = [o for o in _by_platform(offers, name) if o["kind"] == "vehicle"]
    assert len(cars) == 1, offers
    offer = cars[0]["offer"]
    assert offer["ev_current_control_entity"] == "number.test_charge_current"
    assert offer["ev_charging_power_sensor"] == "sensor.test_charger_power"
    if name != "tessie":
        assert offer["ev_start_stop_entity"] == "switch.test_charge"


@pytest.mark.parametrize("name", ["tesla_fleet", "teslemetry", "tessie"])
async def test_r3_the_energy_site_is_not_a_car(hass, name):
    _, offers = await _offers(hass, name)
    for o in _by_platform(offers, name):
        assert "energy_site" not in json.dumps(o["offer"]), o


# ── R4 read-only charger + the car that drives it ────────────────────────

async def test_r4_a_wall_connector_alone_only_reports(hass):
    _, offers = await _offers(hass, "tesla_wall_connector")
    (wc,) = _by_platform(offers, "tesla_wall_connector")
    assert wc["kind"] == "read_only_charger"
    assert wc["complete"] is False and "control" in wc["missing"]
    assert wc["offer"]["ev_connected_sensor"].endswith("vehicle_connected")
    assert wc["offer"]["ev_charging_power_sensor"].endswith("total_power")


async def test_r4_a_wall_connector_is_driven_through_the_one_car(hass):
    _, offers = await _offers(hass, "tesla_wall_connector", "tesla_fleet")
    (wc,) = _by_platform(offers, "tesla_wall_connector")
    assert wc["kind"] == "charger_via_vehicle"
    assert wc["complete"] is True
    assert wc["offer"]["ev_current_control_entity"] == "number.test_charge_current"
    assert wc["offer"]["ev_start_stop_entity"] == "switch.test_charge"
    # power and plug stay the wall connector's own readings
    assert wc["offer"]["ev_charging_power_sensor"].startswith(
        "sensor.tesla_wall_connector")


async def test_r4_two_cars_are_a_question_not_a_guess(hass):
    await replay(hass, load_capture("tessie"), suffix="_car2")
    _, offers = await _offers(hass, "tesla_wall_connector", "tesla_fleet")
    (wc,) = _by_platform(offers, "tesla_wall_connector")
    assert wc["kind"] == "read_only_charger"
    assert len(wc["choose_vehicle"]) == 2


# ── R5 a select read by its options ──────────────────────────────────────

async def test_r5_the_zappi_charge_mode_is_found_by_its_options(hass):
    _, offers = await _offers(hass, "myenergi")
    chargers = sorted(_by_platform(offers, "myenergi"),
                      key=lambda o: o["offer"].get("ev_charge_mode_entity", ""))
    # The fixture has two Zappis. Only the first has an internal-load CT —
    # the clamp that measures the car; the second's CTs measure an AC
    # battery and a monitor point. A charger SEM cannot measure is not
    # offered (the near-miss rule, kept).
    assert len(chargers) == 1, offers
    assert "zappi_1" in chargers[0]["offer"]["ev_charge_mode_entity"]
    for o in chargers:
        off = o["offer"]
        assert off["ev_charge_mode_entity"].endswith("_charge_mode")
        assert off["ev_charge_mode_start"] == "Fast"
        assert off["ev_charge_mode_stop"] == "Stopped"
        assert off["_suggested_phase_switch"]["entity"].endswith("_phase_setting")


async def test_r5_a_water_heater_diverter_is_not_a_charger(hass):
    _, offers = await _offers(hass, "myenergi")
    assert not any("eddi" in json.dumps(o["offer"]) for o in offers)
    assert not any("harvi" in json.dumps(o["offer"]) for o in offers)


# ── R1 companion + R2 start/stop pair ────────────────────────────────────

async def test_r2_the_start_and_stop_buttons_are_the_control(hass):
    _, offers = await _offers(hass, "zaptec_no_limit")
    (chg,) = _by_platform(offers, "zaptec")
    off = chg["offer"]
    assert off["ev_start_service"] == "button.press"
    assert json.loads(off["ev_start_service_data"])["entity_id"].endswith(
        "_resume_charging")
    assert json.loads(off["ev_stop_service_data"])["entity_id"].endswith(
        "_stop_charging_final")
    # the charger's own max current is a stored setting: not rewritten
    assert "ev_current_control_entity" not in off
    assert chg["complete"] is True


async def test_r2_a_stop_button_alone_is_not_a_control(hass):
    _, offers = await _offers(hass, "blue_current")
    for o in offers:
        assert "ev_start_service" not in o["offer"]


async def test_r1_the_installation_is_the_chargers_companion(hass):
    rep, offers = await _offers(hass, "zaptec")
    (chg,) = _by_platform(offers, "zaptec")
    assert len(chg["companions"]) == 1
    # the live current control comes from the installation, not the setting
    assert chg["offer"]["ev_current_control_entity"].endswith("_available_current")
    companion = chg["companions"][0]["device_id"]
    assert companion not in {n.get("device_id") for n in rep["near_misses"]}


# ── R6 services read by their fields ─────────────────────────────────────

async def test_r6_a_per_phase_service_is_named(hass):
    """Easee's chargers are claimed by its own path today (no offer); the
    service role is read the same way for any integration."""
    from custom_components.solar_energy_management.charger_roles import (
        service_roles,
    )
    cap = load_capture("easee")
    roles = service_roles("easee", cap["services"])
    assert roles["phase_current_service"]["service"] == "easee.set_circuit_dynamic_limit"


async def test_r6_a_site_current_service_is_report_data_only(hass):
    _, offers = await _offers(hass, "zaptec_no_limit")
    (chg,) = _by_platform(offers, "zaptec")
    assert chg["site_service"]["service"] == "zaptec.limit_current"
    assert "ev_charger_service" not in chg["offer"]


# ── a charger driven by services, or already set up, is not "read-only" ──

def _keba_without_its_brand_path():
    """The KEBA capture under a domain no brand path knows — what any
    service-driven integration looks like to the role reader alone."""
    cap = json.loads(json.dumps(load_capture("keba")))
    cap["domain"] = "rig_svc_charger"
    for e in cap["entities"]:
        e["entity_id"] = e["entity_id"].replace("keba_p30", "svc_box")
    return cap


async def test_a_service_controlled_charger_is_not_read_only(hass):
    """.175, 02.10: a real KEBA, controlled through keba.set_current, came
    back as a read-only charger with 'missing control'."""
    await replay(hass, _keba_without_its_brand_path())
    rep = crawl(hass)
    (o,) = [x for x in rep["role_offers"] if x["platform"] == "rig_svc_charger"]
    assert o["kind"] == "charger", o
    assert o["complete"] is True
    assert o["offer"]["ev_charger_service"] == "rig_svc_charger.set_current"
    assert o["offer"]["ev_service_param_name"] == "current"


async def test_a_charger_sem_already_drives_gets_no_offer(hass):
    """The real KEBA on .175: the brand path binds it, so no role offer."""
    _, offers = await _offers(hass, "keba")
    assert _by_platform(offers, "keba") == []


async def test_a_configured_charger_gets_no_offer(hass):
    from custom_components.solar_energy_management.hardware_detection import (
        build_detection_report,
    )
    cap = _keba_without_its_brand_path()
    await replay(hass, cap)
    rep = build_detection_report(
        hass, configured_entities={"sensor.svc_box_charging_power"})
    assert [x for x in rep["role_offers"]
            if x["platform"] == "rig_svc_charger"] == []


# ── nothing becomes a charger that is not one ────────────────────────────

@pytest.mark.parametrize("name", ["vicare"])
async def test_no_role_finds_a_charger_on_a_heat_pump(hass, name):
    _, offers = await _offers(hass, name)
    assert offers == []
