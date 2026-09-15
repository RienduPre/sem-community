"""arc #921 — a sink has a STATE, not a price. One verdict per sink per cycle.

Every rule here is about WHEN a destination is open, read off the tariff
LEVEL the provider already classifies. Nothing here is a price, and nothing
downstream may turn a verdict back into one.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.solar_energy_management.coordinator.sink_verdicts import (
    CLOSED, HELD, OPEN, SINKS, next_closed_window, sink_verdicts,
)

TZ = timezone.utc
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=TZ)


def _pp(hour, level):
    return SimpleNamespace(timestamp=NOW.replace(hour=hour),
                           level=SimpleNamespace(value=level))


def _verdicts(**kw):
    base = dict(now=NOW, tariff_level="normal", upcoming=[], export_rate_known=True,
                export_guard_enabled=False, house_sink_enabled=False,
                morning_window_enabled=False, departure=None, morning_hours=2.0,
                forecast_refills_pack=False, pacing_horizon_end=None)
    base.update(kw)
    return sink_verdicts(**base)


class TestGridExport:
    def test_negative_level_with_the_guard_on_closes_the_grid(self):
        v = _verdicts(tariff_level="negative", export_guard_enabled=True)
        assert v["grid_export"].state == CLOSED

    def test_negative_level_with_the_guard_off_stays_open(self):
        assert _verdicts(tariff_level="negative").state_of("grid_export") == OPEN

    def test_an_unknown_price_is_open_never_closed(self):
        """#925 / class 86: 'I could not ask' is not 'the meter is hostile'."""
        v = _verdicts(tariff_level="negative", export_guard_enabled=True,
                      export_rate_known=False)
        assert v["grid_export"].state == OPEN
        assert "unknown" in v["grid_export"].reason

    def test_zero_is_worthless_not_hostile(self):
        assert _verdicts(tariff_level="cheap", export_guard_enabled=True)["grid_export"].state == OPEN

    def test_a_closed_verdict_says_when_the_meter_reopens(self):
        ups = [_pp(12, "negative"), _pp(13, "negative"), _pp(14, "normal")]
        v = _verdicts(tariff_level="negative", export_guard_enabled=True, upcoming=ups)
        assert v["grid_export"].until == NOW.replace(hour=14)

    def test_an_open_verdict_says_when_the_meter_will_close(self):
        ups = [_pp(12, "normal"), _pp(15, "negative")]
        v = _verdicts(tariff_level="normal", export_guard_enabled=True, upcoming=ups)
        assert v["grid_export"].until == NOW.replace(hour=15)


class TestNextClosedWindow:
    def test_finds_the_first_negative_run_after_now(self):
        ups = [_pp(12, "normal"), _pp(13, "negative"), _pp(14, "negative"), _pp(15, "cheap")]
        assert next_closed_window(NOW, ups) == (NOW.replace(hour=13), NOW.replace(hour=15))

    def test_no_negative_hour_is_none(self):
        assert next_closed_window(NOW, [_pp(12, "normal"), _pp(13, "cheap")]) is None

    def test_a_run_already_open_starts_now(self):
        ups = [_pp(12, "negative"), _pp(13, "negative"), _pp(14, "normal")]
        assert next_closed_window(NOW, ups) == (NOW, NOW.replace(hour=14))

    def test_a_curve_ending_negative_closes_one_slot_after_its_last_point(self):
        ups = [_pp(12, "normal"), _pp(13, "negative")]
        assert next_closed_window(NOW, ups) == (NOW.replace(hour=13), NOW.replace(hour=14))

    def test_a_past_run_is_ignored(self):
        ups = [_pp(9, "negative"), _pp(10, "negative"), _pp(11, "normal"), _pp(12, "normal")]
        assert next_closed_window(NOW, ups) is None

    def test_a_15_minute_curve_uses_its_own_cadence(self):
        q = lambda h, m, lv: SimpleNamespace(timestamp=NOW.replace(hour=h, minute=m),
                                             level=SimpleNamespace(value=lv))
        ups = [q(12, 0, "normal"), q(12, 15, "negative"), q(12, 30, "negative"), q(12, 45, "normal")]
        assert next_closed_window(NOW, ups) == (NOW.replace(minute=15), NOW.replace(minute=45))

    def test_an_empty_curve_is_none(self):
        assert next_closed_window(NOW, []) is None
        assert next_closed_window(NOW, None) is None

    def test_an_unreadable_point_is_skipped_not_fatal(self):
        ups = [SimpleNamespace(timestamp=None, level=None), _pp(13, "negative")]
        assert next_closed_window(NOW, ups) == (NOW.replace(hour=13), NOW.replace(hour=14))


