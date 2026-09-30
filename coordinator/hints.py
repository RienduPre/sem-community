"""(#1019) Hints — the five categories and their switch keys.

The engine that decides when a hint fires lives here too (task C2); this
first cut names the categories so the switches, the card and the tests
agree on one spelling.
"""
from __future__ import annotations

HINT_CATEGORIES = (
    "silent_input",
    "night_load",
    "grid_rise",
    "cheap_now",
    "weekly_summary",
)

HINT_SWITCH_KEYS = tuple(f"hint_{category}" for category in HINT_CATEGORIES)
