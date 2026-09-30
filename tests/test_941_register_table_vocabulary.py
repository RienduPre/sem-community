"""#941 — the roster reads register tables, so the ``victron`` integration
(sfstar/hass-victron, 1987 installs) finally has words.

That integration declares every entity in ``const.py`` as
``"settings_ess_acpowersetpoint": RegisterInfo(...)`` — no strings.json, no
per-platform ``key=`` — so the crawler read nothing and SEM knew nothing
(``kind_from: keyword``). A table key has no platform of its own; the lexicon
rule that claims it supplies the platform, and the shipped roster never says
``any``.

The setpoint role is new with this: #809's reporter wired
``ess_ac_grid_setpoint`` by hand, through a template that flips the sign.
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crawler = _load("crawl_integration_roster", "scripts/crawl_integration_roster.py")
lexicon = _load("role_lexicon", "consts/role_lexicon.py")

# the shape of hass-victron's const.py, verbatim lines (24.09.2026)
TABLE = '''
    "settings_ess_acpowersetpoint": RegisterInfo(2700, INT16, UnitOfPower.WATT),
    "settings_ess_maxchargepercentage": RegisterInfo(2701, UINT16, PERCENTAGE),
    "settings_ess_maxdischargepercentage": RegisterInfo(2702, UINT16, PERCENTAGE),
    "settings_ess_acpowersetpoint2": RegisterInfo(2703, INT32, UnitOfPower.WATT),
    "settings_ess_maxdischargepower": RegisterInfo(2704, UINT16, UnitOfPower.WATT, 0.1),
    "settings_ess_maxchargecurrent": RegisterInfo(2705, INT16, UnitOfElectricCurrent.AMPERE),
    "vebus_ess_L1_acpowersetpoint": RegisterInfo(37, INT16, UnitOfPower.WATT),
    "vebus_microgrid_directdrive_reactive_power_setpoint": RegisterInfo(59, INT16),
    "battery_power": RegisterInfo(258, INT16, UnitOfPower.WATT),
    "battery_soc": RegisterInfo(266, UINT16, PERCENTAGE, 10),
    "not_a_register": SomethingElse(1),
'''


class TestTheTableIsRead:
    def test_keys_come_out_under_the_any_platform(self):
        vocab = crawler.mine_register_table(TABLE)
        assert set(vocab) == {"any"}
        assert "settings_ess_acpowersetpoint" in vocab["any"]
        assert "battery_soc" in vocab["any"]
        assert "not_a_register" not in vocab["any"]

    def test_no_table_means_nothing_not_an_empty_platform(self):
        assert crawler.mine_register_table("DOMAIN = 'x'\n") == {}


class TestTheRuleSuppliesThePlatform:
    def _roles(self):
        return crawler.roles_from_vocabulary(
            crawler.mine_register_table(TABLE), lexicon, kind="energy")

    def test_the_ess_setpoint_is_a_number_and_only_the_system_one(self):
        r = self._roles()["battery_power_setpoint"]
        assert r["platform"] == "number"
        assert r["keys"] == ("settings_ess_acpowersetpoint",)

    def test_the_discharge_limit_is_the_watt_register_not_the_percent(self):
        r = self._roles()["battery_discharge_limit"]
        assert r["platform"] == "number"
        assert r["keys"] == ("settings_ess_maxdischargepower",)

    def test_the_reads_are_sensors(self):
        roles = self._roles()
        assert roles["battery_soc"]["platform"] == "sensor"
        assert roles["battery_soc"]["keys"] == ("battery_soc",)
        assert roles["battery_power"]["platform"] == "sensor"

    def test_the_shipped_shape_never_says_any(self):
        assert all(r["platform"] != "any" for r in self._roles().values())


class TestTheSetpointRoleReachesTheConfig:
    def test_it_maps_to_the_generic_adapters_setpoint_key(self):
        assert (lexicon.SEM_CONFIG_KEY_FOR_ROLE["battery_power_setpoint"]
                == "battery_force_discharge_control_entity")


@pytest.mark.unit
class TestTheCommittedRosterKnowsVictron:
    """Runs against the COMMITTED module, like the #915 oracle."""

    def test_victron_is_proposed_at_runtime_from_a_unique_id_suffix(self):
        from custom_components.solar_energy_management.hardware_detection import (
            propose_roles_from_roster,
        )
        # hass-victron: entity_id victron_<key>_<slave>, unique_id <slave>_<key>
        ents = [
            SimpleNamespace(entity_id="number.victron_settings_ess_acpowersetpoint_100",
                            unique_id="100_settings_ess_acpowersetpoint",
                            translation_key=None, original_device_class="power"),
            SimpleNamespace(entity_id="sensor.victron_settings_ess_acpowersetpoint_100",
                            unique_id="100_settings_ess_acpowersetpoint_sensor",
                            translation_key=None, original_device_class="power"),
            SimpleNamespace(entity_id="sensor.victron_battery_soc_225",
                            unique_id="225_battery_soc",
                            translation_key=None, original_device_class="battery"),
        ]
        out = propose_roles_from_roster(ents, "victron")
        assert out["battery_power_setpoint"]["entity"] \
            == "number.victron_settings_ess_acpowersetpoint_100"
        assert out["battery_power_setpoint"]["config_key"] \
            == "battery_force_discharge_control_entity"
        assert out["battery_soc"]["entity"] == "sensor.victron_battery_soc_225"
