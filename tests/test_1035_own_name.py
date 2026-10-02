"""#1035 — a word in the DEVICE name is not a word about the entity.

Found by loading Home Assistant's own Peblar test data (HA 2026.8.2) and
running SEM's detection on it. SEM picked:

* start/stop: ``switch.peblar_ev_charger_force_single_phase``, not
  ``switch.peblar_ev_charger_charge`` — so SEM would switch the box to one
  phase to "stop" a charge;
* charging power: ``sensor.peblar_ev_charger_power_phase_3``, one phase,
  not ``sensor.peblar_ev_charger_power`` — so SEM saw a third of the power.

Root cause, switch: Home Assistant builds an entity id from the device name
and the entity's own name. The Peblar's default device name is "Peblar EV
Charger", so "charge" is in EVERY id of the device, and the brand rule
``"charge" in entity_id`` matched both switches. The loop kept the last one.
The same shape is in every brand rule that tests a word against the whole
id: a charger whose owner named it "Charger" or "Carport" fed every entity
to the rule.

Root cause, power: the brand rule binds on ``device_class: power`` and keeps
the last sensor; the per-phase sensors come after the total. Bug class 89
already swapped a CAPABILITY bound this way, but not one phase.

The pins below assert the class, not the brand:

* the Peblar from HA's own test data, in either order;
* every platform in ``_EV_CHARGER_PLATFORMS``, on a device named "EV
  Charger" and one named "Carport Charger", with role-free entities before
  and after the real ones — no role takes one;
* a code check that no brand rule tests a word against the whole id.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.solar_energy_management import hardware_detection as hd
from custom_components.solar_energy_management.hardware_detection import (
    _EV_CHARGER_PLATFORMS,
    _discover_ocpp,
    _discover_openwb,
    _discover_unit,
    _is_phase_leg,
    _own_names,
    _reject_capability_sensor,
    build_detection_report,
    discover_all_ev_chargers_from_registry,
    ocpp_charge_control_switch,
)


def _entry(entity_id, platform, device_id="dev-1", device_class=None,
           unit=None, translation_key=None, unique_id=None):
    return SimpleNamespace(
        entity_id=entity_id, platform=platform, device_id=device_id,
        original_device_class=device_class, disabled_by=None,
        original_unit_of_measurement=unit, unit_of_measurement=unit,
        translation_key=translation_key,
        unique_id=unique_id or entity_id.split(".", 1)[1],
        config_entry_id="entry-1",
    )


def _registry(entries):
    registry = MagicMock()
    registry.entities.values.return_value = entries
    return registry


def _discover(entries):
    with patch(
        "custom_components.solar_energy_management.hardware_detection."
        "entity_registry.async_get",
        return_value=_registry(entries),
    ):
        return discover_all_ev_chargers_from_registry(MagicMock())


# ── Home Assistant's own Peblar (tests/components/peblar, HA 2026.8.2) ──
# entity id, device class, unit, translation key — as core's snapshots
# register them, in the snapshots' order.
_PEBLAR = [
    ("binary_sensor.peblar_ev_charger_active_errors", "problem", None, "active_error_codes"),
    ("binary_sensor.peblar_ev_charger_active_warnings", "problem", None, "active_warning_codes"),
    ("button.peblar_ev_charger_identify", "identify", None, None),
    ("button.peblar_ev_charger_restart", "restart", None, None),
    ("number.peblar_ev_charger_charge_limit", "current", "A", "charge_current_limit"),
    ("select.peblar_ev_charger_smart_charging", None, None, "smart_charging"),
    ("sensor.peblar_ev_charger_current", "current", "A", None),
    ("sensor.peblar_ev_charger_current_phase_1", "current", "A", "current_phase_1"),
    ("sensor.peblar_ev_charger_current_phase_2", "current", "A", "current_phase_2"),
    ("sensor.peblar_ev_charger_current_phase_3", "current", "A", "current_phase_3"),
    ("sensor.peblar_ev_charger_lifetime_energy", "energy", "kWh", "energy_total"),
    ("sensor.peblar_ev_charger_limit_source", "enum", None, "charge_current_limit_source"),
    ("sensor.peblar_ev_charger_power", "power", "W", None),
    ("sensor.peblar_ev_charger_power_phase_1", "power", "W", "power_phase_1"),
    ("sensor.peblar_ev_charger_power_phase_2", "power", "W", "power_phase_2"),
    ("sensor.peblar_ev_charger_power_phase_3", "power", "W", "power_phase_3"),
    ("sensor.peblar_ev_charger_session_energy", "energy", "kWh", "energy_session"),
    ("sensor.peblar_ev_charger_state", "enum", None, "cp_state"),
    ("sensor.peblar_ev_charger_uptime", "timestamp", None, "uptime"),
    ("sensor.peblar_ev_charger_voltage_phase_1", "voltage", "V", "voltage_phase_1"),
    ("sensor.peblar_ev_charger_voltage_phase_2", "voltage", "V", "voltage_phase_2"),
    ("sensor.peblar_ev_charger_voltage_phase_3", "voltage", "V", "voltage_phase_3"),
    ("switch.peblar_ev_charger_charge", None, None, "charge"),
    ("switch.peblar_ev_charger_force_single_phase", None, None, "force_single_phase"),
]


def _peblar(reverse=False):
    rows = list(reversed(_PEBLAR)) if reverse else _PEBLAR
    return [_entry(eid, "peblar", "peblar-1", dc, unit, key)
            for eid, dc, unit, key in rows]


class TestThePeblarFromHomeAssistantsOwnTestData:
    def test_start_stop_is_the_charge_switch(self):
        charger = _discover(_peblar())[0]
        assert charger["ev_start_stop_entity"] == "switch.peblar_ev_charger_charge"

    def test_charging_power_is_the_total_not_one_phase(self):
        charger = _discover(_peblar())[0]
        assert charger["ev_charging_power_sensor"] == "sensor.peblar_ev_charger_power"

    def test_the_rest_is_unchanged(self):
        charger = _discover(_peblar())[0]
        assert charger["ev_current_control_entity"] == \
            "number.peblar_ev_charger_charge_limit"
        assert charger["ev_session_energy_sensor"] == \
            "sensor.peblar_ev_charger_session_energy"
        assert charger["ev_total_energy_sensor"] == \
            "sensor.peblar_ev_charger_lifetime_energy"

    def test_registry_order_does_not_decide(self):
        forward = _discover(_peblar())[0]
        backward = _discover(_peblar(reverse=True))[0]
        assert forward == backward

    def test_the_diagnostics_report_shows_the_same(self):
        report = build_detection_report(MagicMock(), registry=_registry(_peblar()))
        peblar = [c for c in report["chargers"] if c.get("platform") == "peblar"]
        assert peblar
        mapped = peblar[0]["mapped"]
        assert mapped["ev_start_stop_entity"]["entity"] == \
            "switch.peblar_ev_charger_charge"
        assert mapped["ev_charging_power_sensor"]["entity"] == \
            "sensor.peblar_ev_charger_power"

    def test_the_data_really_holds_the_trap(self):
        """No vacuous pass: the rule that was there, spelled out — a word
        tested against the whole id, the last match kept — hands back
        exactly the two entities #1035 names, on this data, in this order."""
        switches = [e for e, *_ in _PEBLAR if e.startswith("switch.")]
        powers = [e for e, dc, *_ in _PEBLAR
                  if e.startswith("sensor.") and dc == "power"]
        assert [s for s in switches if "charge" in s][-1] == \
            "switch.peblar_ev_charger_force_single_phase"
        assert powers[-1] == "sensor.peblar_ev_charger_power_phase_3"


