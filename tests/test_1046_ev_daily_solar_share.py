"""(#1046) The car's daily solar share is the split of its flows.

PROD, 04.10.2026: the car took 6.3 kWh, the session record said 89 % solar
and the EV card's "Solar Share" (``energy_ev_solar_percentage``) said 49 %.
The day's share divided ``solar_to_ev`` (a flow) by ``daily_ev`` (the
charger's metered kWh). The flows miss every cycle where the inverter reads
dark — the allocator leaves that draw unassigned — so the top counted part
of what the bottom counted (class 119). The session record was already
fixed for this (#1024): it scales the flow split to the meter, so its share
is solar ÷ (solar + grid + battery). The day's share now is too.

The same ratio fed the "mostly from grid" tip and the optimization score,
and the battery charge session divided its solar flow by the measured
charge. All of them go through ``solar_share_pct`` now.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap
from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.solar_energy_management.analytics.energy_assistant import (
    EnergyAssistant,
)
from custom_components.solar_energy_management.coordinator import session_energy
from custom_components.solar_energy_management.coordinator.flow_calculator import (
    FlowCalculator,
)
from custom_components.solar_energy_management.coordinator.types import (
    BatterySessionData,
    PowerReadings,
)
from custom_components.solar_energy_management.utils.helpers import solar_share_pct

PKG = pathlib.Path(__file__).resolve().parent.parent

CYCLE_S = 60.0          # one minute per cycle: 6 kW → 0.1 kWh
EV_W = 6000.0

# PROD's shape: the sun feeds the car, the inverter reads dark for a while
# (solar and battery both 0 W; the charger keeps reading its own 6 kW), and a
# few cycles of grid at the end.
SUN = PowerReadings(solar_power=8000.0, home_consumption_power=500.0,
                    ev_power=EV_W, grid_export_power=1500.0)
DARK = PowerReadings(solar_power=0.0, home_consumption_power=500.0,
                     ev_power=EV_W)
GRID = PowerReadings(home_consumption_power=500.0, ev_power=EV_W,
                     grid_import_power=6500.0)
DAY = [SUN] * 30 + [DARK] * 29 + [GRID] * 4


def _run_day(cycles):
    """The real allocator and the real integrator over one day. Returns the
    day's EnergyFlows, the meter's kWh, and what the session record says."""
    fc = FlowCalculator()
    meter: dict = {}
    metered_kwh = 0.0
    totals = None
    energy_flows = None
    hours = CYCLE_S / 3600.0
    for power in cycles:
        pf = fc.calculate_power_flows(power)
        energy_flows = fc.integrate_energy_flows(pf, CYCLE_S)
        metered_kwh += power.ev_power * hours / 1000.0
        totals = session_energy.step(
            meter,
            solar_kwh=pf.solar_to_ev * hours / 1000.0,
            grid_kwh=pf.grid_to_ev * hours / 1000.0,
            battery_kwh=pf.battery_to_ev * hours / 1000.0,
            cost=0.0, power_w=power.ev_power, hours=hours,
            session_meter_kwh=None, lifetime_meter_kwh=None,
            import_rate=0.30,
        )
    return energy_flows, metered_kwh, totals


def _analyze(ea, flows, daily_ev_kwh, **kw):
    return ea.analyze(
        daily_ev_kwh=daily_ev_kwh,
        solar_to_ev_kwh=flows.solar_to_ev,
        grid_to_ev_kwh=flows.grid_to_ev,
        battery_to_ev_kwh=flows.battery_to_ev,
        **kw,
    )


class TestTheCardMatchesTheSession:
    def test_prod_day_reads_the_session_share_not_49(self, mock_hass):
        flows, metered, totals = _run_day(DAY)
        # the shape is real: dark cycles left the draw unassigned
        assert metered == pytest.approx(6.3)
        assert flows.solar_to_ev == pytest.approx(3.0, abs=0.01)
        assert flows.grid_to_ev == pytest.approx(0.4, abs=0.01)
        old = flows.solar_to_ev / metered * 100
        assert old == pytest.approx(47.6, abs=0.1), "the bug's number"

        data = _analyze(EnergyAssistant(mock_hass), flows, metered)
        session_pct = totals.solar_kwh / totals.energy_kwh * 100
        assert totals.energy_kwh == pytest.approx(6.3)
        assert data.ev_solar_percentage == pytest.approx(88.2, abs=0.1)
        assert data.ev_solar_percentage == pytest.approx(session_pct, abs=0.01)
        assert data.to_dict()["energy_ev_solar_percentage"] == 88.2

    def test_the_meter_size_does_not_move_the_share(self, mock_hass):
        """The bottom is the flows: a meter that counted more (dark cycles,
        or the sunrise EV day holding a pre-midnight charge) changes nothing."""
        flows, metered, _ = _run_day(DAY)
        ea = EnergyAssistant(mock_hass)
        a = _analyze(ea, flows, metered).ev_solar_percentage
        b = _analyze(ea, flows, metered * 3).ev_solar_percentage
        assert a == b

    def test_battery_to_ev_is_in_the_bottom(self, mock_hass):
        data = EnergyAssistant(mock_hass).analyze(
            daily_ev_kwh=4.0, solar_to_ev_kwh=3.0, grid_to_ev_kwh=0.0,
            battery_to_ev_kwh=1.0,
        )
        assert data.ev_solar_percentage == pytest.approx(75.0)

    def test_no_flows_reads_zero_as_before(self, mock_hass):
        data = EnergyAssistant(mock_hass).analyze(daily_ev_kwh=5.0)
        assert data.ev_solar_percentage == 0.0

    def test_flows_without_a_meter_still_say_the_split(self, mock_hass):
        """Before sunrise the EV day can read 0 while the calendar-day flows
        already hold a charge; the split is still known."""
        data = EnergyAssistant(mock_hass).analyze(
            daily_ev_kwh=0.0, solar_to_ev_kwh=0.0, grid_to_ev_kwh=5.0,
        )
        assert data.ev_solar_percentage == 0.0
        data = EnergyAssistant(mock_hass).analyze(
            daily_ev_kwh=0.0, solar_to_ev_kwh=1.0, battery_to_ev_kwh=1.0,
        )
        assert data.ev_solar_percentage == pytest.approx(50.0)


class TestTheTipAndTheScoreUseTheSameSplit:
    def _ev_tips(self, ea):
        return [t for t in ea.get_all_tips() if t["category"] == "ev"
                and "from solar" in t["description"]]

    def test_prod_day_gets_no_mostly_grid_tip(self, mock_hass):
        flows, metered, _ = _run_day(DAY)
        ea = EnergyAssistant(mock_hass)
        _analyze(ea, flows, metered)
        assert self._ev_tips(ea) == [], "an 88 % solar day is not mostly grid"

    def test_tip_quotes_the_flow_split(self, mock_hass):
        ea = EnergyAssistant(mock_hass)
        ea.analyze(daily_ev_kwh=20.0, solar_to_ev_kwh=2.0, grid_to_ev_kwh=8.0)
        tips = self._ev_tips(ea)
        assert len(tips) == 1
        assert "Only 20% of EV charging" in tips[0]["description"]

    def test_no_split_known_gives_no_tip(self, mock_hass):
        """The flows hold nothing: SEM does not know the car was 0 % solar."""
        ea = EnergyAssistant(mock_hass)
        ea.analyze(daily_ev_kwh=10.0)
        assert self._ev_tips(ea) == []

    def test_score_uses_the_flow_split(self, mock_hass):
        flows, metered, _ = _run_day(DAY)
        ea = EnergyAssistant(mock_hass)
        score = _analyze(ea, flows, metered).optimization_score
        # 0 self-consumption, 0 autarky, no solar: only the EV part counts
        assert score == int(min(20, 88.235 * 0.2))
        assert score == 17

    def test_score_without_a_split_gives_no_ev_points(self, mock_hass):
        ea = EnergyAssistant(mock_hass)
        assert ea.analyze(daily_ev_kwh=10.0).optimization_score == 0


def _battery_coordinator():
    from custom_components.solar_energy_management.coordinator.coordinator import (
        SEMCoordinator,
    )
    with patch.object(SEMCoordinator, "__init__", return_value=None):
        coord = SEMCoordinator.__new__(SEMCoordinator)
    coord.config = {"update_interval": 10, "electricity_import_rate": 0.30}
    coord.update_interval = timedelta(seconds=60)
    coord._battery_session = BatterySessionData()
    coord._battery_session_idle_count = 0
    coord._battery_session_opposite_count = 0
    coord._energy_calculator = type("C", (), {"_import_rate": 0.30})()
    return coord


class TestBatterySessionShare:
    def test_dark_cycles_do_not_read_as_not_solar(self):
        sun = PowerReadings(solar_power=3000.0, home_consumption_power=500.0,
                            battery_charge_power=2500.0)
        dark = PowerReadings(home_consumption_power=500.0,
                             battery_charge_power=2500.0)
        grid = PowerReadings(home_consumption_power=500.0, grid_import_power=3000.0,
                             battery_charge_power=2500.0)
        fc = FlowCalculator()
        coord = _battery_coordinator()
        for power in [sun] * 6 + [dark] * 6 + [grid] * 2:
            coord._update_battery_session_tracking(
                power, fc.calculate_power_flows(power))
        s = coord._battery_session
        assert s.session_type == "charge"
        # the measured charge still counts the dark cycles
        assert s.energy_kwh == pytest.approx(2500 * 14 / 60 / 1000)
        old = s.solar_energy_kwh / s.energy_kwh * 100
        assert old == pytest.approx(42.9, abs=0.1), "the bug's number"
        assert s.solar_share_pct == pytest.approx(75.0)

    def test_a_dark_first_cycle_keeps_the_share_unknown_at_zero(self):
        coord = _battery_coordinator()
        dark = PowerReadings(battery_charge_power=2500.0)
        coord._update_battery_session_tracking(
            dark, FlowCalculator().calculate_power_flows(dark))
        assert coord._battery_session.solar_share_pct == 0


class TestHelper:
    @pytest.mark.parametrize("args,want", [
        ((3.0, 0.4, 0.0), 3.0 / 3.4 * 100),
        ((1.0,), 100.0),
        ((0.0, 5.0), 0.0),
        ((0.0, 0.0, 0.0), None),
        ((0.0,), None),
        ((-1.0, 2.0), 0.0),       # a negative flow never counts
        ((2.0, -1.0), 100.0),
        ((None, 1.0), 0.0),
    ])
    def test_split(self, args, want):
        got = solar_share_pct(*args)
        if want is None:
            assert got is None
        else:
            assert got == pytest.approx(want)


class TestTheCallSiteHandsOverEveryFlow:
    def test_battery_to_ev_reaches_analyze(self):
        from custom_components.solar_energy_management.coordinator.coordinator import (
            SEMCoordinator,
        )
        src = textwrap.dedent(inspect.getsource(SEMCoordinator._update_analytics_phases))
        calls = [
            n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "analyze"
            and ast.unparse(n.func.value) == "self._energy_assistant"
        ]
        assert len(calls) == 1
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        assert kw.get("solar_to_ev_kwh") == "energy_flows.solar_to_ev"
        assert kw.get("grid_to_ev_kwh") == "energy_flows.grid_to_ev"
        assert kw.get("battery_to_ev_kwh") == "energy_flows.battery_to_ev", (
            "without it a battery-fed car reads 100 % solar"
        )


# --- the lint: no solar share divides by a meter total ------------------------

_SOLAR_TOP = re.compile(r"solar", re.I)
_METER_BOTTOM = re.compile(r"daily_ev|ev_kwh|energy_kwh|\btotal\b|total_", re.I)

# Each of these divides solar by a total that is ITSELF the flow split scaled
# to the meter, so solar is part of the same total. Keyed by function.
_ALLOWED = {
    ("utils/helpers.py", "solar_share_pct"):
        "the helper: total is the sum of the flows",
    ("coordinator/ev_control.py", "_update_session_tracking"):
        "session totals come from session_energy.step, which scales every "
        "flow by the same factor (#1024)",
    ("coordinator/coordinator.py", "_lifetime_ev_shares"):
        "lifetime totals are sums of those scaled session records",
}


def _solar_over_meter(tree):
    """(function, line, text) for every solar ÷ meter-total division."""
    found = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            f = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if (isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div)
                    and _SOLAR_TOP.search(ast.unparse(child.left))
                    and _METER_BOTTOM.search(ast.unparse(child.right))):
                found.append((f, child.lineno, ast.unparse(child)))
            visit(child, f)

    visit(tree, None)
    return found


def _package_modules():
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(PKG)
        if rel.parts[0] in {"tests", "tools", "scripts", "dashboard"}:
            continue
        if "__pycache__" in rel.parts or "node_modules" in rel.parts:
            continue
        yield rel.as_posix(), p


class TestNoSolarShareOverAMeter:
    def test_the_lint_catches_the_old_code(self):
        old = textwrap.dedent("""
            def analyze(daily_ev_kwh, solar_to_ev_kwh):
                ev_solar_pct = (solar_to_ev_kwh / daily_ev_kwh) * 100
            def _calculate_score(ev_kwh, solar_to_ev_kwh):
                return solar_to_ev_kwh / ev_kwh * 100
            def _update_battery_session_tracking(session):
                session.solar_share_pct = (session.solar_energy_kwh / session.energy_kwh) * 100
        """)
        hits = _solar_over_meter(ast.parse(old))
        assert [h[0] for h in hits] == [
            "analyze", "_calculate_score", "_update_battery_session_tracking",
        ]

    def test_every_solar_share_divides_flows_by_flows(self):
        bad, seen = [], set()
        for rel, path in _package_modules():
            for func, line, text in _solar_over_meter(ast.parse(path.read_text())):
                if (rel, func) in _ALLOWED:
                    seen.add((rel, func))
                    continue
                bad.append(f"{rel}:{line} in {func}: {text}")
        assert not bad, (
            "a solar share divided by a meter total reads solar low whenever "
            "the flows miss a cycle — use utils.helpers.solar_share_pct:\n"
            + "\n".join(bad)
        )
        # an entry whose code moved away must not keep a free pass
        assert seen == set(_ALLOWED), set(_ALLOWED) - seen
