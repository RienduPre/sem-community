"""#1011 — a car that declines a start is recorded as declined, not as full.

PROD, 27.09.2026: the car was plugged in and full. SEM offered a start,
the car did not draw for 3 minutes, and the stall rule wrote "full charge
reached at 11:26" and pinned the estimate to 100 %. Nothing charged that
day — the box's lifetime total was unchanged since the real full the
evening before. Today the guess matched. For #983's install (54 % against
80 %) the same rule wrote 100 % and restarted the deficit from a false
zero, and it fires again after every restart because the taper's latch is
not stored.

Three things change, none of them the truth model (#440: the estimate
never gates charging):

* a stall is recorded as a DECLINED start — first and latest instant —
  and touches no anchor, no full-charge timestamp, no deficit;
* the estimate names its reference ("sensor" / "taper" / "session") and
  when, as attributes of the sensor, so a wrong anchor is visible;
* the night plan keeps treating a declining car as no sink — under that
  name, not as "full" — and the declined-start backoff grows 20 → 40 →
  80 min instead of offering a full car a ladder three times an hour.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management.coordinator.charge_stability import (
    FULL_CAR_BACKOFF_S,
    FULL_CAR_GIVEUP_STREAK,
    ChargeStability,
)
from custom_components.solar_energy_management.coordinator.ev_availability import (
    plan_car_fullness,
)
from custom_components.solar_energy_management.coordinator.ev_taper_detector import (
    EVTaperDetector,
)
from custom_components.solar_energy_management.tests.test_610_full_car_backoff import (
    FakeAdapter,
    _filter,
    _run_ladder_to_giveup,
    _view,
)
from custom_components.solar_energy_management.tests.test_ev_taper_detector import (
    DEFAULT_CONFIG,
    _feed_taper_profile,
)

T1 = "2026-09-27T11:26:00+02:00"
T2 = "2026-09-27T11:46:00+02:00"


class TestADeclinedStartIsNotFull:
    def test_a_fresh_detector_stays_unknown(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.note_declined_start(T1)
        assert det.declined_start
        assert det._soc_anchored is False
        assert det._full_detected is False
        assert det._last_full_timestamp is None
        assert det.anchor_kind is None
        assert not det.still_full, "the plan's 'full' stays unknown"

    def test_a_tapered_full_keeps_its_own_timestamp(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        _feed_taper_profile(det)
        full_at = det._last_full_timestamp
        assert det.anchor_kind == "taper"
        assert det.anchor_at == full_at
        det.note_declined_start(T1)
        assert det._last_full_timestamp == full_at, "a refusal must not re-stamp the full"
        assert det.anchor_kind == "taper"
        assert det._estimated_soc == 100.0
        assert det.declined_start, "both can be true: a full car declines too"

    def test_first_and_latest_refusal_are_kept(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.note_declined_start(T1)
        det.note_declined_start(T2)
        assert det._start_declined_at == T1
        assert det._start_declined_last_at == T2

    def test_a_real_draw_ends_the_refusal(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.note_declined_start(T1)
        det.update_energy(0.0)
        assert det.declined_start, "nothing flowed"
        det.update_energy(0.02)
        assert not det.declined_start, "20 Wh flowed: the car accepted"

    def test_an_unplug_ends_the_refusal(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.note_declined_start(T1)
        det.reset_session()
        assert not det.declined_start

    def test_the_anchor_survives_a_restart_and_the_refusal_does_not(self):
        """The anchor explains a persisted estimate, so it persists with it.
        A decline is re-learned within three minutes; a stored one could
        outlive an unplug that happened while HA was down and brand a new,
        empty car as "no sink" (review, 27.09)."""
        det = EVTaperDetector(DEFAULT_CONFIG)
        _feed_taper_profile(det)
        det.note_declined_start(T1)
        state = det.get_state()
        assert "start_declined_at" not in state
        det2 = EVTaperDetector(DEFAULT_CONFIG)
        det2.restore_state(state)
        assert det2.anchor_kind == "taper"
        assert det2.anchor_at == det.anchor_at
        assert not det2.declined_start
        assert det2.still_full == det.still_full

    def test_a_sensor_anchor_is_stamped_on_a_changed_reading_not_every_cycle(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.get_virtual_soc(54.0)
        first = det.anchor_at
        assert det.anchor_kind == "sensor" and first
        for _ in range(5):
            det.get_virtual_soc(54.0)                 # the same reading, re-read each cycle
        assert det.anchor_at == first, "a re-read must not move the timestamp (#581)"
        det.get_virtual_soc(60.0)                     # the reading changed
        assert det.anchor_at >= first and det.anchor_kind == "sensor"

    def test_a_sensor_reading_names_itself(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.get_virtual_soc(54.0)
        assert det.anchor_kind == "sensor"
        assert det.anchor_at

    def test_an_old_store_without_the_new_keys_still_restores(self):
        det = EVTaperDetector(DEFAULT_CONFIG)
        det.restore_state({"last_full_charge": T1, "estimated_soc": 100.0, "soc_anchored": True,
                           "energy_since_full": 0.0})
        assert det.anchor_kind is None and not det.declined_start
        assert det.still_full


class TestThePlanTreatsARefusalAsNoSink:
    def test_declined_means_no_sink(self):
        assert plan_car_fullness(SimpleNamespace(still_full=False, declined_start=True)) is True

    def test_unknown_stays_unknown(self):
        assert plan_car_fullness(SimpleNamespace(still_full=False, declined_start=False)) is None

    def test_the_meter_still_overrules_a_refusal(self):
        assert plan_car_fullness(SimpleNamespace(still_full=False, declined_start=True),
                                 drawing_w=3000.0) is None

    def test_full_still_works(self):
        assert plan_car_fullness(SimpleNamespace(still_full=True, declined_start=False)) is True

    def test_a_detector_without_the_property_has_no_opinion(self):
        assert plan_car_fullness(SimpleNamespace(still_full=False)) is None


class TestTheWaitGrows:
    def test_20_40_80_then_capped(self):
        st = ChargeStability()
        adapter = FakeAdapter()
        t = 0.0
        seen = []
        d = None
        for n in range(FULL_CAR_GIVEUP_STREAK + 3):
            d, t_end = _run_ladder_to_giveup(st, adapter, t, first=(n == 0))
            if n >= FULL_CAR_GIVEUP_STREAK - 1:
                assert "backing off" in d.reason, d.reason
                seen.append(round((st._giveup_backoff_until["wb"] - t_end) / FULL_CAR_BACKOFF_S))
                t = st._giveup_backoff_until["wb"] + 10.0        # wait it out
            else:
                t = t_end + 10.0
        assert seen == [1, 2, 4, 4], seen
        assert "backing off 80 min" in d.reason

    def test_a_long_backoff_survives_a_restart(self):
        """The restore bound used to stop at the base 20 min: a restart in
        minute 21-79 of a 40/80-minute wait dropped the deadline and the
        ladder fired at once (review, 27.09)."""
        st = ChargeStability()
        adapter = FakeAdapter()
        t = 0.0
        t_end = 0.0
        for n in range(5):                                   # 5th give-up → 80 min
            _, t_end = _run_ladder_to_giveup(st, adapter, t, first=(n == 0))
            t = st._giveup_backoff_until.get("wb", t_end) + 10.0
        armed_until = st._giveup_backoff_until["wb"]
        assert armed_until - t_end > 3 * FULL_CAR_BACKOFF_S  # an extended wait
        snap = st.snapshot_timers(t_end + 100.0)
        st2 = ChargeStability()
        st2.restore_timers(snap, 5.0)
        assert "wb" in st2._giveup_backoff_until, "the long deadline must survive the restart"
        assert st2._giveup_backoff_until["wb"] - 5.0 == pytest.approx(armed_until - (t_end + 100.0), abs=0.1)
        d = _filter(st2, _view(power_w=120.0), FakeAdapter(), now=10.0)
        assert "start backoff" in d.reason

    def test_a_real_draw_resets_the_growth(self):
        st = ChargeStability()
        adapter = FakeAdapter()
        t = 0.0
        d = None
        for n in range(5):
            d, t_end = _run_ladder_to_giveup(st, adapter, t, first=(n == 0))
            t = st._giveup_backoff_until.get("wb", t_end) + 10.0
        assert "backing off 80 min" in d.reason
        adapter.last_intent = None
        _filter(st, _view(power_w=4140.0), adapter, now=t)          # the car draws
        assert "wb" not in st._giveup_streak
        t += 60.0
        for n in range(FULL_CAR_GIVEUP_STREAK):
            d, t_end = _run_ladder_to_giveup(st, adapter, t, first=(n == 0))
            t = t_end + 10.0
        assert "backing off 20 min" in d.reason


class TestTheCoordinatorRecordsADeclineNotAFull:
    """Structural: the stall block in ``_update_ev_intelligence`` must call
    the detector's ``note_declined_start`` and must not write a full-charge
    fact itself. Only the detector's own taper path may set
    ``_last_full_timestamp`` / ``_full_detected``; the self-heal's session
    anchor (``_estimated_soc``, ``_soc_anchored``) stays allowed."""

    def _fn(self):
        from custom_components.solar_energy_management.coordinator.coordinator import (
            SEMCoordinator,
        )
        return SEMCoordinator._update_ev_intelligence

    def test_the_stall_records_a_decline(self):
        from custom_components.solar_energy_management.tests import ast_contracts
        assert ast_contracts.calls(self._fn(), "note_declined_start")

    def test_the_coordinator_never_writes_a_full_charge_fact(self):
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(self._fn())))
        forbidden = {"_last_full_timestamp", "_full_detected"}
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in forbidden:
                    hits.append(f"L{node.lineno}: {t.attr}")
        assert not hits, f"a full-charge fact written outside the detector: {hits}"
