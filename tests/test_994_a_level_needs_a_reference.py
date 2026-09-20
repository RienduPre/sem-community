"""#994 — a price level needs a reference, and may say it has none.

SEM's ``PriceLevel`` is Tibber's vocabulary. Tibber defines it against a
**3-day moving average** (≤60 % very_cheap · 60–90 % cheap · 90–115 % normal
· 115–140 % expensive · ≥140 % very_expensive) and its enum carries **None —
"missing data"** as a first-class variant. SEM kept the five words and
dropped both the reference and the absence, so a CLOCK was free to produce
them and no consumer could tell a measured level from an asserted one.

By that definition a flat tariff is NORMAL, never CHEAP — every hour is
exactly 100 % of the mean. On @traktore-org's install (0.36 = 0.36) SEM said
`cheap`, the house sink read it as "a better hour is coming", and
``decide_battery`` wrote a **0 W discharge limit**: the battery sat at
92–100 % overnight while the house imported 3.66 kWh.

Pinned here: the vocabulary answers ``None`` when nothing was compared, and
each tariff MODEL gets the answer it deserves.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management.coordinator import price_signal as ps
from custom_components.solar_energy_management.tariff.tariff_provider import (
    PriceLevel,
    StaticTariffProvider,
)


def _data(lo, hi, avg=None, level=PriceLevel.CHEAP):
    return SimpleNamespace(today_min_price=lo, today_max_price=hi,
                           today_avg_price=avg if avg is not None else
                           (None if lo is None or hi is None else (lo + hi) / 2),
                           price_level=level)


class _Fake:
    """A provider shaped like the real ones, for the vocabulary's own tests."""

    def __init__(self, lo, hi, level=PriceLevel.CHEAP, avg=None, at=...):
        self._d = _data(lo, hi, avg, level)
        self._level = level
        self._at = level if at is ... else at

    def get_tariff_data(self):
        return self._d

    def get_price_level(self):
        return self._level

    def get_price_level_at(self, when):
        return self._at


# ═══════════════════════════════════════════════════════════════════════
# Task 1 — the vocabulary
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTheVocabulary:
    def test_a_flat_curve_has_no_comparative_level(self):
        p = _Fake(0.36, 0.36)
        assert ps.spread(p) == pytest.approx(0.0)
        assert ps.variation_known(p) is False
        assert ps.comparative_level(p) is None
        assert ps.is_cheap(p) is False
        assert ps.is_expensive(p) is False

    def test_a_real_spread_keeps_its_level(self):
        p = _Fake(0.22, 0.35, level=PriceLevel.CHEAP)
        assert ps.variation_known(p) is True
        assert ps.comparative_level(p) is PriceLevel.CHEAP
        assert ps.is_cheap(p) is True

    def test_the_flat_test_is_relative_so_it_holds_in_any_currency(self):
        """An absolute cutoff in CHF is the #359 defect itself — re-fixed for
        a Slovak tariff at 1.69/kWh (#417) and a Sri Lankan one at 50–70 per
        kWh (#549). 0.5 % of the mean is the same judgement everywhere."""
        # LKR: a 3 rupee split on a 60 rupee tariff is real variation …
        assert ps.variation_known(_Fake(58.0, 61.0)) is True
        # … and the same 3 units would be nonsense on a 0.36 CHF tariff,
        # while a 1 ct HT/NT split on 30 ct (3.3 %) still counts.
        assert ps.variation_known(_Fake(0.295, 0.305)) is True
        # float noise never counts, at any scale
        assert ps.variation_known(_Fake(0.36, 0.360001)) is False
        assert ps.variation_known(_Fake(60.0, 60.0001)) is False

    def test_unknown_is_never_the_cheap_bucket(self):
        """#925, class 86: "I could not ask" is not "no"."""
        for p in (_Fake(None, None), _Fake(0.3, 0.3), None):
            assert ps.is_cheap(p) is False
            assert ps.is_expensive(p) is False
            assert ps.comparative_level(p) is None

    def test_the_sensor_names_the_absence_rather_than_a_level(self):
        """Publication is ``TariffData.to_dict``'s job — the wire string the
        sensor shows. "No data" and "confirmed mid-priced" used to be the
        same word there; briefly both absences were "unknown", which reads
        in Home Assistant like a broken sensor and hid the one of the two
        that is worth a user's attention.

        ``flat`` — the comparison was made and the hours do not differ.
        ``no_prices`` — it could not be made at all.
        """
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_FLAT, StaticTariffProvider,
        )
        flat = StaticTariffProvider()                    # 0.3387 = 0.3387
        published = flat.get_tariff_data().to_dict()["tariff_price_level"]
        assert published == LEVEL_FLAT == "flat"
        assert published != "unknown", (
            "a flat contract is an ANSWER, not a failed read")

    def test_a_dynamic_provider_with_nothing_cached_says_no_prices(self):
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_NO_PRICES, DynamicTariffProvider,
        )
        p = DynamicTariffProvider(MagicMock(), price_entity="sensor.fake",
                                  classification_mode="percentile")
        p.hass.states.get.return_value = SimpleNamespace(state="0.30")
        p._read_prices_list = lambda: []
        d = p.get_tariff_data()
        assert d.price_level is None
        assert d.to_dict()["tariff_price_level"] == LEVEL_NO_PRICES

    def test_neither_absence_is_ever_a_cheap_or_dear_hour(self):
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_FLAT, LEVEL_NO_PRICES,
        )
        for word in (LEVEL_FLAT, LEVEL_NO_PRICES):
            assert not ps.is_cheap_name(word)
            assert not ps.is_expensive_name(word)

    def test_a_provider_that_raises_is_unknown_not_cheap(self):
        class _Boom:
            def get_tariff_data(self): raise RuntimeError("no")
        assert ps.spread(_Boom()) is None
        assert ps.is_cheap(_Boom()) is False

    def test_the_two_level_sets_live_here_and_agree_with_the_enum(self):
        assert set(ps.CHEAP_LEVELS) == {PriceLevel.CHEAP, PriceLevel.VERY_CHEAP,
                                        PriceLevel.NEGATIVE}
        assert set(ps.EXPENSIVE_LEVELS) == {PriceLevel.EXPENSIVE,
                                            PriceLevel.VERY_EXPENSIVE}
        # very_expensive must never fall out of a tuple again
        # (surplus_controller damped the pool on "expensive" alone before #994)
        assert ps.is_expensive_name("very_expensive") is True
        assert ps.is_cheap_name("very_cheap") is True
        assert ps.is_cheap_name(None) is False


