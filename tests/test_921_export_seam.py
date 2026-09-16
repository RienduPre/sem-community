"""#955 — one write for the house's meter limit, and the seam owns its key.

Mirrors ``actuate_battery``: one intent, one adapter method, no branch on
brand, observer cuts the trigger here. The key matters as much as the write:
the first build published the cut under ``battery:<id>`` — the battery's own
key — so the two decisions clobbered each other every cycle and the cut was
invisible on the rig (found live on .175 with the compressed sun sim). A seam
that owns its key cannot have that bug.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.actuate_export import (
    OBSERVER_KEY, actuate_export,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ExportDecision, ExportIntent,
)


def _adapter():
    a = MagicMock()
    a.command_limit_export = AsyncMock()
    a.command_release_export = AsyncMock()
    a._last_error = None
    return a


@pytest.mark.asyncio
class TestTheWrite:
    async def test_limit_writes_the_cap(self):
        a = _adapter()
        assert await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"), a) is None
        a.command_limit_export.assert_awaited_once_with(0.0)

    async def test_a_watt_cap_is_passed_through(self):
        a = _adapter()
        await actuate_export(ExportDecision(ExportIntent.LIMIT, 1500.0, "cap"), a)
        a.command_limit_export.assert_awaited_once_with(1500.0)

    async def test_release_writes_the_release(self):
        a = _adapter()
        await actuate_export(ExportDecision(ExportIntent.RELEASE, 0.0, "open"), a)
        a.command_release_export.assert_awaited_once()

    async def test_none_writes_nothing(self):
        """The overwhelmingly common cycle must cost one enum comparison."""
        a = _adapter()
        await actuate_export(ExportDecision(reason="holding"), a)
        a.command_limit_export.assert_not_awaited()
        a.command_release_export.assert_not_awaited()


@pytest.mark.asyncio
class TestObserverCutsHere:
    async def test_it_calls_nothing_and_records_under_the_seams_own_key(self):
        a = _adapter(); ctl = MagicMock()
        await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"),
                             a, observer=True, controller=ctl)
        a.command_limit_export.assert_not_awaited()
        kw = ctl.publish_observer_decision.call_args.kwargs
        assert kw["key"] == OBSERVER_KEY == "export_guard"   # never battery:<id>
        assert kw["action"] == "limit_export"
        assert kw["kind"] == "battery"          # what sem-sim-compress.sh filters on

    async def test_a_release_is_recorded_too(self):
        a = _adapter(); ctl = MagicMock()
        await actuate_export(ExportDecision(ExportIntent.RELEASE, 0.0, "open"),
                             a, observer=True, controller=ctl)
        a.command_release_export.assert_not_awaited()
        assert ctl.publish_observer_decision.call_args.kwargs["action"] == "release_export"

    async def test_nothing_is_recorded_for_no_intent(self):
        ctl = MagicMock()
        await actuate_export(ExportDecision(reason="holding"), _adapter(),
                             observer=True, controller=ctl)
        ctl.publish_observer_decision.assert_not_called()

    async def test_a_broken_surface_never_breaks_the_seam(self):
        ctl = MagicMock()
        ctl.publish_observer_decision = MagicMock(side_effect=RuntimeError("card gone"))
        assert await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "c"),
                                    _adapter(), observer=True, controller=ctl) is None

    async def test_observer_never_reports_a_refusal(self):
        """Nothing was attempted, so nothing was refused — the guard must not
        latch `refused` on a rig that writes nothing by design."""
        assert await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "c"),
                                    None, observer=True) is None


@pytest.mark.asyncio
class TestRefusalIsAValue:
    async def test_a_brand_without_the_verb(self):
        a = _adapter()
        a.command_limit_export = AsyncMock(side_effect=NotImplementedError("no export control"))
        said = await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"), a)
        assert said and "no export control" in said

    async def test_an_adapter_that_raises(self):
        a = _adapter()
        a.command_release_export = AsyncMock(side_effect=RuntimeError("modbus timeout"))
        said = await actuate_export(ExportDecision(ExportIntent.RELEASE, 0.0, "open"), a)
        assert said and "modbus timeout" in said

    async def test_no_adapter_at_all(self):
        said = await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "c"), None)
        assert said and "no adapter" in said

    async def test_a_successful_write_refuses_nothing(self):
        assert await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "c"),
                                    _adapter()) is None

    async def test_no_intent_never_refuses_even_without_an_adapter(self):
        assert await actuate_export(ExportDecision(reason="holding"), None) is None
