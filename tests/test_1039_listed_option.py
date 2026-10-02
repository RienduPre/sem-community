"""#1039 — a select takes the option it LISTS, never the label a person sees.

Ohme's charge-mode select lists ``max_charge``, ``paused`` and
``smart_charge``. Home Assistant shows them as "Max charge", "Paused" and
"Smart charge". SEM's Ohme detection saved the LABELS as the start and stop
options, and HA's ``select.select_option`` refuses any option the entity
does not list — so the charger never changed mode. Found by loading Home
Assistant's own Ohme test data (HA 2026.8.2).

The same mistake sat in GoodWe's forced charge: it wrote "Eco Charge" and
"General", where core's select lists ``eco_charge`` and ``general``.

The fake hass below does what HA does: a select refuses an option it does
not list, and takes one it does.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.solar_energy_management.utils.select_option import (
    listed_option,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

OHME_SELECT = "select.ohme_home_pro_charge_mode"
OHME_OPTIONS = ["smart_charge", "max_charge", "paused"]
GOODWE_SELECT = "select.goodwe_inverter_operation_mode"
GOODWE_OPTIONS = ["general", "off_grid", "backup", "eco", "peak_shaving",
                  "eco_charge", "eco_discharge"]


class _Refused(Exception):
    """HA's ServiceValidationError for an option the select does not list."""


class _Hass:
    """Selects that behave like HA's: ``select_option`` with an option the
    entity does not list is refused, and changes nothing."""

    def __init__(self, selects: dict, **states):
        self.selects = {e: [s, list(o)] for e, (s, o) in selects.items()}
        self.other = dict(states)
        self.sent: list = []
        self.config = SimpleNamespace(language="en")
        self.states = SimpleNamespace(get=self._get)
        self.services = SimpleNamespace(
            async_call=self._call, has_service=lambda *a: False)

    def _get(self, eid):
        if eid in self.selects:
            state, options = self.selects[eid]
            return SimpleNamespace(state=state, attributes={"options": options})
        return self.other.get(eid)

    async def _call(self, domain, service, data=None, blocking=False, **kw):
        data = dict(data or {})
        self.sent.append((domain, service, data))
        if service == "select_option":
            state = self.selects[data["entity_id"]]
            if data["option"] not in state[1]:
                raise _Refused(f"{data['option']!r} is not one of {state[1]}")
            state[0] = data["option"]

    def options_sent(self, entity_id):
        return [d["option"] for _, s, d in self.sent
                if s == "select_option" and d.get("entity_id") == entity_id]

    def reads(self, entity_id):
        return self.selects[entity_id][0]


# ── the helper ────────────────────────────────────────────────────────

class TestListedOption:
    def _hass(self, options):
        return _Hass({"select.x": ("a", options)})

    def test_a_listed_option_is_written_as_it_is(self):
        assert listed_option(self._hass(OHME_OPTIONS), "select.x",
                             "max_charge") == "max_charge"

    def test_the_ohme_labels_become_the_options_ohme_lists(self):
        hass = self._hass(OHME_OPTIONS)
        assert listed_option(hass, "select.x", "Max charge") == "max_charge"
        assert listed_option(hass, "select.x", "Paused") == "paused"

    def test_the_goodwe_values_sem_wrote_become_the_options_core_lists(self):
        hass = self._hass(GOODWE_OPTIONS)
        assert listed_option(hass, "select.x", "Eco Charge") == "eco_charge"
        assert listed_option(hass, "select.x", "General") == "general"

    def test_a_label_in_the_other_direction_is_found_too(self):
        """A select that lists display strings (OpenWB-style) takes them
        from a lower-case, underscored value."""
        hass = self._hass(["Instant Charging", "PV Charging", "Stop"])
        assert listed_option(hass, "select.x", "instant_charging") == "Instant Charging"

    def test_two_candidates_are_never_guessed_between(self):
        hass = self._hass(["Max charge", "max_charge"])
        assert listed_option(hass, "select.x", "MAX CHARGE") == "MAX CHARGE"

    def test_no_match_returns_the_value_for_ha_to_refuse(self):
        hass = self._hass(OHME_OPTIONS)
        assert listed_option(hass, "select.x", "Turbo") == "Turbo"

    @pytest.mark.parametrize("hass", [
        None,
        MagicMock(),
        _Hass({}),
        _Hass({"select.x": ("a", [])}),
        SimpleNamespace(states=SimpleNamespace(
            get=lambda e: SimpleNamespace(attributes={"options": "abc"}))),
    ])
    def test_nothing_readable_leaves_the_value_alone(self, hass):
        assert listed_option(hass, "select.x", "Max charge") == "Max charge"

    def test_non_text_and_empty_values_pass_through(self):
        hass = self._hass(OHME_OPTIONS)
        assert listed_option(hass, "select.x", None) is None
        assert listed_option(hass, "select.x", "") == ""
        assert listed_option(hass, None, "Max charge") == "Max charge"