# ── The entity's own name ────────────────────────────────────────────
class TestOwnNames:
    def test_the_device_name_is_taken_off(self):
        own = _own_names(_peblar())
        assert own["switch.peblar_ev_charger_force_single_phase"] == \
            "_force_single_phase"
        assert own["switch.peblar_ev_charger_charge"] == "_charge"

    def test_a_main_entity_keeps_the_word_that_names_it(self):
        """GARO's start/stop switch IS the device: its id is the device name
        alone. Every id keeps at least its last word, so the row's
        "laddbox" hint still finds it."""
        own = _own_names([
            _entry("switch.garo_laddbox", "garo_wallbox"),
            _entry("sensor.garo_laddbox_power", "garo_wallbox"),
            _entry("number.garo_laddbox_current_limit", "garo_wallbox"),
        ])
        assert own["switch.garo_laddbox"] == "_laddbox"
        assert own["sensor.garo_laddbox_power"] == "_laddbox_power"

    def test_one_entity_alone_keeps_its_whole_name(self):
        own = _own_names([_entry("switch.ev_charger_charge", "peblar")])
        assert own == {"switch.ev_charger_charge": "_ev_charger_charge"}

    def test_a_transport_keeps_the_whole_name(self):
        """On mqtt the device name is the only mark of the brand: the
        JuiceBox and Wallbox rows ask for it."""
        own = _own_names([
            _entry("sensor.juicebox_power", "mqtt"),
            _entry("sensor.juicebox_lifetime_energy", "mqtt"),
        ])
        assert own["sensor.juicebox_power"] == "_juicebox_power"

    def test_names_that_share_nothing_lose_nothing(self):
        own = _own_names([
            _entry("sensor.garage_power", "keba"),
            _entry("binary_sensor.keba_plug", "keba"),
        ])
        assert own["sensor.garage_power"] == "_garage_power"


