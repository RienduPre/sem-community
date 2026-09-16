"""#955 — a limit at the meter, mirroring the peak guard. Pure; the clock is fed.

And its intents: ``LIMIT_EXPORT`` / ``RELEASE_EXPORT`` dispatched through
``actuate_battery`` like every other battery intent — observer mode records
a WOULD and writes nothing, and a brand without the verb is a recorded
refusal, never a crash.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.actuate_battery import (
    actuate_battery,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    BatteryDecision, BatteryIntent,
)
from custom_components.solar_energy_management.coordinator.export_guard import (
    ENGAGE_HOLD_S, EXPORT_EPS_W, RELEASE_HOLD_S, ExportGuard,
)
from custom_components.solar_energy_management.coordinator.sink_verdicts import CLOSED, OPEN


def _run(guard, seq):
    """seq: [(t, verdict_state, export_w)] → list of intents."""
    return [guard.update(t, state, export_w).intent for t, state, export_w in seq]


class TestHysteresis:
    def test_engages_only_after_the_closed_verdict_holds(self):
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S - 1, CLOSED, 500.0),
                       (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        assert out == [None, None, "limit_export"]
        assert g.state == "engaged"

    def test_a_flapping_verdict_never_engages(self):
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 500.0), (60, OPEN, 500.0),
                       (120, CLOSED, 500.0), (180, OPEN, 500.0)])
        assert set(out) == {None}
        assert g.state != "engaged"

    def test_releases_only_after_open_holds(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        out = _run(g, [(1000, OPEN, 0.0), (1000 + RELEASE_HOLD_S - 1, OPEN, 0.0),
                       (1000 + RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert out == [None, None, "release_export"]
        assert g.state == "idle"

    def test_engaged_once_is_engaged_until_released(self):
        """No second LIMIT_EXPORT while already engaged — one write, not one a cycle."""
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        assert _run(g, [(ENGAGE_HOLD_S + 10, CLOSED, 900.0),
                        (ENGAGE_HOLD_S + 20, CLOSED, 900.0)]) == [None, None]

    def test_the_holds_are_configurable(self):
        g = ExportGuard(engage_hold_s=10.0, release_hold_s=20.0)
        assert _run(g, [(0, CLOSED, 500.0), (11, CLOSED, 500.0)]) == [None, "limit_export"]
        assert _run(g, [(100, OPEN, 0.0), (121, OPEN, 0.0)]) == [None, "release_export"]


class TestLastNotFirst:
    def test_no_measured_export_no_engagement(self):
        """The sinks absorbed everything this cycle — nothing to clip."""
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, EXPORT_EPS_W - 1)])
        assert out == [None, None]
        assert g.state == "holding"

    def test_export_reappearing_while_closed_engages(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, 0.0)])
        assert g.update(ENGAGE_HOLD_S + 2, CLOSED, 900.0).intent == "limit_export"

    def test_a_blind_meter_is_not_export(self):
        """#906: an unreadable meter is None, never a number — and never a reason to cut."""
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, None), (ENGAGE_HOLD_S + 1, CLOSED, None)])
        assert out == [None, None] and g.state == "holding"

    def test_a_blind_meter_says_so_not_that_the_sinks_absorbed_everything(self):
        """Review: 'could not measure' must never read as 'measured zero' (class 86)."""
        g = ExportGuard()
        _run(g, [(0, CLOSED, None), (ENGAGE_HOLD_S + 1, CLOSED, None)])
        assert "unreadable" in g.reason


