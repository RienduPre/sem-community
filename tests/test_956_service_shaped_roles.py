"""#956 — a capability a brand offers as a SERVICE is visible to the roster,
to the rules, and to the config step.

SEM learned what a brand can do by reading the entities its repository
declares. KEBA — SEM's own production wallbox — declares no current entity at
all: it offers ``keba.set_current``. The crawler never read a services.yaml,
``ROLE_RULES`` matched only entity platforms, and a proposal could only ever
name an entity. Three entity-only places; each one reads services now, and
the KEBA shape is the fixture on every one.

Three states at runtime: a proposal is made when the service registry says
the service exists, none when it says it does not — and none, saying so,
when the registry could not be asked (``services_of`` is None).
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.solar_energy_management import hardware_detection as hd

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crawler = _load("crawl_integration_roster", "scripts/crawl_integration_roster.py")
lexicon = _load("role_lexicon", "consts/role_lexicon.py")

# homeassistant/components/keba/services.yaml, read 24.09.2026
KEBA_SERVICES = """
request_data:
  name: Request data
authorize:
  name: Authorize
set_current:
  name: Set current
  fields:
    current:
      name: Current
      selector:
        number:
          min: 6
          max: 63
enable:
  name: Enable
disable:
  name: Disable
set_failsafe:
  name: Set failsafe
  fields:
    failsafe_timeout:
      name: Timeout
    failsafe_fallback:
      name: Fallback
    failsafe_persist:
      name: Persist