@pytest.mark.asyncio
class TestLabelsComeFromHomeAssistant:
    """The label path, through a real HA: a real registry entry and core's
    own GoodWe translation files. Neither "Allgemeiner Modus" nor "General
    mode" is the same words as ``general`` — only the translation knows."""

    async def _goodwe(self, hass, language):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import translation

        hass.config.language = language
        for lang in {language, "en"}:
            await translation.async_get_translations(
                hass, lang, "entity", {"goodwe"})
        entry = er.async_get(hass).async_get_or_create(
            "select", "goodwe", "sn_operation_mode",
            suggested_object_id="goodwe_inverter_operation_mode",
            translation_key="operation_mode")
        hass.states.async_set(entry.entity_id, "general",
                              {"options": GOODWE_OPTIONS})
        return entry.entity_id

    async def test_a_label_in_the_users_language(self, hass):
        eid = await self._goodwe(hass, "de")
        assert listed_option(hass, eid, "Eco-Lademodus") == "eco_charge"
        assert listed_option(hass, eid, "Allgemeiner Modus") == "general"

    async def test_an_english_label_on_a_german_install(self, hass):
        eid = await self._goodwe(hass, "de")
        assert listed_option(hass, eid, "General mode") == "general"
        assert listed_option(hass, eid, "Eco charge mode") == "eco_charge"

    async def test_a_listed_option_is_still_written_as_it_is(self, hass):
        eid = await self._goodwe(hass, "en")
        assert listed_option(hass, eid, "eco") == "eco"


# ── Ohme: the reported charger ────────────────────────────────────────

def _ohme(hass, start="Max charge", stop="Paused"):
    """An Ohme charger as an older SEM SAVED it — the labels."""
    from custom_components.solar_energy_management.devices.base import (
        CurrentControlDevice,
    )
    dev = CurrentControlDevice(
        hass=hass, device_id="ev", name="ohme", priority=1, min_current=6,
        max_current=32, phases=1, voltage=230,
        power_entity_id="sensor.ohme_home_pro_power")
    dev.charge_mode_entity = OHME_SELECT
    dev.charge_mode_start = start
    dev.charge_mode_stop = stop
    return dev