# ═══════════════════════════════════════════════════════════════════════
# Task 2 — the model matrix, on the real providers
# ═══════════════════════════════════════════════════════════════════════

def _monday(h=12):
    return datetime(2026, 9, 21, h, 0)      # a Monday


def _saturday(h=12):
    return datetime(2026, 9, 19, h, 0)      # a Saturday


@pytest.mark.unit
class TestTheStaticModel:
    def test_equal_rates_produce_no_level(self, monkeypatch):
        """The reporter's tariff: 0.36 = 0.36, and SEM said 'cheap' for a
        day and a half."""
        p = StaticTariffProvider(peak_rate=0.36, off_peak_rate=0.36)
        monkeypatch.setattr(p, "_both_rates_occur", lambda when=None: True)
        assert p.get_price_level() is None
        assert p.get_price_level_at(_monday(3)) is None
        assert ps.is_cheap(p) is False

    def test_different_rates_keep_the_ht_nt_answer(self, monkeypatch):
        """The clock is a legitimate mapping when the rates differ."""
        p = StaticTariffProvider(peak_rate=0.35, off_peak_rate=0.22)
        monkeypatch.setattr(p, "_both_rates_occur", lambda when=None: True)
        assert p.get_price_level_at(_monday(3)) is PriceLevel.CHEAP     # NT
        assert p.get_price_level_at(_monday(12)) is PriceLevel.NORMAL   # HT
        assert ps.variation_known(p) is True

    def test_a_weekend_is_flat_in_practice(self):
        """The rate table has two rates; Saturday has one. Without this the
        pack is held all weekend for an hour that arrives on Monday."""
        p = StaticTariffProvider(peak_rate=0.35, off_peak_rate=0.22)
        assert p._both_rates_occur(_saturday()) is False
        assert p.get_price_level_at(_saturday(3)) is None
        assert p.get_price_level_at(_saturday(12)) is None

    def test_the_day_that_occurs_is_what_gets_published(self, monkeypatch):
        p = StaticTariffProvider(peak_rate=0.35, off_peak_rate=0.22)
        monkeypatch.setattr(p, "_both_rates_occur", lambda when=None: False)
        d = p.get_tariff_data()
        assert d.today_min_price == d.today_max_price
        assert d.classifier_path == "static_no_comparison"

    def test_the_shipped_defaults_are_flat_and_now_say_so(self, monkeypatch):
        """0.3387 = 0.3387 ships in the constructor — and the old test suite
        asserted CHEAP for it while asserting min == max in the same test."""
        p = StaticTariffProvider()
        monkeypatch.setattr(p, "_both_rates_occur", lambda when=None: True)
        assert p.get_price_level() is None


@pytest.mark.unit
class TestTheCalendarModel:
    def _provider(self, rules=None, peak=0.35, off=0.22):
        from custom_components.solar_energy_management.tariff.calendar_provider import (
            CalendarTariffProvider,
        )
        return CalendarTariffProvider(None, peak_rate=peak, off_peak_rate=off,
                                      rules=rules or [])

    def test_no_rules_means_no_level(self):
        """Every install that picked this mode got CHEAP forever: the
        coordinator hardcoded an empty schedule and the default is off_peak."""
        p = self._provider()
        assert p.get_price_level() is None
        assert p.get_price_level_at(_monday(12)) is None
        assert p.get_tariff_data().classifier_path == "calendar_no_comparison"

    def test_equal_rates_mean_no_level_even_with_rules(self):
        p = self._provider(rules=[{"days": "mon-fri", "start": "07:00",
                                   "end": "20:00", "tariff": "ht"}],
                           peak=0.30, off=0.30)
        assert p.get_price_level() is None


