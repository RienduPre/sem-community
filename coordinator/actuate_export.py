"""Pure-dispatch ``actuate_export(decision, adapter)`` (#955).

The seam for the house's meter limit, mirroring :func:`actuate_battery`: one
intent, one adapter method, no branch on brand. Observer mode cuts the trigger
here and records what SEM WOULD command.

**The key belongs to this seam.** The first build borrowed the battery's
(``battery:<id>``), so the export cut and the battery's own discharge decision
overwrote each other every cycle and the cut was invisible on the rig — found
live on .175 with the compressed sun sim, for a feature whose whole promise is
that it is holding the meter shut (#855: a case is judged on what would hit
the wire, so the wire must be readable).

Returns the refusal text when the write could not happen (no adapter, a brand
with no export control, an adapter that raised), else ``None``. The caller
hands that to the guard; the seam knows nothing about hysteresis. Observer mode
never refuses: nothing was attempted, so nothing was declined.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from .charger_types import ExportIntent
from ..utils.log_gate import log_on_change

if TYPE_CHECKING:  # pragma: no cover
    from .battery_adapters.base import BatteryControlAdapter
    from .charger_types import ExportDecision

_LOGGER = logging.getLogger(__name__)

#: The one key this axis publishes under. A house has one meter.
OBSERVER_KEY = "export_guard"


async def actuate_export(decision: "ExportDecision",
                         adapter: "Optional[BatteryControlAdapter]", *,
                         observer: bool = False,
                         controller=None) -> Optional[str]:
    """Apply this cycle's export decision. See the module docstring."""
    if decision.intent is ExportIntent.NONE:
        return None

    watts = float(decision.watts or 0.0)
    if observer:
        # (#764) the decision still ran for real; this seam only records the
        # command it WOULD send, under its own key.
        if controller is not None:
            try:
                controller.publish_observer_decision(
                    key=OBSERVER_KEY, name="grid export",
                    action=decision.intent.value, power_w=watts,
                    reason=decision.reason, kind="battery")
            except Exception:  # noqa: BLE001 — the surface never breaks the seam
                pass
        log_on_change(   # (#762) transition-gated
            _LOGGER, OBSERVER_KEY, logging.INFO,
            "OBSERVER · WOULD %s at %.0f W — %s",
            decision.intent.value.upper(), watts, decision.reason)
        return None

    if adapter is None:
        return "no adapter can write the export limit on this install"
    try:
        if decision.intent is ExportIntent.LIMIT:
            await adapter.command_limit_export(watts)
        else:
            await adapter.command_release_export()
    except NotImplementedError as exc:
        return f"export control not available: {exc}"
    except Exception as exc:  # noqa: BLE001 — a refused cut is a state, not a crash
        return f"export control failed: {exc}"
    log_on_change(   # (#762) transition-gated
        _LOGGER, OBSERVER_KEY, logging.INFO,
        "export %s at %.0f W — %s", decision.intent.value, watts, decision.reason)
    return None
