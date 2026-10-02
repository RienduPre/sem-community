"""#1038 — the setup wizard must judge a status sensor with the reader's words.

Found by loading Home Assistant's own Ohme test data (HA 2026.8.2): Ohme's
status sensor reports ``plugged_in``, ``finished`` and ``pending_approval``.
The reader knows all three (``status_enum.py``), but the wizard's check
(``EVChargerDetector._validate_entity``) kept its own word list. That list
had ``"plugged in"`` — the label HA shows — never the state HA stores, so
``validate_ev_configuration`` refused the sensor and the user could not
finish setup or reconfigure while the car sat plugged in.

The same copy had Peblar as ``"no ev connected"``; core stores
``no_ev_connected``, and ``suspended`` for a car plugged in but paused —
a word neither list knew, so the reader also read that car as gone.

Bug class 46 shape (c), the third copy of one vocabulary (#833 removed the
reader's two). The option lists below are core's own, cited per brand.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.solar_energy_management import hardware_detection as hd
from custom_components.solar_energy_management.coordinator.charger_adapters import (
    status_enum as se,
)
from custom_components.solar_energy_management.coordinator.sensor_reader import (
    SensorReader,
)

# Core's ENUM options, as HA stores them (state = option key, never label).
# Ohme: ohme ChargerStatus, rig snapshot HA 2026.8.2 (sensor.ohme_home_pro_status).
OHME = ["unplugged", "pending_approval", "charging", "plugged_in", "paused",
        "finished"]
# Peblar: peblar/const.py PEBLAR_CP_STATE_TO_HOME_ASSISTANT (cp_state).
PEBLAR = ["suspended", "charging", "error", "fault", "invalid",
          "no_ev_connected"]
# NRGkick: rig snapshot HA 2026.8.2 (sensor.nrgkick_test_status) — SEM's
# own nrgkick row binds this sensor as ev_charging_sensor.
NRGKICK = ["standby", "connected", "charging", "error", "wakeup"]
# Blue Current: blue_current/sensor.py — SEM's pattern binds vehicle_status
# as ev_connected and activity as ev_charging.
BLUE_CURRENT_VEHICLE = ["standby", "vehicle_detected", "ready", "no_power",
                        "vehicle_error"]
BLUE_CURRENT_ACTIVITY = ["available", "charging", "unavailable", "error",
                         "offline"]
# Tesla Wall Connector: tesla_wall_connector/sensor.py (status).
TESLA_WC = ["booting", "not_connected", "connected", "ready", "negotiating",
            "error", "charging_finished", "waiting_car", "charging_reduced",
            "charging"]

STATUS = "sensor.charger_status"
POWER = "sensor.charger_power"


def _detector(state, options=None):
    attrs = {"options": list(options)} if options is not None else {}
    st = SimpleNamespace(state=state, attributes=attrs)
    pw = SimpleNamespace(state="0", attributes={"unit_of_measurement": "W"})
    hass = MagicMock()
    hass.states.get = lambda eid: {STATUS: st, POWER: pw}.get(eid)
    with patch.object(hd, "entity_registry"):
        return hd.EVChargerDetector(hass)


def _wizard_errors(state, options=None):
    """What the setup and reconfigure forms say on submit."""
    return _detector(state, options).validate_ev_configuration({
        "ev_connected_sensor": STATUS,
        "ev_charging_sensor": STATUS,
        "ev_charging_power_sensor": POWER,
    })


def _accepts(state, options=None, role="ev_connected"):
    return _detector(state, options)._validate_entity(STATUS, role)


def _reader(state):
    hass = MagicMock()
    hass.states.get = lambda eid: (SimpleNamespace(state=state, attributes={})
                                   if eid == STATUS else None)
    r = SensorReader(hass, {"ev_connected_sensor": STATUS})
    r._sign_vote_warmup = 0
    return r


def _plugged(state):
    return _reader(state)._read_binary_sensor(STATUS, "ev_plug")


def _charging(state):
    return _reader(state)._read_binary_sensor(STATUS, "ev_charging")


class TestTheReportedCase:
    """Ohme's status sensor, in every state core lists."""

    @pytest.mark.parametrize("state", OHME)
    def test_the_form_takes_ohme_in_every_state(self, state):
        assert _wizard_errors(state, OHME) == {}, (
            f"Ohme at {state!r}: the wizard refuses a sensor the reader knows")

    @pytest.mark.parametrize("state", OHME)
    def test_every_ohme_state_is_a_word_the_reader_knows(self, state):
        # without the options attribute: the word list alone must know it
        assert _wizard_errors(state) == {}
        assert se.classify_charger_status(state) != "unknown"

    def test_plugged_in_reads_as_a_car_that_is_not_charging(self):
        assert _plugged("plugged_in") is True
        assert _charging("plugged_in") is False


