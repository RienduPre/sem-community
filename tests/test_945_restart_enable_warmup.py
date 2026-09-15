"""#945 — a restart is not a fault. Bug class 86.

alexmc1510 restarted Home Assistant on 2.1.0-beta.14 and was told, within
about half a minute, that

    "SEM's last 3+ current commands to EV Charger were rejected: enable
     switch unavailable/locked — cannot start charging. The charger is NOT
     under SEM control right now…"

No current command had been sent. The charger was fine. What SEM had
actually observed was ``hass.states.get("switch.…") is None`` — which is
what EVERY entity looks like while its integration is still loading.

The surface for "the enable switch cannot be driven" (#536/#548) borrowed
``CurrentControlDevice._record_actuation_failure``, the counter built for
commands that RAISED (#462). That counter's threshold is three CYCLES, so
at the default 10 s interval SEM filed a persistent ERROR Repair 30 seconds
into every restart. Every other entity-absence Repair in SEM waits out
``UNAVAILABLE_REPAIR_THRESHOLD_S`` of wall clock for exactly this reason
(#611: "a restart's warm-up window must not cry wolf"), and #824 already
applies that hold to this very entity.

Two conditions reach that surface and only one of them is silence, which is
the trap this file guards hardest: a switch that is READABLE but stuck off
(#536 Eco-Smart) is ``controllable`` — so a hold retired on readability
resets every cycle, never elapses, and makes that Repair unreportable.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator import (
    repair_issues as ri,
)
from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (  # noqa: E501
    GenericBatteryAdapter,
)
from custom_components.solar_energy_management.coordinator.charger_adapters import (
    GenericAdapter,
)
from custom_components.solar_energy_management.coordinator.charger_reconciler import (
    Action,
    ActionKind,
    ChargerReconciler,
)
from custom_components.solar_energy_management.coordinator.coordinator import (
    SEMCoordinator,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerDecision,
    ChargerIntent,
    ChargerPower,
)
from custom_components.solar_energy_management.devices.base import (
    CurrentControlDevice,
)

from .ast_contracts import call_sites, invented_evidence_call_sites

#: The one constant, never a fresh literal (class 46).
HOLD = ri.UNAVAILABLE_REPAIR_THRESHOLD_S
CYCLE = 10.0  # DEFAULT_UPDATE_INTERVAL — what "three cycles" was worth
SWITCH = "switch.cargador_coche_carga_de_ve"


def _device(switch_state: str | None = None) -> CurrentControlDevice:
    """A switch-controlled charger. ``switch_state=None`` is the restart: the
    entity is not in the state machine at all."""
    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=lambda eid: (
        SimpleNamespace(state=switch_state, attributes={})
        if (eid == SWITCH and switch_state is not None) else None))
    dev = CurrentControlDevice(
        hass=hass,
        device_id="ev_charger_1",
        name="EV Charger",
        min_current=6.0,
        max_current=32.0,
        phases=3,
    )
    dev.start_stop_entity = SWITCH
    return dev


@pytest.mark.unit
class TestTheRestartWindowIsSilent:
    """The reporter's own half-minute."""

    def test_thirty_seconds_of_a_missing_switch_raises_nothing(self):
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            for cycle in range(6):  # 0–50 s: twice what the old bug needed
                assert dev._note_enable_blocked(now=cycle * CYCLE) is False
            raised.assert_not_called()

    def test_a_non_command_never_spends_the_command_counter(self):
        """The vacuity twin of the whole fix: an observation that no command
        was possible must not look like three rejected commands."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed"):
            for cycle in range(6):
                dev._note_enable_blocked(now=cycle * CYCLE)
        assert dev._actuation_failures == 0, (
            "silence was counted as a rejected command — the #945 shape"
        )

    def test_past_the_hold_it_raises_exactly_once(self):
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            assert dev._note_enable_blocked(now=0.0) is False
            assert dev._note_enable_blocked(now=HOLD - 1.0) is False
            raised.assert_not_called()
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            raised.assert_called_once()
            # It persists; it does not re-file every cycle afterwards.
            for extra in (2.0, 12.0, 22.0):
                assert dev._note_enable_blocked(now=HOLD + extra) is True
            raised.assert_called_once()

    def test_a_switch_that_answers_restarts_the_window(self):
        """A blip must not accumulate towards the threshold across gaps."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised, \
                patch.object(ri, "clear_charger_actuation_failed"):
            dev._note_enable_blocked(now=0.0)
            dev._note_enable_blocked(now=HOLD - 10.0)
            dev._note_enable_unblocked()             # it answered
            dev._note_enable_blocked(now=HOLD)       # fresh window opens here
            assert dev._note_enable_blocked(now=2 * HOLD - 10.0) is False
            raised.assert_not_called()
            assert dev._note_enable_blocked(now=2 * HOLD + 1.0) is True