@pytest.mark.asyncio
class TestOhmeChangesMode:
    async def test_start_writes_max_charge_and_the_charger_takes_it(self):
        hass = _Hass({OHME_SELECT: ("smart_charge", OHME_OPTIONS)})
        await _ohme(hass).start_session()
        assert hass.options_sent(OHME_SELECT) == ["max_charge"]
        assert hass.reads(OHME_SELECT) == "max_charge"

    async def test_stop_writes_paused_and_the_charger_takes_it(self):
        hass = _Hass({OHME_SELECT: ("max_charge", OHME_OPTIONS)})
        await _ohme(hass).stop_session()
        assert hass.options_sent(OHME_SELECT) == ["paused"]
        assert hass.reads(OHME_SELECT) == "paused"

    async def test_the_hand_back_writes_the_option_too(self):
        hass = _Hass({OHME_SELECT: ("paused", OHME_OPTIONS)})
        dev = _ohme(hass)
        dev._sem_parked = True          # only a box SEM parked is handed back
        dev._write_park_record = _noop
        assert await dev.release_to_user(reason="removal")
        assert hass.reads(OHME_SELECT) == "max_charge"

    async def test_the_options_detection_now_saves_need_no_mapping(self):
        hass = _Hass({OHME_SELECT: ("smart_charge", OHME_OPTIONS)})
        dev = _ohme(hass, start="max_charge", stop="paused")
        await dev.start_session()
        await dev.stop_session()
        assert hass.options_sent(OHME_SELECT) == ["max_charge", "paused"]

    async def test_observer_records_the_option_it_would_send(self):
        hass = _Hass({OHME_SELECT: ("smart_charge", OHME_OPTIONS)})
        dev = _ohme(hass)
        dev.observer_mode = True
        await dev.start_session()
        assert hass.sent == []
        assert [c["data"]["option"] for c in dev.withheld_commands
                if c["service"] == "select.select_option"] == ["max_charge"]

    async def test_through_the_reconciler_charge_then_stop(self):
        from custom_components.solar_energy_management.coordinator.charger_adapters import (
            adapter_for,
        )
        from custom_components.solar_energy_management.coordinator.charger_reconciler import (
            ChargerReconciler,
        )
        from custom_components.solar_energy_management.coordinator.charger_types import (
            ChargerDecision, ChargerIntent, ChargerPower,
        )
        hass = _Hass({OHME_SELECT: ("paused", OHME_OPTIONS)})
        dev = _ohme(hass)
        adapter = adapter_for(dev)
        await ChargerReconciler(charger_id="ev", heartbeat_s=5.0).reconcile_and_apply(
            ChargerDecision(charger_id="ev", mode="solar_only",
                            intent=ChargerIntent.CHARGE_AT_AMPS,
                            commanded_amps=10, reason="t", budget_w=0.0),
            adapter, ChargerPower(charger_id="ev", power_w=0.0), now=1.0)
        assert hass.reads(OHME_SELECT) == "max_charge"
        dev._session_active = True
        await ChargerReconciler(charger_id="ev", heartbeat_s=5.0).reconcile_and_apply(
            ChargerDecision(charger_id="ev", mode="off",
                            intent=ChargerIntent.DISABLE,
                            commanded_amps=0, reason="t", budget_w=0.0),
            adapter, ChargerPower(charger_id="ev", power_w=4000.0), now=1.0)
        assert hass.reads(OHME_SELECT) == "paused"


async def _noop(*_a, **_k):
    return None


class TestOhmeDetection:
    def test_detection_saves_options_ohme_lists(self):
        """Pinned against the roster mined from Ohme's own source, not
        against a string typed here."""
        from custom_components.solar_energy_management.consts.integration_roster import (
            ROLE_VOCAB,
        )
        from custom_components.solar_energy_management.hardware_detection import (
            _discover_ohme,
        )
        entity = SimpleNamespace(
            entity_id=OHME_SELECT, original_device_class=None,
            original_name="Charge mode", name=None, translation_key="charge_mode",
            platform="ohme", device_id="d1")
        result = _discover_ohme([entity])
        listed = ROLE_VOCAB["ohme"]["ev_charge_mode"]["options"]
        assert result["ev_charge_mode_entity"] == OHME_SELECT
        assert result["ev_charge_mode_start"] in listed
        assert result["ev_charge_mode_stop"] in listed
        assert (result["ev_charge_mode_start"], result["ev_charge_mode_stop"]) == (
            "max_charge", "paused")


# ── siblings: every other select SEM writes ───────────────────────────

