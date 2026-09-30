"""#991 — a charger's current entity missing at STARTUP is not missing.

alexmc1510 (2.1.0-beta.31, Victron EVCS driven by a ``number.*`` current
entity and no charge service) got, once, at boot::

    WARNING [features.load_management] EV charger current control entity
    not found: number.cargador_coche_consigna_de_corriente_de_carga

The entity is real and worked for the rest of the day. The Victron
integration simply had not finished loading when SEM registered the
charger — ``hass.states.get()`` returns None for every entity in that
state, which means "not published yet", not "does not exist".

``register_ev_charger`` spent that absence on the spot AND permanently:
it nulled ``current_control_entity`` and fell back to ``charger_service``,
which this install does not have. The load-management row therefore
carried no control handle for the whole session — entity ids are
structural, so nothing short of a reload put it back.

Bug class 86 ("absence of evidence spent as evidence"), asked of the other
consumer: #945 asked it of an evidence COUNTER turning silence into a
verdict; here the same empty read is spent on CONFIGURATION. Two checks ask
this charger "are your entities there?", and #763 had already moved the
first — the ``_warn_missing_charger_entities`` roll-up — 120 s past warm-up.
The second ran a few lines later in the same setup loop, still at setup, and
was the only one that also DISCARDED something.

What the drop did and did not cost, stated here so no pin below overclaims:
nothing actuates off this row. ``_peak_managed_elsewhere`` excludes every
``ev_charger`` row from the load manager's shedding (#461-peak), and the
live current write reads the CONFIG (``CurrentControlDevice._set_current``),
not this row. What was lost was a truthful Load Priority card and a truthful
log — the WARNING said "not found" about an entity that was working, which
is the #763 failure verbatim and the reason that one was filed.

What this file pins:

1. The reporter's boot: registration with the entity absent KEEPS it, read
   back through the card payload the user actually sees.
2. The vacuity twin: an entity that IS published registers identically, so
   pin 1 is not passing because nothing ever stores a control entity.
3. No WARNING at registration for a warm-up absence — the false alarm that
   sent the diagnosis down a dead end — with a positive control, because an
   absence-only assertion is satisfied by a registration that died silently.
4. Past warm-up, a fallback with nowhere to fall is an ERROR: the deferred
   #763 check says SEM cannot set this charger's current when the entity is
   still missing and nothing can stand in for it — including the case where
   a service IS configured but writes THROUGH the missing entity.
5. The structural guard, asked of every spelling a handle is written in —
   with the verbatim pre-fix body re-injected as the probe that proves the
   contract can still fail, and its two real limits pinned rather than left
   to be discovered.
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_energy_management import (
    _warn_missing_charger_entities,
)
from custom_components.solar_energy_management.load_management import (
    LoadManagementCoordinator,
)

# The reporter's entities, verbatim.
CURRENT_ENTITY = "number.cargador_coche_consigna_de_corriente_de_carga"
POWER_ENTITY = "sensor.cargador_coche_potencia_de_carga"


@pytest.fixture
def config_entry():
    entry = MagicMock()
    entry.options = {
        "load_management_enabled": True,
        "target_peak_limit": 5.0,
        "warning_peak_level": 4.5,
        "emergency_peak_level": 6.0,
        "peak_hysteresis": 0.3,
    }
    entry.entry_id = "test_entry"
    return entry


@pytest.fixture
def lm(mock_hass, config_entry):
    with patch(
        "custom_components.solar_energy_management.features.load_management"
        ".LoadDeviceDiscovery"
    ), patch(
        "custom_components.solar_energy_management.features.load_management.Store"
    ) as MockStore:
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value=None)
        mock_store.async_save = AsyncMock()
        MockStore.return_value = mock_store
        coordinator = LoadManagementCoordinator(mock_hass, config_entry)
        coordinator._store = mock_store
        yield coordinator


def _warming_up(hass):
    """HA mid-warm-up: the Victron integration has published nothing."""
    hass.states.get = MagicMock(return_value=None)


def _loaded(hass):
    """HA past warm-up: the same entities, published."""
    def _get(eid):
        if eid in (CURRENT_ENTITY, POWER_ENTITY):
            return MagicMock(state="16.0",
                             attributes={"friendly_name": "Cargador Coche"})
        return None
    hass.states.get = MagicMock(side_effect=_get)


@pytest.mark.unit
class TestRegistrationDoesNotSpendAWarmUpAbsence:

    @pytest.mark.asyncio
    async def test_absent_at_registration_keeps_the_configured_entity(self, lm):
        """The reporter's boot. No charger_service — the fallback the old
        code took has nowhere to fall, so dropping the entity left the row
        with no control method at all."""
        _warming_up(lm.hass)

        ok = await lm.register_ev_charger(
            current_control_entity=CURRENT_ENTITY,
            power_entity=POWER_ENTITY,
            charger_service=None,
            charger_id="ev_charger",
            charger_name="Cargador Coche",
            priority=7,
        )

        assert ok is True
        row = lm._devices["load_device_ev_charger"]
        assert row["switch_entity"] == CURRENT_ENTITY, (
            "a warm-up absence was spent: the configured control entity was "
            "discarded and only a reload would put it back (#991)"
        )
        assert row["charger_service"] is None
        # Read back through the payload the Load Priority card is built
        # from, which is the surface that actually showed the gap. No pin
        # here claims an INDEPENDENT consumer, because there is none: this
        # row drives no actuator (``_peak_managed_elsewhere`` excludes every
        # ev_charger row from the load manager's shedding, #461-peak), so
        # what the drop cost was a truthful card and a truthful log.
        card = lm.get_load_management_data()["devices"]["load_device_ev_charger"]
        assert card["switch_entity"] == CURRENT_ENTITY

    @pytest.mark.asyncio
    async def test_present_at_registration_is_identical(self, lm):
        """Vacuity twin: the healthy install stores the same entity, so pin 1
        cannot be passing because registration never stores one."""
        _loaded(lm.hass)

        await lm.register_ev_charger(
            current_control_entity=CURRENT_ENTITY,
            power_entity=POWER_ENTITY,
            charger_service=None,
            charger_id="ev_charger",
            charger_name="Cargador Coche",
        )

        row = lm._devices["load_device_ev_charger"]
        assert row["switch_entity"] == CURRENT_ENTITY
        card = lm.get_load_management_data()["devices"]["load_device_ev_charger"]
        assert card["switch_entity"] == CURRENT_ENTITY

    @pytest.mark.asyncio
    async def test_warm_up_absence_does_not_warn(self, lm, caplog):
        """The log line alexmc1510 reported. It is a DEBUG note now: at
        registration the only honest reading is "I could not ask yet"."""
        _warming_up(lm.hass)
        with caplog.at_level(logging.DEBUG):
            ok = await lm.register_ev_charger(
                current_control_entity=CURRENT_ENTITY,
                power_entity=POWER_ENTITY,
                charger_id="ev_charger",
                charger_name="Cargador Coche",
            )
        # The positive control, without which this pin passes on a
        # registration that died in its own ``except`` and logged nothing:
        # an absence-only assertion is satisfied by total silence.
        assert ok is True
        assert lm._devices["load_device_ev_charger"]["switch_entity"] == (
            CURRENT_ENTITY)
        assert "not published yet" in caplog.text

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []
        assert "current control entity not found" not in caplog.text
        assert "power entity not found" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_control_method_configured_is_still_refused(self, lm):
        """The absence that IS an answer: neither an entity nor a service is
        a CONFIG fact, readable at setup and unchanged by warm-up."""
        _warming_up(lm.hass)
        ok = await lm.register_ev_charger(
            current_control_entity=None,
            charger_service=None,
            charger_id="ev_charger",
        )
        assert ok is False
        assert "load_device_ev_charger" not in lm._devices

    @pytest.mark.asyncio
    async def test_friendly_name_survives_an_unreadable_entity(self, lm):
        """The name lookup reads the same absent entity — the caller's label
        must win rather than the registration blowing up."""
        _warming_up(lm.hass)
        await lm.register_ev_charger(
            current_control_entity=CURRENT_ENTITY,
            charger_id="ev_charger",
            charger_name="EV Charger",   # the placeholder that triggers lookup
        )
        assert lm._devices["load_device_ev_charger"]["friendly_name"] == (
            "EV Charger")


def _hass(existing):
    return SimpleNamespace(
        states=SimpleNamespace(
            get=lambda eid: object() if eid in existing else None,
        ),
    )


@pytest.mark.unit
class TestAFallbackWithNowhereToFallIsAnError:
    """#991 item 2, placed past warm-up — where the absence is a fact."""

    CHK = (
        ("ev_charging_power_sensor", POWER_ENTITY),
        ("ev_current_control_entity", CURRENT_ENTITY),
    )

    def test_missing_current_entity_and_no_service_is_an_error(self, caplog):
        with caplog.at_level(logging.DEBUG):
            missing = _warn_missing_charger_entities(
                _hass(set()), "Cargador Coche", "ev_charger", self.CHK,
                charger_service=None)
        assert ("ev_current_control_entity", CURRENT_ENTITY) in missing
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "no control method at all must not be a line in a list"
        assert CURRENT_ENTITY in errors[0].getMessage()

    def test_a_service_to_fall_back_to_is_not_an_error(self, caplog):
        with caplog.at_level(logging.DEBUG):
            _warn_missing_charger_entities(
                _hass(set()), "Cargador Coche", "ev_charger", self.CHK,
                charger_service="victron.set_current")
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        # …and the existing #763 warning still reports the missing id.
        assert "silently no-op" in caplog.text

    def test_entity_present_is_neither(self, caplog):
        with caplog.at_level(logging.DEBUG):
            missing = _warn_missing_charger_entities(
                _hass({CURRENT_ENTITY, POWER_ENTITY}),
                "Cargador Coche", "ev_charger", self.CHK,
                charger_service=None)
        assert missing == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "silently no-op" not in caplog.text

    def test_another_entity_missing_is_not_a_control_verdict(self, caplog):
        """Only the CURRENT entity is the control surface here. A missing
        power sensor is a measurement gap, not "SEM cannot command this"."""
        with caplog.at_level(logging.DEBUG):
            _warn_missing_charger_entities(
                _hass({CURRENT_ENTITY}), "Cargador Coche", "ev_charger",
                self.CHK, charger_service=None)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_a_service_that_writes_through_the_missing_entity_is_no_fallback(
            self, caplog):
        """Having a service is not the same question as having a FALLBACK.
        An entity-platform service (#462 / #485 K1) targets
        ``ev_current_control_entity or ev_charger_service_entity_id``,
        current entity first — so when the current entity is what vanished,
        it falls exactly where the entity did."""
        for svc in ("number.set_value", "input_number.set_value",
                    "select.select_option"):
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                _warn_missing_charger_entities(
                    _hass(set()), "Cargador Coche", "ev_charger", self.CHK,
                    charger_service=svc)
            errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert errors, f"{svc} is not an independent transport"
            assert svc in errors[0].getMessage()

    def test_a_brand_service_is_its_own_transport(self, caplog):
        """The other side of the same rule, so it is not just "always ERROR":
        a real brand service does not write through the missing entity."""
        for svc in ("keba.set_current", "easee.set_charger_dynamic_limit"):
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                _warn_missing_charger_entities(
                    _hass(set()), "Cargador Coche", "ev_charger", self.CHK,
                    charger_service=svc)
            assert not [r for r in caplog.records
                        if r.levelno >= logging.ERROR], svc

    def test_default_call_shape_is_unchanged(self, caplog):
        """#763's callers pass four arguments. The new parameter is optional
        and its default must not invent an ERROR for them."""
        with caplog.at_level(logging.DEBUG):
            _warn_missing_charger_entities(
                _hass(set()), "EV Charger", "ev_charger",
                [("ev_start_stop_entity", "switch.gone")])
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "switch.gone" in caplog.text


