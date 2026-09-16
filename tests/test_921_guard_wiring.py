"""#955 wiring — the guard ticks after the sinks, the probe holds while it is engaged.

``_run_export_guard`` is exercised unbound on a bare fake (the #820
inert-at-the-wire pattern): it reads the cycle's grid verdict, feeds the
guard the export the meter still shows, dispatches through every battery
adapter's export verbs, and publishes its state. Observer mode records a
WOULD through the surplus controller — the same surface every other
withheld command uses — and writes nothing.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
from custom_components.solar_energy_management.coordinator.export_guard import ExportGuard
from custom_components.solar_energy_management.coordinator.sink_verdicts import (
    CLOSED, OPEN, SinkVerdict,
)


def _fake(engaged=False, verdict=OPEN, export_w=0.0, enabled=True, observer=False):
    hass = MagicMock(); hass.services.async_call = AsyncMock()
    g = ExportGuard()
    if engaged:
        g.state = "engaged"
    adapter = MagicMock()
    adapter.command_limit_export = AsyncMock()
    adapter.command_release_export = AsyncMock()
    adapter._last_error = None
    ctl = MagicMock()
    fake = SimpleNamespace(
        hass=hass,
        config={"export_guard_enabled": enabled, "curtailment_probe_enabled": True,
                "export_guard_engage_s": 120, "export_guard_release_s": 300},
        _observer_mode=observer, _export_guard=g,
        _sink_verdicts={"grid_export": SinkVerdict("grid_export", verdict, "t")},
        _battery_adapters={"b1": adapter},
        _surplus_controller=ctl,
        _curtailment_last=None,
    )
    power = SimpleNamespace(grid_export_power=export_w, solar_power=0.0, home_consumption_power=0.0,
                            battery_charge_power=0.0, ev_power=0.0, ev_connected=False,
                            grid_power_unavailable=False, grid_import_power=0.0,
                            inputs_degraded=False)
    return fake, power, adapter, ctl


class TestTheProbeHolds:
    def test_engaged_guard_holds_the_probe(self):
        fake, power, _, _ = _fake(engaged=True)
        assert SEMCoordinator._curtailment_grant_w(fake, power) == 0.0
        assert fake._curtailment_last["state"] == "held_by_export_guard"


@pytest.mark.asyncio
class TestTheGuardTicks:
    async def test_disabled_guard_never_calls_an_adapter(self):
        fake, power, adapter, _ = _fake(verdict=CLOSED, export_w=3000.0, enabled=False)
        for t in (0, 200, 400):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        adapter.command_limit_export.assert_not_awaited()
        assert fake._export_guard_state["state"] == "idle"

    async def test_closed_meter_with_export_engages_after_the_hold(self):
        fake, power, adapter, _ = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        adapter.command_limit_export.assert_awaited_once_with(0.0)
        assert fake._export_guard_state["state"] == "engaged"

    async def test_observer_mode_records_a_would_and_writes_nothing(self):
        fake, power, adapter, ctl = _fake(verdict=CLOSED, export_w=3000.0, observer=True)
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        adapter.command_limit_export.assert_not_awaited()
        assert fake._export_guard_state["state"] == "engaged"
        assert fake._export_guard_state["would"] == "limit_export"
        assert ctl.publish_observer_decision.call_args.kwargs["action"] == "limit_export"

    async def test_a_refusing_adapter_puts_the_guard_in_refused(self):
        fake, power, adapter, _ = _fake(verdict=CLOSED, export_w=3000.0)
        adapter.command_limit_export = AsyncMock(side_effect=NotImplementedError("no export control"))
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        assert fake._export_guard_state["state"] == "refused"
        assert "no export control" in fake._export_guard_state["reason"]

    async def test_a_blind_meter_never_engages(self):
        fake, power, adapter, _ = _fake(verdict=CLOSED, export_w=3000.0)
        power.grid_power_unavailable = True
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        adapter.command_limit_export.assert_not_awaited()
        assert fake._export_guard_state["state"] == "holding"

    async def test_release_after_the_meter_reopens(self):
        fake, power, adapter, _ = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        fake._sink_verdicts = {"grid_export": SinkVerdict("grid_export", OPEN, "t")}
        for t in (1000, 1400):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        adapter.command_release_export.assert_awaited_once()
        assert fake._export_guard_state["state"] == "idle"


@pytest.mark.asyncio
class TestTheCutIsVisibleInObserverMode:
    """Live on .175 with the compressed sim: the guard ENGAGED and the rig's
    own observer surface showed nothing — the cut published under
    ``battery:<id>``, the same key as the battery's discharge decision, so the
    two clobbered each other every cycle; and ``would`` was set only on the
    single cycle the intent fired. #855: a case is judged on what would hit
    the wire, so the wire must be readable."""

    async def test_the_cut_has_its_own_observer_key(self):
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.solar_energy_management.coordinator.actuate_battery import (
            actuate_battery,
        )
        from custom_components.solar_energy_management.coordinator.charger_types import (
            BatteryDecision, BatteryIntent,
        )
        ctl = MagicMock(); a = MagicMock(command_limit_export=AsyncMock(), _last_error=None)
        await actuate_battery(BatteryDecision("primary", BatteryIntent.LIMIT_EXPORT,
                                              export_limit_w=0.0, reason="closed"),
                              a, observer=True, controller=ctl)
        assert ctl.publish_observer_decision.call_args.kwargs["key"] == "export:primary"

    async def test_a_normal_battery_decision_keeps_its_key(self):
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.solar_energy_management.coordinator.actuate_battery import (
            actuate_battery,
        )
        from custom_components.solar_energy_management.coordinator.charger_types import (
            BatteryDecision, BatteryIntent,
        )
        ctl = MagicMock(); a = MagicMock(command_normal=AsyncMock(), _last_error=None)
        await actuate_battery(BatteryDecision("primary", BatteryIntent.NORMAL, reason="n"),
                              a, observer=True, controller=ctl)
        assert ctl.publish_observer_decision.call_args.kwargs["key"] == "battery:primary"

    async def test_an_engaged_cut_says_so_every_cycle_not_once(self):
        fake, power, adapter, ctl = _fake(verdict=CLOSED, export_w=3000.0, observer=True)
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, power, now=float(t))
        assert fake._export_guard_state["state"] == "engaged"
        assert fake._export_guard_state["would"] == "limit_export"
        ctl.publish_observer_decision.reset_mock()
        await SEMCoordinator._run_export_guard(fake, power, now=200.0)   # a later, quiet cycle
        assert fake._export_guard_state["would"] == "limit_export", "the flash became silence"
        kw = ctl.publish_observer_decision.call_args.kwargs
        assert kw["key"] == "export_guard" and kw["action"] == "limit_export"
        assert kw["kind"] == "battery", "the rig's sim filters on kind battery/charger"

    async def test_an_idle_guard_publishes_nothing(self):
        fake, power, adapter, ctl = _fake(verdict=OPEN, export_w=0.0, observer=True)
        await SEMCoordinator._run_export_guard(fake, power, now=0.0)
        assert not [c for c in ctl.publish_observer_decision.call_args_list
                    if c.kwargs.get("key") == "export_guard"]
