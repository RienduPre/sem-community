"""#994 — a price level is a COMPARISON, and a comparison needs two prices.

SEM's ``PriceLevel`` answers "is now better or worse than another hour?".
Two of its three providers answer that question from a **clock** —
``StaticTariffProvider`` returns CHEAP outside 07:00–20:00 on a weekday and
``CalendarTariffProvider`` returns CHEAP whenever its (never-wired) rule
table is empty — without ever comparing the two rates they hold. On a flat
tariff there is nothing to compare, so the word is decoration; but eighteen
consumers across six decision chains read it as an instruction.

What that cost, on the maintainer's own house (19–20.09, #994): both rates
configured at 0.36, the level published as ``cheap``, the house-sink feature
reading that as "a better hour is coming — keep the pack", and
``decide_battery`` writing a **0 W discharge limit** to the inverter. The
battery sat at 92–100 % overnight while the house drew 3.66 kWh from the
grid, to save energy for an expensive hour that cannot exist on a flat
tariff. The record says this is the sixth time the vocabulary has bitten:
#359 took six waves in four days, #728 two, and three consumers built months
apart (#524, #953, #879) each rediscovered the trap on first contact.

So: one vocabulary, in one place, that can say **I don't know**.

* ``spread`` — how far apart the cheapest and dearest hours in the horizon
  are, or ``None`` when that cannot be read.
* ``variation_known`` — is there a price difference worth acting on at all?
* ``comparative_level`` — the level, but only when a comparison stands
  behind it; ``None`` on a flat curve, on missing data, and whenever the
  provider itself declines to answer.
* ``is_cheap`` / ``is_expensive`` — the two questions consumers actually
  ask, **False** when the answer is unknown. Absence is never the cheap
  bucket (#925, class 86: "I could not ask" is not "no").

The rule every comparative consumer follows, already written down in
``sink_verdicts.py`` for the export side: *an UNKNOWN price is OPEN, never
CLOSED* — there is no better hour to wait for, so act now.

Pure: no hass, no clock of its own, no I/O. Give it a provider.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..tariff.tariff_provider import PriceLevel

#: Two prices are "the same price" when they differ by less than this
#: FRACTION of the day's average. RELATIVE on purpose: an absolute cutoff in
#: CHF is precisely the #359 defect — levels bucketed against a CHF-shaped
#: constant mis-classified every other market — and it was re-fixed twice
#: more on the config surface, for a Slovak tariff at 1.69/kWh (#417) and a
#: Sri Lankan one three orders of magnitude away (#549). 0.5 % of the mean
#: keeps a genuine 1 ct HT/NT split on a 30 ct tariff (3.3 %) while calling
#: float noise flat, in any currency.
FLAT_SPREAD_FRACTION: float = 0.005

#: …with a floor for the degenerate case where the mean is 0 or unreadable
#: (a free or fully-compensated tariff), so the test cannot divide by zero
#: and cannot be fooled by a currency whose unit happens to be tiny.
FLAT_SPREAD_FLOOR: float = 1e-9

#: The levels that mean "this hour is better than the others".
CHEAP_LEVELS = (PriceLevel.CHEAP, PriceLevel.VERY_CHEAP, PriceLevel.NEGATIVE)
#: …and worse. NOTE both tuples live HERE and nowhere else: six
#: independently hand-typed copies existed before #994 and one had already
#: drifted (``surplus_controller`` damped the pool on "expensive" while
#: silently ignoring "very_expensive").
EXPENSIVE_LEVELS = (PriceLevel.EXPENSIVE, PriceLevel.VERY_EXPENSIVE)

_CHEAP_NAMES = frozenset(lv.value for lv in CHEAP_LEVELS)
_EXPENSIVE_NAMES = frozenset(lv.value for lv in EXPENSIVE_LEVELS)


def _name(level: Any) -> str:
    """A level's wire string, whatever shape it arrives in (enum, str, None)."""
    if level is None:
        return ""
    return str(getattr(level, "value", level) or "").strip().lower()


