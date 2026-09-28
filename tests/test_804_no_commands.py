"""#804 — "Wattpilot Flex: SEM sends no commands in the beta" (@HorizonKane).

Three faults, one silence. The reporter's go-e box publishes
``button.carport_wattpilot_91114903_neustart`` — the device RESTART button
(his own screenshot) — and the Wattpilot brand row's ``("start", "resume")``
hint matched the letters inside "neu**start**". So:

* SEM adopted a reboot button as the charging start/stop control
  (class 67 — a substring is not a word);
* that button then answered the stop-capability probe for the whole
  charger, while ``stop_session`` routes a button charger's stop through
  ``_set_current(0)`` and ``_set_current`` skips a 0 A write the number
  entity cannot express (min 6 A on this brand) — each layer deferring to
  the other, which is class 25 re-opened through #804 B4a's new branch;
* and with phase switching on, the sequencer waited for a stop that never
  came, holding every charge command out of the charger for the rest of
  the session (class 108's wait-shaped twin).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.devices.base import (
    CurrentControlDevice,
)
from custom_components.solar_energy_management.hardware_detection import (
    _BRAND_HINTS,
    _EV_CHARGER_PLATFORMS,
    _discover_from_hints,
    _name_hit,
)

# Every language ships one, and every one of them contains "start".
_REBOOT_WORDS = ("neustart", "restart", "herstart", "reboot", "redemarrer")


def _entry(eid, dc=None, platform="wattpilot"):
    return SimpleNamespace(entity_id=eid, platform=platform, device_id="wp-1",
                           unique_id=eid.split(".", 1)[1],
                           original_device_class=dc, disabled_by=None)


def _number_state(min_a):
    return SimpleNamespace(attributes={"min": min_a, "max": 32.0},
                           state="16.0")


def _device(min_a=6.0, **kwargs):
    hass = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.states.get = MagicMock(return_value=_number_state(min_a))
    attrs = {k: kwargs.pop(k) for k in
             ("start_stop_entity", "charge_mode_entity", "charge_mode_stop",
              "stop_service") if k in kwargs}
    dev = CurrentControlDevice(
        hass=hass, device_id="wattpilot", name="Wattpilot",
        min_current=6.0, max_current=32.0, phases=3,
        current_entity_id="number.carport_wattpilot_91114903_ladestrom",
        **kwargs)
    for key, value in attrs.items():
        setattr(dev, key, value)
    return dev


@pytest.mark.unit
class TestASubstringIsNotAWord:
    """Class 67, in the EV brand rows this time."""

    def test_the_reporters_reboot_button_is_not_a_charging_control(self):
        found = _discover_from_hints([
            _entry("sensor.carport_wattpilot_91114903_leistung", "power"),
            _entry("binary_sensor.carport_wattpilot_91114903_fahrzeug", "plug"),
            _entry("number.carport_wattpilot_91114903_ladestrom", "current"),
            _entry("button.carport_wattpilot_91114903_neustart"),
        ], _BRAND_HINTS["wattpilot"])
        assert "ev_start_stop_entity" not in found, (
            "SEM adopted the box's RESTART button as its start/stop control "
            "— every enable rebooted the charger (#804)")
        assert found["ev_current_control_entity"] == \
            "number.carport_wattpilot_91114903_ladestrom"

    def test_a_real_start_button_still_binds(self):
        for eid in ("button.wattpilot_start_charging",
                    "button.wattpilot_resume_charging",
                    "button.wattpilot_laden_starten"):
            found = _discover_from_hints(
                [_entry(eid)], _BRAND_HINTS["wattpilot"])
            assert found.get("ev_start_stop_entity") == eid, eid

    def test_name_hit_reads_segments(self):
        assert _name_hit("button.wattpilot_start_charging", "start")
        assert _name_hit("button.wattpilot_starten", "start")
        assert not _name_hit("button.wattpilot_neustart", "start")
        assert not _name_hit("button.wattpilot_herstart", "start")
        # The rows hint PREFIXES ("charg" for charging/charger, "amp" for
        # amperage), so a hit anchors the start of a segment, not both ends.
        # a hint that carries its own boundary is matched as written
        assert _name_hit("sensor.abl_emh1_state", "_state")
        # and the words the other rows rely on keep matching
        assert _name_hit("number.chargepoint_amperage_limit", "amperage")
        assert _name_hit("sensor.nrgkick_total_charged_energy", "charged_energy")
        assert _name_hit("sensor.juicebox_1234_power", "juicebox")

    def test_ha_s_own_restart_class_is_enough(self):
        """"Neu starten" and "Starta om" begin a word with "start" — no word
        list can hold them all. HA labels every one of these ``restart``."""
        from custom_components.solar_energy_management.hardware_detection import (
            apply_charger_discovery_guards,
        )
        for eid in ("button.wattpilot_neu_starten", "button.garo_starta_om"):
            ents = [_entry(eid, "restart")]
            found = _discover_from_hints(ents, _BRAND_HINTS["wattpilot"])
            assert "ev_start_stop_entity" not in found, eid
            result = {"ev_start_stop_entity": eid}
            apply_charger_discovery_guards(result, ents)
            assert result == {}, eid

    def test_no_brand_adopts_a_reboot_entity(self):
        """The class guard: feed every brand a device whose only button is
        a reboot, and no role may point at it."""
        for token, discover in _EV_CHARGER_PLATFORMS:
            entities = [
                _entry(f"sensor.{token}_power", "power", token),
                _entry(f"sensor.{token}_energy_total", "energy", token),
                _entry(f"binary_sensor.{token}_car_connected", "plug", token),
                _entry(f"binary_sensor.{token}_charging", "battery_charging", token),
                _entry(f"number.{token}_charging_current", "current", token),
            ] + [_entry(f"button.{token}_{word}", None, token)
                 for word in _REBOOT_WORDS] + [
                _entry(f"switch.{token}_{word}", None, token)
                for word in _REBOOT_WORDS]
            result = discover(entities)
            for role, eid in (result or {}).items():
                assert not any(w in str(eid) for w in _REBOOT_WORDS), (
                    f"{token}: {role} bound the reboot entity {eid}")


    def test_the_choke_point_drops_a_reboot_from_every_role(self):
        """Every registry path funnels through the guards — so the rule
        holds for the hand-written brands and the prober, not only for the
        row whose hint was wrong."""
        from custom_components.solar_energy_management.hardware_detection import (
            apply_charger_discovery_guards,
        )
        result = {
            "ev_start_stop_entity": "button.wattpilot_neustart",
            "ev_charge_mode_entity": "select.wattpilot_restart_mode",
            "ev_phase_switch_entity": "number.wattpilot_reboot_phases",
            "ev_charging_power_sensor": "sensor.wattpilot_power",
        }
        apply_charger_discovery_guards(result, [])
        assert result == {"ev_charging_power_sensor": "sensor.wattpilot_power"}

    def test_a_saved_reboot_button_is_never_pressed(self):
        """The detection fix cannot reach a config that is already SAVED."""
        dev = _device(start_stop_entity="button.carport_wattpilot_91114903_neustart")
        assert dev.start_stop_entity is None
        assert dev.session_start_mechanism() != "start_stop_entity"

    def test_a_saved_restart_class_button_is_refused_too(self):
        dev = _device(start_stop_entity="button.wattpilot_neu_starten")
        dev.hass.states.get = MagicMock(return_value=SimpleNamespace(
            attributes={"device_class": "restart"}))
        dev.start_stop_entity = "button.wattpilot_neu_starten"
        assert dev.start_stop_entity is None

    def test_the_charge_mode_select_is_covered_too(self):
        dev = _device()
        dev.charge_mode_entity = "select.wattpilot_neustart_modus"
        assert dev.charge_mode_entity is None
        dev.charge_mode_entity = "select.wattpilot_frc_force_state"
        assert dev.charge_mode_entity == "select.wattpilot_frc_force_state"

    def test_a_real_start_entity_round_trips(self):
        dev = _device(start_stop_entity="switch.wallbox_charging")
        assert dev.start_stop_entity == "switch.wallbox_charging"


@pytest.mark.unit
class TestAButtonOnlyCloses:
    """Class 25 — the capability must mirror what ``stop_session`` does."""

    def test_a_button_and_a_6a_number_cannot_stop(self):
        dev = _device(start_stop_entity="button.carport_wattpilot_neustart")
        assert dev.can_stop_charging() is False, (
            "a button press cannot open a contactor, and the 0 A write is "
            "skipped below the entity's own minimum — nothing stops this box")

    def test_the_press_is_still_a_relay_close(self):
        dev = _device(start_stop_entity="button.wattpilot_start_charging")
        assert dev.contactor_surface is True, (
            "#940's anti-cycle floor counts the press — it reads both sides")

    def test_a_button_on_a_charger_that_takes_0a_still_stops(self):
        # The Zaptec shape: the resume button plus a number that accepts 0.
        dev = _device(min_a=0.0, start_stop_entity="button.zaptec_resume")
        assert dev.can_stop_charging() is True

    def test_a_start_stop_switch_is_unchanged(self):
        dev = _device(start_stop_entity="switch.wallbox_charging")
        assert dev.can_stop_charging() is True

    def test_the_stop_claims_nothing_it_did_not_do(self):
        dev = _device(start_stop_entity="button.carport_wattpilot_neustart")
        dev.send = AsyncMock(return_value=True)
        dev._remember_parked = AsyncMock()
        dev.arm_failsafe_off = AsyncMock()
        dev._session_active = True
        asyncio.run(dev.stop_session())
        dev.send.assert_not_awaited()
        dev._remember_parked.assert_not_awaited()

    def test_a_keba_style_stop_is_not_claimed_by_the_button(self):
        """The press is not the stop, so the stop must ask only whether the
        0 A write can land — not whether some other surface exists."""
        dev = _device(start_stop_entity="button.wattpilot_start_charging")
        dev.charger_service = "keba.set_current"
        dev.hass.services.has_service = MagicMock(
            side_effect=lambda d, sv: d == "keba" and sv == "disable")
        assert dev.can_stop_charging() is True, "keba.disable can open it"
        dev.send = AsyncMock(return_value=True)
        dev._remember_parked = AsyncMock()
        dev.arm_failsafe_off = AsyncMock()
        dev._session_active = True
        asyncio.run(dev.stop_session())
        dev._remember_parked.assert_not_awaited(), (
            "the button branch wrote nothing — no park debt for it")

    def test_it_is_pressed_to_start_but_never_to_stop(self):
        dev = _device(min_a=0.0, start_stop_entity="button.zaptec_resume")
        dev.send = AsyncMock(return_value=True)
        dev._remember_parked = AsyncMock()
        dev.arm_failsafe_off = AsyncMock()
        dev._session_active = True
        asyncio.run(dev.stop_session())
        for call in dev.send.await_args_list:
            assert call.args[1] != "press", "a stop must never press a button"


@pytest.mark.unit
class TestTheHoldGivesUp:
    """Class 108, wait-shaped: a hold with no exit is a charger SEM never
    commands again."""

    def _seq(self):
        from custom_components.solar_energy_management.coordinator.ev_phase_sequencer import (
            PhaseSwitchSequencer,
        )
        return PhaseSwitchSequencer()

    def _tick(self, seq, now, charging):
        return seq.tick(now=now, desired_phases=1, believed_phases=3,
                        charging=charging, capability_ready=True)

    def test_a_box_that_never_stops_is_given_up_on(self):
        from custom_components.solar_energy_management.coordinator.ev_phase_sequencer import (
            STOP_WAIT_S,
        )
        seq = self._seq()
        assert self._tick(seq, 0.0, True).state == "stopping"
        r = self._tick(seq, STOP_WAIT_S - 1, True)
        assert r.hold_charging is True and r.gave_up is False
        r = self._tick(seq, STOP_WAIT_S, True)
        assert r.gave_up is True
        assert r.state == "idle" and r.hold_charging is False
        assert r.issue_switch is None, "never switch under load, not even now"

    def test_a_box_that_stops_in_time_is_unchanged(self):
        from custom_components.solar_energy_management.coordinator.ev_phase_sequencer import (
            STOP_WAIT_S,
        )
        seq = self._seq()
        self._tick(seq, 0.0, True)
        r = self._tick(seq, STOP_WAIT_S - 1, False)
        assert r.issue_switch == 1 and r.gave_up is False

    def test_the_charger_is_commanded_again_after_the_give_up(self):
        """End to end through the real control path: the hold releases and
        the charge decision passes through untouched."""
        from custom_components.solar_energy_management.coordinator import (
            ev_control,
        )
        from custom_components.solar_energy_management.coordinator.charger_types import (
            ChargerDecision, ChargerIntent, ChargerPower,
        )
        from custom_components.solar_energy_management.coordinator.ev_phase_sequencer import (
            STOP_WAIT_S,
        )
        h = SimpleNamespace()
        h._phase_switch_tick = ev_control.EVControlMixin._phase_switch_tick.__get__(h)
        h._observer_mode = False
        h._surplus_controller = None
        h.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda eid: MagicMock()),
            services=SimpleNamespace(async_call=AsyncMock()),
        )
        cfg = {"ev_phase_switch_entity": "number.wattpilot_phases",
               "ev_phase_switching_enabled": True, "phase_mode": "1",
               "ev_voltage": 230, "ev_min_current": 6}

        def run(t):
            d = ChargerDecision(
                charger_id="c1", mode="solar_only",
                intent=ChargerIntent.CHARGE_AT_AMPS, commanded_amps=10,
                budget_w=6900.0, reason="test")
            cp = ChargerPower(charger_id="c1", power_w=6900.0,
                              connected=True, charging=True)
            return asyncio.run(h._phase_switch_tick("c1", cfg, d, cp, t))

        assert run(0.0).intent is ChargerIntent.DISABLE
        assert run(60.0).intent is ChargerIntent.DISABLE
        assert run(STOP_WAIT_S).intent is ChargerIntent.CHARGE_AT_AMPS, (
            "the box never stopped and SEM held the charge command out of it "
            "for the whole session (#804)")
        # …and it does not start asking again two minutes later.
        assert run(STOP_WAIT_S + 300.0).intent is ChargerIntent.CHARGE_AT_AMPS
        assert h._phase_switch_states["c1"] == "not_taking"
        h.hass.services.async_call.assert_not_awaited()

    def test_observer_mode_does_not_blame_the_charger(self):
        """In observer mode SEM sends no stop, so a box that keeps drawing
        is SEM's own doing — it must not be marked not-taking."""
        from custom_components.solar_energy_management.coordinator import (
            ev_control,
        )
        from custom_components.solar_energy_management.coordinator.charger_types import (
            ChargerDecision, ChargerIntent, ChargerPower,
        )
        from custom_components.solar_energy_management.coordinator.ev_phase_sequencer import (
            STOP_WAIT_S,
        )
        h = SimpleNamespace()
        h._phase_switch_tick = ev_control.EVControlMixin._phase_switch_tick.__get__(h)
        h._observer_mode = True
        h._surplus_controller = None
        h.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda eid: MagicMock()),
            services=SimpleNamespace(async_call=AsyncMock()),
        )
        cfg = {"ev_phase_switch_entity": "number.wattpilot_phases",
               "ev_phase_switching_enabled": True, "phase_mode": "1",
               "ev_voltage": 230, "ev_min_current": 6}
        for t in (0.0, STOP_WAIT_S, STOP_WAIT_S + 10.0):
            asyncio.run(h._phase_switch_tick(
                "c1", cfg,
                ChargerDecision(charger_id="c1", mode="solar_only",
                                intent=ChargerIntent.CHARGE_AT_AMPS,
                                commanded_amps=10, budget_w=6900.0,
                                reason="test"),
                ChargerPower(charger_id="c1", power_w=6900.0,
                             connected=True, charging=True), t))
        assert h._phase_switch_states["c1"] != "not_taking"
