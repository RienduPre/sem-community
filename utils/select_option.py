"""The option a select LISTS, for a string SEM was given (#1039).

Home Assistant's ``select.select_option`` takes an option exactly as the
entity lists it in its ``options`` attribute. What a person sees in the UI is
something else: the option's LABEL, translated through the integration's
``translation_key``. Ohme lists ``max_charge`` and shows "Max charge"; GoodWe
lists ``eco_charge`` and shows "Eco charge mode". A label written as an
option is refused by HA before it reaches the device, so the charger never
changed mode while SEM believed it had.

Every select write SEM makes goes through :func:`listed_option`:

1. a value the select lists is written as it is;
2. otherwise the ONE listed option whose label (the user's language, then
   English) or whose own spelling is the same words as the value;
3. otherwise the value is returned unchanged — HA refuses it with its own
   error, which names the options. SEM never guesses between two candidates.

``tests/test_1039_listed_option.py`` holds every select write in the package
to this rule.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

_LOGGER = logging.getLogger(__name__)

_WORDS = re.compile(r"[^\W_]+")


def _words(text: Any) -> str:
    """``"Max charge"``, ``"max_charge"`` and ``"MAX-CHARGE"`` are one key."""
    return "_".join(_WORDS.findall(str(text).casefold()))


def _labels(hass: Any, entity_id: str, options: List[str]) -> Dict[str, List[str]]:
    """Option → the labels HA shows for it, from HA's own translation cache.

    A probe: any failure (no registry entry, no ``translation_key``, a test
    double) is "no labels", never an error."""
    try:
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.translation import (
            async_get_cached_translations,
        )
        entry = er.async_get(hass).async_get(entity_id)
        platform = getattr(entry, "platform", None)
        key = getattr(entry, "translation_key", None)
        if not isinstance(platform, str) or not isinstance(key, str):
            return {}
        domain = entity_id.split(".", 1)[0]
        languages = [getattr(hass.config, "language", None), "en"]
        out: Dict[str, List[str]] = {}
        for lang in dict.fromkeys(x for x in languages if isinstance(x, str)):
            table = async_get_cached_translations(hass, lang, "entity", platform)
            for option in options:
                label = table.get(
                    f"component.{platform}.entity.{domain}.{key}.state.{option}")
                if isinstance(label, str) and label:
                    out.setdefault(option, []).append(label)
        return out
    except Exception:  # noqa: BLE001 — a probe, never fatal
        return {}


def listed_option(hass: Any, entity_id: Any, wanted: Any) -> Any:
    """The option ``entity_id`` lists for ``wanted`` — see the module doc."""
    if not isinstance(wanted, str) or not wanted or not entity_id:
        return wanted
    try:
        state = hass.states.get(entity_id)
        options = getattr(state, "attributes", {}).get("options")
    except Exception:  # noqa: BLE001 — no readable select, nothing to map
        return wanted
    if not isinstance(options, (list, tuple)) or not options:
        return wanted
    options = [str(o) for o in options]
    if wanted in options:
        return wanted
    key = _words(wanted)
    labels = _labels(hass, str(entity_id), options)
    hits = [o for o in options
            if _words(o) == key
            or any(_words(label) == key for label in labels.get(o, ()))]
    if len(hits) != 1:
        return wanted
    _LOGGER.debug("%s: '%s' is not an option it lists — writing '%s' (#1039)",
                  entity_id, wanted, hits[0])
    return hits[0]
