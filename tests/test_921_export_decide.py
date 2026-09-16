"""#955 — the export cut decides in the DECIDE layer, like the other two axes.

Pure: same inputs, same decision, no hass, no adapter, no clock of its own.
``ExportGuard`` (the tracker) still owns hysteresis and "last, not first"; it
has already answered *whether* a write is due and left its command on the
fleet. This turns that into the intent the seam will write, and nothing else.

It takes the **fleet**, not a ``BatteryView``: the meter is a house quantity
and no battery owns it. (The first draft of the plan wrote ``decide_export(view)``
and leaned on whatever the per-battery loop had last assigned — which survives
only because ``battery_items`` always gets a synthetic ``"primary"`` entry.)
"""
from types import SimpleNamespace

from custom_components.solar_energy_management.coordinator.charger_types import (
    ExportDecision, ExportIntent,
)
from custom_components.solar_energy_management.coordinator.decide_export import (
    decide_export,
)
from custom_components.solar_energy_management.coordinator.export_guard import (
    LIMIT_EXPORT, RELEASE_EXPORT, ExportCommand,
)


def _fleet(cmd=None, enabled=True):
    return SimpleNamespace(export_command=cmd, export_guard_enabled=enabled)


class TestTheDecision:
    def test_a_limit_command_becomes_a_limit_intent(self):
        d = decide_export(_fleet(ExportCommand(LIMIT_EXPORT, 0.0, "closed + 3 kW export")))
        assert d.intent is ExportIntent.LIMIT and d.watts == 0.0
        assert "closed" in d.reason

    def test_a_release_command_becomes_a_release_intent(self):
        d = decide_export(_fleet(ExportCommand(RELEASE_EXPORT, 0.0, "meter open")))
        assert d.intent is ExportIntent.RELEASE

    def test_a_watt_cap_is_carried(self):
        d = decide_export(_fleet(ExportCommand(LIMIT_EXPORT, 1500.0, "cap")))
        assert d.watts == 1500.0

    def test_no_command_is_no_intent(self):
        """The overwhelmingly common cycle: the guard is merely holding."""
        d = decide_export(_fleet(ExportCommand(None, 0.0, "holding")))
        assert d.intent is ExportIntent.NONE and "holding" in d.reason

    def test_an_absent_command_is_no_intent(self):
        """Every install before the first tick, and every rig-shaped stub."""
        assert decide_export(_fleet(None)).intent is ExportIntent.NONE

    def test_an_absent_fleet_is_no_intent(self):
        assert decide_export(None).intent is ExportIntent.NONE

    def test_the_switch_off_is_no_intent_whatever_the_command_says(self):
        d = decide_export(_fleet(ExportCommand(LIMIT_EXPORT, 0.0, "closed"), enabled=False))
        assert d.intent is ExportIntent.NONE and "off" in d.reason


class TestItIsPure:
    def test_called_twice_it_answers_the_same(self):
        f = _fleet(ExportCommand(LIMIT_EXPORT, 0.0, "closed"))
        assert decide_export(f) == decide_export(f)

    def test_it_mutates_nothing(self):
        cmd = ExportCommand(LIMIT_EXPORT, 0.0, "closed")
        f = _fleet(cmd)
        decide_export(f)
        assert f.export_command is cmd and f.export_guard_enabled is True

    def test_the_decision_is_frozen(self):
        import dataclasses
        assert dataclasses.fields(ExportDecision)
        d = decide_export(_fleet(ExportCommand(LIMIT_EXPORT, 0.0, "c")))
        try:
            d.watts = 99.0
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("ExportDecision must be frozen — a decision is a value")

# The "decide_export reaches nothing outside" claim used to live here as a
# substring search over the module's own text. It is made properly — over the
# parsed tree, for all three deciders at once — in
# tests/test_921_one_track.py::TestTheDecidersStayInTheDecideLayer. One
# producer of a claim, here too (#924/#925).
