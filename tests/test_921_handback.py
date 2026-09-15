"""#955 obeys #908/#936/#949: only what SEM commanded is handed back, and it is handed back first.

An inverter left at zero feed-in by a removed SEM would throw away every
surplus kWh with nothing left on the system that knows why. So the release
runs on unload and disable, BEFORE observer mode flips (the #949 order), and
only when the guard is actually holding something.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management import cleanup
from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
from custom_components.solar_energy_management.coordinator.export_guard import ExportGuard


class TestInventory:
    def test_the_guard_store_is_in_the_per_entry_inventory(self):
        assert "sem.export_guard.{entry_id}" in cleanup._PER_ENTRY_STORE_FORMATS


def _fake(state):
    g = ExportGuard(); g.state = state
    adapter = MagicMock(command_release_export=AsyncMock(), _last_error=None)
    return SimpleNamespace(_export_guard=g, _battery_adapters={"b1": adapter},
                           _observer_mode=False), g, adapter


@pytest.mark.asyncio
class TestRelease:
    async def test_an_engaged_guard_is_released_on_unload(self):
        fake, g, adapter = _fake("engaged")
        said = await SEMCoordinator.async_release_export_guard(fake, reason="unloaded")
        adapter.command_release_export.assert_awaited_once()
        assert g.state == "idle" and "released" in said and "unloaded" in said

    async def test_a_releasing_or_refused_guard_is_released_too(self):
        for st in ("releasing", "refused"):
            fake, g, adapter = _fake(st)
            await SEMCoordinator.async_release_export_guard(fake, reason="disabled")
            adapter.command_release_export.assert_awaited_once()
            assert g.state == "idle"

    async def test_an_idle_guard_touches_nothing(self):
        fake, _, adapter = _fake("idle")
        assert await SEMCoordinator.async_release_export_guard(fake, reason="unloaded") is None
        adapter.command_release_export.assert_not_awaited()

    async def test_a_holding_guard_has_written_nothing_and_releases_nothing(self):
        fake, _, adapter = _fake("holding")
        assert await SEMCoordinator.async_release_export_guard(fake, reason="unloaded") is None
        adapter.command_release_export.assert_not_awaited()

    async def test_observer_mode_releases_nothing_and_resets_the_guard(self):
        """Review of the first cut, HIGH: the guard reaches 'engaged' in observer
        mode too (its state is time + measured export), but nothing was ever
        written — so a release would be the first REAL write, on the rig's
        shared Huawei. #936's rule: left exactly as found."""
        fake, g, adapter = _fake("engaged")
        fake._observer_mode = True
        said = await SEMCoordinator.async_release_export_guard(fake, reason="unloaded")
        adapter.command_release_export.assert_not_awaited()
        assert g.state == "idle" and said is None

    async def test_no_guard_at_all_is_fine(self):
        fake = SimpleNamespace(_battery_adapters={}, _observer_mode=False)
        assert await SEMCoordinator.async_release_export_guard(fake, reason="unloaded") is None

    async def test_an_adapter_that_raises_never_stops_the_teardown(self):
        fake, g, adapter = _fake("engaged")
        adapter.command_release_export = AsyncMock(side_effect=RuntimeError("modbus gone"))
        said = await SEMCoordinator.async_release_export_guard(fake, reason="unloaded")
        assert g.state == "idle" and "nothing to release" in said


class TestTheUnloadHookOrder:
    def test_the_export_release_precedes_the_pacer_release_in_unload(self):
        """Read the source: the #955 release sits inside async_unload_entry,
        before the #949 pacer read and therefore before observer mode flips."""
        import inspect
        import custom_components.solar_energy_management as init_mod
        src = inspect.getsource(init_mod.async_unload_entry)
        i = src.index("async_release_export_guard")
        j = src.index("pending_pacing_release")
        assert i < j