"""


class TestTheCrawlerReadsServices:
    def test_every_service_is_a_key_under_the_service_platform(self):
        vocab = crawler.mine_services(KEBA_SERVICES, "keba")
        assert set(vocab) == {"service"}
        assert set(vocab["service"]) == {
            "keba.request_data", "keba.authorize", "keba.set_current",
            "keba.enable", "keba.disable", "keba.set_failsafe"}
        assert vocab["service"]["keba.set_current"]["fields"] == ("current",)

    def test_a_broken_file_is_nothing_not_a_crash(self):
        assert crawler.mine_services("this: [is: not yaml", "x") == {}
        assert crawler.mine_services("- a list, not a map\n", "x") == {}


class TestTheRulesMatchServices:
    def test_set_current_is_the_current_control_and_failsafe_is_not(self):
        roles = crawler.roles_from_vocabulary(
            crawler.mine_services(KEBA_SERVICES, "keba"), lexicon, kind="charger")
        assert roles["ev_current_control"] == {
            "platform": "service", "keys": ("keba.set_current",), "options": (),
            "services": {"keba.set_current": {"fields": ("current",), "target": None}}}

    def test_an_entity_wins_over_a_service_for_the_same_role(self):
        """A brand with a current NUMBER and a set_current SERVICE (NRGkick's
        key beside KEBA's service): SEM's number-entity path is the better
        control, the service is the fallback."""
        vocab = {"number": {"set_current": {"options": ()}},
                 "service": {"brand.set_current": {
                     "options": (), "fields": ("current",)}}}
        roles = crawler.roles_from_vocabulary(vocab, lexicon, kind="charger")
        assert roles["ev_current_control"]["platform"] == "number"


def _fake_roster(vocab):
    return SimpleNamespace(ROLE_VOCAB=vocab, ROSTER={
        "keba": {"name": "Keba Charging Station", "installs": 427}})


KEBA_VOCAB = {"keba": {"ev_current_control": {
    "platform": "service", "keys": ("keba.set_current",), "options": (),
    "services": {"keba.set_current": {"fields": ("current",), "target": None}}}}}


@pytest.mark.unit
class TestTheProposalCanNameAService:
    def test_proposed_when_the_registry_has_the_service(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(KEBA_VOCAB)):
            out = hd.propose_roles_from_roster(
                [], "keba", services_of=lambda d: {"set_current", "enable"})
        p = out["ev_current_control"]
        assert p["service"] == "keba.set_current"
        assert p["action"] == "per_charger"
        assert p["per_charger_key"] == "ev_charger_service"
        assert "entity" not in p

    def test_nothing_when_the_registry_lacks_it(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(KEBA_VOCAB)):
            out = hd.propose_roles_from_roster(
                [], "keba", services_of=lambda d: {"enable"})
        assert out == {}

    def test_an_unknown_is_a_row_that_says_so_not_an_empty_answer(self):
        """Not a no — an unknown, and visibly so (ruflo, 24.09)."""
        with patch.object(hd, "_roster", return_value=_fake_roster(KEBA_VOCAB)):
            out = hd.propose_roles_from_roster([], "keba", services_of=None)
        assert out["ev_current_control"]["action"] == "unaskable"
        assert out["ev_current_control"]["service"] is None

    def test_services_of_is_none_without_a_running_hass(self):
        assert hd._services_of(None) is None
        assert hd._services_of(SimpleNamespace(is_running=False)) is None

    def test_services_of_reads_the_live_registry(self):
        hass = SimpleNamespace(
            is_running=True,
            services=SimpleNamespace(
                async_services=lambda: {"keba": {"set_current": object()}}))
        assert hd._services_of(hass)("keba") == {"set_current"}
        assert hd._services_of(hass)("nothing") == set()


@pytest.mark.unit
class TestTheNearMissBecomesAServiceCharger:
    def _ents(self):
        return [SimpleNamespace(entity_id="sensor.keba_p30_power", platform="keba",
                                device_id="keba-1", original_device_class="power"),
                SimpleNamespace(entity_id="binary_sensor.keba_p30_plug", platform="keba",
                                device_id="keba-1", original_device_class="plug")]

    def test_the_offer_carries_the_service_not_an_entity(self):
        proposed = {"ev_current_control": {"service": "keba.set_current",
                                           "action": "per_charger", "param": "current"}}
        out = hd.charger_from_near_miss(self._ents(), "keba", proposed)
        assert out["ev_charger_service"] == "keba.set_current"
        assert out["ev_service_param_name"] == "current"
        assert "ev_current_control_entity" not in out
        assert out["ev_charging_power_sensor"] == "sensor.keba_p30_power"
        assert out["ev_connected_sensor"] == "binary_sensor.keba_p30_plug"

    def test_no_power_sensor_still_means_please_report(self):
        proposed = {"ev_current_control": {"service": "keba.set_current"}}
        assert hd.charger_from_near_miss(self._ents()[1:], "keba", proposed) == {}


# ── (ruflo, 24.09.2026) the refutation, pinned ─────────────────────────────

ABL_SERVICES = """
set_charging_current:
  name: Set charging current
  target:
    entity:
      domain: number
      integration: ev_charger_modbus
  fields:
    current:
      name: Current
"""

GOE_SERVICES = """
set_max_current:
  name: Set max current
  fields:
    charger_name:
      name: Charger
    max_current:
      name: Max current
"""


class TestWhatAServiceTakesIsRecorded:
    def test_a_targeted_service_says_so(self):
        v = crawler.mine_services(ABL_SERVICES, "ev_charger_modbus")["service"]
        assert v["ev_charger_modbus.set_charging_current"]["target"] == "entity"
        assert v["ev_charger_modbus.set_charging_current"]["fields"] == ("current",)

    def test_a_global_service_has_no_target(self):
        v = crawler.mine_services(KEBA_SERVICES, "keba")["service"]
        assert v["keba.set_current"]["target"] is None

    def test_the_roster_slot_carries_each_services_fields_and_target(self):
        roles = crawler.roles_from_vocabulary(
            crawler.mine_services(GOE_SERVICES, "goecharger"), lexicon, kind="charger")
        meta = roles["ev_current_control"]["services"]["goecharger.set_max_current"]
        assert meta == {"fields": ("charger_name", "max_current"), "target": None}


class TestTheKindGateHoldsForServices:
    def test_a_vehicle_integrations_cloud_api_is_not_a_wallbox_control(self):
        """kia_uvo declares a service literally named set_charging_current.
        It is the car's, and a vehicle contributes only vehicle roles."""
        vocab = {"sensor": {"odometer": {"options": ()}, "ev_battery_level": {"options": ()}},
                 "service": {"kia_uvo.set_charging_current": {
                     "options": (), "fields": ("current",), "target": None}}}
        assert crawler.classify_kind(vocab, lexicon) == "vehicle"
        roles = crawler.roles_from_vocabulary(vocab, lexicon, kind="vehicle")
        assert "ev_current_control" not in roles


class TestAnEntityBeatsAServiceByRuleNotByAlphabet:
    def test_a_switch_beats_a_service_for_the_same_role(self, monkeypatch):
        """'service' sorts before 'switch'; the rule must not care."""
        lex = crawler._lexicon_module()
        monkeypatch.setitem(lex.SERVICE_ROLE_RULES, "battery_force_charge",
                            {"platform": "service", "any": (r"\.force_charge$",)})
        vocab = {"sensor": {"battery_soc": {"options": ()}},
                 "switch": {"force_charge": {"options": ()}},
                 "service": {"brand.force_charge": {"options": (), "fields": (), "target": None}}}
        roles = crawler.roles_from_vocabulary(vocab, lexicon, kind="energy")
        assert roles["battery_force_charge"]["platform"] == "switch"
        assert roles["battery_force_charge"]["keys"] == ("force_charge",)


def _vocab(domain, key, fields, target=None):
    return {domain: {"ev_current_control": {
        "platform": "service", "keys": (key,), "options": (),
        "services": {key: {"fields": fields, "target": target}}}}}


@pytest.mark.unit
class TestThreeStatesAreThreeAnswers:
    def test_unaskable_is_visible_not_empty(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("keba", "keba.set_current", ("current",)))):
            out = hd.propose_roles_from_roster([], "keba", services_of=None)
        p = out["ev_current_control"]
        assert p["action"] == "unaskable"
        assert p["service"] is None
        assert p["candidates"] == ["keba.set_current"]
        assert hd.charger_from_near_miss([], "keba", out) == {}

    def test_absent_is_empty(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("keba", "keba.set_current", ("current",)))):
            out = hd.propose_roles_from_roster([], "keba", services_of=lambda d: set())
        assert out == {}


