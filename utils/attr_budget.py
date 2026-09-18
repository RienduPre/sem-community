"""#979 — the recorder's attribute cap, checked on the side that is capped.

Home Assistant stores an entity's attributes only while the RECORDED subset
of them fits ``MAX_STATE_ATTRS_BYTES`` (16 KB). Past that it stores
**nothing** — not the oversize attribute, not the six small ones beside it —
warns every cycle, and the entity's history is empty forever
(``recorder.db_schema.StateAttributes.shared_attrs_bytes_from_event``).

"Recorded subset" is the whole point. ``_unrecorded_attributes`` is removed
BEFORE that measurement, so an attribute declared there costs the cap
nothing however big it is — which is how SEM's large live-card helpers are
meant to ride (#581). RienduPre's ``diag_charger_control`` (#979) blew the cap
because ONE attribute on it — #814's detection report, whose size grows with
the install — had never been added to that hand-maintained set, while its own
sibling ``control_entities`` had (class 24).

``fit_state_attributes`` is the backstop that makes the omission survivable:
it measures what the recorder will measure and, if that would not be stored,
drops the largest RECORDED attributes until it is — largest first, because
that is the one costing the others their history — leaving
``attributes_trimmed`` so the surface says it is incomplete instead of
pretending. Unrecorded attributes are never touched: the cards read those off
the live state, and the recorder was never going to see them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Collection, Dict, Mapping, Optional

from ..consts.core import RECORDER_ATTR_BUDGET_BYTES

_LOGGER = logging.getLogger(__name__)

# Room for the ``attributes_trimmed`` marker itself, so adding the honesty
# note can never be what pushes the set back over the cap.
_MARKER_HEADROOM = 256

TRIMMED_KEY = "attributes_trimmed"


def json_size(value: Any) -> Optional[int]:
    """Serialised size in bytes, or ``None`` when it cannot be measured.

    ``default=str`` only affects the MEASUREMENT (a datetime is sized as the
    ISO string HA's own encoder writes); the published value is untouched. An
    unmeasurable payload is never trimmed — refusing to guess is the #660
    rule, and a test double is the common case.
    """
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):  # noqa: BLE001
        return None


def fit_state_attributes(
    attrs: Optional[Mapping[str, Any]],
    unrecorded: Optional[Collection[str]] = None,
    limit: int = RECORDER_ATTR_BUDGET_BYTES,
) -> Optional[Dict[str, Any]]:
    """Keep the recorded half of ``attrs`` inside what the recorder will store.

    ``limit`` is the cap MINUS the attributes HA lays over ours before the
    recorder measures (``friendly_name``, unit, device class, …): the gate
    must leave that room, or an entity within a hundred bytes of the cap
    reproduces the very symptom it exists to stop (challenge record).

    Returns the input unchanged when the recorded subset already fits (the
    overwhelmingly common case — one ``json.dumps`` of a small dict) or when
    its size cannot be measured. Otherwise returns a copy with the largest
    recorded non-scalar attributes removed, plus ``attributes_trimmed``: the
    names that went, so a reader knows to go to the diagnostics download
    rather than conclude the data does not exist.
    """
    if not attrs:
        return attrs if attrs is None else dict(attrs)
    exempt = set(unrecorded or ())
    recorded = {k: v for k, v in attrs.items() if k not in exempt}
    size = json_size(recorded)
    if size is None or size <= limit:
        return attrs if isinstance(attrs, dict) else dict(attrs)

    out: Dict[str, Any] = dict(attrs)
    dropped: list[str] = []
    budget = max(0, limit - _MARKER_HEADROOM)
    while True:
        sizes = {
            k: json_size(v) or 0
            for k, v in out.items()
            if k not in exempt
            and not isinstance(v, (str, int, float, bool, type(None)))
        }
        if not sizes:
            break
        biggest = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))[0]
        del out[biggest]
        dropped.append(biggest)
        remaining = json_size({k: v for k, v in out.items() if k not in exempt})
        if remaining is None or remaining <= budget:
            break

    if dropped:
        out[TRIMMED_KEY] = dropped
        _LOGGER.debug(
            "Recorded attributes exceeded the recorder cap (%d > %d bytes); "
            "dropped %s — the full payload is in the diagnostics download",
            size, limit, ", ".join(dropped),
        )
    return out