@pytest.mark.asyncio
class TestGoodWeForcedCharge:
    def _adapter(self, hass, **cfg):
        from custom_components.solar_energy_management.coordinator.battery_adapters.force_charge import (
            GoodWeChargeAdapter,
        )
        return GoodWeChargeAdapter(hass, {
            "battery_charge_platform": "goodwe",
            "inverter_work_mode_entity": GOODWE_SELECT, **cfg})

    async def test_forced_charge_selects_eco_charge(self):
        from custom_components.solar_energy_management.coordinator.battery_adapters.force_charge import (
            ChargeCommand, ChargeCommandStatus,
        )
        hass = _Hass({GOODWE_SELECT: ("general", GOODWE_OPTIONS)})
        status = await self._adapter(hass).start_forced_charge(
            ChargeCommand(target_soc=80.0, max_power_w=3000))
        assert status.status is ChargeCommandStatus.CHARGING
        assert hass.reads(GOODWE_SELECT) == "eco_charge"

    async def test_the_stop_restores_general_also_from_a_saved_label(self):
        from custom_components.solar_energy_management.coordinator.battery_adapters.force_charge import (
            ChargeCommandStatus,
        )
        for saved in (None, "General"):
            hass = _Hass({GOODWE_SELECT: ("eco_charge", GOODWE_OPTIONS)})
            cfg = {} if saved is None else {"inverter_normal_work_mode": saved}
            status = await self._adapter(hass, **cfg).stop_forced_charge()
            assert status.status is ChargeCommandStatus.IDLE, saved
            assert hass.reads(GOODWE_SELECT) == "general", saved


DIR = "select.batt_direction"
SP = "number.batt_setpoint"


@pytest.mark.asyncio
class TestBatteryDirectionSelect:
    def _adapter(self, hass):
        from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
            GenericBatteryAdapter,
        )
        return GenericBatteryAdapter(hass, {
            "battery_force_discharge_control_entity": SP,
            "battery_max_discharge_power": 5000,
            "battery_setpoint_model": "direction_select",
            "battery_power_direction_entity": DIR,
            "battery_direction_discharge_value": "entladen",
            "battery_direction_charge_value": "laden"})

    async def test_the_direction_is_written_as_listed_and_then_read_as_landed(self):
        hass = _Hass({DIR: ("Laden", ["Laden", "Entladen"])})
        a = self._adapter(hass)
        assert await a._direction_ready(1000.0) is False
        assert hass.reads(DIR) == "Entladen"
        # the next cycle READS what it wrote — before, it waited forever
        assert await a._direction_ready(1000.0) is True


SEL = "select.sessy_power_strategy"


@pytest.mark.asyncio
class TestBatteryStrategySelect:
    def _adapter(self, hass):
        from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
            GenericBatteryAdapter,
        )
        return GenericBatteryAdapter(hass, {
            "battery_force_discharge_control_entity": SP,
            "battery_strategy_control_entity": SEL,
            "battery_strategy_active_value": "API",
            "battery_strategy_idle_value": "Eco"})

    async def test_a_label_typed_as_the_active_value_lands_and_is_read(self):
        hass = _Hass({SEL: ("nom", ["api", "nom", "eco", "idle", "roi"])})
        a = self._adapter(hass)
        assert await a._enter_active_strategy() is True
        assert hass.reads(SEL) == "api"
        # the user's own mode is what gets handed back — not "api"
        assert a._restore_strategy == "nom"
        await a._release_strategy()
        assert hass.reads(SEL) == "nom"

    async def test_a_stranded_api_is_still_adopted_after_a_reload(self):
        hass = _Hass({SEL: ("api", ["api", "nom", "eco", "idle"])})
        assert self._adapter(hass)._took_control is True


