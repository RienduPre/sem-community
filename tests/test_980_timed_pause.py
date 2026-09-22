"""#980 — a timed pause: charge mode Off for a while, then back as it was.

@RienduPre, discussion #958, having just accepted the #898 answer on Off:

    "I now understand and it's a good option. But I still like to have an
    option to stop charging for some time if needed and I don't want to go
    to my Wallbox app for that. If it's possible to add an option like
    that, some sort of pause SEM charging."

The first build of this made SEM *hold* the charger stopped: a new intent
re-asserting DISABLE every cycle. A review took it apart — and the answer
that replaced it is better than the fix.

**Off is the pause.** Off is hands-off by construction (#898/#942): SEM
sends one stop for its own session and then nothing, so a box that restarts
itself is left alone. Holding it stopped instead means re-asserting against
a box that disagrees, which is a stop war — and #763's ceasefire would have
silently surrendered up to four hours of a one-hour pause on exactly the
self-restarting Pulsar this was built for.

So SEM adds the one part the user cannot do: remembering to put the mode
back. Two facts, two entities, and the pure function below decides all of
it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.solar_energy_management.coordinator.charge_pause import (
    PAUSE_DURATIONS,
    PAUSE_RESUME_MODE_KEY,
    PAUSE_UNTIL_KEY,
    deadline_for_minutes,
    duration_minutes,
    parse_deadline,
    press,
    tick,
)

NOW = datetime(2026, 9, 22, 19, 0, 0)


def _cfg(mode="solar_plus_cheap", **kw):
    c = {"id": "wallbox", "charge_mode": mode}
    c.update(kw)
    return c


def _armed(minutes=60, at=NOW):
    return deadline_for_minutes(minutes, at)


@pytest.mark.unit
class TestTheDropdown:

    def test_it_offers_durations_only(self):
        """No "resume now" entry: setting the charge mode back is already
        the gesture for that, and a second way to say it would be one more
        option earning its place by undoing another (#830)."""
        assert [duration_minutes(k) for k in PAUSE_DURATIONS] == [
            30, 60, 120, 240, 480, 720]

    def test_an_unknown_option_does_nothing(self):
        assert duration_minutes("fortnight") == 0
        assert duration_minutes(None) == 0
        assert press(_cfg(), "fortnight", NOW) == {}


@pytest.mark.unit
class TestPressingPause:

    def test_it_sets_the_mode_off_and_remembers_the_way_back(self):
        out = press(_cfg("solar_plus_cheap"), "1_hour", NOW)
        assert out["charge_mode"] == "off"
        assert out[PAUSE_RESUME_MODE_KEY] == "solar_plus_cheap"
        assert parse_deadline(out[PAUSE_UNTIL_KEY]) == NOW + timedelta(hours=1)

    def test_each_duration_lands_where_it_says(self):
        for option, minutes in PAUSE_DURATIONS.items():
            if minutes <= 0:
                continue
            out = press(_cfg(), option, NOW)
            assert parse_deadline(out[PAUSE_UNTIL_KEY]) == (
                NOW + timedelta(minutes=minutes)), option

    def test_re_arming_keeps_the_ORIGINAL_mode_to_return_to(self):
        """The trap: mid-pause the live mode IS off, because SEM put it
        there. Recording that as the mode to come back to would strand the
        charger in off for good."""
        paused = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60),
                                PAUSE_RESUME_MODE_KEY: "min_plus_solar"})
        out = press(paused, "4_hours", NOW + timedelta(minutes=10))
        assert out[PAUSE_RESUME_MODE_KEY] == "min_plus_solar"
        assert "charge_mode" not in out, "already off — nothing to write"

    def test_pausing_from_off_comes_back_to_off(self):
        out = press(_cfg("off"), "2_hours", NOW)
        assert out[PAUSE_RESUME_MODE_KEY] == "off"
        assert "charge_mode" not in out




def _resume(cfg, now):
    """The mode `tick` gives back this cycle, or None. Reading the dict the
    way the coordinator reads it, so a test cannot pass on a shape the
    coordinator never writes."""
    return tick(cfg, now).get("charge_mode")