# ═══════════════════════════════════════════════════════════════════════
# Task 4 — every comparative chain gains the third branch
#
# One pin per chain, each naming the harm. All of them turn on the same
# fact: with no comparison there is no better hour, so nothing waits.
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestNothingWaitsForAnHourThatCannotCome:

    def test_the_house_sink_does_not_hold_the_pack(self):
        """THE INCIDENT. Flat tariff ⇒ the house verdict is OPEN, so
        ``decide_battery`` never writes the 0 W discharge limit that kept a
        92–100 % battery idle while the house imported 3.66 kWh."""
        from custom_components.solar_energy_management.coordinator.sink_verdicts import (
            sink_verdicts,
        )
        v = sink_verdicts(
            now=_monday(2), tariff_level=None,          # flat ⇒ no level
            upcoming=[], export_rate=0.075, export_rate_known=True,
            export_guard_enabled=False, house_sink_enabled=True,
            morning_window_enabled=False, departure=None, morning_hours=2.0,
            forecast_refills_pack=False, pacing_horizon_end=None,
        )
        assert v["house"].state == "open"
        assert "cheap" not in v["house"].reason

    def test_a_real_cheap_hour_still_holds_it(self):
        """The feature is not disabled — only its false trigger is."""
        from custom_components.solar_energy_management.coordinator.sink_verdicts import (
            sink_verdicts,
        )
        v = sink_verdicts(
            now=_monday(2), tariff_level="cheap",
            upcoming=[], export_rate=0.075, export_rate_known=True,
            export_guard_enabled=False, house_sink_enabled=True,
            morning_window_enabled=False, departure=None, morning_hours=2.0,
            forecast_refills_pack=False, pacing_horizon_end=None,
        )
        assert v["house"].state == "held"

    def test_the_cloud_bridge_still_bridges(self):
        """A flat tariff's HT hour used to read NORMAL, which made every
        daytime cloud dip STRUCTURAL — a hard stop instead of a bridge, on
        a price that never moved."""
        from custom_components.solar_energy_management.coordinator.decide import (
            _NOT_CHEAP_LEVELS,
        )
        assert "" not in _NOT_CHEAP_LEVELS
        assert None not in _NOT_CHEAP_LEVELS      # unknown is not "not cheap"
        assert "normal" in _NOT_CHEAP_LEVELS      # a real NORMAL still is

    def test_the_ev_planner_never_books_an_unpriced_hour(self):
        """``_rank``'s ``.get(..., 3)`` scored silence as NORMAL, so the
        cheapest-slot search would pick an hour it had no price for — in the
        one place that commits money."""
        from custom_components.solar_energy_management.coordinator import (
            ev_tariff_planner as evp,
        )
        assert evp._rank(None) == evp.UNPRICED_RANK
        assert evp._rank("") == evp.UNPRICED_RANK
        assert evp._rank("normal") < evp.UNPRICED_RANK
        assert evp._rank("very_expensive") < evp.UNPRICED_RANK
        # …and an unpriced hour is never "expensive" either, so nothing
        # holds through it on a guess
        assert evp._is_expensive(None) is False

    def test_affordable_start_ignores_hours_it_cannot_price(self):
        from custom_components.solar_energy_management.coordinator.ev_tariff_planner import (
            affordable_start,
        )
        now, deadline = _monday(22), _monday(22) + timedelta(hours=8)
        # Every hour unpriced ⇒ nothing to wait FOR. Either encoding of
        # "don't wait" is fine (``None`` means "charge now" here); what must
        # never happen is a start pushed into the future on a guess.
        start = affordable_start(now, deadline, 10.0, 3.0, lambda _t: None)
        assert start is None or start <= now, start

    def test_the_surplus_pool_damps_on_very_expensive_too(self):
        """The one drift the six hand-typed copies had already produced."""
        from custom_components.solar_energy_management.coordinator.surplus_controller import (
            price_damped_pool,
        )
        assert price_damped_pool(3000.0, "very_expensive") < 3000.0
        assert price_damped_pool(3000.0, "expensive") < 3000.0
        assert price_damped_pool(3000.0, "normal") == 3000.0
        assert price_damped_pool(3000.0, None) == 3000.0      # unknown ≠ dear

    def test_cheap_hours_loads_do_not_defer_on_a_flat_tariff(self):
        """``day_ledger.tariff_cheap_at`` is the documented ONE accessor the
        deferred-load packer fires on."""
        from custom_components.solar_energy_management.coordinator.day_ledger import (
            tariff_cheap_at,
        )
        flat = StaticTariffProvider()                 # 0.3387 = 0.3387
        assert tariff_cheap_at(flat, _monday(3)) is False
        varying = StaticTariffProvider(peak_rate=0.35, off_peak_rate=0.22)
        assert tariff_cheap_at(varying, _monday(3)) is True     # NT, weekday

    def test_the_assistant_does_not_advise_on_a_price_that_never_moves(self):
        from custom_components.solar_energy_management.analytics.energy_assistant import (
            EnergyAssistant,
        )
        tips = EnergyAssistant._analyze_price(object(), None, 5.0)
        assert not [t for t in (tips or []) if "cheap" in str(getattr(t, "title", "")).lower()]