@pytest.mark.unit
class TestTheStuckSwitchWaitsOutTheWarmUpToo:
    """Round one (2.1.0-beta.17) exempted this branch, on the reasoning that
    a READABLE switch sitting ``off`` with the #536 re-assert budget spent is
    evidence — "SEM wrote ``turn_on`` five times and watched it come back
    off" — and so kept its three-CYCLE speed.

    alexmc1510 restarted onto beta.22 and got the very same Repair with the
    other sentence in it. The reasoning had two holes. The three cycles that
    file it are cycles on which SEM sends NOTHING (the reconciler returns the
    report ALONE, so a successful write cannot flap the notice), and a
    restart reaches this branch as soon as the switch entity appears — still
    ``off``, because its integration has not reached the box yet. So the
    verdict landed ~80 s after the entity loaded, well inside the warm-up the
    same fix was built to respect.

    The fear that justified the exemption was already answered on the other
    side of the fix: the hold is retired by the emitted ACTIONS, never by
    "can I read the entity?", so a readable switch does not reset it."""

    @pytest.mark.asyncio
    async def test_a_readable_but_stuck_switch_is_held_like_any_other(self):
        dev = _device(switch_state="off")
        adapter = GenericAdapter(dev)
        assert adapter.enable_state() == (False, True), (
            "the premise: a switch that reads 'off' is CONTROLLABLE"
        )
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            for cycle in range(8):     # 80 s — what round one filed at
                await adapter.report_enable_blocked(cycle * CYCLE)
            raised.assert_not_called()
            await adapter.report_enable_blocked(HOLD + 1.0)
            raised.assert_called_once()
        assert raised.call_args.kwargs["error"] == ri.ENABLE_WILL_NOT_HOLD, (
            "the two faults are different and keep different sentences"
        )

    @pytest.mark.asyncio
    async def test_a_non_command_never_spends_the_command_counter(self):
        """The vacuity twin, for THIS branch. The cycles that used to file
        this Repair sent nothing at all, yet each one spent a strike on the
        counter whose Repair says "your last 3+ current commands were
        rejected"."""
        dev = _device(switch_state="off")
        adapter = GenericAdapter(dev)
        with patch.object(ri, "raise_charger_actuation_failed"):
            for cycle in range(8):
                await adapter.report_enable_blocked(cycle * CYCLE)
        assert dev._actuation_failures == 0, (
            "an observation was counted as a rejected command — the #945 shape"
        )

    @pytest.mark.asyncio
    async def test_a_charger_with_no_enable_switch_files_nothing(self):
        """``enable_state()`` answers ``(None, True)`` for a KEBA, a service-
        or a button-controlled charger: N/A, not "fine". There is no surface
        here to be blocked, and the issue id is shared with the write path —
        so a verdict invented here would be a verdict about somebody else's
        command."""
        dev = _device()
        dev.start_stop_entity = None
        adapter = GenericAdapter(dev)
        assert adapter.enable_state() == (None, True)
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            for cycle in range(40):              # far past the hold
                await adapter.report_enable_blocked(cycle * CYCLE)
            raised.assert_not_called()
        assert dev._actuation_failures == 0