# ── One phase is not the reading ─────────────────────────────────────
class TestOnePhaseIsSwappedForTheTotal:
    def test_a_bound_phase_is_swapped_for_the_total(self):
        entities = _peblar()
        result = {"ev_charging_power_sensor": "sensor.peblar_ev_charger_power_phase_3"}
        _reject_capability_sensor(result, entities)
        assert result["ev_charging_power_sensor"] == "sensor.peblar_ev_charger_power"

    def test_l1_l2_l3_are_phases_too(self):
        entities = [
            _entry("sensor.wb_power", "wallbox", device_class="power", unit="W"),
            _entry("sensor.wb_power_l3", "wallbox", device_class="power", unit="W"),
        ]
        result = {"ev_charging_power_sensor": "sensor.wb_power_l3"}
        _reject_capability_sensor(result, entities)
        assert result["ev_charging_power_sensor"] == "sensor.wb_power"

    def test_swap_only_a_phase_with_no_total_stays(self):
        """Class 89: a charger with no power entity is worse than one that
        reads a third of it."""
        entities = [
            _entry("sensor.wb_power_phase_1", "wallbox", device_class="power", unit="W"),
            _entry("sensor.wb_status", "wallbox"),
        ]
        result = {"ev_charging_power_sensor": "sensor.wb_power_phase_1"}
        _reject_capability_sensor(result, entities)
        assert result["ev_charging_power_sensor"] == "sensor.wb_power_phase_1"

    def test_three_phase_power_is_the_sum_not_a_leg(self):
        assert _is_phase_leg("sensor.box_power_phase_3")
        assert _is_phase_leg("sensor.box_phase_2_power")
        assert _is_phase_leg("sensor.box_power_l1")
        assert not _is_phase_leg("sensor.box_3_phase_power")
        assert not _is_phase_leg("sensor.box_power")

    def test_a_device_named_phase_1_is_not_one_phase(self):
        """Whether a sensor is one phase is read from its own name."""
        entities = [
            _entry("sensor.phase_1_box_power", "wallbox", device_class="power", unit="W"),
            _entry("sensor.phase_1_box_power_l2", "wallbox", device_class="power", unit="W"),
        ]
        result = {"ev_charging_power_sensor": "sensor.phase_1_box_power"}
        _reject_capability_sensor(result, entities)
        assert result["ev_charging_power_sensor"] == "sensor.phase_1_box_power"


