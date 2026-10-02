"""Which way a charger's start/stop switch reads (#1042).

A switch says in its name what "on" means. Most charger switches are named
for the charge — NRGkick's "Charging enabled", OCPP's "Charge control",
Wallbox's "Pause/resume" — and are on while the box may charge. V2C's
"Pause session" (core key ``paused``) is named for the pause: on is the
stop, and ``turn_on`` pauses the box. SEM drove it like the others, so every
start paused the charge and every stop resumed it.

Every write SEM makes to a charger's start/stop switch, and every read of
one, goes through this module. ``tests/test_1042_pause_switch.py`` holds the
package to that rule.
"""
from __future__ import annotations

from typing import Any, Optional

from ..consts.devices import names_a_pause


def on_means_paused(hass: Any, entity_id: Any) -> bool:
    """True when this switch's name says it pauses: on is the stop.

    Home Assistant's translation key is asked first. The integration wrote
    it, and it is the same in every language: a German V2C's id ends in
    ``vorgang_pausieren``, its key is still ``paused``. An owner who renamed
    the id renamed a label, not what on does. The id's words decide only
    when there is no key (a template switch, an ``input_boolean``, an
    integration without translations).
    """
    eid = str(entity_id or "")
    if not eid.startswith(("switch.", "input_boolean.")):
        return False
    key = None
    try:
        from homeassistant.helpers import entity_registry as er
        entry = er.async_get(hass).async_get(eid)
        key = getattr(entry, "translation_key", None) if entry is not None else None
    except Exception:  # noqa: BLE001 — no registry: the id is all there is
        key = None
    if isinstance(key, str) and key:
        return names_a_pause(key)
    return names_a_pause(eid.split(".", 1)[1])


def switch_service(hass: Any, entity_id: Any, *, run: bool) -> str:
    """The service that lets the charge run (``run=True``) or stops it."""
    on = run != on_means_paused(hass, entity_id)
    return "turn_on" if on else "turn_off"


def reads_running(hass: Any, entity_id: Any, state: Any) -> Optional[bool]:
    """Does this switch state let the charge run?

    ``None`` for anything but ``on``/``off``: ``unavailable``, ``unknown``
    or no state at all is not an answer either way (#945)."""
    if state not in ("on", "off"):
        return None
    return (state == "on") != on_means_paused(hass, entity_id)