class TestPeblarStoresTheKeyNotTheLabel:
    """The wizard's copy spelled Peblar ``no ev connected``; core stores
    ``no_ev_connected``. ``suspended`` (a car plugged in, paused) was in
    neither list, so the reader read that car as gone."""

    @pytest.mark.parametrize("state", PEBLAR)
    def test_the_form_takes_peblar_in_every_state(self, state):
        assert _wizard_errors(state, PEBLAR) == {}

    @pytest.mark.parametrize("state", ["suspended", "charging",
                                       "no_ev_connected"])
    def test_the_states_with_a_meaning_are_words_the_reader_knows(self, state):
        assert _wizard_errors(state) == {}

    def test_suspended_is_a_car_that_is_plugged_in(self):
        assert _plugged("suspended") is True
        assert _charging("suspended") is False

    def test_no_ev_connected_is_an_empty_bay(self):
        assert _plugged("no_ev_connected") is False
        assert _charging("no_ev_connected") is False

    def test_charging_is_plugged_and_charging(self):
        assert _plugged("charging") is True
        assert _charging("charging") is True

    @pytest.mark.parametrize("state", ["error", "fault", "invalid"])
    def test_a_fault_says_nothing_about_the_cable(self, state):
        # unknown on purpose: the reader falls back, as before
        assert se.classify_charger_status(state) == "unknown"
        assert se.is_cable_present(state) is None


class TestASensorIsJudgedByTheOptionsItLists:
    """A sensor caught at setup in a state with no control meaning (a
    fault, a boot) is still the right sensor when it lists words SEM reads."""

    @pytest.mark.parametrize("options", [
        NRGKICK, BLUE_CURRENT_VEHICLE, BLUE_CURRENT_ACTIVITY, TESLA_WC,
    ], ids=["nrgkick", "blue_current_vehicle", "blue_current_activity",
            "tesla_wall_connector"])
    def test_every_listed_state_passes(self, options):
        refused = [s for s in options
                   if s != "unavailable" and _wizard_errors(s, options)]
        assert refused == [], f"the wizard refuses {refused}"

    def test_the_options_carry_it_not_the_state(self):
        # the proof that the options are read: the same state on a sensor
        # that lists nothing is refused
        assert _accepts("wakeup", NRGKICK) is True
        assert _accepts("wakeup") is False

    @pytest.mark.parametrize("options", [
        # Peblar charge_current_limit_source, NRGkick cellular_mode: ENUM
        # sensors of a charger that are not its status
        ["charging_cable", "current_limiter", "solar_charging"],
        ["no_service", "gsm", "lte_cat_m1", "lte_nb_iot"],
    ])
    def test_a_list_with_no_status_word_is_still_refused(self, options):
        assert _accepts(options[0], options) is False

    def test_unavailable_is_refused_whatever_it_lists(self):
        assert _accepts("unavailable", BLUE_CURRENT_ACTIVITY) is False


class TestTheWizardAndTheReaderAgree:
    """Every word the reader knows passes the wizard, for both roles; a word
    the reader cannot read does not."""

    WORDS = sorted(se._CHARGING | se._NOT_CHARGING | se._LOCKED)

    @pytest.mark.parametrize("role", ["ev_connected", "ev_charging"])
    def test_every_known_word_passes(self, role):
        refused = [w for w in self.WORDS if not _accepts(w, role=role)]
        assert refused == [], f"the wizard refuses words the reader knows: {refused}"

    @pytest.mark.parametrize("state", ["on", "off", "ON", "Paused", "0", "1",
                                       "7.4"])
    def test_binary_and_numbers_pass(self, state):
        # the reader reads a number as on when > 0
        assert _accepts(state) is True

    @pytest.mark.parametrize("state", ["maybe", "idle", "true", "nan",
                                       "a", "b1", "firmware 1.2.3"])
    def test_a_word_the_reader_cannot_read_is_refused(self, state):
        assert se.classify_charger_status(state) == "unknown"
        assert _accepts(state) is False

    def test_the_power_role_keeps_its_own_check(self):
        det = _detector("plugged_in")
        assert det._validate_entity(POWER, "ev_charging_power") is True


class TestTheCopyIsGone:
    """No status word may be written in the wizard's check again — the
    words belong to status_enum.py, which the reader uses too."""

    def test_no_status_word_in_validate_entity(self):
        src = textwrap.dedent(inspect.getsource(
            hd.EVChargerDetector._validate_entity))
        found = sorted({
            n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and se.classify_charger_status(n.value) != "unknown"
        })
        assert found == [], (
            f"_validate_entity names status words {found} — ask "
            "status_enum.knows_status instead")

    def test_the_lint_can_fail(self):
        bad = 'def f(s):\n    return s in ("plugged in", "charging")\n'
        found = {n.value for n in ast.walk(ast.parse(bad))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and se.classify_charger_status(n.value) != "unknown"}
        assert found == {"plugged in", "charging"}