@pytest.mark.unit
class TestRecovery:

    def test_an_unblocked_cycle_retires_the_repair_this_path_raised(self):
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed"), \
                patch.object(ri, "clear_charger_actuation_failed") as cleared:
            dev._note_enable_blocked(now=0.0)
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            dev._note_enable_unblocked()
            cleared.assert_called_once_with(dev.hass, "ev_charger_1")
        assert dev._enable_blocked_repair_raised is False
        assert dev._actuation_repair_raised is False

    def test_a_write_raised_repair_survives_an_enable_observation(self):
        """The two conditions share ONE issue id. Three rejected WRITES are
        harder evidence than an unblocked switch — and on a KEBA, service or
        button charger there is no switch at all, so an unblocked verdict
        there must never delete the write side's Repair."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised, \
                patch.object(ri, "clear_charger_actuation_failed") as cleared:
            for _ in range(3):
                dev._record_actuation_failure(RuntimeError("schema rejected"))
            raised.assert_called_once()
            dev._note_enable_unblocked()
            cleared.assert_not_called()
        assert dev._actuation_repair_raised is True

    def test_a_zero_amp_stop_write_does_not_retire_the_hold(self):
        """``stop_session`` writes 0 A on every non-KEBA stop, and a charger
        drawing against SEM's IDLE takes one per 60 s reassert dwell. A write
        to the CURRENT entity is no evidence about the ENABLE switch, and
        zeroing the hold there meant 300 s never elapsed."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised, \
                patch.object(ri, "clear_charger_actuation_failed"):
            dev._note_enable_blocked(now=0.0)
            for stop in range(1, 5):                 # a 0 A stop each dwell
                dev._clear_actuation_failure()
                assert dev._enable_blocked_since is not None
                assert dev._note_enable_blocked(now=stop * 60.0) is False
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            raised.assert_called_once()

    def test_a_write_after_a_failed_one_still_does_not_retire_the_hold(self):
        """The same claim from the OTHER side of ``_clear_actuation_failure``.
        With a write streak in flight the function runs past its early
        return, and zeroing the hold there would be just as wrong."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed"), \
                patch.object(ri, "clear_charger_actuation_failed"):
            dev._note_enable_blocked(now=0.0)
            dev._record_actuation_failure(RuntimeError("one bad write"))
            assert dev._actuation_failures == 1
            dev._clear_actuation_failure()        # …and the next one lands
            assert dev._enable_blocked_since is not None
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True

    def test_a_current_write_does_not_retire_the_enable_surfaces_repair(self):
        """Once the hold HAS filed, a successful current write must not take
        the notice down: the switch is still un-commandable, and deleting it
        while the elapsed hold stayed armed churned the Repair — deleted per
        write, re-raised on the next blocked cycle with no fresh wait."""
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised, \
                patch.object(ri, "clear_charger_actuation_failed") as cleared:
            dev._note_enable_blocked(now=0.0)
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            raised.assert_called_once()
            dev._clear_actuation_failure()        # a current write landed
            cleared.assert_not_called()
            assert dev._enable_blocked_repair_raised is True
            assert dev._enable_blocked_since is not None
            # …and no re-raise churn on the next blocked cycle either.
            assert dev._note_enable_blocked(now=HOLD + 2.0) is True
            raised.assert_called_once()
            # The condition ending is what retires it.
            dev._note_enable_unblocked()
            cleared.assert_called_once_with(dev.hass, "ev_charger_1")


@pytest.mark.unit
class TestTheCommandCounterKeepsItsContract:
    """#462 is framework-tier: a command that RAISED is real evidence and
    keeps its three-strike threshold, with no wall clock at all."""

    def test_three_rejected_writes_still_raise_immediately(self):
        dev = _device()
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            dev._record_actuation_failure(RuntimeError("boom"))
            dev._record_actuation_failure(RuntimeError("boom"))
            raised.assert_not_called()
            dev._record_actuation_failure(RuntimeError("boom"))
            raised.assert_called_once()


@pytest.mark.unit
class TestTheAdapterHook:
    """End-to-end through the REAL adapter hook the reconciler calls, on the
    reporter's own configuration: a switch that is not in the state machine."""

    @pytest.mark.asyncio
    async def test_the_hook_holds_through_the_warm_up_then_surfaces(self):
        dev = _device()
        adapter = GenericAdapter(dev)
        assert adapter.enable_state() == (None, False), (
            "the premise: a missing switch is not commandable"
        )
        with patch.object(ri, "raise_charger_actuation_failed") as raised:
            await adapter.report_enable_blocked()
            raised.assert_not_called()
            assert dev._enable_blocked_since is not None, (
                "the hook never started the clock, so it can never surface"
            )
            # …and once the block has outlasted a restart's warm-up:
            dev._enable_blocked_since -= (HOLD + 1.0)
            await adapter.report_enable_blocked()
            raised.assert_called_once()
            assert dev._actuation_failures == 0


