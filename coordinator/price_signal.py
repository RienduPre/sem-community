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

from ..tariff.tariff_provider import (
    CHEAP_LEVELS, EXPENSIVE_LEVELS, LEVEL_FLAT, LEVEL_NO_PRICES, PriceLevel,
)

__all__ = [
    "CHEAP_LEVELS", "EXPENSIVE_LEVELS", "LEVEL_FLAT", "LEVEL_NO_PRICES",
    "FLAT_SPREAD_FRACTION", "FLAT_SPREAD_FLOOR", "spread", "variation_known",
    "comparative_level", "absence_word", "is_cheap", "is_expensive",
    "is_cheap_name", "is_expensive_name",
]

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

#: The two level sets are DEFINED beside the enum they are made of, in
#: ``tariff.tariff_provider``, and re-exported here as the vocabulary's
#: public surface — importing them the other way round would close a cycle.
#: They exist in exactly one place either way: six independently hand-typed
#: copies existed before #994 and one had already drifted
#: (``surplus_controller`` damped the pool on "expensive" while silently
#: ignoring "very_expensive"). A reviewer found two more survivors inside
#: the provider itself after the first pass; those now import these.

_CHEAP_NAMES = frozenset(lv.value for lv in CHEAP_LEVELS)
_EXPENSIVE_NAMES = frozenset(lv.value for lv in EXPENSIVE_LEVELS)


def _name(level: Any) -> str:
    """A level's wire string, whatever shape it arrives in (enum, str, None)."""
    if level is None:
        return ""
    return str(getattr(level, "value", level) or "").strip().lower()


#: Below this many upcoming slots the forward curve is too short to be a
#: horizon of its own — the same floor the percentile classifier uses.
MIN_HORIZON_POINTS: int = 4


def _horizon(provider: Any) -> Optional[tuple]:
    """``(low, high, mean)`` over the horizon the LEVEL was judged against.

    (#994, second review) This used ``today_min_price``/``today_max_price``
    — today by the wall clock — while the dynamic classifier buckets against
    a ROLLING window that runs into tomorrow. Two references, one question:
    a day whose own slots are flat beside a curve that rises sharply after
    midnight produced a sensor reading ``cheap`` and a fleet reading no
    level at all, for the same instant. The forward curve is both the
    classifier's own reference and the only one a consumer can act on — you
    cannot move load into an hour that has passed — so prefer it, and fall
    back to today's range when it is too short to mean anything.
    """
    if provider is None:
        return None
    try:
        data = provider.get_tariff_data()
    except Exception:  # noqa: BLE001 — a provider that cannot answer is unknown
        return None
    prices = []
    for p in (getattr(data, "upcoming_prices", None) or []):
        value = getattr(p, "price", None)
        if value is None:
            continue
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            continue
    if len(prices) >= MIN_HORIZON_POINTS:
        return min(prices), max(prices), sum(prices) / len(prices)
    lo, hi = getattr(data, "today_min_price", None), getattr(data, "today_max_price", None)
    if lo is None or hi is None:
        return None
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return None
    avg = getattr(data, "today_avg_price", None)
    try:
        avg = float(avg) if avg is not None else (lo + hi) / 2.0
    except (TypeError, ValueError):
        avg = (lo + hi) / 2.0
    return lo, hi, avg


def spread(provider: Any) -> Optional[float]:
    """The horizon's price range, or ``None`` when it cannot be read.

    Every provider populates ``today_min_price``/``today_max_price`` — and
    for the two clock-based ones those ARE the two configured rates, so the
    fact that refutes their verdict is already in their own payload. A
    dynamic provider additionally publishes the forward curve, which is the
    reference its own classifier used; see ``_horizon``.
    """
    h = _horizon(provider)
    if h is None:
        return None
    return abs(h[1] - h[0])


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
    """The horizon's mean, for the relative test. ``None`` when unreadable.

    Same horizon as ``spread``, necessarily: a range measured over one
    window and a mean over another do not make a ratio.
    """
    h = _horizon(provider)
    return None if h is None else abs(h[2])


def comparative_level(provider: Any,
                      when: Optional[datetime] = None) -> Optional[PriceLevel]:
    """The level, when a comparison stands behind it — else ``None``.

    ``when`` asks about a specific hour (the planners' question); omitted, it
    asks about now. A provider that declines to answer, or a horizon with no
    spread, is ``None`` — never a level, and never the cheap bucket.

    **NEGATIVE is the exception, and it is not one of principle.** Being paid
    to consume is an ABSOLUTE fact about a price, not a claim about some
    other hour, so it survives a horizon nobody could read — a spot entity
    with no published curve reports its own negative state perfectly well.
    Gating it on ``variation_known`` cost the house sink its hold in a
    negative import hour (``test_921_sink_scenario``), which is the one hour
    where holding the pack is unarguable.
    """
    if provider is None:
        return None
    try:
        level = (provider.get_price_level_at(when) if when is not None
                 else provider.get_price_level())
    except Exception:  # noqa: BLE001
        return None
    if level is None:
        return None
    name = _name(level)
    resolved = next((lv for lv in PriceLevel if lv.value == name), None)
    if resolved is None:
        return None
    if resolved is PriceLevel.NEGATIVE:
        return resolved
    return resolved if variation_known(provider) else None


def absence_word(provider: Any) -> str:
    """Which absence to publish when ``comparative_level`` answers ``None``.

    Two different refusals reach that ``None`` and they are not the same
    thing to say out loud. The PROVIDER may have declined — no curve, too
    few points, equal rates, a day that holds one price — and it names which
    (``TariffData.level_absence``). Or the provider answered and THIS layer
    declined, because the horizon it can still act on holds no difference:
    the prices are known perfectly well, they simply do not vary from here.
    That is ``flat``, not ``no_prices``, and reporting the provider's word
    for it told the user their price feed was broken when it was fine.
    """
    try:
        data = provider.get_tariff_data()
    except Exception:  # noqa: BLE001
        return LEVEL_NO_PRICES
    if getattr(data, "price_level", None) is None:
        return str(getattr(data, "level_absence", LEVEL_NO_PRICES)
                   or LEVEL_NO_PRICES)
    return LEVEL_FLAT


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
