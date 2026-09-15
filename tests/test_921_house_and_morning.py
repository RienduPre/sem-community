"""#879 the house as a sink, #892 the car before it leaves — decided from the verdicts.

Both are consumers of ``sink_verdicts``; neither reads a price. The house
verdict says HELD in a cheap/negative hour: let the house import, keep the
pack for the expensive hours (the WHEN is the tariff level, the HOW MUCH is
zero house cover). The ev verdict says OPEN inside a morning window: the
pack is SPENT into the car deliberately, down to a drain floor, and the EV
protection clamp must not fight a window the user opened.
"""
from custom_components.solar_energy_management.coordinator.charger_types import (
    BatteryIntent, BatteryRuntime, BatteryView, ChargerEnergy, ChargerPower,
    ChargerView, FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide import (
    battery_assist_budget_w,
)
from custom_components.solar_energy_management.coordinator.decide_battery import (
    decide_battery,
)
from custom_components.solar_energy_management.coordinator.sink_verdicts import (
    HELD, OPEN, SinkVerdict,
)


def _view(*, house=None, ev=None, soc=80.0, ev_connected=False, cfg_extra=None):
    cfg = {"battery_max_discharge_power": 4000, "battery_max_charge_power_w": 5000,
           "battery_mode": "auto", "battery_morning_drain_floor_soc": 50.0}
    cfg.update(cfg_extra or {})
    verdicts = {}
    if house:
        verdicts["house"] = SinkVerdict("house", house, "t")
    if ev:
        verdicts["ev"] = SinkVerdict("ev", ev, "t")
    return BatteryView(runtime=BatteryRuntime(battery_id="b1", last_known_soc=soc), config=cfg,
                       fleet=FleetContext(), charging_state="idle", ev_charging=ev_connected,
                       ev_connected=ev_connected, home_consumption_w=800.0,
                       scheduler_decision=None, sink_verdicts=verdicts)


class TestHouse:
    def test_held_clamps_discharge_to_zero(self):
        d = decide_battery(_view(house=HELD))
        assert d.intent is BatteryIntent.LIMIT_DISCHARGE and d.discharge_limit_w == 0.0
        assert "house" in d.reason

    def test_open_is_normal(self):
        assert decide_battery(_view(house=OPEN)).intent is BatteryIntent.NORMAL

    def test_no_verdict_is_todays_behaviour(self):
        assert decide_battery(_view()).intent is BatteryIntent.NORMAL

    def test_held_yields_to_a_force_charge_mode(self):
        d = decide_battery(_view(house=HELD, cfg_extra={"battery_mode": "force_charge"}))
        assert d.intent is BatteryIntent.FORCE_CHARGE

    def test_held_yields_to_off(self):
        d = decide_battery(_view(house=HELD, cfg_extra={"battery_mode": "off"}))
        assert d.intent is BatteryIntent.OFF


class TestMorningEv:
    def test_open_window_lifts_the_ev_protection_clamp(self):
        d = decide_battery(_view(ev=OPEN, ev_connected=True, soc=80.0))
        assert d.intent is BatteryIntent.NORMAL and "morning window" in d.reason

    def test_the_drain_floor_ends_the_window(self):
        d = decide_battery(_view(ev=OPEN, ev_connected=True, soc=49.0))
        assert "morning window" not in d.reason

    def test_held_keeps_todays_clamp(self):
        d = decide_battery(_view(ev=HELD, ev_connected=True, soc=80.0))
        assert "morning window" not in d.reason

    def test_an_unavailable_soc_never_opens_the_window(self):
        v = _view(ev=OPEN, ev_connected=True, soc=80.0)
        v.runtime.available = False
        assert "morning window" not in decide_battery(v).reason

    def test_the_window_beats_the_house_hold(self):
        """The user opened the window deliberately; a HELD house does not close it."""
        d = decide_battery(_view(ev=OPEN, house=HELD, ev_connected=True, soc=80.0))
        assert d.intent is BatteryIntent.NORMAL and "morning window" in d.reason


# ── Task 10: the charger side ────────────────────────────────────────────

def _cview(*, morning=False, surplus_w=300.0, soc=80.0):
    return ChargerView(
        power=ChargerPower(charger_id="k", power_w=0.0, connected=True, charging=False),
        energy=ChargerEnergy(charger_id="k"), mode="min_plus_solar",
        config={"ev_min_current": 6, "ev_phases": 3, "ev_voltage": 230, "ev_max_current": 32},
        fleet=FleetContext(solar_w=surplus_w + 500.0, home_w=500.0, battery_soc=soc,
                           battery_soc_known=True, battery_priority=5,
                           battery_assist_max_power_w=4500.0, battery_assist_min_surplus_w=1200.0,
                           ev_morning_window_open=morning),
        ev_priority=1)


class TestTheChargerSide:
    def test_below_the_solar_gate_the_window_still_offers_the_pack(self):
        assert battery_assist_budget_w(_cview(morning=True)) > battery_assist_budget_w(_cview())

    def test_without_the_window_the_gate_holds_as_today(self):
        assert battery_assist_budget_w(_cview()) == 300.0

    def test_the_flag_rides_the_fleet_state(self):
        from custom_components.solar_energy_management.coordinator.charger_types import (
            FleetCycleState,
        )
        assert FleetCycleState.__dataclass_fields__["morning_window_open"].default is False
        assert FleetContext().ev_morning_window_open is False