# ═══════════════════════════════════════════════════════════════════════
# Live finding on .175, 20.09 10:10 — the path is a SIDE-EFFECT, and the
# tri-state answer was reading it
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestThePathDescribesTheLevelItShipsWith:
    """``classifier_path`` said ``negative_price_shortcircuit`` beside a
    published ``normal``, on a positive current price — read off the rig.

    ``_get_percentile_breaks`` sets the path as a SIDE-EFFECT and returns
    early on a cache hit **without setting it**, so the string left behind
    belongs to whichever price was classified last. ``_apply_levels``
    classifies every point in the curve on each read, so on a day with one
    negative slot the attribute a user reads to learn WHY describes some
    other hour entirely (#359's whole purpose).

    #994 made that string load-bearing: ``get_price_level`` answered
    ``None`` when it started with ``percentile_fallback_``. A stale
    fallback string from another call would then erase a level that real
    breaks had produced. A tri-state answer may not rest on a value a
    different question wrote.
    """

    @staticmethod
    def _provider():
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.tariff_provider import (
            DynamicTariffProvider, PricePoint,
        )
        from homeassistant.util import dt as dt_util

        p = DynamicTariffProvider(MagicMock(), price_entity="sensor.fake",
                                  classification_mode="percentile")
        base = dt_util.now().replace(minute=0, second=0, microsecond=0)
        # A real spread, one negative slot — an ordinary NL solar-glut day.
        curve = [-0.02] + [0.05 + 0.02 * i for i in range(23)]
        p._prices_cache = [
            PricePoint(timestamp=base + timedelta(hours=i), price=v,
                       currency="EUR", level=PriceLevel.NORMAL)
            for i, v in enumerate(curve)
        ]
        p.hass.states.get.return_value = SimpleNamespace(state="0.30")
        # The parser is not under test here; the cache IS the curve, and
        # reading it relevels every point exactly as the real one does.
        def _read():
            pts = list(p._prices_cache)
            p._apply_levels(pts)
            return pts
        p._read_prices_list = _read
        return p

    def test_a_cache_hit_still_says_which_path_produced_the_level(self):
        p = self._provider()
        first = p.get_price_level()                      # cache MISS — sets the path
        assert p._last_classifier_path.startswith("percentile_active(")
        p._classify_price(-0.02)                         # what _apply_levels does
        assert p._last_classifier_path == "negative_price_shortcircuit"
        again = p.get_price_level()                      # cache HIT
        assert again == first
        assert p._last_classifier_path.startswith("percentile_active("), (
            "the path must describe the price just classified, not the last "
            f"one some other caller passed: {p._last_classifier_path}")

    def test_a_stale_fallback_string_cannot_erase_a_real_level(self):
        p = self._provider()
        level = p.get_price_level()
        assert level is not None
        p._last_classifier_path = "percentile_fallback_flat_day(spread=0.0001)"
        assert p.get_price_level() == level, (
            "a level the breaks really produced was erased by a string "
            "left behind by a different call")

    def test_the_published_path_and_level_come_from_the_same_answer(self):
        p = self._provider()
        p._classify_price(-0.02)
        d = p.get_tariff_data()
        assert d.price_level is not None
        assert d.classifier_path.startswith("percentile_active("), d.classifier_path

    def test_an_hour_the_classifier_could_not_compare_has_no_level_either(self):
        """``get_price_level_at`` used to answer NORMAL where
        ``get_price_level`` answered None — the disagreement #994 exists
        to end, still live on two of the three fallbacks."""
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.tariff_provider import (
            DynamicTariffProvider, PricePoint,
        )
        from homeassistant.util import dt as dt_util

        p = DynamicTariffProvider(MagicMock(), price_entity="sensor.fake",
                                  classification_mode="percentile")
        base = dt_util.now().replace(minute=0, second=0, microsecond=0)
        # A flat day: real points, no spread — "percentile_fallback_flat_day".
        pts = [PricePoint(timestamp=base + timedelta(hours=i), price=0.30,
                          currency="EUR", level=PriceLevel.NORMAL)
               for i in range(24)]
        p._prices_cache = list(pts)
        p.hass.states.get.return_value = SimpleNamespace(state="0.30")

        def _read():
            out = list(p._prices_cache)
            p._apply_levels(out)
            return out
        p._read_prices_list = _read

        assert p.get_price_level() is None
        assert p.get_price_level_at(base + timedelta(hours=1)) is None, (
            "the hour-wise accessor still handed out a confident word")