@pytest.mark.unit
class TestTheHoldIsRetiredByTheCycleNotByReadability:
    """The hold's other half. "Did this cycle report the surface blocked?" is
    the question — asked of the ACTIONS, because both sub-cases answer it and
    only one of them is distinguishable by reading the switch."""

    @staticmethod
    def _apply(rec, dev, actions):
        adapter = MagicMock()
        adapter._device = dev
        # Every surface the loop AWAITS has to be awaitable.
        adapter.report_enable_blocked = AsyncMock()
        adapter.command_current = AsyncMock()
        adapter.arm_failsafe = AsyncMock()
        asyncio.run(rec._apply_actions(
            actions, adapter, SimpleNamespace(reason="test"),
            SimpleNamespace(power_w=0.0), now=1.0))

    def test_a_cycle_without_the_report_retires_the_hold(self):
        dev, rec = _device(), ChargerReconciler(charger_id="ev_charger_1",
                                               heartbeat_s=5.0)
        with patch.object(ri, "raise_charger_actuation_failed"), \
                patch.object(ri, "clear_charger_actuation_failed") as cleared:
            dev._note_enable_blocked(now=0.0)
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            self._apply(rec, dev, [Action(ActionKind.WRITE_CURRENT, amps=16)])
            cleared.assert_called_once()
        assert dev._enable_blocked_since is None

    def test_a_cycle_that_reports_blocked_keeps_the_hold_running(self):
        """The twin. If this retired too, the window could never elapse."""
        dev, rec = _device(), ChargerReconciler(charger_id="ev_charger_1",
                                               heartbeat_s=5.0)
        with patch.object(ri, "clear_charger_actuation_failed") as cleared:
            dev._note_enable_blocked(now=0.0)
            self._apply(rec, dev,
                        [Action(ActionKind.REPORT_ENABLE_BLOCKED)])
            cleared.assert_not_called()
        assert dev._enable_blocked_since is not None


# ── Round 2: alexmc1510's restart, end to end (2.1.0-beta.22) ────────────


def _restart_device(world):
    """The reporter's charger as an HA restart actually presents it: the
    start/stop entity is absent for the first stretch, then appears reading
    ``off`` because its own integration has not reached the box yet."""
    hass = MagicMock()
    hass.states.get = MagicMock(side_effect=lambda eid: (
        SimpleNamespace(state=world["switch"], attributes={})
        if (eid == SWITCH and world["switch"] is not None) else None))

    async def _call(domain, service, data, blocking=False):
        world["sent"].append(f"{domain}.{service}")
    hass.services.async_call = AsyncMock(side_effect=_call)
    hass.services.has_service = MagicMock(return_value=False)
    dev = CurrentControlDevice(hass=hass, device_id="ev_charger_1",
                               name="EV Charger", min_current=6.0,
                               max_current=32.0, phases=3)
    dev.start_stop_entity = SWITCH
    return dev


def _replay_restart(cycles, *, switch_appears_at=6, switch_recovers_at=None):
    """Drive ``reconcile_and_apply`` — the real one — for ``cycles`` cycles of
    a charger SEM wants to charge. Returns (raised_errors, sent_per_cycle)."""
    world = {"switch": None, "sent": []}
    dev = _restart_device(world)
    adapter = GenericAdapter(dev)
    rec = ChargerReconciler(charger_id="ev_charger_1", heartbeat_s=CYCLE)
    raised, per_cycle = [], []
    with patch.object(ri, "raise_charger_actuation_failed",
                      side_effect=lambda h, d, name=None, error=None:
                      raised.append(error)), \
            patch.object(ri, "clear_charger_actuation_failed"):
        for i in range(cycles):
            if switch_appears_at is not None and i == switch_appears_at:
                world["switch"] = "off"
            if switch_recovers_at is not None and i == switch_recovers_at:
                world["switch"] = "on"
            before = len(world["sent"])
            decision = ChargerDecision(charger_id="ev_charger_1", mode="solar",
                                       intent=ChargerIntent.CHARGE_AT_AMPS,
                                       commanded_amps=16, reason="solar")
            power = ChargerPower(charger_id="ev_charger_1", power_w=0.0,
                                 connected=True, charging=False)
            asyncio.run(rec.reconcile_and_apply(decision, adapter, power,
                                                i * CYCLE))
            per_cycle.append(len(world["sent"]) - before)
    return raised, per_cycle