def spread(provider: Any) -> Optional[float]:
    """The horizon's price range, or ``None`` when it cannot be read.

    Every provider populates ``today_min_price``/``today_max_price`` — and
    for the two clock-based ones those ARE the two configured rates, so the
    fact that refutes their verdict is already in their own payload.
    """
    if provider is None:
        return None
    try:
        data = provider.get_tariff_data()
        lo, hi = data.today_min_price, data.today_max_price
    except Exception:  # noqa: BLE001 — a provider that cannot answer is unknown
        return None
    if lo is None or hi is None:
        return None
    try:
        return abs(float(hi) - float(lo))
    except (TypeError, ValueError):
        return None


def variation_known(provider: Any) -> bool:
    """True when the prices differ enough for "better hour" to mean anything.

    Judged RELATIVE to the day's own average, so the answer is the same for
    a tariff quoted in CHF, in cents, or in rupees. Every tariff model SEM
    supports lands somewhere sensible:

    * **flat / single rate** — spread 0 → False. Nothing to wait for.
    * **HT/NT with two DIFFERENT rates** — spread = |HT − NT| → True. The
      clock is a legitimate mapping here; what was never legitimate was
      using it when the two rates are equal.
    * **multi-tier ToU** (2.0TD, Nighttime Savers) — spread across the
      tiers → True; #728's tier detection still decides WHICH tier.
    * **dynamic / spot** — today's own min…max → True on any real curve,
      False on the flat day the percentile classifier already names.
    * **negative prices** — not a comparison at all; handled by the raw
      sign wherever it matters (export verdicts, force-charge), never by
      this function.
    """
    s = spread(provider)
    if s is None:
        return False
    avg = _average_price(provider)
    if avg is None or avg <= 0.0:
        return s > FLAT_SPREAD_FLOOR
    return (s / avg) > FLAT_SPREAD_FRACTION


def _average_price(provider: Any) -> Optional[float]:
    """The day's mean, for the relative test. ``None`` when unreadable."""
    try:
        data = provider.get_tariff_data()
    except Exception:  # noqa: BLE001
        return None
    avg = getattr(data, "today_avg_price", None)
    if avg is None:
        lo, hi = getattr(data, "today_min_price", None), getattr(data, "today_max_price", None)
        if lo is None or hi is None:
            return None
        try:
            avg = (float(lo) + float(hi)) / 2.0
        except (TypeError, ValueError):
            return None
    try:
        return abs(float(avg))
    except (TypeError, ValueError):
        return None


def comparative_level(provider: Any,
                      when: Optional[datetime] = None) -> Optional[PriceLevel]:
    """The level, when a comparison stands behind it — else ``None``.

    ``when`` asks about a specific hour (the planners' question); omitted, it
    asks about now. A provider that declines to answer, or a horizon with no
    spread, is ``None`` — never a level, and never the cheap bucket.
    """
    if provider is None or not variation_known(provider):
        return None
    try:
        level = (provider.get_price_level_at(when) if when is not None
                 else provider.get_price_level())
    except Exception:  # noqa: BLE001
        return None
    if level is None:
        return None
    name = _name(level)
    for lv in PriceLevel:
        if lv.value == name:
            return lv
    return None


def is_cheap(provider: Any, when: Optional[datetime] = None) -> bool:
    """Is this hour cheaper than the others? **False** when nobody knows."""
    return _name(comparative_level(provider, when)) in _CHEAP_NAMES


def is_expensive(provider: Any, when: Optional[datetime] = None) -> bool:
    """Is this hour dearer than the others? **False** when nobody knows."""
    return _name(comparative_level(provider, when)) in _EXPENSIVE_NAMES


def is_cheap_name(level: Any) -> bool:
    """The membership test for a level SEM has already resolved.

    For consumers that are handed a level string rather than a provider
    (the fleet-state threading). Unknown/None is False, as everywhere else.
    """
    return _name(level) in _CHEAP_NAMES


def is_expensive_name(level: Any) -> bool:
    return _name(level) in _EXPENSIVE_NAMES