# ═══════════════════════════════════════════════════════════════════════
# What the ruflo reviewer refuted (20.09) — the first pass fixed the clock
# in one provider and left the same question unasked in the other
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTheCalendarKnowsWhatDayItIs:
    """``_ht_can_occur`` asked whether an HT rule exists ANYWHERE in the
    weekly table, never whether one can arrive on the day being classified.

    ``StaticTariffProvider._both_rates_occur(when)`` had asked the right
    question from the start; the calendar got the rate test and not the day
    test. Three of the five shipped presets have days with no HT rule — EKZ
    and ewz stop at Saturday lunchtime, CKW at Friday — so on a Sunday they
    reproduced this issue's own incident through the calendar instead of
    the clock, and because ``get_tariff_data`` used the same blind check,
    today_min/today_max carried the full spread and ``variation_known``
    was fooled as well. Going through ``comparative_level`` did not save a
    consumer: the defect was inside the reference.
    """

    @staticmethod
    def _cal(preset="ekz", peak=0.36, off_peak=0.22):
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.calendar_provider import (
            TARIFF_PRESETS, CalendarTariffProvider,
        )
        return CalendarTariffProvider(
            MagicMock(), peak_rate=peak, off_peak_rate=off_peak,
            rules=TARIFF_PRESETS[preset]["rules"])

    SUNDAY = datetime(2026, 9, 20, 9, 0)      # the day the rig ran on
    MONDAY = datetime(2026, 9, 21, 9, 0)

    @pytest.mark.parametrize("preset", ["ekz", "ckw", "ewz"])
    def test_a_day_the_schedule_never_marks_ht_has_no_level(self, preset):
        p = self._cal(preset)
        assert p.get_price_level_at(self.SUNDAY) is None, (
            f"{preset} has no HT rule on a Sunday, so nothing distinguishes "
            "its hours — and CHEAP would hold the pack until Monday")

    @pytest.mark.parametrize("preset", ["ekz", "ckw", "ewz", "bkw"])
    def test_a_weekday_still_gets_its_answer(self, preset):
        p = self._cal(preset)
        assert p.get_price_level_at(self.MONDAY) is not None, preset

    def test_saturday_morning_under_ekz_is_a_real_comparison(self):
        """EKZ runs HT 07:00–13:00 on Saturday: the day HAS both rates."""
        p = self._cal("ekz")
        sat_am = datetime(2026, 9, 19, 9, 0)
        sat_pm = datetime(2026, 9, 19, 18, 0)
        assert p.get_price_level_at(sat_am) == PriceLevel.NORMAL
        assert p.get_price_level_at(sat_pm) == PriceLevel.CHEAP

    def test_an_all_week_schedule_is_unaffected(self):
        p = self._cal("bkw")          # HT every day 07:00–21:00
        assert p.get_price_level_at(self.SUNDAY) is not None

    def test_an_nt_carve_out_of_an_ht_default_still_compares(self):
        """The mirror case: default HT with an NT window named by a rule."""
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.calendar_provider import (
            CalendarTariffProvider,
        )
        p = CalendarTariffProvider(
            MagicMock(), peak_rate=0.36, off_peak_rate=0.22,
            rules=[{"days": [6], "start": "00:00", "end": "06:00",
                    "tariff": "nt"}],
            default_tariff="peak")
        assert p.get_price_level_at(self.SUNDAY) is not None

    def test_the_reference_the_second_gate_reads_agrees(self, monkeypatch):
        """``variation_known`` reads today_min/today_max — on a day with one
        price those must BE one price, or the vocabulary's own gate passes
        a level the provider had already refused."""
        from homeassistant.util import dt as dt_util

        p = self._cal("ekz")
        monkeypatch.setattr(dt_util, "now", lambda: self.SUNDAY)
        d = p.get_tariff_data()
        assert d.price_level is None
        assert d.today_min_price == d.today_max_price
        assert ps.comparative_level(p) is None