class TestBatteryHeadroom:
    def test_held_when_a_closed_window_starts_inside_the_pacing_horizon(self):
        ups = [_pp(12, "normal"), _pp(15, "negative"), _pp(16, "negative")]
        v = _verdicts(upcoming=ups, export_guard_enabled=True,
                      pacing_horizon_end=NOW.replace(hour=19))
        assert v["battery"].state == HELD
        assert v["battery"].until == NOW.replace(hour=15)

    def test_open_when_the_closed_window_is_past_the_horizon(self):
        ups = [_pp(20, "negative")]
        v = _verdicts(upcoming=ups, export_guard_enabled=True,
                      pacing_horizon_end=NOW.replace(hour=19))
        assert v["battery"].state == OPEN

    def test_open_when_the_guard_is_off(self):
        """No guard, no closure to hold headroom for — #926 rides on #955."""
        ups = [_pp(15, "negative")]
        v = _verdicts(upcoming=ups, pacing_horizon_end=NOW.replace(hour=19))
        assert v["battery"].state == OPEN

    def test_open_when_the_price_is_unknown(self):
        ups = [_pp(15, "negative")]
        v = _verdicts(upcoming=ups, export_guard_enabled=True, export_rate_known=False,
                      pacing_horizon_end=NOW.replace(hour=19))
        assert v["battery"].state == OPEN


class TestHouse:
    def test_off_by_default(self):
        assert _verdicts(tariff_level="expensive")["house"].state == OPEN

    def test_held_in_cheap_hours(self):
        assert _verdicts(tariff_level="cheap", house_sink_enabled=True)["house"].state == HELD

    def test_held_in_negative_hours(self):
        assert _verdicts(tariff_level="negative", house_sink_enabled=True)["house"].state == HELD

    def test_open_in_expensive_hours(self):
        assert _verdicts(tariff_level="expensive", house_sink_enabled=True)["house"].state == OPEN

    def test_open_when_the_level_is_unknown(self):
        assert _verdicts(tariff_level=None, house_sink_enabled=True)["house"].state == OPEN


class TestMorningEv:
    def test_open_inside_the_window_when_the_forecast_refills(self):
        v = _verdicts(morning_window_enabled=True, departure=NOW + timedelta(hours=1),
                      forecast_refills_pack=True)
        assert v["ev"].state == OPEN
        assert v["ev"].until == NOW + timedelta(hours=1)

    def test_held_before_the_window_opens(self):
        v = _verdicts(morning_window_enabled=True, departure=NOW + timedelta(hours=5),
                      forecast_refills_pack=True)
        assert v["ev"].state == HELD
        assert v["ev"].until == NOW + timedelta(hours=3)

    def test_held_when_the_forecast_will_not_refill(self):
        v = _verdicts(morning_window_enabled=True, departure=NOW + timedelta(hours=1),
                      forecast_refills_pack=False)
        assert v["ev"].state == HELD

    def test_no_departure_no_window(self):
        v = _verdicts(morning_window_enabled=True, departure=None, forecast_refills_pack=True)
        assert v["ev"].state == HELD

    def test_held_once_the_car_has_left(self):
        v = _verdicts(morning_window_enabled=True, departure=NOW - timedelta(minutes=1),
                      forecast_refills_pack=True)
        assert v["ev"].state == HELD

    def test_off_by_default_is_the_legacy_rule(self):
        v = _verdicts(departure=NOW + timedelta(hours=1), forecast_refills_pack=True)
        assert v["ev"].state == OPEN and v["ev"].reason == "legacy assist rule"


class TestTheDictIsComplete:
    def test_every_sink_has_a_verdict(self):
        v = _verdicts()
        assert set(v) == set(SINKS)
        for sv in v.values():
            assert sv.state in (OPEN, HELD, CLOSED) and sv.reason

    def test_everything_off_is_every_sink_open(self):
        """The inert default: no switch on ⇒ no sink ever HELD or CLOSED."""
        for level in ("negative", "cheap", "normal", "expensive", None):
            v = _verdicts(tariff_level=level, upcoming=[_pp(13, "negative")],
                          pacing_horizon_end=NOW.replace(hour=19),
                          departure=NOW + timedelta(hours=1), forecast_refills_pack=True)
            assert {sv.state for sv in v.values()} == {OPEN}, level

    def test_to_dict_is_serialisable(self):
        import json
        v = _verdicts(tariff_level="negative", export_guard_enabled=True,
                      upcoming=[_pp(12, "negative"), _pp(13, "normal")])
        json.dumps({k: sv.to_dict() for k, sv in v.items()})


class TestTheVerdictsRideTheFleetState:
    """Computed in ONE place, threaded like every other fleet input."""

    def test_fleet_state_and_context_carry_them(self):
        from custom_components.solar_energy_management.coordinator.charger_types import (
            FleetContext, FleetCycleState,
        )
        assert FleetCycleState.__dataclass_fields__["sink_verdicts"].default_factory() == {}
        assert FleetContext().sink_verdicts == {}

    def test_build_view_threads_them(self):
        from .ast_contracts import call_kwargs
        from custom_components.solar_energy_management.coordinator import build_view
        kwargs = call_kwargs(build_view.build_charger_view, "FleetContext")
        assert kwargs and "sink_verdicts" in kwargs[0]

    def test_the_coordinator_computes_them_in_one_place(self):
        """The #924 question — are the siblings right? — answered structurally:
        exactly one production call site, the fleet-state builder."""
        from .ast_contracts import call_sites
        sites = call_sites("sink_verdicts")
        assert [s[0] for s in sites] == ["coordinator/coordinator.py"], sites