class TestRefusal:
    def test_refused_is_sticky_until_release(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        g.report_refused("no export control on this brand")
        assert g.state == "refused"
        assert g.update(ENGAGE_HOLD_S + 30, CLOSED, 500.0).intent is None
        assert g.update(5000, OPEN, 0.0).intent is None
        assert g.update(5000 + RELEASE_HOLD_S + 1, OPEN, 0.0).intent is None
        assert g.state == "idle"

    def test_three_refusals_ask_for_a_repair(self):
        g = ExportGuard()
        for _ in range(3):
            g.report_refused("adapter raised")
        assert g.repair_wanted is True

    def test_a_release_clears_the_refusal_count(self):
        g = ExportGuard()
        g.report_refused("x"); g.report_refused("x")
        _run(g, [(0, OPEN, 0.0), (RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert g.state == "idle" and g.repair_wanted is False


class TestUnknownIsNotHostile:
    def test_open_from_the_start_does_nothing(self):
        g = ExportGuard()
        assert _run(g, [(0, OPEN, 5000.0), (600, OPEN, 5000.0)]) == [None, None]
        assert g.state == "idle"

    def test_held_is_not_a_grid_state(self):
        """Only CLOSED counts; anything else is the open side."""
        g = ExportGuard()
        assert _run(g, [(0, "held", 5000.0), (600, "held", 5000.0)]) == [None, None]


# ── Task 6: the intents through the actuation seam ──────────────────────

def _adapter():
    a = MagicMock()
    a.command_limit_export = AsyncMock()
    a.command_release_export = AsyncMock()
    a.last_intent = None
    a._last_error = None
    return a


@pytest.mark.asyncio
class TestTheIntentsDispatch:
    async def test_limit_export_calls_the_verb_with_the_cap(self):
        a = _adapter()
        await actuate_battery(BatteryDecision("b1", BatteryIntent.LIMIT_EXPORT,
                                              export_limit_w=0.0, reason="closed"), a)
        a.command_limit_export.assert_awaited_once_with(0.0)

    async def test_release_export_calls_the_release_verb(self):
        a = _adapter()
        await actuate_battery(BatteryDecision("b1", BatteryIntent.RELEASE_EXPORT, reason="open"), a)
        a.command_release_export.assert_awaited_once()

    async def test_observer_mode_calls_nothing_and_records_a_would(self):
        a = _adapter(); ctl = MagicMock()
        await actuate_battery(BatteryDecision("b1", BatteryIntent.LIMIT_EXPORT,
                                              export_limit_w=0.0, reason="closed"),
                              a, observer=True, controller=ctl)
        a.command_limit_export.assert_not_awaited()
        assert ctl.publish_observer_decision.call_args.kwargs["action"] == "limit_export"

    async def test_a_brand_without_the_verb_is_a_recorded_refusal_not_a_crash(self):
        a = _adapter()
        a.command_limit_export = AsyncMock(side_effect=NotImplementedError("no export control"))
        await actuate_battery(BatteryDecision("b1", BatteryIntent.LIMIT_EXPORT,
                                              export_limit_w=0.0, reason="closed"), a)
        assert "no export control" in str(a._last_error)

    async def test_an_adapter_error_is_recorded_too(self):
        a = _adapter()
        a.command_release_export = AsyncMock(side_effect=RuntimeError("modbus timeout"))
        await actuate_battery(BatteryDecision("b1", BatteryIntent.RELEASE_EXPORT, reason="open"), a)
        assert "modbus timeout" in str(a._last_error)

    async def test_the_intents_are_not_power_derived(self):
        """(#818) a blipping power sensor must never move them."""
        from custom_components.solar_energy_management.coordinator import actuate_battery as m
        assert BatteryIntent.LIMIT_EXPORT not in m._POWER_DERIVED_INTENTS
        assert BatteryIntent.RELEASE_EXPORT not in m._POWER_DERIVED_INTENTS


class TestOnlyUndoWhatYouTook:
    """Live on .175: the guard emitted RELEASE_EXPORT after a hold that never
    cut. On Huawei that is a real ``reset_maximum_feed_grid_power`` call over a
    limit SEM never set — or over one somebody ELSE set. #908's rule."""

    def test_a_hold_that_never_cut_releases_nothing(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, 0.0)])   # holding, never engaged
        assert g.state == "holding"
        out = _run(g, [(1000, OPEN, 0.0), (1000 + RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert out == [None, None]
        assert g.state == "idle"

    def test_a_hold_that_never_cut_goes_idle_immediately(self):
        """No release hysteresis to serve when nothing is held down."""
        g = ExportGuard()
        _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, 0.0)])
        assert g.update(1000, OPEN, 0.0).intent is None
        assert g.state == "idle" and "nothing to release" in g.reason

    def test_a_real_cut_still_releases(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        assert g.state == "engaged"
        out = _run(g, [(1000, OPEN, 0.0), (1000 + RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert out == [None, "release_export"]

    def test_the_flag_resets_so_a_second_episode_behaves(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0),
                 (1000, OPEN, 0.0), (1000 + RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert g.state == "idle"
        _run(g, [(2000, CLOSED, 0.0), (2000 + ENGAGE_HOLD_S + 1, CLOSED, 0.0)])   # holds only
        assert _run(g, [(3000, OPEN, 0.0)]) == [None] and g.state == "idle"

    def test_a_teardown_hands_back_nothing_after_a_mere_hold(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        import asyncio
        from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
        g = ExportGuard()
        _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, 0.0)])
        adapter = MagicMock(command_release_export=AsyncMock(), _last_error=None)
        fake = SimpleNamespace(_export_guard=g, _battery_adapters={"b1": adapter},
                               _observer_mode=False)
        assert asyncio.run(SEMCoordinator.async_release_export_guard(fake, reason="unloaded")) is None
        adapter.command_release_export.assert_not_awaited()
        assert SEMCoordinator.export_release_recipes(fake) == {}