@pytest.mark.unit
class TestAnOfferIsOnlyMadeWhereTheFactoryCanDriveIt:
    def _power(self):
        return [SimpleNamespace(entity_id="sensor.box_power", platform="x",
                                device_id="d", original_device_class="power")]

    def test_keba_is_offered_with_its_parameter_name(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("keba", "keba.set_current", ("current",)))):
            out = hd.propose_roles_from_roster([], "keba", services_of=lambda d: {"set_current"})
        assert out["ev_current_control"]["action"] == "per_charger"
        offer = hd.charger_from_near_miss(self._power(), "keba", out)
        assert offer["ev_charger_service"] == "keba.set_current"
        assert offer["ev_service_param_name"] == "current"

    def test_goecharger_needs_a_name_sem_cannot_fill_so_no_offer(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("goecharger", "goecharger.set_max_current", ("charger_name", "max_current")))):
            out = hd.propose_roles_from_roster([], "goecharger", services_of=lambda d: {"set_max_current"})
        p = out["ev_current_control"]
        assert p["action"] == "needs_hand_wiring"
        assert "charger_name" in p["reason"]
        assert p["param"] == "max_current"
        assert hd.charger_from_near_miss(self._power(), "goecharger", out) == {}

    def test_a_targeted_service_is_not_offered_either(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("ev_charger_modbus", "ev_charger_modbus.set_charging_current",
                       ("current",), target="entity"))):
            out = hd.propose_roles_from_roster([], "ev_charger_modbus",
                                               services_of=lambda d: {"set_charging_current"})
        assert out["ev_current_control"]["action"] == "needs_hand_wiring"
        assert "targets a entity" in out["ev_current_control"]["reason"]
        assert hd.charger_from_near_miss(self._power(), "ev_charger_modbus", out) == {}

    def test_a_global_one_field_service_names_its_own_field(self):
        with patch.object(hd, "_roster", return_value=_fake_roster(
                _vocab("brand", "brand.set_max_current", ("max_current",)))):
            out = hd.propose_roles_from_roster([], "brand", services_of=lambda d: {"set_max_current"})
        assert hd.charger_from_near_miss(self._power(), "brand", out)["ev_service_param_name"] == "max_current"


