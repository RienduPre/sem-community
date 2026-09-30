"""#804 — SEM could not stop a Wattpilot, and its start handed the box back
to its own logic.

@HorizonKane runs ruaan-deysel/ha-wattpilot. That integration has no stop
switch: it writes the box's force state ``frc`` through three buttons —
``-frc0`` neutral ("Laden starten"), ``-frc1`` off ("Laden stoppen"),
``-frc2`` on ("Laden erzwingen") — plus a restart button. SEM's only stop for
a button-started charger was a 0 A write, below the 6 A minimum of the
current number, so it was skipped: Off, Solar only and every phase switch did
nothing. Its start was the neutral button, so the box's own Eco / PV-surplus
regulation kept the current. Entity ids are his own (German install); the
unique ids follow the fork's ``<charger>-<uid>`` rule.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.solar_energy_management import hardware_detection as hd
from custom_components.solar_energy_management.devices.base import CurrentControlDevice

CID = "91114903"
NUM = "number.carport_wattpilot_91114903_ladestrom"
START0 = "button.carport_wattpilot_91114903_laden_starten"
STOP = "button.carport_wattpilot_91114903_laden_stoppen"
FORCE = "button.carport_wattpilot_91114903_laden_erzwingen"
RESTART = "button.carport_wattpilot_91114903_neustart"


def _e(eid, uid, platform="wattpilot", device_id="dev-wp", dc=None):
    return SimpleNamespace(entity_id=eid, unique_id=f"{CID}-{uid}", platform=platform,
                           device_id=device_id, original_device_class=dc,
                           disabled_by=None, translation_key=None)


def _his_box(with_force=True):
    ents = [
        _e(NUM, "amp", dc="current"),
        _e(START0, "frc0"),
        _e(STOP, "frc1"),
        _e(RESTART, "rst", dc="restart"),
        _e("sensor.carport_wattpilot_91114903_leistung", "nrg", dc="power"),
        _e("binary_sensor.carport_wattpilot_91114903_fahrzeug", "car", dc="plug"),
    ]
    if with_force:
        ents.append(_e(FORCE, "frc2"))
    return ents


class _Reg:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}

    def async_get(self, eid):
        return self.entities.get(eid)


def _patched(entries):
    reg = _Reg(entries)
    return (patch("homeassistant.helpers.entity_registry.async_get", return_value=reg),
            patch("homeassistant.helpers.entity_registry.async_entries_for_device",
                  side_effect=lambda r, dev_id: [e for e in entries if e.device_id == dev_id]))


def _hass():
    hass = MagicMock()
    st = SimpleNamespace(state="10", attributes={"min": 6.0, "max": 16.0, "step": 1})
    hass.states.get = MagicMock(return_value=st)
    hass.services.async_call = AsyncMock(return_value=None)
    hass.services.has_service = MagicMock(return_value=False)
    return hass


def _device(hass, start=None):
    d = CurrentControlDevice(
        hass=hass, device_id="ev_charger", name="Wattpilot", priority=3,
        min_current=6.0, max_current=16.0, phases=3, voltage=230.0,
        power_entity_id="sensor.carport_wattpilot_91114903_leistung",
        charger_service=None, charger_service_entity_id=None, current_entity_id=NUM)
    if start:
        d.start_stop_entity = start
    return d


def _calls(hass):
    return [(c.args[0], c.args[1], dict(c.args[2]) if len(c.args) > 2 else {})
            for c in hass.services.async_call.await_args_list]


class TestTheBoxOwnButtonsAreFound:
    def test_stop_is_frc1_and_start_is_frc2_by_unique_id(self):
        a, b = _patched(_his_box())
        with a, b:
            ctl = hd.wattpilot_force_buttons(MagicMock(), NUM)
        assert ctl["stop"] == STOP
        assert ctl["start"] == FORCE

    def test_without_frc2_the_neutral_start_is_the_fallback(self):
        a, b = _patched(_his_box(with_force=False))
        with a, b:
            ctl = hd.wattpilot_force_buttons(MagicMock(), NUM)
        assert ctl["start"] == START0

    def test_another_brand_finds_nothing(self):
        ents = [_e(NUM, "amp", platform="goecharger", dc="current"), _e(STOP, "frc1", platform="goecharger")]
        a, b = _patched(ents)
        with a, b:
            assert hd.wattpilot_force_buttons(MagicMock(), NUM) == {}

    def test_a_registry_that_fails_finds_nothing(self):
        with patch("homeassistant.helpers.entity_registry.async_get", side_effect=RuntimeError):
            assert hd.wattpilot_force_buttons(MagicMock(), NUM) == {}


class TestHisSavedConfigGetsAStop:
    """His install was set up before this fix: the saved start was the restart
    button (now refused) and there was no stop. Setup must still give SEM a
    stop without him touching the config."""

    def _wired(self, start=None, entries=None):
        hass = _hass()
        d = _device(hass, start=start)
        a, b = _patched(entries or _his_box())
        with a, b:
            hd.wire_current_entity(hass, d, "ev_charger", NUM)
        return d, hass

    def test_the_refused_restart_is_replaced_by_the_forced_start(self):
        d, _ = self._wired(start=RESTART)
        assert d.start_stop_entity == FORCE
        assert d.stop_service == "button.press"
        assert d.stop_service_data == {"entity_id": STOP}

    def test_the_neutral_start_detection_chose_is_promoted_to_forced(self):
        d, _ = self._wired(start=START0)
        assert d.start_stop_entity == FORCE

    def test_a_start_the_user_chose_elsewhere_is_kept(self):
        d, _ = self._wired(start="switch.garage_wallbox_enable")
        assert d.start_stop_entity == "switch.garage_wallbox_enable"
        assert d.stop_service == "button.press"

    async def test_stop_presses_laden_stoppen_and_no_zero_write(self):
        d, hass = self._wired(start=RESTART)
        d._session_active = True
        await d.stop_session()
        calls = _calls(hass)
        assert ("button", "press", {"entity_id": STOP}) in calls, calls
        assert not any(c[1] == "press" and c[2].get("entity_id") == RESTART for c in calls)

    def test_sem_can_now_say_it_can_stop(self):
        d, _ = self._wired(start=RESTART)
        can_open, can_close = d._discrete_contactor_surfaces()
        assert can_open and can_close

    def test_no_stop_button_is_said_out_loud(self, caplog):
        ents = [e for e in _his_box() if not e.unique_id.endswith("frc1")]
        with caplog.at_level(logging.WARNING):
            d, _ = self._wired(start=RESTART, entries=ents)
        assert d.stop_service in (None, "")
        assert "no stop button" in caplog.text


class TestAFreshInstallIsDetectedWithStopAndStart:
    def test_discovery_carries_the_stop_service_and_the_forced_start(self):
        r = hd._discover_wattpilot(_his_box())
        assert r["ev_stop_service"] == "button.press"
        assert json.loads(r["ev_stop_service_data"]) == {"entity_id": STOP}
        assert r["ev_start_stop_entity"] == FORCE
        assert r["ev_current_control_entity"] == NUM
        assert RESTART not in r.values()