@pytest.mark.unit
class TestWhenItRunsOut:

    def test_the_mode_comes_back(self):
        cfg = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60),
                             PAUSE_RESUME_MODE_KEY: "solar_plus_cheap"})
        assert _resume(cfg, NOW + timedelta(minutes=61)) == "solar_plus_cheap"

    def test_not_while_it_is_still_running(self):
        cfg = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60),
                             PAUSE_RESUME_MODE_KEY: "solar_plus_cheap"})
        assert _resume(cfg, NOW + timedelta(minutes=59)) is None

    def test_nothing_armed_is_nothing_to_do(self):
        assert _resume(_cfg("solar_only"), NOW) is None
        assert _resume({}, NOW) is None
        assert _resume(None, NOW) is None

    def test_a_user_who_changed_the_mode_keeps_it(self):
        """A pause is a convenience, never a claim on the knob. Snapping a
        mode back over a deliberate choice is the #779 class."""
        cfg = _cfg("always_max", **{PAUSE_UNTIL_KEY: _armed(60),
                                    PAUSE_RESUME_MODE_KEY: "solar_only"})
        assert _resume(cfg, NOW + timedelta(minutes=61)) is None

    def test_an_unreadable_deadline_releases_rather_than_holding(self):
        """#925 — the third state has to do something sensible, and here
        that is giving the charger back, not holding it off forever."""
        cfg = _cfg("off", **{PAUSE_UNTIL_KEY: "not-a-date",
                             PAUSE_RESUME_MODE_KEY: "solar_only"})
        assert _resume(cfg, NOW) == "solar_only"

    def test_a_pause_that_expired_while_ha_was_down_resumes_at_once(self):
        """The reason the stored fact is an instant and not a duration."""
        cfg = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60),
                             PAUSE_RESUME_MODE_KEY: "min_plus_solar"})
        assert _resume(cfg, NOW + timedelta(days=3)) == "min_plus_solar"

    def test_a_restart_does_not_hand_back_the_minutes_already_spent(self):
        cfg = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60),
                             PAUSE_RESUME_MODE_KEY: "solar_only"})
        assert _resume(cfg, NOW + timedelta(minutes=45)) is None
        assert _resume(cfg, NOW + timedelta(minutes=61)) == "solar_only"


@pytest.mark.unit
class TestCancellingIsSettingTheChargerBack:
    """There is no Resume button and no Resume option. Guido: *"instead of
    resume now the user could also just set the charger back"* — and they
    can, because the charge-mode select is right there. What that costs is
    the bookkeeping below: a pause nobody cancelled explicitly still has to
    be FORGOTTEN, or its record outlives it."""

    def test_taking_the_knob_back_ends_the_pause(self):
        cfg = _cfg("always_max", **{PAUSE_UNTIL_KEY: _armed(60),
                                    PAUSE_RESUME_MODE_KEY: "solar_only"})
        out = tick(cfg, NOW + timedelta(minutes=5))
        assert "charge_mode" not in out, "must not overwrite their choice"
        assert out[PAUSE_UNTIL_KEY] is None
        assert out[PAUSE_RESUME_MODE_KEY] is None

    def test_a_forgotten_record_would_hijack_a_later_deliberate_off(self):
        """The trap this clearing exists for. Cancel by picking a mode;
        hours later pick Off on purpose. A stale deadline — already in the
        past — would fire on the next cycle and put the OLD mode back over
        the Off they just chose."""
        stale = {PAUSE_UNTIL_KEY: _armed(60), PAUSE_RESUME_MODE_KEY: "solar_only"}
        cancelled = tick(_cfg("always_max", **stale), NOW + timedelta(minutes=5))
        assert cancelled[PAUSE_UNTIL_KEY] is None

        kept = _cfg("off", **stale)      # what it would look like unforgotten
        assert tick(kept, NOW + timedelta(hours=3))["charge_mode"] == "solar_only", (
            "this is the hijack — the assertion above is what prevents it"
        )

    def test_half_a_record_is_cleared_not_acted_on(self):
        """An upgrade, a hand-edited options file, a partial write — a
        deadline with nowhere to go back to."""
        expired = _cfg("off", **{PAUSE_UNTIL_KEY: _armed(60)})
        out = tick(expired, NOW + timedelta(minutes=61))
        assert out == {PAUSE_UNTIL_KEY: None, PAUSE_RESUME_MODE_KEY: None}

    def test_an_untouched_charger_is_written_nothing(self):
        """The tick runs every cycle for every charger. It must be silent
        on the ones with no pause, or it writes config forever (#829)."""
        assert tick(_cfg("solar_only"), NOW) == {}
        assert tick(_cfg("off"), NOW) == {}


@pytest.mark.unit
class TestItAddsNoStopWar:
    """The property that made this design the right one. SEM does not hold
    the contactor open against a box that disagrees — it selects a mode the
    user could have selected, and Off's hands-off contract does the rest."""

    def test_the_pause_never_produces_a_charger_command(self):
        """`press` writes CONFIG. Nothing here reaches an adapter, so
        #763's ceasefire cannot be triggered by a pause and cannot silently
        surrender one."""
        out = press(_cfg("always_max"), "12_hours", NOW)
        assert set(out) <= {"charge_mode", PAUSE_UNTIL_KEY, PAUSE_RESUME_MODE_KEY}

    def test_decide_has_no_pause_branch_left(self):
        """The retired design. A stray branch would put SEM back to
        re-asserting DISABLE, which is the thing this replaced."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "coordinator" / "decide.py").read_text(encoding="utf-8")
        assert "pause_remaining_min" not in src