def _reg(entries):
    return SimpleNamespace(entities={e.entity_id: e for e in entries})


def _keba_box(platform="keba"):
    return [SimpleNamespace(entity_id=f"sensor.{platform}_power", platform=platform,
                            device_id="k1", original_device_class="power", disabled_by=None,
                            unique_id="p", translation_key=None),
            SimpleNamespace(entity_id=f"binary_sensor.{platform}_plug", platform=platform,
                            device_id="k1", original_device_class="plug", disabled_by=None,
                            unique_id="q", translation_key=None)]


@pytest.mark.unit
class TestAConfiguredServiceIsNotProposedAgain:
    def test_the_installed_walk_skips_a_service_already_in_use(self):
        roster = _fake_roster(_vocab("keba", "keba.set_current", ("current",)))
        with patch.object(hd, "_roster", return_value=roster):
            fresh = hd.propose_for_installed(_reg(_keba_box()), services_of=lambda d: {"set_current"})
            used = hd.propose_for_installed(_reg(_keba_box()), services_of=lambda d: {"set_current"},
                                            configured_entities={"keba.set_current"})
        assert [r["domain"] for r in fresh] == ["keba"]
        assert used == []


def _hass_with(services: dict):
    return SimpleNamespace(is_running=True,
                           services=SimpleNamespace(async_services=lambda: services))


@pytest.mark.unit
class TestAControlLessBrandFunctionFallsThroughToTheRoster:
    """go-eCharger's box has sensors and a service, no current number: the
    hand-written function found the sensors and CLAIMED the device, so the
    roster never spoke and the card showed a charger with 'see mapping'."""

    def _goe_box(self):
        d = "goe-1"
        return [SimpleNamespace(entity_id="sensor.goe_power", platform="goecharger", device_id=d,
                                original_device_class="power", disabled_by=None, unique_id="a", translation_key=None),
                SimpleNamespace(entity_id="binary_sensor.goe_plug", platform="goecharger", device_id=d,
                                original_device_class="plug", disabled_by=None, unique_id="b", translation_key=None),
                SimpleNamespace(entity_id="sensor.goe_energy_total", platform="goecharger", device_id=d,
                                original_device_class="energy", disabled_by=None, unique_id="c", translation_key=None)]

    def test_it_is_a_near_miss_with_the_reason_not_a_charger_with_no_control(self):
        roster = _fake_roster(_vocab("goecharger", "goecharger.set_max_current",
                                     ("charger_name", "max_current")))
        with patch.object(hd, "_roster", return_value=roster):
            rep = hd.build_detection_report(
                hass=_hass_with({"goecharger": {"set_max_current": object()}}),
                registry=_reg(self._goe_box()))
        assert not any(c["platform"] == "goecharger" for c in rep["chargers"])
        miss = [m for m in rep["near_misses"] if m["platform"] == "goecharger"]
        assert miss and miss[0]["proposed_roles"]["ev_current_control"]["action"] == "needs_hand_wiring"
        assert miss[0]["suggested_charger"] == {}

    def test_a_global_service_becomes_an_offer(self):
        roster = _fake_roster(_vocab("goecharger", "goecharger.set_max_current", ("max_current",)))
        with patch.object(hd, "_roster", return_value=roster):
            rep = hd.build_detection_report(
                hass=_hass_with({"goecharger": {"set_max_current": object()}}),
                registry=_reg(self._goe_box()))
        miss = [m for m in rep["near_misses"] if m["platform"] == "goecharger"]
        assert miss[0]["suggested_charger"]["ev_charger_service"] == "goecharger.set_max_current"
        assert miss[0]["suggested_charger"]["ev_service_param_name"] == "max_current"

    def test_without_the_roster_the_old_row_stays(self):
        with patch.object(hd, "_roster", return_value=None):
            rep = hd.build_detection_report(hass=None, registry=_reg(self._goe_box()))
        assert any(c["platform"] == "goecharger" and c["control"] == "see mapping"
                   for c in rep["chargers"])
