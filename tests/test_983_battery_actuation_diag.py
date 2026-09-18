"""#983 — a battery-control report answers itself from the download.

@RienduPre, 2026-09-18 (2× Sessy, Growatt, beta.29): "surplus is going to grid
while battery is not full". His download carried 460 state keys, a full
per-charger ``ev_actuation`` block — and nothing at all about the battery's
control surface. The two facts that would have settled it were held by SEM and
published nowhere:

* what the power-strategy select READS versus what SEM believes it set (the
  #978 defect: a flip cached as done on a normal service return), and
* whether the setpoint write was refused, and how often (the #840 withdrawal
  that had already fired in his log at 10:00).

So the diagnose payload carries the battery half of the #548 actuation truth.
Pinned here on a fabricated adapter, because the cases worth having are the
ones nobody can reproduce on demand: a select stuck on ``nom``, an entity that
has vanished, an adapter that raises.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.battery_diag import (
    battery_actuation_diag,
)

SEL, SP = "select.sessy_1_power_strategy", "number.sessy_1_power_setpoint"


def _hass(states=None):
    hass = MagicMock()
    st = dict(states or {})
    hass.states.get = MagicMock(
        side_effect=lambda eid: SimpleNamespace(state=st[eid]) if eid in st else None)
    return hass


class _Adapter:
    """The fields the generic adapter really carries (see generic.py)."""

    def __init__(self, **kw):
        self._strategy_entity = SEL
        self._strategy_active, self._strategy_idle = "api", "eco"
        self._strategy_self_consume, self._strategy_off = "nom", "idle"
        self._last_strategy = None
        self._strategy_sent = {}
        self._took_control = False
        self._restore_strategy = None
        self._force_discharge_entity = SP
        self._last_force_discharge_w = 0.0
        self._last_force_discharge_attempt_w = None
        self._force_discharge_failures = 0
        self._fd_unit_refusals = 0
        self._discharge_control_entity = ""
        self._last_discharge_limit_w = -1.0
        self._last_error = None
        self.write_not_taken_strikes = 0
        self.last_verified_entity = ""
        self.last_unverified_entity = ""
        self.last_unverified_wanted = ""
        self.last_unverified_seen = ""
        self.last_intent = None
        self.supports_forced_discharge = True
        self.supports_forced_charge = True
        for k, v in kw.items():
            setattr(self, k, v)


def _coord(adapters):
    return SimpleNamespace(_battery_adapters=adapters)


@pytest.mark.unit
class TestTheTwoFactsThatAnswerTheReport:
    def test_it_says_what_the_select_reads_and_what_sem_believes(self):
        """The #978 shape: SEM thinks ``api``, the select is still on ``nom``."""
        ad = _Adapter(_last_strategy="api", _took_control=True,
                      _strategy_sent={"api": 0.0})
        out = battery_actuation_diag(
            _hass({SEL: "nom", SP: "0"}), _coord({"primary": ad}))
        s = out["primary"]["strategy"]
        assert s["entity"] == SEL
        assert (s["reads"], s["sem_believes"]) == ("nom", "api")
        assert s["values"]["active"] == "api" and s["values"]["self_consume"] == "nom"
        assert s["sem_took_control"] is True
        assert "api" in s["sent_not_seen"] and s["sent_not_seen"]["api"] >= 0.0

    def test_it_says_the_setpoint_was_refused_and_how_often(self):
        """His 10:00 log: both setpoints refused 3× → capability withdrawn."""
        ad = _Adapter(_force_discharge_failures=3, _fd_unit_refusals=2,
                      supports_forced_discharge=False,
                      _last_force_discharge_attempt_w=1700.0,
                      _last_error="write_force_discharge failed")
        out = battery_actuation_diag(
            _hass({SEL: "nom", SP: "0"}), _coord({"primary": ad}))
        sp = out["primary"]["setpoint"]
        assert (sp["entity"], sp["reads"]) == (SP, "0")
        assert sp["device_refusals"] == 3 and sp["unit_refusals"] == 2
        assert sp["last_attempt_w"] == 1700.0
        assert out["primary"]["verdicts"]["supports_forced_discharge"] is False
        assert out["primary"]["last_error"] == "write_force_discharge failed"

    def test_the_write_ledger_rides_along(self):
        """#915's read-back verdict, named — the Repair's own evidence."""
        ad = _Adapter(write_not_taken_strikes=2, last_unverified_entity=SEL,
                      last_unverified_wanted="api", last_unverified_seen="nom")
        v = battery_actuation_diag(
            _hass({SEL: "nom"}), _coord({"primary": ad}))["primary"]["verdicts"]
        assert v["write_not_taken_strikes"] == 2
        assert v["last_unverified"] == {"entity": SEL, "wanted": "api", "seen": "nom"}

    def test_every_battery_is_reported_not_just_the_primary(self):
        """Rien has two Sessys; the Repair only ever names one (#978 follow-up),
        so the download must at least SHOW both."""
        out = battery_actuation_diag(
            _hass({SEL: "nom"}), _coord({"b1": _Adapter(), "b2": _Adapter()}))
        assert set(out) == {"b1", "b2"}


@pytest.mark.unit
class TestAbsenceIsSaid:
    def test_a_vanished_entity_reads_missing_not_none(self):
        """#925: "I could not ask" is its own value. A configured entity with
        no state is exactly the fault being hunted."""
        out = battery_actuation_diag(_hass({}), _coord({"primary": _Adapter()}))
        assert out["primary"]["strategy"]["reads"] == "<missing>"
        assert out["primary"]["setpoint"]["reads"] == "<missing>"

    def test_a_battery_without_a_strategy_select_says_none(self):
        ad = _Adapter(_strategy_entity="")
        s = battery_actuation_diag(
            _hass({SP: "0"}), _coord({"primary": ad}))["primary"]["strategy"]
        assert s["entity"] is None and s["reads"] is None

    def test_no_adapter_yet_is_a_note_not_an_empty_dict(self):
        out = battery_actuation_diag(_hass({}), _coord({}))
        assert "note" in out and "not run" in out["note"]
        assert battery_actuation_diag(_hass({}), SimpleNamespace())["note"]

    def test_one_battery_that_raises_never_costs_the_others(self):
        class _Boom:
            """An adapter whose attribute access explodes — a half-built one
            mid-reload. ``getattr(..., default)`` swallows AttributeError, not
            this."""

            def __getattr__(self, name):
                raise RuntimeError("boom")

        out = battery_actuation_diag(
            _hass({SEL: "nom"}), _coord({"bad": _Boom(), "good": _Adapter()}))
        assert "boom" in out["bad"]["error"]
        assert out["good"]["strategy"]["reads"] == "nom"


@pytest.mark.unit
class TestItIsWiredIntoTheService:
    def test_the_diagnose_payload_builds_the_block_for_all_and_battery(self):
        """The wiring, structurally: the service asks this module for the block
        under the sections a battery report is filed from."""
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "__init__.py").read_text()
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", None))
                 == "battery_actuation_diag"]
        assert calls, "the diagnose service never builds the battery block"
        assert 'payload["battery_actuation"]' in src
        i = src.index('payload["battery_actuation"]')
        gate = src.rindex("if section in", 0, i)
        for want in ("all", "battery_zones", "battery_scheduler"):
            assert f'"{want}"' in src[gate:i], f"section {want} misses the block"
