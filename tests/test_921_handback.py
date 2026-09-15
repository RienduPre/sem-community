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
        """AST, not source strings (#925): the #955 release sits inside
        async_unload_entry before the #949 pacer read, so it runs before
        observer mode flips."""
        import ast, inspect
        import custom_components.solar_energy_management as init_mod
        tree = ast.parse(inspect.getsource(init_mod.async_unload_entry))
        first = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in ("async_release_export_guard", "pending_pacing_release"):
                    first.setdefault(name, node.lineno)
        assert first["async_release_export_guard"] < first["pending_pacing_release"]


# ── review (HIGH): the cut must outlive a restart, survive a reload, and be replayable on removal ──

from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
    GenericBatteryAdapter,
)
from custom_components.solar_energy_management.coordinator.battery_adapters.huawei import (
    HuaweiBatteryAdapter,
)
from custom_components.solar_energy_management.coordinator.sink_verdicts import CLOSED, SinkVerdict


class _Store:
    def __init__(self, rec=None):
        self.rec = rec; self.saved = []
    async def async_load(self):
        return self.rec
    async def async_save(self, data):
        self.saved.append(data); self.rec = data


def _live(store, *, observer=False, verdict=CLOSED, export_w=3000.0):
    hass = MagicMock(); hass.services.async_call = AsyncMock()
    hass.states.get = MagicMock(return_value=SimpleNamespace(state="11000"))
    gen = GenericBatteryAdapter(hass, {"export_limit_entity": "number.inv_export_limit"})
    fake = SimpleNamespace(
        hass=hass, config={"export_guard_enabled": True}, _observer_mode=observer,
        _export_guard=None, _sink_verdicts={"grid_export": SinkVerdict("grid_export", verdict, "t")},
        _battery_adapters={"b1": gen}, _surplus_controller=MagicMock(),
        _export_guard_store=lambda: store,
        export_release_recipes=lambda: SEMCoordinator.export_release_recipes(fake),
        _export_guard_persist=lambda engaged: SEMCoordinator._export_guard_persist(fake, engaged),
        _export_guard_adopt=lambda g: SEMCoordinator._export_guard_adopt(fake, g),
    )
    return fake, hass, gen


@pytest.mark.asyncio
class TestTheCutOutlivesALifetime:
    async def test_a_live_engage_writes_the_store_with_a_release_recipe(self):
        store = _Store(); fake, hass, gen = _live(store)
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, SimpleNamespace(grid_export_power=3000.0, grid_power_unavailable=False), now=float(t))
        assert fake._export_guard.state == "engaged"
        assert store.saved and store.saved[-1]["engaged"] is True
        rec = store.saved[-1]["recipes"]["b1"]
        assert rec == {"domain": "number", "service": "set_value",
                       "data": {"entity_id": "number.inv_export_limit", "value": 11000.0}}

    async def test_an_observer_engage_writes_nothing(self):
        store = _Store(); fake, hass, gen = _live(store, observer=True)
        for t in (0, 60, 130):
            await SEMCoordinator._run_export_guard(fake, SimpleNamespace(grid_export_power=3000.0, grid_power_unavailable=False), now=float(t))
        assert fake._export_guard.state == "engaged" and store.saved == []

    async def test_a_new_lifetime_adopts_an_engaged_cut_and_its_prior(self):
        store = _Store({"engaged": True, "since": "t", "recipes": {"b1": {
            "domain": "number", "service": "set_value",
            "data": {"entity_id": "number.inv_export_limit", "value": 9000.0}}}})
        fake, hass, gen = _live(store, verdict="open", export_w=0.0)
        await SEMCoordinator._run_export_guard(fake, SimpleNamespace(grid_export_power=0.0, grid_power_unavailable=False), now=0.0)
        assert fake._export_guard.state in ("engaged", "releasing")   # adopted, now on the open side
        assert gen._export_prior == 9000.0                             # the ORIGINAL prior, not today's read
        await SEMCoordinator._run_export_guard(fake, SimpleNamespace(grid_export_power=0.0, grid_power_unavailable=False), now=400.0)
        assert ("number", "set_value", {"entity_id": "number.inv_export_limit", "value": 9000.0}) in \
            [(c.args[0], c.args[1], c.args[2]) for c in hass.services.async_call.await_args_list]
        assert store.saved[-1] == {"engaged": False}

    async def test_a_store_that_says_not_engaged_adopts_nothing(self):
        store = _Store({"engaged": False}); fake, hass, gen = _live(store, verdict="open", export_w=0.0)
        await SEMCoordinator._run_export_guard(fake, SimpleNamespace(grid_export_power=0.0, grid_power_unavailable=False), now=0.0)
        assert fake._export_guard.state == "idle"


class TestRecipes:
    def test_huawei_recipe_is_the_integrations_reset(self):
        a = HuaweiBatteryAdapter(MagicMock(), {}); a._inverter_device_id = "dev"
        assert a.export_release_recipe() == {"domain": "huawei_solar", "service": "reset_maximum_feed_grid_power",
                                             "data": {"device_id": "dev"}}

    def test_generic_recipe_needs_a_captured_prior(self):
        a = GenericBatteryAdapter(MagicMock(), {"export_limit_entity": "number.x"})
        assert a.export_release_recipe() is None
        a._export_prior = 5000.0
        assert a.export_release_recipe()["data"] == {"entity_id": "number.x", "value": 5000.0}

    def test_recipes_are_empty_in_observer_mode_or_when_idle(self):
        g = ExportGuard(); g.state = "engaged"
        a = GenericBatteryAdapter(MagicMock(), {"export_limit_entity": "number.x"}); a._export_prior = 1.0
        assert SEMCoordinator.export_release_recipes(SimpleNamespace(_export_guard=g, _battery_adapters={"b1": a}, _observer_mode=True)) == {}
        g2 = ExportGuard()
        assert SEMCoordinator.export_release_recipes(SimpleNamespace(_export_guard=g2, _battery_adapters={"b1": a}, _observer_mode=False)) == {}
        assert SEMCoordinator.export_release_recipes(SimpleNamespace(_export_guard=g, _battery_adapters={"b1": a}, _observer_mode=False)) == {"b1": a.export_release_recipe()}


class TestUnloadSemantics:
    def test_reload_stashes_and_disable_releases(self):
        """AST, not source strings (#925): a plain reload stashes recipes and
        never releases; a disable calls the release; removal replays the stash;
        the next setup clears a stale stash."""
        import ast, inspect
        import custom_components.solar_energy_management as init_mod

        def calls(fn, name):
            return [n for n in ast.walk(ast.parse(inspect.getsource(fn)))
                    if isinstance(n, ast.Call)
                    and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == name]

        def names(fn, ident):
            return [n for n in ast.walk(ast.parse(inspect.getsource(fn)))
                    if isinstance(n, ast.Name) and n.id == ident]

        rel = calls(init_mod.async_unload_entry, "async_release_export_guard")
        assert rel and all(
            any(k.arg == "reason" and getattr(k.value, "value", None) == "disabled" for k in c.keywords)
            for c in rel), "the unload release is only ever the DISABLE release"
        assert names(init_mod.async_unload_entry, "_PENDING_EXPORT_RELEASE"), "reload must stash"
        assert names(init_mod.async_remove_entry, "_PENDING_EXPORT_RELEASE") and \
            calls(init_mod.async_remove_entry, "async_call"), "removal must replay the stash"
        assert names(init_mod.async_setup_entry, "_PENDING_EXPORT_RELEASE"), "setup must clear a stale stash"
