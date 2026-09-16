"""#955 wiring — the coordinator TICKS, and the probe holds while engaged.

After the 16.09 re-layering the coordinator does not decide and does not
write: ``_compute_export_command`` feeds the tracker the cycle's verdict and
the export the meter still shows, and leaves an ``ExportCommand`` on the
cycle. ``decide_export(fleet)`` turns that into an intent and
``actuate_export`` writes it once — both tested in their own files. What is
left here is the tick, the probe hold, and the capability selector.

Exercised unbound on a bare fake, the #820 inert-at-the-wire pattern.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from custom_components.solar_energy_management.coordinator.coordinator import SEMCoordinator
from custom_components.solar_energy_management.coordinator.export_guard import ExportGuard
from custom_components.solar_energy_management.coordinator.sink_verdicts import (
    CLOSED, OPEN, SinkVerdict,
)


def _fake(engaged=False, verdict=OPEN, export_w=0.0, enabled=True, observer=False):
    hass = MagicMock(); hass.services.async_call = AsyncMock()
    g = ExportGuard()
    if engaged:
        g.state = "engaged"
    adapter = MagicMock()
    adapter.command_limit_export = AsyncMock()
    adapter.command_release_export = AsyncMock()
    adapter._last_error = None
    ctl = MagicMock()
    fake = SimpleNamespace(
        hass=hass,
        config={"export_guard_enabled": enabled, "curtailment_probe_enabled": True,
                "export_guard_engage_s": 120, "export_guard_release_s": 300},
        _observer_mode=observer, _export_guard=g,
        _sink_verdicts={"grid_export": SinkVerdict("grid_export", verdict, "t")},
        _battery_adapters={"b1": adapter},
        _surplus_controller=ctl,
        _curtailment_last=None,
    )
    power = SimpleNamespace(grid_export_power=export_w, solar_power=0.0, home_consumption_power=0.0,
                            battery_charge_power=0.0, ev_power=0.0, ev_connected=False,
                            grid_power_unavailable=False, grid_import_power=0.0,
                            inputs_degraded=False)
    return fake, power, adapter, ctl


class TestTheProbeHolds:
    def test_engaged_guard_holds_the_probe(self):
        fake, power, _, _ = _fake(engaged=True)
        assert SEMCoordinator._curtailment_grant_w(fake, power) == 0.0
        assert fake._curtailment_last["state"] == "held_by_export_guard"


class TestTheTick:
    """It computes a COMMAND. It does not decide and it does not write."""

    def test_a_closed_meter_with_export_commands_a_cut_after_the_hold(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 60, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent == "limit_export"
        assert fake._export_guard.state == "engaged"

    def test_before_the_hold_there_is_no_command(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        SEMCoordinator._compute_export_command(fake, power, now=0.0)
        assert fake._export_command.intent is None
        assert fake._export_guard.state == "holding"

    def test_the_guard_off_means_no_command(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0, enabled=False)
        for t in (0, 200, 400):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent is None
        assert fake._export_guard.state == "idle"

    def test_a_blind_meter_never_commands(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        power.grid_power_unavailable = True
        for t in (0, 60, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent is None
        assert "unreadable" in fake._export_guard.reason

    def test_a_reopened_meter_commands_a_release_only_after_a_real_cut(self):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        fake._sink_verdicts = {"grid_export": SinkVerdict("grid_export", OPEN, "t")}
        for t in (1000, 1400):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        assert fake._export_command.intent == "release_export"

    def test_the_tick_writes_nothing(self):
        fake, power, adapter, ctl = _fake(verdict=CLOSED, export_w=3000.0)
        for t in (0, 60, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        adapter.command_limit_export.assert_not_awaited()
        ctl.publish_observer_decision.assert_not_called()


class TestTheCapabilitySelector:
    """#531 shape: the adapter that can cut is not always the first one."""

    def _adapters(self, *recipes):
        from unittest.mock import MagicMock
        out = {}
        for i, rec in enumerate(recipes):
            a = MagicMock(); a.export_release_recipe = MagicMock(return_value=rec)
            out[f"b{i}"] = a
        return out

    def test_it_prefers_the_adapter_that_can_undo_its_own_cut(self):
        ads = self._adapters(None, {"domain": "huawei_solar", "service": "reset", "data": {}})
        fake = SimpleNamespace(_battery_adapters=ads)
        assert SEMCoordinator._export_control_adapter(fake) is ads["b1"]

    def test_an_adapter_that_raises_is_skipped_not_fatal(self):
        ads = self._adapters(None, {"domain": "d", "service": "s", "data": {}})
        ads["b0"].export_release_recipe = MagicMock(side_effect=RuntimeError("no"))
        fake = SimpleNamespace(_battery_adapters=ads)
        assert SEMCoordinator._export_control_adapter(fake) is ads["b1"]

    def test_no_adapters_is_none(self):
        assert SEMCoordinator._export_control_adapter(SimpleNamespace(_battery_adapters={})) is None

    def test_a_single_battery_install_falls_back_to_the_primary(self):
        ads = self._adapters(None)
        fake = SimpleNamespace(
            _battery_adapters=ads,
            _primary_battery_adapter=lambda: ads["b0"])
        assert SEMCoordinator._export_control_adapter(fake) is ads["b0"]


class TestTheCardSeesAStandingState:
    """Live on .175: the guard ENGAGED and a person watching saw one flash and
    then silence. Two surfaces, two jobs — ``actuate_export`` publishes the
    COMMAND under its own key (and ``observer_decisions`` keeps it, since the
    map carries the current would-state until overwritten); this is the LIVE
    state, refreshed every cycle, which is what makes "holding the meter shut
    right now" readable."""

    def _engaged(self, observer=True):
        fake, power, _, _ = _fake(verdict=CLOSED, export_w=3000.0, observer=observer)
        for t in (0, 60, 130):
            SEMCoordinator._compute_export_command(fake, power, now=float(t))
        return fake

    def test_an_engaged_cut_says_so_on_a_quiet_later_cycle(self):
        fake = self._engaged()
        SEMCoordinator._compute_export_command(fake, _fake()[1], now=200.0)
        assert fake._export_command.intent is None      # no new command this cycle
        SEMCoordinator._publish_export_guard_state(fake)
        assert fake._export_guard_state["state"] == "engaged"
        assert fake._export_guard_state["would"] == "engaged", "the flash became silence"

    def test_a_live_install_reports_no_would(self):
        """``would`` is an observer-mode word; a live install is DOING it."""
        fake = self._engaged(observer=False)
        SEMCoordinator._publish_export_guard_state(fake)
        assert fake._export_guard_state["would"] is None
        assert fake._export_guard_state["state"] == "engaged"

    def test_an_idle_guard_carries_no_would(self):
        fake, power, _, _ = _fake(verdict=OPEN, export_w=0.0, observer=True)
        SEMCoordinator._compute_export_command(fake, power, now=0.0)
        SEMCoordinator._publish_export_guard_state(fake)
        assert fake._export_guard_state["state"] == "idle"
        assert fake._export_guard_state["would"] is None

    def test_no_guard_yet_publishes_nothing(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(_export_guard=None)
        SEMCoordinator._publish_export_guard_state(fake)
        assert not hasattr(fake, "_export_guard_state")
