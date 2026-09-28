"""(#1018) A Repair notice is short: the one thing to do, in every language.

Seven notices had grown to 148–285 words with nested conditions. The detail
belongs behind Learn more (every raiser passes a docs URL — test_831), so the
notice itself stays under the cap for anything a person reads. Chinese has no
word spaces, so it is capped by characters.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FILES = [_ROOT / "strings.json"] + sorted((_ROOT / "translations").glob("*.json"))

DESC_WORDS = 150      # the issue cap; a notice should sit well under it
TITLE_WORDS = 15
ZH_DESC_CHARS = 800


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_repair_text_is_under_the_cap(path):
    issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
    if path.name == "zh-Hans.json":
        over = {k: len(v["description"]) for k, v in issues.items()
                if "description" in v and len(v["description"]) > ZH_DESC_CHARS}
    else:
        over = {k: _words(v["description"]) for k, v in issues.items()
                if "description" in v and _words(v["description"]) > DESC_WORDS}
    assert not over, f"{path.name}: over the cap: {over}"
    titles = {k: _words(v["title"]) for k, v in issues.items()
              if _words(v.get("title", "")) > TITLE_WORDS}
    assert not titles, f"{path.name}: titles over {TITLE_WORDS} words: {titles}"


def test_the_seven_are_short_everywhere():
    """The seven #1018 rewrote stay short in every language, not only in English."""
    seven = ("charger_failsafe_suspected", "battery_force_discharge_unsupported",
             "load_shed_futile", "load_current_control_wrong_unit",
             "charger_phase_count_mismatch", "charger_stop_war_stand_down",
             "battery_control_write_not_taken")
    for path in _FILES:
        issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
        for key in seven:
            text = issues[key]["description"]
            size = len(text) if path.name == "zh-Hans.json" else _words(text)
            cap = 300 if path.name == "zh-Hans.json" else 90
            assert size <= cap, f"{path.name}: {key} is {size} — the rewrite grew back"
