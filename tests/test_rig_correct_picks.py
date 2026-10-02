"""#1032 review — what the crawler SHOULD pick on each rig integration,
stated from what the integration's entities mean, not from what the code
happened to produce. The golden snapshots in tests/integrations_rig/crawler
are regenerated only after these pass. A known wrong pick that belongs to
another fix is an ``xfail(strict=True)`` naming that issue: it fails loudly
the day the fix lands, so the snapshot is never the place a bug hides.
"""
from __future__ import annotations

import pytest

from .integrations_rig.rig import crawl, load_capture, replay


async def _rep(hass, *names):
    for n in names:
        await replay(hass, load_capture(n))
    return crawl(hass)


def _charger(rep, platform):
    rows = [c for c in rep["chargers"] if c["platform"] == platform]
    assert len(rows) == 1, rows
    return {k: (v.get("entity") or v.get("value")) for k, v in rows[0]["mapped"].items()}


def _offer(rep, platform):
    offers = [n["suggested_charger"] for n in rep["near_misses"]
              if n["platform"] == platform and n["suggested_charger"]]
    assert len(offers) == 1, rep["near_misses"]
    return offers[0]


async def test_ohme(hass):
    m = _charger(await _rep(hass, "ohme"), "ohme")
    assert m["ev_charge_mode_entity"] == "select.ohme_home_pro_charge_mode"
    assert m["ev_charging_power_sensor"] == "sensor.ohme_home_pro_power"


async def test_keba(hass):
    m = _charger(await _rep(hass, "keba"), "keba")
    assert m["ev_charger_service"] == "keba.set_current"
    assert m["ev_charging_power_sensor"] == "sensor.keba_p30_charging_power"
    assert m["ev_connected_sensor"] == "binary_sensor.keba_p30_plug"


async def test_easee_the_equalizer_is_a_meter(hass):
    rep = await _rep(hass, "easee")
    m = _charger(rep, "easee")
    assert m["ev_charging_power_sensor"] == "sensor.easee_home_eh123456_power"
    assert m["ev_charger_service"] == "easee.set_charger_dynamic_limit"
    assert "equalizer" not in str(m)
    assert [x for x in rep["meters"] if x["platform"] == "easee"]


async def test_nrgkick(hass):
    o = _offer(await _rep(hass, "nrgkick"), "nrgkick")
    assert o["ev_current_control_entity"] == "number.nrgkick_test_charging_current"
    assert o["ev_charging_power_sensor"] == "sensor.nrgkick_test_total_active_power"


async def test_openevse(hass):
    o = _offer(await _rep(hass, "openevse"), "openevse")
    assert o["ev_current_control_entity"] == "number.openevse_mock_config_charge_rate"
    assert o["ev_charging_power_sensor"] == "sensor.openevse_mock_config_charging_power"
    assert o["ev_connected_sensor"] == "binary_sensor.openevse_mock_config_vehicle_connected"


async def test_zaptec(hass):
    rep = await _rep(hass, "zaptec")
    o = _offer(rep, "zaptec")
    assert o["ev_current_control_entity"] == "number.abbastova_available_current"
    assert o["ev_charging_power_sensor"] == "sensor.zaptec_go2_zap012345_total_charge_power"
    assert o["ev_start_service"] == "button.press"


async def test_myenergi(hass):
    o = _offer(await _rep(hass, "myenergi"), "myenergi")
    assert o["ev_charge_mode_entity"] == "select.test_zappi_1_myenergi_test_zappi_1_charge_mode"
    assert o["ev_charging_power_sensor"] == (
        "sensor.test_zappi_1_myenergi_test_zappi_1_power_ct_internal_load")


@pytest.mark.xfail(strict=True, reason="#1035 (autopilot): Peblar picks the "
                   "force-single-phase switch and one phase's power")
async def test_peblar(hass):
    m = _charger(await _rep(hass, "peblar"), "peblar")
    assert m["ev_current_control_entity"] == "number.peblar_ev_charger_charge_limit"
    assert m["ev_start_stop_entity"] == "switch.peblar_ev_charger_charge"
    assert m["ev_charging_power_sensor"] == "sensor.peblar_ev_charger_power"


@pytest.mark.xfail(strict=True, reason="#1034 (autopilot): V2C picks the "
                   "minimum current and the PV power")
async def test_v2c(hass):
    m = _charger(await _rep(hass, "v2c"), "v2c")
    assert m["ev_current_control_entity"] == "number.evse_1_1_1_1_intensity"
    assert m["ev_charging_power_sensor"] == "sensor.evse_1_1_1_1_charge_power"


@pytest.mark.parametrize("name", ["vicare", "blue_current"])
async def test_no_charger_offered(hass, name):
    rep = await _rep(hass, name)
    domain = load_capture(name)["domain"]
    assert not [c for c in rep["chargers"] if c["platform"] == domain]
    assert not [n for n in rep["near_misses"]
                if n["platform"] == domain and n["suggested_charger"]]
