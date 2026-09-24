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
            "platform": "service", "keys": ("keba.set_current",), "options": ()}

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
    "platform": "service", "keys": ("keba.set_current",), "options": ()}}}


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

    def test_nothing_when_the_registry_could_not_be_asked(self):
        """Not a no — an unknown. Nothing is proposed and nothing crashes."""
        with patch.object(hd, "_roster", return_value=_fake_roster(KEBA_VOCAB)):
            out = hd.propose_roles_from_roster([], "keba", services_of=None)
        assert out == {}

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
                                           "action": "per_charger"}}
        out = hd.charger_from_near_miss(self._ents(), "keba", proposed)
        assert out["ev_charger_service"] == "keba.set_current"
        assert "ev_current_control_entity" not in out
        assert out["ev_charging_power_sensor"] == "sensor.keba_p30_power"
        assert out["ev_connected_sensor"] == "binary_sensor.keba_p30_plug"

    def test_no_power_sensor_still_means_please_report(self):
        proposed = {"ev_current_control": {"service": "keba.set_current"}}
        assert hd.charger_from_near_miss(self._ents()[1:], "keba", proposed) == {}
