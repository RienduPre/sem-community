"""#955 — the export dispatch reads a fleet that actually carries the command.

Found LIVE on .175, 17.09.2026 21:00 local, observer OFF: the guard went
``engaged`` ("export 2000 W into a closed meter — cutting to 0 W") and NOTHING
reached the inverter. No service call in the log (SEM logs at DEBUG there, so
the seam's own INFO line would have shown), no error, no refusal reported, no
guard store row, both readbacks unchanged.

The cause has two halves:

* **Two producers of one context.** ``build_view.build_charger_view`` builds a
  ``FleetContext`` for the chargers and the arc threaded ``export_command`` /
  ``export_guard_enabled`` / ``sink_verdicts`` through it — and pinned that
  site (``test_921_sink_verdicts``). ``_run_battery_pipeline`` builds its OWN
  ``FleetContext`` for the batteries, and THAT is the one handed to
  ``_apply_export_decision``. It carried neither field, so ``decide_export``
  read "export guard off" on every cycle and never issued a command.
* **The standing row masked it.** With no command, the seam's ``standing``
  branch re-published "export cut engaged — holding the meter shut" under the
  same key, and the dry-run row for it named the same service — so an engaged
  guard that had written nothing looked, on the observer surface, exactly
  like one that had. Two rigs, since 15.09. The log was the only witness:
  "OBSERVER · WOULD LIMIT_EXPORT" never appeared.

The pins here are the two that would have caught it: a behavioural one that
runs the real pipeline and asks whether the adapter was called, and the
structural sibling question — does EVERY ``FleetContext`` producer pass the
export axis?
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator.charger_types import (
    FleetCycleState,
)
from custom_components.solar_energy_management.coordinator.coordinator import (
    SEMCoordinator,
)
from custom_components.solar_energy_management.coordinator.export_guard import (
    LIMIT_EXPORT, RELEASE_EXPORT, ExportCommand, ExportGuard,
)
from custom_components.solar_energy_management.coordinator.surplus_controller import (
    SurplusController,
)
from custom_components.solar_energy_management.utils.log_gate import reset_log_gate


@pytest.fixture(autouse=True)
def _gate():
    reset_log_gate()
    yield
    reset_log_gate()


def _battery_adapter():
    a = MagicMock()
    for n in ("command_off", "command_normal", "command_limit_discharge",
              "command_force_charge", "command_stop_force_charge",
              "command_force_discharge", "async_recover_pending"):
        setattr(a, n, AsyncMock())
    a.supports_forced_discharge = False
    a.max_charge_power_w = 5000.0
    a.max_discharge_power_w = 5000.0
    return a


def _export_adapter():
    a = MagicMock()
    a.command_limit_export = AsyncMock()
    a.command_release_export = AsyncMock()
    a.export_dry_run = MagicMock(return_value={
        "service": "huawei_solar.set_zero_power_grid_connection",
        "data": {"device_id": "inv"}, "why": None})
    return a


def _fleet_state(command, enabled=True):
    return FleetCycleState(
        power=SimpleNamespace(), config={}, peak_state=None, peak_slot_allowed_w=None,
        is_night=True, tariff_level=None, forecast_remaining_kwh=0.0,
        export_command=command, export_guard_enabled=enabled,
    )


def _coord(*, observer: bool, command, guard_state="engaged"):
    coord = SEMCoordinator(MagicMock(), {
        "battery_capacity_kwh": 15.0, "battery_buffer_soc": 20,
        "export_guard_enabled": True,
    })
    hass = MagicMock()
    hass.services = SimpleNamespace(async_call=AsyncMock(return_value=None))
    coord.hass = hass
    coord.config_entry = SimpleNamespace(entry_id="entry-955")
    coord._observer_mode = observer
    sc = SurplusController(MagicMock()); sc.hass.bus.async_fire = MagicMock()
    coord._surplus_controller = sc
    coord._battery_charge_scheduler._config.enabled = False
    coord.time_manager = SimpleNamespace(is_night_mode=lambda: True)
    # Step 6's answer, exactly as _build_charging_context leaves it (3288,
    # before the pipeline at 4157)
    coord._cycle_fleet_state = _fleet_state(command)
    g = ExportGuard(engage_hold_s=60, release_hold_s=90)
    g.state = guard_state; g._applied = guard_state in ("engaged", "releasing")
    coord._export_guard = g
    coord._export_guard_adopted = True
    coord._export_guard_persist = AsyncMock()
    ex = _export_adapter()
    coord._export_control_adapter = lambda: ex
    return coord, ex


def _power():
    return SimpleNamespace(batteries={}, battery_soc=50.0, battery_power=-200.0,
                           solar_power=0.0, home_consumption_power=300.0,
                           ev_charging=False, ev_connected=False,
                           battery_soc_unavailable=False)


async def _run(coord):
    with patch("custom_components.solar_energy_management.coordinator."
               "battery_adapters.adapter_for", return_value=_battery_adapter()):
        await coord._run_battery_pipeline(_power(), SimpleNamespace(), "idle")


LIMIT = ExportCommand(LIMIT_EXPORT, 0.0, "export 2000 W into a closed meter — cutting to 0 W")
RELEASE = ExportCommand(RELEASE_EXPORT, 0.0, "meter open — releasing the cut")
QUIET = ExportCommand(None, 0.0, "export cut holding — the meter is closed")


# ═══════════════════════════════════════════════════════════════════════
# The pin that would have caught 17.09: the pipeline WRITES the cut
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestTheLivePipelineWritesTheCommand:
    async def test_a_limit_command_reaches_the_adapter(self):
        coord, ex = _coord(observer=False, command=LIMIT)
        await _run(coord)
        ex.command_limit_export.assert_awaited_once_with(0.0)
        coord._export_guard_persist.assert_awaited_once_with(True)

    async def test_a_release_command_reaches_the_adapter(self):
        coord, ex = _coord(observer=False, command=RELEASE, guard_state="idle")
        await _run(coord)
        ex.command_release_export.assert_awaited_once()
        coord._export_guard_persist.assert_awaited_once_with(False)

    async def test_a_quiet_engaged_cycle_writes_nothing(self):
        coord, ex = _coord(observer=False, command=QUIET)
        await _run(coord)
        ex.command_limit_export.assert_not_awaited()
        ex.command_release_export.assert_not_awaited()

    async def test_the_guard_off_writes_nothing_even_with_a_command_on_the_fleet(self):
        coord, ex = _coord(observer=False, command=LIMIT)
        coord._cycle_fleet_state = _fleet_state(LIMIT, enabled=False)
        await _run(coord)
        ex.command_limit_export.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Observer: the command and the standing row are now told apart
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestObserverTellsACommandFromTheStandingRow:
    async def test_the_command_cycle_publishes_the_decision_and_a_command_row(self):
        coord, ex = _coord(observer=True, command=LIMIT)
        await _run(coord)
        ex.command_limit_export.assert_not_awaited()
        rows = coord._export_withheld
        assert len(rows) == 1 and rows[0]["standing"] is False, rows
        assert rows[0]["service"] == "huawei_solar.set_zero_power_grid_connection"
        pub = coord._surplus_controller.observer_decisions.get("export_guard") or {}
        assert pub.get("action") == "limit_export"
        assert "cutting to 0 W" in (pub.get("reason") or ""), pub

    async def test_a_quiet_engaged_cycle_publishes_a_standing_row(self):
        coord, ex = _coord(observer=True, command=QUIET)
        await _run(coord)
        rows = coord._export_withheld
        assert len(rows) == 1 and rows[0]["standing"] is True, rows
        pub = coord._surplus_controller.observer_decisions.get("export_guard") or {}
        assert "holding the meter shut" in (pub.get("reason") or ""), pub


# ═══════════════════════════════════════════════════════════════════════
# The sibling question (#924): EVERY FleetContext producer carries the axis
# ═══════════════════════════════════════════════════════════════════════

class TestEveryFleetProducerCarriesTheExportAxis:
    REQUIRED = ("export_command", "export_guard_enabled", "sink_verdicts")

    def test_the_battery_pipelines_own_context_threads_them(self):
        from .ast_contracts import call_kwargs
        kwargs = call_kwargs(SEMCoordinator._run_battery_pipeline, "FleetContext")
        assert kwargs, "the pipeline no longer builds its own FleetContext?"
        for kw in kwargs:
            for f in self.REQUIRED:
                assert f in kw, (f, kw)

    def test_every_production_site_threads_them(self):
        """The question no per-function pin can ask: are the SIBLINGS right?
        On 17.09 build_view's site passed and the pipeline's did not."""
        from .ast_contracts import call_sites
        sites = call_sites("FleetContext")
        assert len(sites) >= 2, sites
        for path, line, kw in sites:
            for f in self.REQUIRED:
                assert f in kw, (path, line, f, kw)
