"""#940 (second pass) — asserting the enable switch is not starting a session.

alexmc1510, on 2.1.0-beta.21 with the anti-cycle floor already shipped::

    SEM's last 3+ current commands to EV Charger were rejected: enable
    switch will not stay on — cannot start charging.

His charger is a current number, a charge-mode select (``charge_mode_start
= 'manual'``, no stop option) and ``switch.cargador_coche_carga_de_ve``.
``start_session`` is an elif CHAIN, so on that config the SELECT is the
session start and the switch is never part of it; the relay is closed by
``ChargerAdapter.ensure_enabled``.

And ``ensure_enabled`` ended with ``dev._session_active = True`` (#536) —
a latch scoped wider than its evidence (bug class 83). That flag is what
``GenericAdapter.command_current`` reads to decide whether to call
``start_session`` at all, so:

    reconcile() → [ENABLE, START_AND_WRITE]
      ensure_enabled()   → switch.turn_on          … and _session_active=True
      command_current()  → sees a session → SKIPS start_session()
                         → number.set_value        … select never written

The ENABLE is prepended on exactly the cycle where the enable switch is
off — which is every transition out of a stop, because SEM's own stop is
what turned it off. So the brand's start was suppressed on the ONE cycle
that needed it, on every charge, forever. The box stayed on its own mode,
dropped the switch again, SEM re-asserted it five times, spent the #536
budget and filed ``charger_actuation_failed`` against healthy hardware —
while the relay went on/off once per cycle, which is #940's contactor
cycling arriving from underneath the floor (the box opens it, so SEM's
own-operation clocks never arm).

The oracle below is the closure: over the cross-product of every session
start mechanism × every enable surface, a transition out of a stop must
dispatch the charger's OWN session start exactly once.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.charger_adapters import (
    GenericAdapter,
)
from custom_components.solar_energy_management.coordinator.charger_reconciler import (
    ActionKind,
    ChargerReconciler,
    observe,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerDecision,
    ChargerIntent,
    ChargerPower,
)
from custom_components.solar_energy_management.devices import base as devbase
from custom_components.solar_energy_management.devices.base import (
    CurrentControlDevice,
)

from .ast_contracts import assigns_attribute, calls, reads_attribute


def _selects_a_start_branch(fn) -> bool:
    """A method that READS both start-mechanism fields is picking a branch.
    ``__init__`` only ASSIGNS them, which is not a selection."""
    return all(reads_attribute(fn, "self", a) and not assigns_attribute(fn, "self", a)
               for a in ("start_service", "charge_mode_start"))

CYCLE_S = 10.0
CURRENT = "number.ev_current"

#: (name, device kwargs, the call that IS this charger's session start,
#: the call that IS its current write).
#: The enable switch is present and readable-OFF in every shape that has
#: one — that is the state SEM's own stop leaves behind.
SHAPES = [
    (
        # alexmc1510's own: select start (no stop option) + enable switch.
        "charge_mode+switch",
        {"charge_mode_entity": "select.cargador_coche_modo",
         "charge_mode_start": "manual",
         "start_stop_entity": "switch.cargador_coche_carga_de_ve"},
        ("select", "select_option"), ("number", "set_value"),
    ),
    (
        # Easee: a brand start service beside a readable enable switch.
        "start_service+switch",
        {"start_service": "easee.action_command",
         "start_service_data": {"action_command": "resume"},
         "start_stop_entity": "switch.easee_enable"},
        ("easee", "action_command"), ("number", "set_value"),
    ),
    (
        # Wallbox / Heidelberg: the switch IS the session start.
        "switch only",
        {"start_stop_entity": "switch.wallbox_pause_resume"},
        ("switch", "turn_on"), ("number", "set_value"),
    ),
    (
        # go-e / OpenWB / Ohme: a select and nothing else to assert.
        "charge_mode only",
        {"charge_mode_entity": "select.go_e_frc", "charge_mode_start": "2"},
        ("select", "select_option"), ("number", "set_value"),
    ),
    (
        # A KEBA whose owner also named an enable switch (the config
        # ``test_ev_charger_post_install_surface`` models): the switch
        # SHADOWS ``keba.enable`` in the chain, and is therefore the start.
        "charger_service+switch",
        {"charger_service": "keba.set_current",
         "start_stop_entity": "switch.keba_enable"},
        ("switch", "turn_on"), ("keba", "set_current"),
    ),
]


def _device(**kw):
    """A real ``CurrentControlDevice`` recording every service call."""
    sent: list[tuple] = []
    hass = MagicMock()

    async def _call(domain, service, data=None, blocking=True):
        sent.append((domain, service, dict(data or {})))

    hass.services.async_call = _call
    hass.services.has_service = MagicMock(return_value=False)
    switch = kw.get("start_stop_entity") or ""
    hass.states.get = MagicMock(side_effect=lambda eid: (
        SimpleNamespace(state="off", attributes={})
        if eid == switch and switch.startswith(("switch.", "input_boolean."))
        else None))
    dev = CurrentControlDevice(
        hass=hass, device_id="ev", name="EV Charger", priority=5,
        min_current=6.0, max_current=32.0, phases=3, voltage=230.0,
        power_entity_id="sensor.p", current_entity_id=CURRENT,
        charger_service=kw.pop("charger_service", None))
    for k, v in kw.items():
        setattr(dev, k, v)
    return dev, sent


def _charge_transition(dev, *, cycles: int = 1) -> None:
    """Drive the REAL reconciler out of a stop and into CHARGE.

    Nothing is hand-assembled: the ENABLE that triggers the bug is the one
    the decision table itself prepends."""
    adapter = GenericAdapter(dev)
    rec = ChargerReconciler(charger_id="ev", heartbeat_s=300.0)
    decision = ChargerDecision(
        charger_id="ev", mode="solar", intent=ChargerIntent.CHARGE_AT_AMPS,
        commanded_amps=10, reason="surplus")
    power = ChargerPower(charger_id="ev", power_w=0.0, connected=True)
    for cycle in range(cycles):
        asyncio.run(rec.reconcile_and_apply(
            decision, adapter, power, now=cycle * CYCLE_S))


@pytest.mark.unit
@pytest.mark.parametrize("name,kw,expected,write", SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_the_transition_dispatches_the_chargers_own_session_start(
        name, kw, expected, write):
    """THE oracle. Whatever mechanism this charger starts on, the first
    charge cycle out of a stop must send it — exactly once."""
    dev, sent = _device(**kw)
    _charge_transition(dev)
    starts = [c for c in sent if (c[0], c[1]) == expected]
    assert len(starts) == 1, (
        f"{name}: session start {expected} was sent {len(starts)}× on the "
        f"transition cycle — sent: {sent}")
    assert any((c[0], c[1]) == write for c in sent), (
        f"{name}: the current write {write} never happened — {sent}")


@pytest.mark.unit
@pytest.mark.parametrize("name,kw,expected,write", SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_the_old_rule_fails_this_oracle_where_the_start_is_elsewhere(
        name, kw, expected, write):
    """The vacuity twin. Restore the #536 rule — ``ensure_enabled`` claims
    the session unconditionally — and the oracle must FAIL on exactly the
    shapes whose start is NOT the enable entity, and still pass on the
    others. An oracle that cannot fire is decoration."""
    dev, sent = _device(**kw)
    dev.enable_entity_is_session_start = lambda: True   # the old behaviour
    _charge_transition(dev)
    starts = [c for c in sent if (c[0], c[1]) == expected]
    start_is_the_switch = (dev.session_start_mechanism()
                           == devbase.SESSION_START_STOP_ENTITY)
    has_switch = bool(dev.start_stop_entity)
    if has_switch and not start_is_the_switch:
        assert not starts, (
            f"{name}: the old rule should have swallowed {expected} — "
            f"this oracle cannot catch the bug it was written for")
    else:
        assert len(starts) == 1, f"{name}: {sent}"


@pytest.mark.unit
def test_a_button_start_is_not_pressed_twice_by_the_follow_up_write():
    """#804/#536's reason for the latch: a ``button.`` start is NOT
    idempotent, so where the button IS the session start, asserting it must
    keep suppressing the ``start_session`` that follows.

    Driven at the seam, not through ``reconcile``: a button has no readable
    state, so ``enable_state()`` answers ``(None, True)`` and the decision
    table never emits ENABLE for one. This composes the two calls the way
    ``_apply_actions`` would if it ever did — which is exactly the case the
    guard must not regress."""
    dev, sent = _device(start_stop_entity="button.zaptec_resume_charging")
    adapter = GenericAdapter(dev)
    asyncio.run(adapter.ensure_enabled())
    assert dev._session_active is True, (
        "the button IS this charger's session start — claiming it is the "
        "whole point of the latch")
    asyncio.run(adapter.command_current(10))
    presses = [c for c in sent if (c[0], c[1]) == ("button", "press")]
    assert len(presses) == 1, f"the button was pressed {len(presses)}×: {sent}"


@pytest.mark.unit
def test_a_button_start_still_reaches_the_box_on_the_transition():
    """…and the liveness twin: the press must happen at all. (Today it is
    ``start_session`` that presses it, because ENABLE is never emitted for a
    stateless surface — if that ever changes, the test above is what stops
    the press doubling.)"""
    dev, sent = _device(start_stop_entity="button.zaptec_resume_charging")
    _charge_transition(dev)
    presses = [c for c in sent if (c[0], c[1]) == ("button", "press")]
    assert len(presses) == 1, f"the button was pressed {len(presses)}×: {sent}"
    assert dev._session_active is True


@pytest.mark.unit
def test_ensure_enabled_alone_does_not_claim_a_session_it_did_not_start():
    """The unit under the oracle: the reporter's config, the adapter call
    on its own."""
    dev, sent = _device(
        charge_mode_entity="select.cargador_coche_modo",
        charge_mode_start="manual",
        start_stop_entity="switch.cargador_coche_carga_de_ve")
    asyncio.run(GenericAdapter(dev).ensure_enabled())
    assert sent == [("switch", "turn_on",
                     {"entity_id": "switch.cargador_coche_carga_de_ve"})]
    assert dev._session_active is False, (
        "the enable switch was asserted, not the session — the select is "
        "this charger's start and nobody has written it yet")


@pytest.mark.unit
def test_ensure_enabled_still_claims_the_session_when_it_IS_the_start():
    dev, _ = _device(start_stop_entity="switch.wallbox_pause_resume")
    asyncio.run(GenericAdapter(dev).ensure_enabled())
    assert dev._session_active is True


@pytest.mark.unit
def test_a_device_that_cannot_answer_keeps_the_old_claim():
    """Fail-safe direction: a stub charger that has no opinion must not
    acquire a second start it never asked for."""
    dev = MagicMock()
    dev.start_stop_entity = "switch.x"
    dev._session_active = False
    dev.enable_entity_is_session_start = None      # not callable
    dev.send = AsyncMock()
    dev.hass.states.get = MagicMock(
        return_value=SimpleNamespace(state="off", attributes={}))
    asyncio.run(GenericAdapter(dev).ensure_enabled())
    assert dev._session_active is True


@pytest.mark.unit
class TestTheMechanismIsNamedOnceAndReadEverywhere:
    """Class 46: the elif chain is the source of truth for WHICH start
    fires, and it had three hand-written copies (``start_session``,
    ``release_to_user``, and ``ensure_enabled``'s implicit assumption)."""

    @pytest.mark.parametrize("kw,expected", [
        ({"start_service": "easee.action_command"},
         devbase.SESSION_START_SERVICE),
        ({"charge_mode_entity": "select.m", "charge_mode_start": "manual"},
         devbase.SESSION_START_CHARGE_MODE),
        # a select with no START option is not a start mechanism
        ({"charge_mode_entity": "select.m", "start_stop_entity": "switch.s"},
         devbase.SESSION_START_STOP_ENTITY),
        ({"start_stop_entity": "switch.s"},
         devbase.SESSION_START_STOP_ENTITY),
        ({"charger_service": "keba.set_current"},
         devbase.SESSION_START_CHARGER_SERVICE),
        ({}, devbase.SESSION_START_NONE),
    ])
    def test_the_resolver_names_the_branch_start_session_takes(
            self, kw, expected):
        dev, _ = _device(**kw)
        assert dev.session_start_mechanism() == expected

    def test_precedence_matches_the_chain_start_session_used_to_inline(self):
        """Every mechanism at once: the service wins, as it always did."""
        dev, sent = _device(
            start_service="easee.action_command",
            start_service_data={"action_command": "resume"},
            charge_mode_entity="select.m", charge_mode_start="manual",
            start_stop_entity="switch.s", charger_service="keba.set_current")
        assert dev.session_start_mechanism() == devbase.SESSION_START_SERVICE
        asyncio.run(dev.start_session())
        assert [(c[0], c[1]) for c in sent] == [("easee", "action_command")]

    #: The one method allowed to read the raw fields: the resolver's own
    #: sibling, which answers a different question (#627/#940 capability,
    #: both directions at once) and deliberately reads every field rather
    #: than picking one branch.
    RAW_READERS_ALLOWED = {"_discrete_contactor_surfaces",
                           "session_start_mechanism"}

    def test_every_start_dispatcher_reads_the_one_resolver(self):
        """#935's hand-back re-sends the session start, and was a second
        copy of the chain; a brand added to one would have been missed by
        the other.

        DERIVED, not listed (class 76): any method that reads both
        ``self.start_service`` and ``self.charge_mode_start`` is selecting a
        start branch, whatever it is called and whenever it is added — and
        it must ASK the resolver rather than re-derive it."""
        import inspect
        offenders = []
        for name, fn in inspect.getmembers(
                CurrentControlDevice, predicate=inspect.isfunction):
            if name in self.RAW_READERS_ALLOWED:
                continue
            if not _selects_a_start_branch(fn):
                continue
            if not calls(fn, "session_start_mechanism"):
                offenders.append(name)
        assert not offenders, (
            f"{offenders} re-derive the session-start chain by hand — "
            "session_start_mechanism() is the one place it is decided")

    def test_that_derivation_can_actually_fire(self):
        """The vacuity twin of the lint: it must SEE the dispatchers it is
        meant to police, or it is an assertion about an empty set."""
        import inspect
        seen = {n for n, fn in inspect.getmembers(
                    CurrentControlDevice, predicate=inspect.isfunction)
                if _selects_a_start_branch(fn)}
        assert {"start_session", "release_to_user"} <= seen, seen