@pytest.mark.unit
class TestTheScheduleCardIsNotToldNormal:
    """``_coarse_level(None)`` fell through both membership tests to a
    confident ``normal`` — for the exact slots ``_apply_levels`` had just
    marked absent. Sixteen lines from the caller, the sibling diagnostic
    counted the same slots correctly, so one cycle published both answers
    about the same hours."""

    def test_an_unclassified_slot_is_not_normal(self):
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_FLAT, LEVEL_NO_PRICES, DynamicTariffProvider, PricePoint,
        )
        from homeassistant.util import dt as dt_util

        p = DynamicTariffProvider(MagicMock(), price_entity="sensor.fake",
                                  classification_mode="percentile")
        base = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
        pts = [PricePoint(timestamp=base + timedelta(hours=i), price=0.30,
                          currency="EUR", level=None) for i in range(24)]
        p._prices_cache = list(pts)
        p.hass.states.get.return_value = SimpleNamespace(state="0.30")

        def _read():
            out = list(p._prices_cache)
            p._apply_levels(out)
            return out
        p._read_prices_list = _read

        rows = p.get_schedule_for_day()
        assert rows, "the day should still render"
        assert {r["level"] for r in rows} == {LEVEL_FLAT}, rows
        assert {r["tariff"] for r in rows} == {None}, (
            "an unclassified block is neither NT nor HT")
        assert LEVEL_NO_PRICES not in {r["level"] for r in rows}

    def test_the_two_absences_are_told_apart(self):
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_FLAT, LEVEL_NO_PRICES, absence_for_path,
        )
        assert absence_for_path("percentile_fallback_flat_day(spread=0.0001)") == LEVEL_FLAT
        assert absence_for_path("static_no_comparison") == LEVEL_FLAT
        assert absence_for_path("calendar_no_comparison") == LEVEL_FLAT
        assert absence_for_path("percentile_fallback_cache_empty") == LEVEL_NO_PRICES
        assert absence_for_path("percentile_fallback_too_few_prices(n=2)") == LEVEL_NO_PRICES
        assert absence_for_path("") == LEVEL_NO_PRICES


@pytest.mark.unit
class TestThereIsOneListOfLevels:
    """The module claimed both tuples lived in one place; a reviewer found
    four more copies inside the provider after the first pass."""

    def test_the_vocabulary_re_exports_the_definitions(self):
        from custom_components.solar_energy_management.tariff import (
            tariff_provider as tp,
        )
        assert ps.CHEAP_LEVELS is tp.CHEAP_LEVELS
        assert ps.EXPENSIVE_LEVELS is tp.EXPENSIVE_LEVELS

    def test_no_module_hand_types_a_level_tuple(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        names = {"CHEAP", "VERY_CHEAP", "NEGATIVE", "EXPENSIVE", "VERY_EXPENSIVE"}
        offenders = []
        for f in sorted(root.rglob("*.py")):
            if set(f.relative_to(root).parts) & {"tests", "scripts", "node_modules"}:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if not isinstance(n, (ast.Tuple, ast.Set, ast.List)):
                    continue
                attrs = [e for e in n.elts
                         if isinstance(e, ast.Attribute) and e.attr in names]
                if len(attrs) >= 2:
                    offenders.append(
                        f"{f.relative_to(root)}:{n.lineno}")
        # The two definitions themselves are the only literals allowed.
        allowed = {"tariff/tariff_provider.py"}
        stray = [o for o in offenders if o.rsplit(":", 1)[0] not in allowed]
        assert not stray, (
            "hand-typed level tuple(s) — import CHEAP_LEVELS / "
            f"EXPENSIVE_LEVELS instead: {stray}")


@pytest.mark.unit
class TestOneSituationTellsOneStory:
    """The sensor published ``flat`` while the sink verdict's reason said
    "no prices to compare" — the fleet state dropped the absence word and
    left it None, so the verdict fell to its unreadable branch. Caught live
    on .175 under the EKZ preset on a Sunday, minutes after the calendar fix
    landed."""

    @staticmethod
    def _verdict(level):
        from custom_components.solar_energy_management.coordinator.sink_verdicts import (
            sink_verdicts,
        )
        return sink_verdicts(
            now=datetime(2026, 9, 20, 9, 0), tariff_level=level, upcoming=[],
            export_rate=0.07, export_rate_known=True,
            export_guard_enabled=False, house_sink_enabled=True,
            morning_window_enabled=False, departure=None, morning_hours=0.0,
            forecast_refills_pack=False, pacing_horizon_end=None,
        )["house"]

    def test_a_flat_tariff_says_flat(self):
        v = self._verdict("flat")
        assert v.state == "open"
        assert "flat tariff" in v.reason
        assert "no prices" not in v.reason

    def test_an_unreadable_price_says_so(self):
        v = self._verdict("no_prices")
        assert v.state == "open"
        assert "no prices to compare" in v.reason

    def test_a_missing_word_still_opens_the_sink(self):
        for level in (None, ""):
            assert self._verdict(level).state == "open"

    def test_neither_absence_holds_the_pack(self):
        for level in ("flat", "no_prices", None, ""):
            assert self._verdict(level).state == "open", level


@pytest.mark.unit
class TestAnAbsenceIsNotACheapHour:
    """The second review's headline: threading a WORD where None used to
    sit flipped every ``is not None`` test that had meant "no comparative
    signal". ``decide``'s day-cheap gate read "not one of the dear words
    and not None" — so ``flat`` and ``no_prices`` sailed through both
    halves and SEM would have topped the car up FROM THE GRID on a flat
    tariff, believing it a cheap hour. This issue's own disease, freshly
    reintroduced by its own fix."""

    @pytest.mark.parametrize("level,expected", [
        (None, False), ("flat", False), ("no_prices", False),
        ("normal", False), ("expensive", False), ("very_expensive", False),
        ("cheap", True), ("very_cheap", True), ("negative", True),
    ])
    def test_only_a_cheap_hour_may_start_a_day_grid_charge(self, level, expected):
        assert ps.is_cheap_name(level) is expected

    def test_the_gate_asks_the_vocabulary_and_not_for_none(self):
        """An AST contract, because the defect was the SHAPE of the test:
        a None-check standing in for "is there a level"."""
        import ast
        import inspect

        from custom_components.solar_energy_management.coordinator import decide as d

        src = inspect.getsource(d.SolarPlusCheapMode.decide)
        tree = ast.parse(src.lstrip() if src.startswith(" ") else src)
        nones = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Compare)
                 and any(isinstance(o, (ast.Is, ast.IsNot)) for o in n.ops)
                 and any(isinstance(c, ast.Constant) and c.value is None
                         for c in n.comparators)
                 and isinstance(n.left, ast.Attribute)
                 and n.left.attr == "tariff_level"]
        assert not nones, (
            "a None-check on tariff_level is no longer the question — "
            "absence has its own words now; ask is_cheap_name / "
            "is_expensive_name")


