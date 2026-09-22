"""(#970) The battery as the peak-shaving instrument.

@Hanzzzie85 (discussion #969, SolarEdge + a heat-pumped outdoor pool, on a
capacity tariff) wrote the worked example this module implements:

    "house requires 5kw, i have a 2kw solar power at the moment and peak
    import can be 2.5kw, so lacking 0.5kw. battery can, in my case through
    automation, give the house 500w that it requires to limit import
    capacity at 2.5kw."

Note what he is NOT asking for. A battery in plain self-consumption already
covers the whole 3 kW gap and the meter reads zero — that is the default and
it costs him nothing on the energy bill. What he wants is the opposite: let
the grid fund everything **up to** the ceiling, because import below the
capacity limit adds nothing to the bill he is actually billed on, and keep
the kilowatt-hours in the pack for the evening. The battery supplies only
the part the meter is not allowed to carry.

So this is not a new drain path. It can only ever discharge the pack LESS
than today, or leave it alone — which is why the whole feature is a cap on
``LIMIT_DISCHARGE``, the write target SEM already owns (and the one he
already drives by hand: SolarEdge's *Storage Discharge Limit*).

The second half of his ask is the escape:

    "When battery gets to a certain level, and next day yield is high
    enough, it goes to 0 grid to maximize battery offload untill the
    evening."

That question is already answered, by ``spendable_budget`` (#778): how much
of the pack is genuinely surplus given tonight's need and tomorrow's
forecast. When it says there is something spendable, holding import at the
ceiling is miserliness — the sun will refill what the house takes. The shave
lifts and the battery goes back to covering everything.
"""
from __future__ import annotations

from typing import Optional


def shave_discharge_limit_w(
    home_w: float,
    solar_w: float,
    allowed_import_w: Optional[float],
    *,
    battery_count: int = 1,
) -> Optional[float]:
    """Per-battery discharge cap that lands the meter ON the slot ceiling.

    ``allowed_import_w`` is #864's slot allowance: what the rest of the
    billed 15-minute slot may average so the slot lands on target.
    ``None`` means there is no ceiling — an unlimited install, or a slot
    the guard could not compute — and absence of a ceiling is NOT a
    ceiling of zero (#864's own rule, and #925's in general). Returning
    ``None`` here is what tells the caller to leave the battery alone.

    The split by ``battery_count`` is #531's: N packs each told to supply
    the whole gap over-supply it N times over.
    """
    if allowed_import_w is None:
        return None
    try:
        gap = float(home_w) - float(solar_w) - float(allowed_import_w)
    except (TypeError, ValueError):
        return None
    n = max(1, int(battery_count or 1))
    return max(0.0, gap) / n


def zero_grid_open(spendable_kwh: Optional[float]) -> bool:
    """Is tonight's budget saying the pack can afford to cover the house?

    One consumer more of #778's number, not a second calculation of it.
    ``None`` — the budget could not be computed — is not "yes" (#925): the
    shave stays on, which is the conservative side for the pack.
    """
    if spendable_kwh is None:
        return False
    try:
        return float(spendable_kwh) > 0.0
    except (TypeError, ValueError):
        return False
