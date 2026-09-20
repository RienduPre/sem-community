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

    def test_the_sensor_publishes_unknown_rather_than_a_level(self):
        """Publication is ``TariffData.to_dict``'s job — the wire string the
        sensor shows. "No data" and "confirmed mid-priced" used to be the
        same word there."""
        from custom_components.solar_energy_management.tariff.tariff_provider import (
            StaticTariffProvider,
        )
        flat = StaticTariffProvider()                    # 0.3387 = 0.3387
        assert flat.get_tariff_data().to_dict()["tariff_price_level"] == "unknown"

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
