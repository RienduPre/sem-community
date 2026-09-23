"""(#891) The house as the inverter measures it — shown, not used.

@SandmanNCL's Fronius GEN24 publishes the house load directly. SEM works it
out from four other readings, and on a hybrid the battery runs through the
inverter's DC side, so the conversion losses are never measured: they land
in that arithmetic. His two numbers differ by 30-60 W.

His complaint was not that SEM decides badly. It was that two dashboards
showing the same house disagree. So this publishes his number beside SEM's
and names the difference, and leaves ``home_consumption_power`` alone.

Why it is not used. A first build substituted the measured value into the
house figure, and a review found eleven consumers that were entitled — by
construction — to assume the energy balance closes. The worst inflated the
surplus every charger's decision runs on, handing the car watts the sun was
not producing. And the gain would have been nothing anyone could perceive:
30-60 W against a 1200 W surplus gate flips no decision SEM makes.

One number is better for showing. The other is what closes the balance.
They are different facts, and pretending they are one was the mistake.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from .units import power_state_to_watts

_LOGGER = logging.getLogger(__name__)


def read_house_meter(hass, entity_id: Optional[str]) -> Optional[float]:
    """The house in watts as ``entity_id`` reports it, or ``None``.

    ``None`` is every way of not having a number — nobody configured one,
    the entity is gone, it is unavailable this cycle, or it reads negative.
    All of them mean "say nothing", never "the house is drawing nothing":
    a fabricated zero here would be published on a sensor people read
    (#818, #925).

    A negative reading is refused because a house does not generate. A minus
    sign means a sign convention nobody declared, and guessing at one broke
    every Huawei install once (00e449c).
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", None, ""):
        return None
    watts = power_state_to_watts(state, default=None)
    if watts is None or not math.isfinite(watts) or watts < 0:
        return None
    return float(watts)


def house_gap_w(derived_w: float, measured_w: Optional[float]) -> Optional[float]:
    """What SEM's own figure has over the meter. ``None`` when there is no
    meter to compare with.

    SIGNED on purpose. The first build clamped this at zero and was blind to
    a meter reading HIGHER than the derived sum — which is the case most
    worth seeing, because it means a wrong scale, a wrong phase count, or a
    sensor that also counts the car.
    """
    if measured_w is None:
        return None
    try:
        return float(derived_w) - float(measured_w)
    except (TypeError, ValueError):
        return None
