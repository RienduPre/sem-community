"""#967 (D4) — the diagnostics download carries the joint energy plan.

@alexmc1510 sent his download at the issue's request; ``data.coordinator``
held three keys and none of them was the plan. The issue's own diagnosis —
"does ``ev:<id>`` read covered, or a named doubt? are there blocks, and at
which hours?" — could not be answered from it, and the strip he photographed
was reconstructed from the code instead. Now the download carries what the
strip is drawn from: the stamped plan, the per-demand coverage verdict, the
per-charger strip rows and the night need each charger was planned for.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.solar_energy_management.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.fixture
def mock_hass(tmp_path):
    hass = MagicMock()
    hass.config = MagicMock()
    hass.config.config_dir = str(tmp_path)

    async def _executor(func, *args, **kwargs):
        return func(*args, **kwargs)
    hass.async_add_executor_job = _executor
    return hass


@pytest.fixture
def entry():
    entry = MagicMock()
    entry.entry_id = "test_entry_967"
    entry.version = 1
    entry.title = "Solar Energy Management"
    entry.domain = "solar_energy_management"
    entry.data = {}
    entry.options = {}
    return entry


def _coordinator(**attrs):
    coord = MagicMock()
    coord.last_update_success = True
    coord.update_interval = timedelta(seconds=10)
    coord._observer_mode = False
    coord._load_manager = None
    coord._energy_dashboard_config = None
    coord.data = attrs.pop("data", {})
    for k, v in attrs.items():
        setattr(coord, k, v)
    return coord


SHADOW = {
    "computed_at": "2026-09-16T14:06:00+02:00", "fits": False,
    "demands": [{"id": "ev:ev_charger", "status": "yields", "planned_kwh": 0.0,
                 "needed_kwh": 19.8, "note": None}],
    "slots": [{"start": "2026-09-16T21:00:00+02:00", "end": "2026-09-16T22:00:00+02:00"}],
    "blocks": [],
}


@pytest.mark.asyncio
async def test_the_download_carries_the_plan_its_coverage_and_the_strip_rows(mock_hass, entry):
    rows = [{"when": "2026-09-16T20:36:00+02:00", "kind": "night_open"}]
    coord = _coordinator(
        data={"charger_ev_charger_today_plan": rows, "solar_power": 1.0},
        _energy_plan_shadow=SHADOW,
        _plan_coverage_view=lambda: {"ev:ev_charger": "verdict yields"},
        _night_target_per_charger_map={"ev_charger": 19.8},
    )
    entry.runtime_data = coord        # the download reads the coordinator from here
    mock_hass.data = {"solar_energy_management": {entry.entry_id: coord}}
    out = (await async_get_config_entry_diagnostics(mock_hass, entry))["coordinator"]
    assert out["energy_plan"]["demands"][0]["status"] == "yields"
    assert out["plan_coverage"] == {"ev:ev_charger": "verdict yields"}
    assert out["per_charger_plans"] == {"ev_charger": rows}
    assert out["night_targets"] == {"ev_charger": 19.8}
    json.dumps(out)      # everything the card reads must survive the download


@pytest.mark.asyncio
async def test_no_plan_reads_as_none_never_as_a_crash(mock_hass, entry):
    """A coordinator that has not stamped a night — or a rig-shaped stand-in
    whose attributes are all MagicMocks — must still produce a download."""
    coord = _coordinator(data={})
    entry.runtime_data = coord        # the download reads the coordinator from here
    mock_hass.data = {"solar_energy_management": {entry.entry_id: coord}}
    out = (await async_get_config_entry_diagnostics(mock_hass, entry))["coordinator"]
    assert out["energy_plan"] is None
    assert out["plan_coverage"] is None
    assert out["per_charger_plans"] == {}
    assert out["night_targets"] is None
    json.dumps(out)


@pytest.mark.asyncio
async def test_a_coverage_view_that_raises_is_none_not_a_failed_download(mock_hass, entry):
    def _boom():
        raise RuntimeError("no gate yet")
    coord = _coordinator(data={}, _plan_coverage_view=_boom)
    entry.runtime_data = coord        # the download reads the coordinator from here
    mock_hass.data = {"solar_energy_management": {entry.entry_id: coord}}
    out = (await async_get_config_entry_diagnostics(mock_hass, entry))["coordinator"]
    assert out["plan_coverage"] is None
