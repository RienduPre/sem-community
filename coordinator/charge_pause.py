"""(#980) A SEM-enforced pause — the opposite intent to Off.

@RienduPre, discussion #958, after accepting the #898 answer on Off mode:

    "I now understand and it's a good option. But I still like to have an
    option to stop charging for some time if needed and I don't want to go
    to my Wallbox app for that. If it's possible to add an option like
    that, some sort of pause SEM charging."

*Off* is hands-off by design: SEM sends ONE stop for whatever is drawing
and then sends nothing, so a wallbox that restarts itself is left alone —
which is exactly what his Pulsar does, and exactly why he still has to open
its app. ``stop_commanded_while_drawing 2`` in his own dump is that contract
working.

A pause is the other intent: *keep acting, to hold it stopped*. It maps to
``ChargerIntent.DISABLE``, whose contract already says the adapter invokes
the brand disable and re-asserts it every cycle until the draw drops. So
there is no new actuation here at all — only a reason to command it.

The whole state is ONE per-charger key: the wall-clock deadline. A duration
would have had to be re-armed after a restart (silently extending the pause
the user asked for), and a countdown that lives only in memory would have
been lost by it. A deadline survives both and answers "how much longer?"
without a second number to keep in step.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

#: The per-charger config key. One key, one fact: when the pause ends.
PAUSE_UNTIL_KEY = "pause_charging_until"

#: The knob's granularity, in minutes. The displayed remaining time rounds UP
#: to this, so a pause never reads 0 while it is still holding.
PAUSE_STEP_MIN: int = 15

#: The longest pause the knob offers. Twelve hours is a working day away from
#: the house; beyond that the user wants Off, which is a different intent.
PAUSE_MAX_MIN: int = 720


def parse_deadline(value) -> Optional[datetime]:
    """The stored deadline as a datetime, or ``None`` — nothing armed.

    An unparseable value is ``None``: a pause SEM cannot read is a pause it
    must not enforce, because the alternative is holding a charger stopped
    forever on a corrupt string (#925 — "I could not ask" is not "yes", and
    here the safe side of not-knowing is releasing, not holding).
    """
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def pause_remaining_s(value, now: datetime) -> Optional[float]:
    """Seconds left on an armed pause, or ``None`` when none is holding."""
    deadline = parse_deadline(value)
    if deadline is None or now is None:
        return None
    try:
        left = (deadline - now).total_seconds()
    except TypeError:          # naive vs aware — a stored value from elsewhere
        return None
    return left if left > 0 else None


def is_paused(value, now: datetime) -> bool:
    """Is this charger being held stopped right now?"""
    return pause_remaining_s(value, now) is not None


def remaining_minutes(value, now: datetime, step: int = PAUSE_STEP_MIN) -> float:
    """What the knob reads: minutes left, rounded UP to ``step``.

    Rounding up rather than to nearest, so the last quarter hour of a pause
    still reads 15 and never 0 — a knob at 0 means released, and it must not
    say that while SEM is still holding the contactor open.
    """
    left = pause_remaining_s(value, now)
    if left is None:
        return 0.0
    step = max(1, int(step or 1))
    minutes = left / 60.0
    return float(min(PAUSE_MAX_MIN, -(-minutes // step) * step))


def deadline_for_minutes(minutes, now: datetime) -> Optional[str]:
    """The value to store when the user sets the knob to ``minutes``.

    ``None`` clears the pause — that is how resuming early is spelled, and
    it is the same gesture as never having paused.
    """
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return None
    if m <= 0 or now is None:
        return None
    m = min(m, float(PAUSE_MAX_MIN))
    return (now + timedelta(minutes=m)).isoformat()