@pytest.mark.unit
class TestTheCalendarAsksTheThingThatDecides:
    """Every remaining way the reachability check and the decision could
    disagree, from the second review."""

    @staticmethod
    def _cal(rules=None, default="off_peak", holiday=None, schedule=None,
             peak=0.36, off_peak=0.22, states=None):
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.calendar_provider import (
            CalendarTariffProvider,
        )
        hass = MagicMock()
        hass.states.get = lambda eid: (states or {}).get(eid)
        return CalendarTariffProvider(
            hass, peak_rate=peak, off_peak_rate=off_peak, rules=rules or [],
            default_tariff=default, holiday_entity=holiday,
            schedule_entity=schedule)

    MON = datetime(2026, 9, 21, 9, 0)

    def test_a_holiday_is_one_price_all_day(self):
        p = self._cal(rules=[{"days": [0,1,2,3,4], "start": "07:00",
                              "end": "20:00", "tariff": "ht"}],
                      holiday="binary_sensor.holiday",
                      states={"binary_sensor.holiday": SimpleNamespace(state="on")})
        assert p.get_price_level_at(self.MON) is None

    def test_the_same_day_without_the_holiday_still_compares(self):
        p = self._cal(rules=[{"days": [0,1,2,3,4], "start": "07:00",
                              "end": "20:00", "tariff": "ht"}],
                      holiday="binary_sensor.holiday",
                      states={"binary_sensor.holiday": SimpleNamespace(state="off")})
        assert p.get_price_level_at(self.MON) is not None

    def test_a_schedule_helper_is_not_silenced(self):
        """A schedule-helper install has NO rules — reading the rule table
        made this entire input mode answer None forever."""
        p = self._cal(rules=[], schedule="schedule.tariff",
                      states={"schedule.tariff": SimpleNamespace(state="on")})
        assert p.get_price_level_at(self.MON) == PriceLevel.NORMAL

    def test_a_schedule_helper_that_is_gone_says_nothing(self):
        p = self._cal(rules=[], schedule="schedule.tariff", states={})
        assert p.get_price_level_at(self.MON) is None

    def test_a_mis_cased_rule_word_still_means_high_tariff(self):
        """``_get_tariff_at`` returned the word verbatim and the decision
        site compared it case-SENSITIVELY, so "HT" was never high tariff
        while every other reader thought it was."""
        p = self._cal(rules=[{"days": [0,1,2,3,4], "start": "07:00",
                              "end": "20:00", "tariff": "HT"}])
        assert p.get_price_level_at(self.MON) == PriceLevel.NORMAL
        assert p.get_price_level_at(datetime(2026, 9, 21, 22, 0)) == PriceLevel.CHEAP

    def test_a_zero_width_window_is_not_a_peak_period(self):
        p = self._cal(rules=[{"days": [0,1,2,3,4], "start": "07:00",
                              "end": "07:00", "tariff": "ht"}])
        assert p.get_price_level_at(self.MON) is None

    def test_a_rule_without_days_does_not_crash_the_cycle(self):
        p = self._cal(rules=[{"days": None, "start": "07:00",
                              "end": "20:00", "tariff": "ht"}])
        assert p.get_price_level_at(self.MON) is None
        assert p.get_tariff_data().price_level is None

    def test_a_short_window_is_found_exactly_not_sampled(self):
        """A five-minute peak window is still a peak window."""
        p = self._cal(rules=[{"days": [0], "start": "09:00", "end": "09:05",
                              "tariff": "ht"}])
        assert p.get_price_level_at(self.MON) is not None
        assert p.get_price_level_at(datetime(2026, 9, 21, 9, 2)) == PriceLevel.NORMAL