@pytest.mark.unit
class TestTheReportersRestart:
    """The whole arc through the real reconciler, the real adapter and the
    real device — the only shape that shows why round one did not hold."""

    def test_no_repair_inside_the_warm_up(self):
        raised, _ = _replay_restart(int(HOLD // CYCLE))
        assert raised == [], (
            f"a restart filed {raised!r} inside the warm-up window"
        )

    def test_round_one_would_have_filed_at_eighty_seconds(self):
        """The pin that makes the one above non-vacuous: the old threshold
        really is crossed here. Five re-asserts spend the #536 budget, then
        three REPORT-only cycles — and on those three SEM sends NOTHING,
        which is what makes "your last 3+ commands were rejected" a lie."""
        _, per_cycle = _replay_restart(14)
        asserts = [i for i, n in enumerate(per_cycle) if n]
        assert len(asserts) == 5, (
            f"the #536 budget did not run out — re-asserts on {asserts}"
        )
        silent_after = per_cycle[asserts[-1] + 1:]
        assert len(silent_after) >= 3 and not any(silent_after), (
            "the cycles that used to file the Repair must send nothing"
        )

    def test_a_charger_that_comes_up_late_but_works_is_never_accused(self):
        """A slow integration is the common case, and it must cost the owner
        nothing at all."""
        raised, _ = _replay_restart(60, switch_appears_at=6,
                                    switch_recovers_at=20)
        assert raised == []

    def test_a_switch_that_really_will_not_hold_is_still_reported(self):
        """The #536 Eco-Smart fault the surface exists for. It waits out the
        warm-up now; it does not become unreportable."""
        raised, _ = _replay_restart(int(HOLD // CYCLE) + 2)
        assert raised == [ri.ENABLE_WILL_NOT_HOLD]

    def test_the_re_asserts_do_not_restart_the_clock(self):
        """The mechanism, stated on its own. If the five ENABLE cycles count
        as quiet the episode clock restarts 50 s before the verdict — and,
        worse, a switch dropping mid-session DELETES a standing Repair once
        per drop and re-raises it five cycles later."""
        world = {"switch": "off", "sent": []}
        dev = _restart_device(world)
        adapter = GenericAdapter(dev)
        rec = ChargerReconciler(charger_id="ev_charger_1", heartbeat_s=CYCLE)
        with patch.object(ri, "raise_charger_actuation_failed"), \
                patch.object(ri, "clear_charger_actuation_failed") as cleared:
            dev._note_enable_blocked(now=0.0)
            assert dev._note_enable_blocked(now=HOLD + 1.0) is True
            asyncio.run(rec._apply_actions(
                [Action(ActionKind.ENABLE)], adapter,
                SimpleNamespace(reason="test"), SimpleNamespace(power_w=0.0),
                now=HOLD + 2.0))
            cleared.assert_not_called()
        assert dev._enable_blocked_since is not None
        assert dev._enable_blocked_repair_raised is True


@pytest.mark.unit
class TestTheCounterCanOnlyBeFedByACommand:
    """The structural guard — the class made unrepresentable rather than the
    instance patched. ``_record_actuation_failure`` is #462's evidence
    counter: three commands that RAISED. An exception SEM constructs to
    describe something it merely OBSERVED is the class-86 shape, and it is
    exactly how this bug was written twice."""

    def test_no_production_call_invents_its_own_evidence(self):
        invented = invented_evidence_call_sites("_record_actuation_failure")
        assert invented == [], (
            "a verdict was manufactured for the rejected-command counter at "
            + "; ".join(f"{f}:{ln} ({what})" for f, ln, what in invented)
        )

    def test_the_guard_can_actually_see_a_violation(self):
        """No-vacuous-pass: the contract above is worth nothing if the walker
        cannot find the shape it forbids."""
        import ast

        from . import ast_contracts

        src = (
            "def outer():\n"
            "    try:\n"
            "        send()\n"
            "    except OSError as e:\n"
            "        dev._record_actuation_failure(e)\n"
            "def observer():\n"
            "    dev._record_actuation_failure(RuntimeError('switch is off'))\n"
        )
        tree = ast.parse(src)
        bound = ast_contracts._except_bound_names(tree)
        calls = sorted(
            (n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast_contracts._callee_name(n) == "_record_actuation_failure"),
            key=lambda n: n.lineno)   # ast.walk is breadth-first, not source
        assert len(calls) == 2
        caught, invented = calls
        assert caught.args[0].id in bound[id(caught)], (
            "a genuinely caught exception must be accepted"
        )
        assert not isinstance(invented.args[0], ast.Name), (
            "the invented one must not look like a caught name"
        )

    def test_only_the_write_path_may_spend_it(self):
        """The observation layer — every charger adapter — must not reach
        into the write layer's evidence at all. That import direction is
        what made both instances of this bug a one-line change."""
        offenders = [(f, ln) for f, ln, _ in
                     call_sites("_record_actuation_failure")
                     if not f.startswith("devices/")]
        assert offenders == [], (
            f"an observing layer spends the command counter at {offenders}"
        )


# ── Sibling sweep: the same shape on the battery write verifier (#915) ────


def _hass(state=None):
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=state)
    return hass


def _state(value, unit="W", **attrs):
    return SimpleNamespace(state=str(value),
                           attributes={"unit_of_measurement": unit, **attrs})


def _battery_adapter(hass):
    return GenericBatteryAdapter(hass, {
        "battery_discharge_control_entity": "number.limit",
    })


def _strikes_out(ad, hass):
    """An adapter that has really produced a not-reflected verdict and is at
    the Repair threshold, plus an owner double of the shape the #915 tests
    drive this method with.

    The verdict has to be REAL: ``_raise_or_clear_battery_write_repair``
    needs ``last_unverified_entity``, so an adapter that was never asked to
    judge a write makes every assertion here pass vacuously (class 8).
    """
    ad._note_pending_write("number.limit", 1200.0)
    with patch("time.monotonic", return_value=1e9):
        assert ad.verify_pending_write() is False
    assert ad.last_unverified_entity == "number.limit"
    ad.write_not_taken_strikes = SEMCoordinator.BATTERY_WRITE_STRIKES
    return SimpleNamespace(
        hass=hass,
        BATTERY_WRITE_STRIKES=SEMCoordinator.BATTERY_WRITE_STRIKES,
    )


@pytest.mark.unit
class TestABatteryEntityThatSaidNothing:
    """#915 counts three writes the register CONTRADICTED, and reports a
    vanished entity as "reads missing" — that verdict is deliberate (it is
    the evidence the Repair shows the owner) and stays. What must not happen
    is the ERROR Repair landing three cycles into a restart, while the
    battery's own integration is still loading."""

    def test_the_adapters_missing_verdict_is_unchanged(self):
        """The #915 pin this sweep must not break."""
        ad = _battery_adapter(_hass(None))
        ad._note_pending_write("number.limit", 1200.0)
        with patch("time.monotonic", return_value=1e9):
            assert ad.verify_pending_write() is False
        assert ad.last_unverified_seen == "missing"

    def test_silence_files_no_repair_inside_the_warm_up(self):
        ad = _battery_adapter(_hass(None))
        owner = _strikes_out(ad, _hass(None))
        with patch.object(ri, "raise_battery_control_write_not_taken") as raised:
            with patch("time.monotonic", return_value=1000.0):
                SEMCoordinator._raise_or_clear_battery_write_repair(
                    owner, ad, False)
            raised.assert_not_called()

    def test_silence_that_outlasts_the_warm_up_does_file(self):
        ad = _battery_adapter(_hass(None))
        owner = _strikes_out(ad, _hass(None))
        with patch.object(ri, "raise_battery_control_write_not_taken") as raised:
            with patch("time.monotonic", return_value=1000.0):
                SEMCoordinator._raise_or_clear_battery_write_repair(
                    owner, ad, False)
            raised.assert_not_called()
            with patch("time.monotonic", return_value=1000.0 + HOLD + 1.0):
                SEMCoordinator._raise_or_clear_battery_write_repair(
                    owner, ad, False)
            raised.assert_called_once()

    def test_a_register_that_contradicted_the_write_files_at_once(self):
        """The evidence twin: a live entity answering with the WRONG number
        is not silence, and must keep #915's original speed."""
        ad = _battery_adapter(_hass(_state(5000)))
        ad._note_pending_write("number.limit", 1200.0)
        with patch("time.monotonic", return_value=1e9):
            assert ad.verify_pending_write() is False
        assert ad.write_not_taken_strikes == 1
        owner = _strikes_out(ad, _hass(_state(5000)))
        with patch.object(ri, "raise_battery_control_write_not_taken") as raised:
            SEMCoordinator._raise_or_clear_battery_write_repair(
                owner, ad, False)
            raised.assert_called_once()

    def test_a_reflected_write_retires_only_the_entity_it_proved(self):
        ad = _battery_adapter(_hass(None))
        owner = _strikes_out(ad, _hass(None))
        with patch.object(ri, "raise_battery_control_write_not_taken"), \
                patch.object(ri, "clear_battery_control_write_not_taken"):
            with patch("time.monotonic", return_value=1000.0):
                SEMCoordinator._raise_or_clear_battery_write_repair(
                    owner, ad, False)
            assert owner._battery_write_silent_since == {"number.limit": 1000.0}
            # Another battery's hold must not be re-armed from zero by this.
            owner._battery_write_silent_since["number.other"] = 900.0
            ad.last_verified_entity = "number.limit"
            SEMCoordinator._raise_or_clear_battery_write_repair(owner, ad, True)
        assert owner._battery_write_silent_since == {"number.other": 900.0}
