# #955 — put the export cut back on SEM's one track

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The export cut decides in a pure decider beside `decide_battery`, dispatches through one seam beside `actuate_battery`, and is issued ONCE per cycle to ONE house-level adapter — so SEM's three layers hold for the export axis and no second `BatteryDecision` producer exists.

**Architecture:** Exactly the peak guard's shape, which #955 always claimed to mirror. A stateful tracker ticks in the coordinator and puts a value on the fleet state; a PURE function turns that value into an intent; one seam writes it and observer cuts there. Today the tracker, the decision and the dispatch all live in one 76-line coordinator method.

**Tech Stack:** Python 3.13/3.14, HA custom integration, pytest. Tests from the CI layout — abbreviated below as `semtest <file>`:
`rsync -a --delete --exclude=.git --exclude=node_modules /home/sem/sem-arc-921/ /tmp/ha-config-arc/custom_components/solar_energy_management/ && cd /tmp/ha-config-arc && PYTHONPATH=/tmp/ha-config-arc python3.12 -m pytest custom_components/solar_energy_management/tests/<file> -q -p no:warnings`
Lint: `/tmp/venv-ci/bin/ruff check <files>` (0.16.3, the CI pin).

**Branch:** continues on `feature/921-grid-not-always-a-sink` (worktree `/home/sem/sem-arc-921`). **No behaviour change** — this is a re-layering of code that is already live-proven, so every existing test and the .175 evidence carry over unchanged. Any test that has to be *rewritten* rather than *moved* is a signal the refactor changed behaviour; stop and look.

---

## Why (the evidence, so the reader does not have to re-derive it)

