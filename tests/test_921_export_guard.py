"""#955 — a limit at the meter, mirroring the peak guard. Pure; the clock is fed.

The intents this tracker's commands become, and the one seam that writes
them, live in ``test_921_export_seam.py`` — the tracker itself knows nothing
about either.
"""
from custom_components.solar_energy_management.coordinator.export_guard import (
    ENGAGE_HOLD_S, EXPORT_EPS_W, RELEASE_HOLD_S, ExportGuard,
)
from custom_components.solar_energy_management.coordinator.sink_verdicts import CLOSED, OPEN


def _run(guard, seq):
    """seq: [(t, verdict_state, export_w)] → list of intents."""
    return [guard.update(t, state, export_w).intent for t, state, export_w in seq]


class TestHysteresis:
    def test_engages_only_after_the_closed_verdict_holds(self):
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S - 1, CLOSED, 500.0),
                       (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        assert out == [None, None, "limit_export"]
        assert g.state == "engaged"

    def test_a_flapping_verdict_never_engages(self):
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 500.0), (60, OPEN, 500.0),
                       (120, CLOSED, 500.0), (180, OPEN, 500.0)])
        assert set(out) == {None}
        assert g.state != "engaged"

    def test_releases_only_after_open_holds(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        out = _run(g, [(1000, OPEN, 0.0), (1000 + RELEASE_HOLD_S - 1, OPEN, 0.0),
                       (1000 + RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert out == [None, None, "release_export"]
        assert g.state == "idle"

    def test_engaged_once_is_engaged_until_released(self):
        """No second LIMIT_EXPORT while already engaged — one write, not one a cycle."""
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        assert _run(g, [(ENGAGE_HOLD_S + 10, CLOSED, 900.0),
                        (ENGAGE_HOLD_S + 20, CLOSED, 900.0)]) == [None, None]

    def test_the_holds_are_configurable(self):
        g = ExportGuard(engage_hold_s=10.0, release_hold_s=20.0)
        assert _run(g, [(0, CLOSED, 500.0), (11, CLOSED, 500.0)]) == [None, "limit_export"]
        assert _run(g, [(100, OPEN, 0.0), (121, OPEN, 0.0)]) == [None, "release_export"]


class TestLastNotFirst:
    def test_no_measured_export_no_engagement(self):
        """The sinks absorbed everything this cycle — nothing to clip."""
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, EXPORT_EPS_W - 1)])
        assert out == [None, None]
        assert g.state == "holding"

    def test_export_reappearing_while_closed_engages(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 0.0), (ENGAGE_HOLD_S + 1, CLOSED, 0.0)])
        assert g.update(ENGAGE_HOLD_S + 2, CLOSED, 900.0).intent == "limit_export"

    def test_a_blind_meter_is_not_export(self):
        """#906: an unreadable meter is None, never a number — and never a reason to cut."""
        g = ExportGuard()
        out = _run(g, [(0, CLOSED, None), (ENGAGE_HOLD_S + 1, CLOSED, None)])
        assert out == [None, None] and g.state == "holding"

    def test_a_blind_meter_says_so_not_that_the_sinks_absorbed_everything(self):
        """Review: 'could not measure' must never read as 'measured zero' (class 86)."""
        g = ExportGuard()
        _run(g, [(0, CLOSED, None), (ENGAGE_HOLD_S + 1, CLOSED, None)])
        assert "unreadable" in g.reason


class TestRefusal:
    def test_refused_is_sticky_until_release(self):
        g = ExportGuard()
        _run(g, [(0, CLOSED, 500.0), (ENGAGE_HOLD_S + 1, CLOSED, 500.0)])
        g.report_refused("no export control on this brand")
        assert g.state == "refused"
        assert g.update(ENGAGE_HOLD_S + 30, CLOSED, 500.0).intent is None
        assert g.update(5000, OPEN, 0.0).intent is None
        assert g.update(5000 + RELEASE_HOLD_S + 1, OPEN, 0.0).intent is None
        assert g.state == "idle"

    def test_three_refusals_ask_for_a_repair(self):
        g = ExportGuard()
        for _ in range(3):
            g.report_refused("adapter raised")
        assert g.repair_wanted is True

    def test_a_release_clears_the_refusal_count(self):
        g = ExportGuard()
        g.report_refused("x"); g.report_refused("x")
        _run(g, [(0, OPEN, 0.0), (RELEASE_HOLD_S + 1, OPEN, 0.0)])
        assert g.state == "idle" and g.repair_wanted is False


class TestUnknownIsNotHostile:
    def test_open_from_the_start_does_nothing(self):
        g = ExportGuard()
        assert _run(g, [(0, OPEN, 5000.0), (600, OPEN, 5000.0)]) == [None, None]
        assert g.state == "idle"

    def test_held_is_not_a_grid_state(self):
        """Only CLOSED counts; anything else is the open side."""
        g = ExportGuard()
        assert _run(g, [(0, "held", 5000.0), (600, "held", 5000.0)]) == [None, None]