@pytest.mark.unit
class TestTheDeeperFindings:
    """The rest of the second review: a price nobody read, two horizons for
    one question, a European ruler, and an absence that drifted."""

    @staticmethod
    def _dyn(mode="percentile", entity_state=None, cache=None):
        from unittest.mock import MagicMock

        from custom_components.solar_energy_management.tariff.tariff_provider import (
            DynamicTariffProvider,
        )
        p = DynamicTariffProvider(MagicMock(), price_entity="sensor.fake",
                                  classification_mode=mode)
        p.hass.states.get.return_value = (
            SimpleNamespace(state=entity_state) if entity_state else None)
        p._prices_cache = list(cache or [])
        p._read_prices_list = lambda: list(p._prices_cache)
        return p

    def test_static_cutoffs_do_not_classify_an_invented_price(self):
        """``static`` classification mode never met a percentile fallback,
        so a dead price entity was answered with the configured constant
        and a confident level."""
        p = self._dyn(mode="static", entity_state=None)
        assert p.get_price_level() is None
        d = p.get_tariff_data()
        assert d.price_level is None
        assert d.classifier_path == "no_price_to_classify"
        assert d.to_dict()["tariff_price_level"] == "no_prices"

    def test_static_cutoffs_still_classify_a_price_that_was_read(self):
        p = self._dyn(mode="static", entity_state="0.05")
        assert p.get_price_level() is not None

    def test_the_flat_day_guard_is_relative(self):
        """A 1 ct absolute cutoff is a European ruler. #417 was at 1.69/kWh
        and #549 three orders of magnitude away."""
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            FLAT_DAY_SPREAD_FRACTION,
        )
        # A tariff quoted in a unit 1000x smaller, genuinely varying.
        assert (300.0 * FLAT_DAY_SPREAD_FRACTION) > 1.0
        # …and one quoted in the usual EUR scale keeps roughly the old cut.
        assert 0.005 < (0.30 * FLAT_DAY_SPREAD_FRACTION) < 0.02

    def test_the_spread_is_measured_over_the_curve_the_level_used(self):
        """``variation_known`` read today by the wall clock while the
        classifier bucketed against a rolling window running into tomorrow
        — two references for one question."""
        flat_today_rising_tomorrow = SimpleNamespace(
            today_min_price=0.30, today_max_price=0.30, today_avg_price=0.30,
            upcoming_prices=[SimpleNamespace(price=v)
                             for v in (0.30, 0.30, 0.10, 0.50, 0.40, 0.05)],
            price_level=PriceLevel.CHEAP)
        prov = SimpleNamespace(
            get_tariff_data=lambda: flat_today_rising_tomorrow,
            get_price_level=lambda: PriceLevel.CHEAP,
            get_price_level_at=lambda when: PriceLevel.CHEAP)
        assert ps.variation_known(prov) is True
        assert ps.comparative_level(prov) == PriceLevel.CHEAP

    def test_a_short_curve_falls_back_to_the_day(self):
        two_points = SimpleNamespace(
            today_min_price=0.10, today_max_price=0.40, today_avg_price=0.25,
            upcoming_prices=[SimpleNamespace(price=0.40),
                             SimpleNamespace(price=0.39)],
            price_level=PriceLevel.CHEAP)
        prov = SimpleNamespace(
            get_tariff_data=lambda: two_points,
            get_price_level=lambda: PriceLevel.CHEAP,
            get_price_level_at=lambda when: PriceLevel.CHEAP)
        assert ps.spread(prov) == pytest.approx(0.30)

    def test_a_slot_remembers_which_absence_it_was(self):
        """One provider-level word described whichever read ran last, so a
        slot declined for want of points was relabelled `flat` once the
        cache grew into a flat day."""
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            LEVEL_NO_PRICES, PricePoint,
        )
        from homeassistant.util import dt as dt_util

        base = dt_util.now().replace(minute=0, second=0, microsecond=0)
        p = self._dyn(entity_state="0.30", cache=[
            PricePoint(timestamp=base - timedelta(hours=2), price=0.30,
                       currency="EUR", level=PriceLevel.NORMAL),
            PricePoint(timestamp=base - timedelta(hours=1), price=0.30,
                       currency="EUR", level=PriceLevel.NORMAL),
        ])
        pts = p._read_prices_list()
        p._apply_levels(pts)
        # Two points: too few to bucket against — that is "no prices", not
        # "the prices are all the same".
        assert [x.level for x in pts] == [None, None]
        assert {x.level_absence for x in pts} == {LEVEL_NO_PRICES}
