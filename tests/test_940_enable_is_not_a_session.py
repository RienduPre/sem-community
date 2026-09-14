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
from custom_components.solar_energy_management.devices.base import (
    CurrentControlDevice,
)

from .ast_contracts import calls

CYCLE_S = 10.0
CURRENT = "number.ev_current"

#: (name, device kwargs, the call that IS this charger's session start).
#: The enable switch is present and readable-OFF in every shape that has
#: one — that is the state SEM's own stop leaves behind.
SHAPES = [
    (
        # alexmc1510's own: select start (no stop option) + enable switch.
        "charge_mode+switch",
        {"charge_mode_entity": "select.cargador_coche_modo",
         "charge_mode_start": "manual",
         "start_stop_entity": "switch.cargador_coche_carga_de_ve"},
        ("select", "select_option"),
    ),
    (
        # Easee: a brand start service beside a readable enable switch.
        "start_service+switch",
        {"start_service": "easee.action_command",
         "start_service_data": {"action_command": "resume"},
         "start_stop_entity": "switch.easee_enable"},
        ("easee", "action_command"),
    ),
    (
        # Wallbox / Heidelberg: the switch IS the session start.
        "switch only",
        {"start_stop_entity": "switch.wallbox_pause_resume"},
        ("switch", "turn_on"),
    ),
    (
        # go-e / OpenWB / Ohme: a select and nothing else to assert.
        "charge_mode only",
        {"charge_mode_entity": "select.go_e_frc", "charge_mode_start": "2"},
        ("select", "select_option"),
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
@pytest.mark.parametrize("name,kw,expected", SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_the_transition_dispatches_the_chargers_own_session_start(
        name, kw, expected):
    """THE oracle. Whatever mechanism this charger starts on, the first
    charge cycle out of a stop must send it — exactly once."""
    dev, sent = _device(**kw)
    _charge_transition(dev)
    starts = [c for c in sent if (c[0], c[1]) == expected]
    assert len(starts) == 1, (
        f"{name}: session start {expected} was sent {len(starts)}× on the "
        f"transition cycle — sent: {sent}")
    assert any(c[0] == "number" for c in sent), (
        f"{name}: the current write never happened — {sent}")


@pytest.mark.unit
@pytest.mark.parametrize("name,kw,expected", SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_the_old_rule_fails_this_oracle_where_the_start_is_elsewhere(
        name, kw, expected):
    """The vacuity twin. Restore the #536 rule — ``ensure_enabled`` claims
    the session unconditionally — and the oracle must FAIL on exactly the
    shapes whose start is NOT the enable entity, and still pass on the
    others. An oracle that cannot fire is decoration."""
    dev, sent = _device(**kw)
    dev.enable_entity_is_session_start = lambda: True   # the old behaviour
    _charge_transition(dev)
    starts = [c for c in sent if (c[0], c[1]) == expected]
    start_is_the_switch = (dev.session_start_mechanism()
                           == dev.SESSION_START_STOP_ENTITY)
    has_switch = bool(dev.start_stop_entity)
    if has_switch and not start_is_the_switch:
        assert not starts, (
            f"{name}: the old rule should have swallowed {expected} — "
            f"this oracle cannot catch the bug it was written for")
    else:
        assert len(starts) == 1, f"{name}: {sent}"


@pytest.mark.unit
def test_a_button_start_is_still_pressed_exactly_once():
    """#804/#536's reason for the latch: a ``button.`` start is NOT
    idempotent, so when the button IS the session start the press must not
    be doubled by a follow-up ``start_session``."""
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
         CurrentControlDevice.SESSION_START_SERVICE),
        ({"charge_mode_entity": "select.m", "charge_mode_start": "manual"},
         CurrentControlDevice.SESSION_START_CHARGE_MODE),
        # a select with no START option is not a start mechanism
        ({"charge_mode_entity": "select.m", "start_stop_entity": "switch.s"},
         CurrentControlDevice.SESSION_START_STOP_ENTITY),
        ({"start_stop_entity": "switch.s"},
         CurrentControlDevice.SESSION_START_STOP_ENTITY),
        ({"charger_service": "keba.set_current"},
         CurrentControlDevice.SESSION_START_CHARGER_SERVICE),
        ({}, CurrentControlDevice.SESSION_START_NONE),
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
        assert dev.session_start_mechanism() == dev.SESSION_START_SERVICE
        asyncio.run(dev.start_session())
        assert [(c[0], c[1]) for c in sent] == [("easee", "action_command")]

    def test_every_start_dispatcher_reads_the_one_resolver(self):
        """#935's hand-back re-sends the session start, and was a second
        copy of the chain; a brand added to one would have been missed by
        the other. Both dispatchers must ASK rather than re-derive."""
        for fn in (CurrentControlDevice.start_session,
                   CurrentControlDevice.release_to_user):
            assert calls(fn, "session_start_mechanism"), (
                f"{fn.__name__} re-derives the start chain by hand again")