# ── A read role is never lost to the own names (class 89) ─────────────
class TestAReadRoleKeepsTheWholeIdAnswer:
    """openWB's power rule asks for "charg". A loadpoint whose device name
    says "Chargepoint" and whose power sensor is named in German matched
    only through the device name. Losing the reading costs more than the
    guess (class 89), so a measurand role the own names leave empty keeps
    the whole-id answer."""

    def _loadpoint(self):
        return [
            _entry("sensor.chargepoint_3_ladeleistung", "openwb2mqtt", "lp3", "power", "W"),
            _entry("binary_sensor.chargepoint_3_plug", "openwb2mqtt", "lp3", "plug"),
            _entry("number.chargepoint_3_current", "openwb2mqtt", "lp3", "current", "A"),
        ]

    def test_the_own_names_alone_find_no_power(self):
        """Non-vacuous: without the fallback the role is gone."""
        assert "ev_charging_power_sensor" not in _discover_openwb(self._loadpoint())

    def test_the_power_reading_stays(self):
        charger = _discover(self._loadpoint())[0]
        assert charger["ev_charging_power_sensor"] == \
            "sensor.chargepoint_3_ladeleistung"

    def test_a_control_is_not_filled_from_the_device_name(self):
        """A switch that only the device name calls "charge" is a guess that
        acts — it stays unbound."""
        entities = [
            _entry("sensor.charger_power_active_import", "ocpp", "cp", "power", "W"),
            _entry("sensor.charger_status_connector", "ocpp", "cp"),
            _entry("number.charger_maximum_current", "ocpp", "cp", "current", "A"),
            _entry("switch.charger_child_lock", "ocpp", "cp"),
        ]
        assert "ev_start_stop_entity" not in _discover_unit(_discover_ocpp, entities)

    def test_the_fallback_never_takes_an_entity_another_role_holds(self):
        def _fn(entities):
            own = _own_names(entities)
            out = {"ev_session_energy_sensor": "sensor.total_box_session"}
            for e in entities:
                if "total" in own[str(e.entity_id)]:
                    out["ev_total_energy_sensor"] = str(e.entity_id)
            return out
        # the whole-id pass keeps the LAST "total": the session counter
        entities = [_entry("sensor.total_box_power", "x"),
                    _entry("sensor.total_box_session", "x")]
        assert _fn(hd._WholeIds(entities))["ev_total_energy_sensor"] == \
            "sensor.total_box_session"
        result = _discover_unit(_fn, entities)
        assert "ev_total_energy_sensor" not in result


# ── The class, across every brand SEM discovers ──────────────────────
# Each platform gets one device whose NAME carries the words the rules look
# for. Beside the real entities sit entities whose own names name no role:
# placed first AND last, so neither a last-wins nor a first-wins loop can
# hide one.
_ROLE_ROWS = [
    ("binary_sensor", "plug_connected", "plug", None),
    ("binary_sensor", "charging", "power", None),
    ("sensor", "status_connector", None, None),
    ("sensor", "charging_power", "power", "W"),
    ("sensor", "total_energy", "energy", "kWh"),
    ("sensor", "session_energy", "energy", "kWh"),
    ("number", "charging_current", "current", "A"),
    ("switch", "charge", None, None),
]
_ROLE_FREE = [
    ("switch", "force_single_phase", None, None),
    ("switch", "child_lock", None, None),
    ("number", "led_brightness", None, None),
    ("select", "display_language", None, None),
    ("binary_sensor", "update_available", None, None),
    ("binary_sensor", "online", "connectivity", None),
    ("sensor", "wifi_signal", "signal_strength", "dBm"),
    ("button", "identify", "identify", None),
]
_DEVICE_NAMES = ("ev_charger", "carport_charger")


def _every_platform(device_name, decoys_last=True):
    entries = []
    for platform, _fn in _EV_CHARGER_PLATFORMS:
        dev = f"{platform}-{device_name}"
        real = [_entry(f"{dom}.{device_name}_{own}", platform, dev, dc, unit)
                for dom, own, dc, unit in _ROLE_ROWS]
        free = [_entry(f"{dom}.{device_name}_{own}", platform, dev, dc, unit)
                for dom, own, dc, unit in _ROLE_FREE]
        entries += real + free if decoys_last else free + real
    return entries


def _role_free_ids(device_name):
    return {f"{dom}.{device_name}_{own}" for dom, own, _dc, _u in _ROLE_FREE}


