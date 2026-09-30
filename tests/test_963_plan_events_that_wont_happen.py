"""(#963) Today's Plan announced a start per plan BLOCK, not per charge.

@HorizonKane, on 2.1.0-beta.22 — "Scheduler full of events that won't happen…
in reality, I don't do any scheduled charging and have a fixed tariff". His
screenshot shows eight rows: ``Now``, ``Batterie voll``, and then SIX separate
``EV charging starts`` at 14:00, 15:00, 16:00, 18:00, 19:00 and 20:00.

The joint plan hands the composer one block per pricing slot, and the composer
emitted a start row for each. Six of them describe two charges. Worse, the
composer sorts by time and keeps the first eight rows, so the duplicates pushed
out everything later than 20:00 — his screenshot has no ``Min reached`` row even
though the composer emits one at the last block's end (21:00), and no night
window or deadline. The plan became almost entirely events that never happen,
which is exactly what he called it.

The screenshot also shows the second half of this issue: the row titles are
English ("EV charging starts", "Now", "TODAY'S PLAN") on a German install whose
subtitles ARE German ("geplantes Fenster — Block des Energieplans"). Thirteen
of the card's eighteen keys carried the English source text in thirteen of the
sixteen languages.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from custom_components.solar_energy_management.coordinator.today_plan import (
    KIND_EV_CHARGE_START,
    KIND_EV_MIN_REACHED,
    _merge_touching,
    compose_today_plan,
)

TZ = timezone.utc
NOW = datetime(2026, 9, 15, 13, 12, tzinfo=TZ)   # the clock in his screenshot


def _block(start_h, end_h, day=15):
    return {"start": datetime(2026, 9, day, start_h, tzinfo=TZ).isoformat(),
            "end": datetime(2026, 9, day, end_h, tzinfo=TZ).isoformat()}


# His plan exactly: 14–17 and 18–21, as six hourly blocks.
HORIZONKANE_BLOCKS = [
    _block(14, 15), _block(15, 16), _block(16, 17),
    _block(18, 19), _block(19, 20), _block(20, 21),
]


def _starts(plan):
    return [r["when"] for r in plan if r["kind"] == KIND_EV_CHARGE_START]


class TestTheReportedPlan:
    def test_six_blocks_become_two_starts(self):
        plan = compose_today_plan(
            now=NOW, ev_min_remaining_kwh=12.0, ev_plan_blocks=HORIZONKANE_BLOCKS,
        )
        starts = _starts(plan)
        assert len(starts) == 2, (
            f"a charge starts once per window, not once per pricing slot; got {starts}"
        )

    def test_the_two_starts_are_the_window_starts(self):
        plan = compose_today_plan(
            now=NOW, ev_min_remaining_kwh=12.0, ev_plan_blocks=HORIZONKANE_BLOCKS,
        )
        assert _starts(plan) == [
            datetime(2026, 9, 15, 14, tzinfo=TZ).isoformat(),
            datetime(2026, 9, 15, 18, tzinfo=TZ).isoformat(),
        ]

    def test_the_17_00_gap_is_kept_as_a_real_second_start(self):
        """SEM stops at 17:00 and starts again at 18:00 — that IS a transition."""
        plan = compose_today_plan(
            now=NOW, ev_min_remaining_kwh=12.0, ev_plan_blocks=HORIZONKANE_BLOCKS,
        )
        assert datetime(2026, 9, 15, 18, tzinfo=TZ).isoformat() in _starts(plan)

    def test_min_reached_still_lands_on_the_last_blocks_end(self):
        plan = compose_today_plan(
            now=NOW, ev_min_remaining_kwh=12.0, ev_plan_blocks=HORIZONKANE_BLOCKS,
        )
        mins = [r["when"] for r in plan if r["kind"] == KIND_EV_MIN_REACHED]
        assert mins == [datetime(2026, 9, 15, 21, tzinfo=TZ).isoformat()]

    def test_the_other_rows_are_no_longer_evicted(self):
        """The 8-row cap was being spent on duplicates. With the real plan's
        other inputs present, they must now fit."""
        plan = compose_today_plan(
            now=NOW,
            ev_min_remaining_kwh=12.0,
            ev_plan_blocks=HORIZONKANE_BLOCKS,
            night_start=datetime(2026, 9, 15, 21, 30, tzinfo=TZ),
            ev_deadline=datetime(2026, 9, 16, 7, tzinfo=TZ),
            battery_full_eta=datetime(2026, 9, 15, 14, 18, tzinfo=TZ),
            solar_peak_time=datetime(2026, 9, 15, 13, 40, tzinfo=TZ).isoformat(),
            solar_remaining_kwh=8.4,
        )
        kinds = {r["kind"] for r in plan}
        for expected in ("night_open", "ev_deadline", "battery_full", "solar_peak"):
            assert expected in kinds, f"{expected} was crowded out by duplicate starts"


class TestMergeTouching:
    def test_adjacent_blocks_merge(self):
        a = (datetime(2026, 9, 15, 14, tzinfo=TZ), datetime(2026, 9, 15, 15, tzinfo=TZ))
        b = (datetime(2026, 9, 15, 15, tzinfo=TZ), datetime(2026, 9, 15, 16, tzinfo=TZ))
        assert _merge_touching([a, b]) == [(a[0], b[1])]

    def test_a_real_gap_does_not_merge(self):
        a = (datetime(2026, 9, 15, 14, tzinfo=TZ), datetime(2026, 9, 15, 15, tzinfo=TZ))
        b = (datetime(2026, 9, 15, 18, tzinfo=TZ), datetime(2026, 9, 15, 19, tzinfo=TZ))
        assert _merge_touching([a, b]) == [a, b]

    def test_sub_second_slot_arithmetic_still_merges(self):
        a = (datetime(2026, 9, 15, 14, tzinfo=TZ),
             datetime(2026, 9, 15, 14, 59, 59, 999000, tzinfo=TZ))
        b = (datetime(2026, 9, 15, 15, tzinfo=TZ), datetime(2026, 9, 15, 16, tzinfo=TZ))
        assert _merge_touching([a, b]) == [(a[0], b[1])]

    def test_a_contained_block_does_not_shrink_the_window(self):
        outer = (datetime(2026, 9, 15, 14, tzinfo=TZ), datetime(2026, 9, 15, 18, tzinfo=TZ))
        inner = (datetime(2026, 9, 15, 15, tzinfo=TZ), datetime(2026, 9, 15, 16, tzinfo=TZ))
        assert _merge_touching([outer, inner]) == [outer]

    def test_empty_is_empty(self):
        assert _merge_touching([]) == []

    def test_a_single_block_is_untouched(self):
        a = (datetime(2026, 9, 15, 14, tzinfo=TZ), datetime(2026, 9, 15, 15, tzinfo=TZ))
        assert _merge_touching([a]) == [a]

    def test_one_long_run_is_one_window(self):
        blocks = [(datetime(2026, 9, 15, h, tzinfo=TZ),
                   datetime(2026, 9, 15, h + 1, tzinfo=TZ)) for h in range(8, 20)]
        assert _merge_touching(blocks) == [
            (datetime(2026, 9, 15, 8, tzinfo=TZ), datetime(2026, 9, 15, 20, tzinfo=TZ))]


class TestPlanIsTranslated:
    """The other half of his screenshot: English titles over German subtitles."""

    ROOT = Path(__file__).resolve().parent.parent
    KEYS = sorted(set(re.findall(
        r'(?:label|detail)="([a-z0-9_]+)"',
        (ROOT / "coordinator" / "today_plan.py").read_text(encoding="utf-8"),
    )) | {"today_plan_title"})

    @pytest.fixture(scope="class")
    def tables(self):
        return json.loads(
            (self.ROOT / "dashboard" / "translations.json").read_text(encoding="utf-8"))

    def test_every_plan_key_exists_in_every_language(self, tables):
        for lang, table in tables.items():
            missing = [k for k in self.KEYS if k not in table]
            assert not missing, f"{lang} is missing {missing}"

    def test_no_language_still_carries_the_english_source(self, tables):
        en = tables["en"]
        offenders = {
            lang: [k for k in self.KEYS if table.get(k) == en.get(k)]
            for lang, table in tables.items() if lang != "en"
        }
        offenders = {k: v for k, v in offenders.items() if v}
        assert not offenders, (
            "a card that translates its subtitle but not its title reads as broken: "
            f"{offenders}"
        )

    def test_placeholders_survive_translation(self, tables):
        """A dropped {kwh} silently prints nothing where a number belongs."""
        en = tables["en"]
        for lang, table in tables.items():
            for k in self.KEYS:
                want = set(re.findall(r"\{(\w+)\}", en[k]))
                got = set(re.findall(r"\{(\w+)\}", table[k]))
                assert got == want, f"{lang}/{k}: expected {want}, got {got}"