@pytest.mark.asyncio
class TestSgReadySelectContact:
    def _controller(self, hass):
        from custom_components.solar_energy_management.devices.heat_pump_controller import (
            HeatPumpController,
        )
        return HeatPumpController(
            hass=hass, relay1_entity_id="select.sg1", relay2_entity_id="select.sg2",
            relay1_on_value="On", relay1_off_value="Off",
            relay2_on_value="On", relay2_off_value="Off")

    async def test_boost_is_written_and_read_back_as_listed(self):
        from custom_components.solar_energy_management.devices.heat_pump_controller import (
            SGReadyState,
        )
        hass = _Hass({"select.sg1": ("on", ["on", "off"]),
                      "select.sg2": ("on", ["on", "off"])})
        c = self._controller(hass)
        await c._set_sg_ready_state(SGReadyState.BOOST)
        assert (hass.reads("select.sg1"), hass.reads("select.sg2")) == ("off", "on")
        assert c._read_sg_ready_state() == (True, SGReadyState.BOOST)


class TestPhaseSwitchSelect:
    def test_the_phase_value_is_written_as_listed(self):
        from custom_components.solar_energy_management.coordinator.ev_phases import (
            phase_switch_command,
        )
        hass = _Hass({"select.psm": ("1 Phase", ["1 Phase", "3 Phasen"])})
        assert phase_switch_command("select.psm", "3 phasen", hass) == (
            "select", "select_option",
            {"entity_id": "select.psm", "option": "3 Phasen"})


# ── the guard: no select write leaves without the mapping ─────────────

#: (file, function) → why its option needs no mapping. A function that
#: writes a select and is not here must call ``listed_option`` — or be the
#: charger path, whose one seam (``send``) maps every select write.
ALLOWED = {
    ("consts/devices.py", "<module>"):
        "the service table only; heat_pump_controller._contact_service "
        "maps the value it writes",
    ("coordinator/battery_adapters/deye.py", "_write_and_verify"):
        "Deye checks every configured option against the select's own list "
        "and refuses one it does not list (_validate_*) before any write",
    ("coordinator/battery_adapters/deye.py", "export_release_recipe"):
        "the prior is read off the select, so it is an option it lists",
    ("coordinator/battery_adapters/deye.py", "export_dry_run"):
        "a checked option or the prior read off the select — nothing is sent",
}

_SKIP = {"tests", "scripts", "tools", "dashboard", "__pycache__", "node_modules"}


def _source_files():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if _SKIP.intersection(rel.parts):
            continue
        yield rel.as_posix(), path


def _select_writes():
    """(file, innermost function, line) of every ``select_option``."""
    out = []
    for rel, path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner: dict = {}
        for scope in ast.walk(tree):        # BFS: inner scopes come later
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(scope):
                    owner[node] = scope
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and node.value in ("select_option", "select.select_option")):
                out.append((rel, owner.get(node), node.lineno))
    return out


def _mentions(scope, name):
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(scope))


def _sends_through_the_seam(scope):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "send" and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "self" for n in ast.walk(scope))


def test_every_select_write_maps_its_option():
    writes = _select_writes()
    assert len(writes) >= 10, "the scan found too few select writes to mean anything"
    bad = []
    for rel, scope, line in writes:
        name = scope.name if scope is not None else "<module>"
        if (rel, name) in ALLOWED:
            continue
        if scope is not None and (_mentions(scope, "listed_option")
                                  or _sends_through_the_seam(scope)):
            continue
        bad.append(f"{rel}:{line} in {name}")
    assert not bad, (
        "these write a select option without listed_option() — a label "
        "would be refused by HA (#1039):\n  " + "\n  ".join(bad))


def test_the_charger_seam_maps_select_writes():
    tree = ast.parse((ROOT / "devices" / "base.py").read_text(encoding="utf-8"))
    sends = [n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "send"]
    assert len(sends) == 1 and _mentions(sends[0], "listed_option")


def test_every_allowance_still_names_a_select_write():
    seen = {(rel, s.name if s is not None else "<module>")
            for rel, s, _ in _select_writes()}
    stale = set(ALLOWED) - seen
    assert not stale, f"allowances that excuse nothing any more: {stale}"