@pytest.mark.unit
class TestAbsenceIsNeverSpentOnConfigAnywhere:
    """The structural guard — the class-86 sweep asked of every file, so the
    next brand's registration cannot re-acquire the shape under a new name."""

    def _contract(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ast_contracts
        return ast_contracts

    def test_production_tree_is_clean(self):
        hits = self._contract().absence_spent_as_config()
        assert hits == [], (
            "a configured handle is discarded on a state read that may simply "
            f"be a warm-up: {hits} (#991, bug class 86)"
        )

    def test_the_pre_fix_body_is_caught(self, tmp_path):
        """The probe. Verbatim from ``register_ev_charger`` before #991."""
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def register(self, current_control_entity, charger_service):
                if current_control_entity and not self.hass.states.get(
                        current_control_entity):
                    LOGGER.warning("not found: %s", current_control_entity)
                    current_control_entity = None  # Fall back to service
                return current_control_entity
        """), encoding="utf-8")
        hits = self._contract().absence_spent_as_config(root=tmp_path)
        assert [h[2] for h in hits] == ["current_control_entity"]

    @pytest.mark.parametrize("name,src,nulled", [
        # The dict slot. THE spelling to get right: this tree keeps every
        # device as ``self._devices[device_id] = {...}``, so nulling the
        # slot is the most natural way to re-acquire the class in the very
        # file #991 fixes — and a contract written to the instance's
        # spelling would wave it straight through.
        ("dict slot", """
            def f(self, did):
                if not self.hass.states.get("x"):
                    self._devices[did]["switch_entity"] = None
        """, ["switch_entity"]),
        ("attribute", """
            def f(self, device, e):
                if not self.hass.states.get(e):
                    device.current_entity_id = None
        """, ["current_entity_id"]),
        ("tuple unpack", """
            def f(self, e, o):
                if self.hass.states.get(e) is None:
                    e, o = None, None
        """, ["e", "o"]),
        ("annotated + conditional expression", """
            def f(self, e):
                entity: str = None if self.hass.states.get(e) is None else e
                return entity
        """, ["entity"]),
        ("bare conditional expression", """
            def f(self, e):
                e = None if self.hass.states.get(e) is None else e
                return e
        """, ["e"]),
        ("nested in a try inside the branch", """
            def f(self, e):
                if not self.hass.states.get(e):
                    try:
                        e = None
                    except Exception:
                        pass
                return e
        """, ["e"]),
        ("elif", """
            def f(self, e, x):
                if x:
                    pass
                elif not self.hass.states.get(e):
                    e = None
                return e
        """, ["e"]),
    ])
    def test_every_spelling_of_the_same_mistake_is_caught(
            self, tmp_path, name, src, nulled):
        (tmp_path / "probe.py").write_text(
            textwrap.dedent(src), encoding="utf-8")
        hits = self._contract().absence_spent_as_config(root=tmp_path)
        assert sorted(h[2] for h in hits) == sorted(nulled), name

    def test_the_two_step_spelling_is_caught_by_pin_one_not_here(self, tmp_path):
        """A named limit, pinned so nobody reads a pass as proof: test and
        assignment must be syntactically connected, so the dataflow form is
        invisible to the contract. The behavioural pin above is what covers
        it — which is why that one drives the real registration."""
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def register(self, entity):
                st = self.hass.states.get(entity)
                if st is None:
                    entity = None
                return entity
        """), encoding="utf-8")
        assert self._contract().absence_spent_as_config(root=tmp_path) == []

    def test_a_helper_predicate_is_the_other_named_limit(self, tmp_path):
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def register(self, entity):
                if self._is_dead(entity):
                    entity = None
                return entity
        """), encoding="utf-8")
        assert self._contract().absence_spent_as_config(root=tmp_path) == []

    def test_a_present_reading_acted_on_is_not_the_class(self, tmp_path):
        """No false positive: clearing a value because a reading SAYS so is
        evidence, and the opposite shape."""
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def clear(self, entity):
                if self.hass.states.get(entity):
                    cached = None
                    return cached
                return entity
        """), encoding="utf-8")
        assert self._contract().absence_spent_as_config(root=tmp_path) == []

    def test_a_plain_dict_get_is_not_a_state_read(self, tmp_path):
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def f(self, cfg, key):
                if not cfg.get(key):
                    key = None
                return key
        """), encoding="utf-8")
        assert self._contract().absence_spent_as_config(root=tmp_path) == []

    def test_a_positive_reading_stored_as_none_is_not_the_class(self, tmp_path):
        """A reading SEM actually has, acted on, is evidence — the opposite
        shape. The contract must not fail the build on it."""
        (tmp_path / "probe.py").write_text(textwrap.dedent("""
            def f(self, e):
                if self.hass.states.get(e):
                    cached = None
                    return cached
                return e
        """), encoding="utf-8")
        assert self._contract().absence_spent_as_config(root=tmp_path) == []