| | today | the peak guard (#864), same claim |
|---|---|---|
| tracker | `ExportGuard` in `coordinator._run_export_guard` | `PeakSlotTracker` in `coordinator._compute_peak_slot_allowance` |
| value on the fleet state | — (never leaves the method) | `peak_slot_allowed_w` → `FleetCycleState` → `FleetContext` |
| decision | **in the coordinator** — builds `BatteryDecision`s itself | `decide.py:1150` `clamp_to_peak_slot(result, view)` — **in the decide layer** |
| dispatch | **its own** `await actuate_battery(...)` at `coordinator.py:10871` | the charger's existing seam |

Consequences, all present in the tree right now:

- **Two producers of `BatteryDecision`**: `decide_battery.py` and `coordinator.py`. Two `actuate_battery` call sites: `:7836` (the battery loop) and `:10871` (the guard).
- **The observer-key collision was a symptom, not a bug.** Both producers published under `battery:<id>` and clobbered each other; the fix in `1bfcc1e0` patched the key instead of removing the second producer.
- **Category error:** the export cut is HOUSE-level (one inverter) but rides the PER-BATTERY path — the guard loops every adapter, so a two-battery single-inverter install issues the same service call twice. Huawei's #538 de-dup hides it. `_primary_battery_adapter()` already exists for exactly this, and its own docstring is the lesson: *"One accessor now, and it is the only way in."*

**Why it happened, so it is not repeated:** `decide_battery` returns ONE intent per battery per cycle, and a battery can need `LIMIT_DISCHARGE` *and* an export cut in the same cycle. That is a real constraint. The answer is a separate axis with its own decider — not a coordinator that decides for itself.

**On "last, not first":** the tracker moves to the top of the cycle (beside the peak guard) instead of after the battery loop. That is not a weakening. The rule means the guard clips only export the sinks did not absorb; over a 10 s cycle with minute-scale holds, the meter reading at the top of cycle *N* already reflects what the sinks did in cycle *N−1*. The review accepted the same one-cycle lag for the probe (finding 8, "immaterial under multi-minute hold timers"). Task 4 pins it.

## File structure

| File | Responsibility after this plan |
|---|---|
| `coordinator/export_guard.py` | unchanged — the pure hysteresis tracker + `ExportCommand` |
| `coordinator/decide_export.py` (new) | `decide_export(view) → ExportDecision` — pure, brand-blind |
| `coordinator/actuate_export.py` (new) | `actuate_export(decision, adapter, *, observer, controller)` — one write, observer cuts here, publishes the observer decision |
| `coordinator/charger_types.py` | `ExportDecision`; `export_command` on `FleetCycleState` / `FleetContext`; `BatteryIntent.LIMIT_EXPORT/RELEASE_EXPORT` **removed** |
| `coordinator/actuate_battery.py` | the export branch and the `export:<id>` key **removed** — batteries go back to one axis |
| `coordinator/coordinator.py` | `_compute_export_command(power)` (tick only), the fleet-state field, one `actuate_export` call after the battery loop; `_run_export_guard` **deleted** |
| `tests/test_921_export_decide.py` (new) | the pure decider |
| `tests/test_921_guard_wiring.py`, `test_921_handback.py`, `test_921_export_guard.py` | moved to the new seam; the observer-key tests now assert the seam's own key |
| `tests/test_921_one_track.py` (new) | the structural pins that make the second track unrepresentable |

---

### Task 1: `ExportDecision` + a pure `decide_export`

**Files:** Create `coordinator/decide_export.py`; modify `coordinator/charger_types.py`; test `tests/test_921_export_decide.py`.

- [ ] **Step 1: Write the failing test**

```python
"""#955 — the export cut decides in the DECIDE layer, like everything else.

Pure: same inputs, same decision, no hass, no adapter, no clock of its own.
The tracker (``ExportGuard``) still owns hysteresis; this turns its command
into the intent the seam will write.
"""
from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management.coordinator.charger_types import (
    ExportDecision, ExportIntent,
)
from custom_components.solar_energy_management.coordinator.decide_export import decide_export
from custom_components.solar_energy_management.coordinator.export_guard import (
    LIMIT_EXPORT, RELEASE_EXPORT, ExportCommand,
)


def _view(cmd=None, enabled=True):
    return SimpleNamespace(fleet=SimpleNamespace(export_command=cmd,
                                                 export_guard_enabled=enabled))


class TestTheDecision:
    def test_a_limit_command_becomes_a_limit_intent(self):
        d = decide_export(_view(ExportCommand(LIMIT_EXPORT, 0.0, "closed + 3 kW export")))
        assert d.intent is ExportIntent.LIMIT and d.watts == 0.0
        assert "closed" in d.reason

    def test_a_release_command_becomes_a_release_intent(self):
        d = decide_export(_view(ExportCommand(RELEASE_EXPORT, 0.0, "meter open")))
        assert d.intent is ExportIntent.RELEASE

    def test_no_command_is_no_intent(self):
        assert decide_export(_view(ExportCommand(None, 0.0, "holding"))).intent is ExportIntent.NONE

    def test_an_absent_command_is_no_intent(self):
        """Every install before the first tick, and every rig-shaped stub."""
        assert decide_export(_view(None)).intent is ExportIntent.NONE

    def test_the_switch_off_is_no_intent_whatever_the_command_says(self):
        d = decide_export(_view(ExportCommand(LIMIT_EXPORT, 0.0, "closed"), enabled=False))
        assert d.intent is ExportIntent.NONE

    def test_it_is_pure(self):
        """Called twice on the same view, it answers the same — no state."""
        v = _view(ExportCommand(LIMIT_EXPORT, 0.0, "closed"))
        assert decide_export(v) == decide_export(v)
```

- [ ] **Step 2: Run it and watch it fail** — `semtest tests/test_921_export_decide.py` → `ModuleNotFoundError: decide_export`.

- [ ] **Step 3: The types** (`coordinator/charger_types.py`, beside `BatteryDecision`):

```python
class ExportIntent(Enum):
    """(#955) What ``actuate_export`` should ask the inverter to do. A HOUSE
    axis, not a per-battery one: the meter is one meter."""

    NONE = "none"
    LIMIT = "limit_export"
    RELEASE = "release_export"


@dataclass(frozen=True)
class ExportDecision:
    intent: ExportIntent = ExportIntent.NONE
    watts: float = 0.0          # the cap; 0.0 for a closed meter
    reason: str = ""
```

and `export_command: "Any" = None` + `export_guard_enabled: bool = False` on **`FleetCycleState`** and **`FleetContext`**, beside `peak_slot_allowed_w` (which is the field this one is modelled on).

- [ ] **Step 4: The decider** (`coordinator/decide_export.py`):

```python
"""Pure ``decide_export(view) → ExportDecision`` (#955).

The house's meter limit, decided the way every other SEM control is decided:
a pure function of the view. ``ExportGuard`` (the tracker) owns hysteresis and
"last, not first"; it has already answered *whether* a write is due this cycle
and put its command on the fleet state. This turns that into the intent the
seam will write — and nothing else. No hass, no adapter, no clock.

It exists because the first build decided IN the coordinator and dispatched
from there: two producers of a battery decision, two seams, and an observer
surface where the two clobbered each other under one key.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .charger_types import ExportDecision, ExportIntent
from .export_guard import LIMIT_EXPORT, RELEASE_EXPORT

if TYPE_CHECKING:  # pragma: no cover
    from .charger_types import BatteryView

_BY_COMMAND = {LIMIT_EXPORT: ExportIntent.LIMIT, RELEASE_EXPORT: ExportIntent.RELEASE}


def decide_export(view: "BatteryView") -> ExportDecision:
    """This cycle's export intent. ``NONE`` whenever the guard is off, has no
    command, or is merely holding — the overwhelmingly common case."""
    fleet = getattr(view, "fleet", None)
    if not bool(getattr(fleet, "export_guard_enabled", False)):
        return ExportDecision(reason="export guard off")
    cmd = getattr(fleet, "export_command", None)
    intent = _BY_COMMAND.get(getattr(cmd, "intent", None), ExportIntent.NONE)
    if intent is ExportIntent.NONE:
        return ExportDecision(reason=getattr(cmd, "reason", "") or "no export command")
    return ExportDecision(intent=intent,
                          watts=float(getattr(cmd, "watts", 0.0) or 0.0),
                          reason=str(getattr(cmd, "reason", "")))
```

- [ ] **Step 5: Run** → 6 passed. **Step 6: Commit** `git commit -m "feat(#955): decide_export — the meter limit decides in the decide layer"`

---

### Task 2: `actuate_export` — one seam, and the observer key falls out of it

**Files:** Create `coordinator/actuate_export.py`; test `tests/test_921_export_seam.py`.

- [ ] **Step 1: Write the failing test**

```python
"""#955 — one write, observer cuts here, and the key belongs to the seam.

The first build published the cut under ``battery:<id>`` — the battery's own
key — so the two decisions clobbered each other every cycle and the cut was
invisible on the rig (found live on .175 with the compressed sim). A seam that
owns its key cannot have that bug.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.actuate_export import (
    actuate_export,
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
class TestTheSeam:
    async def test_limit_writes_the_cap(self):
        a = _adapter()
        await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"), a)
        a.command_limit_export.assert_awaited_once_with(0.0)

    async def test_release_writes_the_release(self):
        a = _adapter()
        await actuate_export(ExportDecision(ExportIntent.RELEASE, 0.0, "open"), a)
        a.command_release_export.assert_awaited_once()

    async def test_none_writes_nothing(self):
        a = _adapter()
        await actuate_export(ExportDecision(reason="holding"), a)
        a.command_limit_export.assert_not_awaited()
        a.command_release_export.assert_not_awaited()

    async def test_observer_cuts_the_trigger_and_records_under_the_seams_key(self):
        a = _adapter(); ctl = MagicMock()
        await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"),
                             a, observer=True, controller=ctl)
        a.command_limit_export.assert_not_awaited()
        kw = ctl.publish_observer_decision.call_args.kwargs
        assert kw["key"] == "export_guard"          # never battery:<id>
        assert kw["action"] == "limit_export"
        assert kw["kind"] == "battery"              # what the .175 sim filters on

    async def test_a_brand_without_the_verb_is_a_refusal_not_a_crash(self):
        a = _adapter()
        a.command_limit_export = AsyncMock(side_effect=NotImplementedError("no export control"))
        said = await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "closed"), a)
        assert said and "no export control" in said

    async def test_an_error_is_a_refusal_too(self):
        a = _adapter()
        a.command_limit_export = AsyncMock(side_effect=RuntimeError("modbus timeout"))
        assert "modbus timeout" in (await actuate_export(
            ExportDecision(ExportIntent.LIMIT, 0.0, "closed"), a) or "")

    async def test_a_successful_write_refuses_nothing(self):
        assert await actuate_export(ExportDecision(ExportIntent.LIMIT, 0.0, "c"), _adapter()) is None

    async def test_no_adapter_is_a_refusal(self):
        assert "no adapter" in (await actuate_export(
            ExportDecision(ExportIntent.LIMIT, 0.0, "c"), None) or "")
```

- [ ] **Step 2: Run and watch it fail.**

- [ ] **Step 3: The seam** (`coordinator/actuate_export.py`) — returns the refusal string (or `None`), so the caller can feed `ExportGuard.report_refused` without the seam knowing about the tracker:

```python
"""Pure-dispatch ``actuate_export(decision, adapter)`` (#955).

Mirrors :func:`actuate_battery` for the house's meter limit: one intent, one
adapter method, no branch on brand. Observer mode cuts the trigger here and
records what SEM WOULD command, under **this seam's own key** — the first
build borrowed the battery's key and the two overwrote each other every cycle.

Returns the refusal text when the write could not happen (no adapter, a brand
with no export control, an adapter that raised), else ``None``. The caller
hands that to the guard; the seam itself knows nothing about hysteresis.
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
    if decision.intent is ExportIntent.NONE:
        return None
    if observer:
        if controller is not None:
            try:
                controller.publish_observer_decision(
                    key=OBSERVER_KEY, name="grid export",
                    action=decision.intent.value, power_w=float(decision.watts or 0.0),
                    reason=decision.reason, kind="battery")
            except Exception:  # noqa: BLE001 — the surface never breaks the seam
                pass
        log_on_change(_LOGGER, OBSERVER_KEY, logging.INFO,
                      "OBSERVER · WOULD %s at %.0f W — %s",
                      decision.intent.value.upper(), float(decision.watts or 0.0),
                      decision.reason)
        return None
    if adapter is None:
        return "no adapter to write the export limit"
    try:
        if decision.intent is ExportIntent.LIMIT:
            await adapter.command_limit_export(float(decision.watts or 0.0))
        else:
            await adapter.command_release_export()
    except NotImplementedError as exc:
        return f"export control not available: {exc}"
    except Exception as exc:  # noqa: BLE001 — a refused cut is a state, not a crash
        return f"export control failed: {exc}"
    log_on_change(_LOGGER, OBSERVER_KEY, logging.INFO,
                  "export %s at %.0f W — %s", decision.intent.value,
                  float(decision.watts or 0.0), decision.reason)
    return None
```

- [ ] **Step 4: Run** → 8 passed. **Step 5: Commit** `git commit -m "feat(#955): actuate_export — one write for the house's meter limit, and the seam owns its observer key"`

---

### Task 3: Batteries go back to one axis

**Files:** `coordinator/actuate_battery.py`, `coordinator/charger_types.py`; tests `tests/test_921_export_guard.py`, `tests/test_921_guard_wiring.py`.

- [ ] **Step 1:** Delete from `actuate_battery.py` the `LIMIT_EXPORT/RELEASE_EXPORT` branch, the `_export` key selection added in `1bfcc1e0`, and the `BatteryIntent.LIMIT_EXPORT: decision.export_limit_w` entry in `_observe`'s watts map. Delete `BatteryIntent.LIMIT_EXPORT` / `RELEASE_EXPORT` and `BatteryDecision.export_limit_w` from `charger_types.py`.

- [ ] **Step 2:** Move the affected tests rather than rewriting them: the `TestTheIntentsDispatch` class in `test_921_export_guard.py` and `TestTheCutIsVisibleInObserverMode` in `test_921_guard_wiring.py` are now Task 2's seam tests — delete the duplicates, keeping `test_921_export_seam.py` as the one home. **If a moved test needs its ASSERTION changed (not just its import), stop:** the refactor changed behaviour and that is not the goal.

- [ ] **Step 3: Run** `semtest tests/test_921_export_guard.py tests/test_921_export_seam.py tests/test_actuate_battery*.py tests/test_818*.py tests/test_battery_modes_523.py` → all pass.

- [ ] **Step 4: Commit** `git commit -m "refactor(#955): the battery decision carries one axis again — the export cut has its own"`

---

### Task 4: The coordinator ticks, and nothing more

**Files:** `coordinator/coordinator.py`, `coordinator/build_view.py`; test `tests/test_921_guard_wiring.py` (rewritten around the tick).

- [ ] **Step 1: Write the failing test**

```python
class TestTheCoordinatorOnlyTicks:
    """After the re-layering the coordinator computes a COMMAND (like the peak
    guard computes an allowance) and dispatches one decision through one seam.
    It does not decide, and it does not build a BatteryDecision."""

    def test_the_tick_puts_a_command_on_the_fleet_state(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent == "limit_export"

    def test_the_guard_is_off_no_command(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0, enabled=False)
        SEMCoordinator._compute_export_command(fake, power, now=0.0)
        assert fake._export_command.intent is None

    def test_a_blind_meter_never_commands(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        power.grid_power_unavailable = True
        for t in (0, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent is None

    def test_the_command_rides_the_view(self):
        from tests.ast_contracts import call_kwargs
        from custom_components.solar_energy_management.coordinator import build_view
        kwargs = call_kwargs(build_view.build_charger_view, "FleetContext")
        assert kwargs and "export_command" in kwargs[0]
```

(Use `from .ast_contracts import …` — the package-relative form the other tests use.)

- [ ] **Step 2: Run and watch it fail.**

- [ ] **Step 3: The tick**, beside `_compute_peak_slot_allowance` (`coordinator.py:10675`) and called from the same place in the cycle:

```python
    def _compute_export_command(self, power, *, now=None) -> None:
        """(#955) Tick the export tracker and put its command on the cycle —
        the peak guard's shape exactly (``_compute_peak_slot_allowance`` →
        ``peak_slot_allowed_w``). No decision here and no write: the pure
        ``decide_export`` reads the command off the view and one seam writes it.

        On ordering: the tracker reads the meter at the TOP of the cycle, so
        "last, not first" means it clips only export the sinks did not absorb
        in the previous cycle. Over a 10 s loop with minute-scale holds that is
        the same statement — the review accepted the identical one-cycle lag
        for the #743 probe.
        """
        import time as _time
        from .export_guard import ExportGuard
        from .sink_verdicts import OPEN
        if getattr(self, "_export_guard", None) is None:
            self._export_guard = ExportGuard(
                engage_hold_s=float(self.config.get("export_guard_engage_s", 120) or 120),
                release_hold_s=float(self.config.get("export_guard_release_s", 300) or 300))
            _adopt = getattr(self, "_export_guard_adopt", None)
            if callable(_adopt):
                self.hass.async_create_task(_adopt(self._export_guard))
        enabled = bool(self.config.get("export_guard_enabled", False))
        verdict = (getattr(self, "_sink_verdicts", None) or {}).get("grid_export")
        state = getattr(verdict, "state", OPEN) if enabled else OPEN
        export_w = (None if getattr(power, "grid_power_unavailable", False)
                    else float(getattr(power, "grid_export_power", 0.0) or 0.0))
        self._export_command = self._export_guard.update(
            _time.monotonic() if now is None else now, state, export_w)
```

`_build_fleet_cycle_state` passes `export_command=getattr(self, "_export_command", None)` and `export_guard_enabled=bool(self.config.get("export_guard_enabled", False))`; `build_view.py` copies both onto `FleetContext` beside `sink_verdicts`.

- [ ] **Step 4: The one dispatch**, replacing the whole of `_run_export_guard` (delete it), placed immediately after the per-battery loop in the battery pipeline:

```python
        # (#955) The house's meter limit: ONE decision, ONE seam, ONE adapter.
        # Not per battery — the meter is one meter, and _primary_battery_adapter
        # is "the only way in" for house-level adapter work (its own docstring).
        from .actuate_export import actuate_export
        from .decide_export import decide_export
        _xd = decide_export(view)                     # the last view built this cycle
        _refused = await actuate_export(
            _xd, self._primary_battery_adapter(),
            observer=self._observer_mode, controller=self._surplus_controller)
        if _refused:
            self._export_guard.report_refused(_refused)
        elif _xd.intent is not ExportIntent.NONE and not self._observer_mode:
            _persist = getattr(self, "_export_guard_persist", None)
            if callable(_persist):
                await _persist(_xd.intent is ExportIntent.LIMIT)
        self._export_guard_state = {
            "enabled": bool(self.config.get("export_guard_enabled", False)),
            "state": self._export_guard.state, "reason": self._export_guard.reason,
            "would": (self._export_guard.state if self._observer_mode
                      and self._export_guard.state in ("engaged", "releasing", "refused")
                      else None),
            "repair_wanted": self._export_guard.repair_wanted,
            "verdict": getattr((getattr(self, "_sink_verdicts", None) or {}).get("grid_export"),
                               "reason", "no verdict"),
        }
```

The standing observer entry now comes from the seam (Task 2) on the cycles a command exists; keep publishing the *state* every cycle while engaged by calling `actuate_export` with a `LIMIT` decision only when the guard commands one, and letting the `_export_guard_state` dict carry `would` for the card. **If that reads as two mechanisms again, prefer the seam:** have the tracker re-issue its command while engaged (a one-line change in `ExportGuard.update`'s `engaged` branch) so the seam publishes every cycle and the coordinator publishes nothing.

- [ ] **Step 5:** `export_release_recipes` and `async_release_export_guard` take `_primary_battery_adapter()` instead of looping `_battery_adapters` — one recipe, keyed `"primary"`, so unload/removal hand back once.

- [ ] **Step 6: Run** `semtest tests/test_921_guard_wiring.py tests/test_921_handback.py tests/test_921_sink_scenario.py tests/test_873_cycle_executes.py tests/test_864*.py tests/test_743*.py` → all pass.

- [ ] **Step 7: Commit** `git commit -m "refactor(#955): the coordinator ticks the tracker; decide_export decides; actuate_export writes once"`

---

### Task 5: Make the second track unrepresentable

**Files:** `tests/test_921_one_track.py` (new).

- [ ] **Step 1: Write the test** (it is the whole task)

```python
"""#955 — the structural pins that keep this on one track.

Guido, 16.09, reading the first build: "is this implemented where all the
decisions are taking place, or are we on a second track? I remember having 3
layers in SEM." He was right — the export cut decided in the coordinator and
dispatched itself. These are the guards that make that unrepresentable.
"""
from pathlib import Path

from .ast_contracts import call_sites, symbol_reference_files

ROOT = Path(__file__).resolve().parent.parent


class TestOneProducerPerDecision:
    def test_only_decide_battery_builds_a_battery_decision(self):
        sites = {p for p, _, _ in call_sites("BatteryDecision")}
        assert sites == {"coordinator/decide_battery.py"}, sites

    def test_only_decide_export_builds_an_export_decision(self):
        sites = {p for p, _, _ in call_sites("ExportDecision")}
        assert sites == {"coordinator/decide_export.py"}, sites


class TestOneSeamPerAxis:
    def test_actuate_battery_has_one_production_call_site(self):
        assert {p for p, _, _ in call_sites("actuate_battery")} == {"coordinator/coordinator.py"}

    def test_actuate_export_has_one_production_call_site(self):
        sites = [s for s in call_sites("actuate_export")]
        assert len(sites) == 1 and sites[0][0] == "coordinator/coordinator.py", sites

    def test_the_observer_key_is_named_once(self):
        """The key belongs to the seam; nothing else may spell it."""
        assert symbol_reference_files("OBSERVER_KEY") <= {"coordinator/actuate_export.py"}


class TestTheDecidersArePure:
    def test_neither_decider_touches_hass_or_an_adapter(self):
        for mod in ("decide_battery.py", "decide_export.py"):
            src = (ROOT / "coordinator" / mod).read_text(encoding="utf-8")
            for forbidden in ("hass.", "async_call(", "await ", "_battery_adapters"):
                assert forbidden not in src, f"{mod} reaches outside the decide layer: {forbidden}"


class TestTheHouseAxisIsHouseLevel:
    def test_the_cut_goes_to_one_adapter_not_every_battery(self):
        """A two-battery single-inverter install must not issue the cut twice."""
        import inspect
        from custom_components.solar_energy_management.coordinator.coordinator import (
            SEMCoordinator,
        )
        for fn in (SEMCoordinator.export_release_recipes,
                   SEMCoordinator.async_release_export_guard):
            src = inspect.getsource(fn)
            assert "_primary_battery_adapter" in src, fn.__name__
            assert "_battery_adapters" not in src, f"{fn.__name__} still loops every battery"
```

`call_sites` returns `(relative_path, lineno, [kwargs])` and skips `tests/`; confirm the tuple shape before relying on it (`grep -n "def call_sites" -A 12 tests/ast_contracts.py`). If `symbol_reference_files` takes different arguments, read its signature — do not guess.

- [ ] **Step 2: Run** — expect real failures first (they are the point), then fix what they name until green.

- [ ] **Step 3: Commit** `git commit -m "test(#955): the second track is now unrepresentable — one producer, one seam, pure deciders, one adapter"`

---

### Task 6: Re-prove on .175, and write it down

- [ ] **Step 1: Suite + lint.** `semtest tests/ -q -rf` green; `/tmp/venv-ci/bin/ruff check .` clean; push and wait for CI on PR #965.

- [ ] **Step 2: The same live episode, on the new layering.** Deploy (`SRC=/home/sem/sem-arc-921 ~/bin/sem-deploy-175.sh`), stage export through the split-pair override (`grid_import_power_entity` → the 6000 W entity, `grid_export_power_entity` → the 0 W entity, **both**, then reload the entry — see `reference_175_harness.md`), hold the feed-in at −0.05, `export_guard_enabled: true`, `export_guard_engage_s: 60`. Read the rig's own surfaces with `~/bin/sem-sim-compress.sh 10.10.20.175 18.0 20`. Expect exactly what the first build produced, from the new seam:

```
WOULD export_guard    limit_export     | export cut holding — the meter is closed
WOULD battery:primary limit_discharge  | …
```

then flip the feed-in positive and watch `releasing → idle` with the entry absent once idle. **Restore:** clear the four override keys, delete the synthetic entities, reload.

- [ ] **Step 3: Update the record.** Append the outcome to `~/claude-jobs/challenge-feature-921-grid-not-always-a-sink.md`; add one line to `docs/BUG_CLASSES.md` if the second-track shape deserves a class of its own (*"a new control that decides in the orchestrator instead of the decide layer — two producers of one decision, discovered as an observer-surface collision"*); note in `docs/ARCHITECTURE.md` that the export axis has its own decider and seam beside the battery's.

- [ ] **Step 4: Commit** `git commit -m "docs(#955): the export axis has its own decider and seam — the three layers hold"`

---

## Before merge

- Full suite green on the merge result; ruff clean; CI green (3.13 + 3.14).
- The challenge record carries both the ruflo round and both live rounds.
- **The judgement that matters:** open `coordinator/coordinator.py` and ask of the export axis the question Guido asked — *is the decision here, or in the decide layer?* The answer must be visible without running anything.
- Merge on Guido's word with `SEM_FEAT_OK`.

## Cost and risk

**Cost:** 6 tasks, ~4 files touched plus test moves. No behaviour change — which is also the risk: a refactor with no behaviour change is one where a mistake is silent. Task 5's structural pins and Task 6's live re-proof are what make it visible.

**What does NOT change:** the verdicts, the tracker's hysteresis and "last, not first", the adapters, the persistence and hand-back semantics, the surface, the plan rows, the 16 languages. All of it is already live-proven and carries over.

**If the refactor is not taken:** the arc still works — it is proven on real hardware — but SEM gains a second place where a decision is made, and the next feature that needs the same shape will copy it. That is the cost to weigh, and it is Guido's call, not mine.