class TestNoBrandReadsTheDeviceName:
    def test_most_brands_actually_bite(self):
        """Non-vacuous: the registry must really produce chargers."""
        for device_name in _DEVICE_NAMES:
            assert len(_discover(_every_platform(device_name))) >= 10

    def test_the_device_names_do_carry_the_rule_words(self):
        """Non-vacuous: every role-free entity carries "charg" through the
        device name — the word half the brand rules ask for."""
        for device_name in _DEVICE_NAMES:
            for eid in _role_free_ids(device_name):
                assert "charg" in eid

    def test_no_role_takes_an_entity_whose_own_name_names_none(self):
        for device_name in _DEVICE_NAMES:
            free = _role_free_ids(device_name)
            for decoys_last in (True, False):
                for charger in _discover(_every_platform(device_name, decoys_last)):
                    for role, eid in charger.items():
                        if role.startswith("_"):
                            continue
                        assert eid not in free, (
                            f"{charger.get('_platform')} bound {eid} as {role} "
                            f"(device {device_name})")


# ── The manual OCPP path (#976) ──────────────────────────────────────
def _patched_registry(entries):
    class _Reg:
        def __init__(self):
            self.entities = {e.entity_id: e for e in entries}

        def async_get(self, eid):
            return self.entities.get(eid)
    return patch("homeassistant.helpers.entity_registry.async_get",
                 return_value=_Reg())


class TestTheOcppManualPath:
    def test_a_charge_point_named_charger_finds_its_charge_control(self):
        """OCPP names a charge point "charger" unless told otherwise, so
        "charge" is in every switch it has."""
        entries = [
            _entry("number.charger_maximum_current", "ocpp", "cp"),
            _entry("switch.charger_reset_lock", "ocpp", "cp"),
            _entry("switch.charger_charge_control", "ocpp", "cp"),
            _entry("switch.charger_availability", "ocpp", "cp"),
        ]
        with _patched_registry(entries):
            assert ocpp_charge_control_switch(
                MagicMock(), "number.charger_maximum_current") == \
                "switch.charger_charge_control"


# ── The code check ───────────────────────────────────────────────────
_WHOLE_ID_NAMES = {"eid", "eid_lower", "entity_id"}


def _brand_functions():
    names = {fn.__name__ for _p, fn in _EV_CHARGER_PLATFORMS
             if getattr(fn, "__name__", "<lambda>") != "<lambda>"}
    names |= {n for n in dir(hd) if n.startswith("_discover_")}
    names |= {"charger_from_near_miss", "ocpp_charge_control_switch"}
    return sorted(n for n in names if callable(getattr(hd, n, None)))


def _whole_id_word_tests(fn) -> list:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                if (isinstance(op, (ast.In, ast.NotIn))
                        and isinstance(left, ast.Constant)
                        and isinstance(left.value, str)
                        and isinstance(right, ast.Name)
                        and right.id in _WHOLE_ID_NAMES):
                    hits.append(f"{left.value!r} in {right.id}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_name_hit" and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in _WHOLE_ID_NAMES):
            hits.append(f"_name_hit({node.args[0].id}, …)")
    return hits


class TestNoBrandRuleTestsAWordAgainstTheWholeId:
    def test_the_check_sees_the_brand_functions(self):
        """Non-vacuous: the walk covers the hand-written brands and the
        hint matcher."""
        names = _brand_functions()
        for must in ("_discover_peblar", "_discover_ocpp", "_discover_zaptec",
                     "_discover_from_hints", "charger_from_near_miss"):
            assert must in names

    def test_the_check_catches_the_old_rule(self):
        def _old(entities):
            for entry in entities:
                eid = entry.entity_id
                if eid.startswith("switch.") and "charge" in eid:
                    return eid
        assert _whole_id_word_tests(_old) == ["'charge' in eid"]

    def test_no_brand_function_does(self):
        offenders = {n: _whole_id_word_tests(getattr(hd, n))
                     for n in _brand_functions()}
        assert not {n: h for n, h in offenders.items() if h}
