"""#980 — a SEM-enforced pause, which is not what Off means.

@RienduPre, discussion #958, having just accepted the #898 answer:

    "I now understand and it's a good option. But I still like to have an
    option to stop charging for some time if needed and I don't want to go
    to my Wallbox app for that. If it's possible to add an option like
    that, some sort of pause SEM charging."

Off is hands-off: ONE stop for whatever is drawing, then silence, so a
wallbox that restarts itself is left alone. His Pulsar does exactly that —
``stop_commanded_while_drawing 2`` in his own dump is the contract working,
and the reason he still has to open the app.

A pause is the other intent: keep acting, to hold it stopped.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.solar_energy_management.coordinator.charge_pause import (
    PAUSE_MAX_MIN,
    deadline_for_minutes,
    is_paused,
    parse_deadline,
    remaining_minutes,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerEnergy,
    ChargerIntent,
    ChargerPower,
    ChargerView,
    FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide import decide

NOW = datetime(2026, 9, 22, 19, 0, 0)


@pytest.mark.unit
class TestTheDeadlineIsTheWholeState:

    def test_arming_stores_a_wall_clock_deadline(self):
        assert deadline_for_minutes(60, NOW) == (NOW + timedelta(minutes=60)).isoformat()

    def test_zero_clears_it(self):
        """Resuming early is the same gesture as never having paused."""
        assert deadline_for_minutes(0, NOW) is None
        assert deadline_for_minutes(-30, NOW) is None

    def test_a_pause_cannot_outlast_the_maximum(self):
        armed = deadline_for_minutes(99999, NOW)
        assert parse_deadline(armed) == NOW + timedelta(minutes=PAUSE_MAX_MIN)

    def test_it_holds_until_the_deadline_and_not_after(self):
        armed = deadline_for_minutes(60, NOW)
        assert is_paused(armed, NOW) is True
        assert is_paused(armed, NOW + timedelta(minutes=59)) is True
        assert is_paused(armed, NOW + timedelta(minutes=60)) is False
        assert is_paused(armed, NOW + timedelta(hours=9)) is False

    def test_nothing_armed_is_not_paused(self):
        for value in (None, "", 0):
            assert is_paused(value, NOW) is False

    def test_an_unreadable_deadline_releases_rather_than_holds(self):
        """#925 — "I could not ask" is not "yes". The safe side of not
        knowing is letting the charger go, not holding a contactor open
        forever on a corrupt string."""
        assert is_paused("not-a-date", NOW) is False
        assert parse_deadline("not-a-date") is None

    def test_a_restart_does_not_extend_the_pause(self):
        """The reason the stored fact is a deadline and not a duration: a
        duration has to be re-armed on the way back up, which silently
        gives back the minutes the user had already spent."""
        armed = deadline_for_minutes(60, NOW)
        restarted_at = NOW + timedelta(minutes=45)
        assert remaining_minutes(armed, restarted_at) == 15.0


@pytest.mark.unit
class TestTheKnobReadsTheMinutesLeft:

    @pytest.mark.parametrize("elapsed,shown", [
        (0, 60.0), (1, 60.0), (44, 30.0), (46, 15.0), (59, 15.0), (60, 0.0),
    ])
    def test_it_rounds_up_so_it_never_reads_zero_while_holding(self, elapsed, shown):
        """A knob at 0 means released. It must not say that while SEM is
        still commanding the stop."""
        armed = deadline_for_minutes(60, NOW)
        assert remaining_minutes(armed, NOW + timedelta(minutes=elapsed)) == shown

    def test_nothing_armed_reads_zero(self):
        assert remaining_minutes(None, NOW) == 0.0


def _view(mode="solar_only", pause_min=0.0, connected=True, charging=True):
    return ChargerView(
        power=ChargerPower(charger_id="wallbox", power_w=10960.0,
                           connected=connected, charging=charging),
        energy=ChargerEnergy(charger_id="wallbox"),
        mode=mode,
        config={"id": "wallbox"},
        fleet=FleetContext(solar_w=0.0, home_w=500.0),
        pause_remaining_min=pause_min,
    )


@pytest.mark.unit
class TestTheDecision:

    def test_a_paused_charger_is_disabled_every_cycle(self):
        """DISABLE is the intent whose contract already says: invoke the
        brand disable and re-assert it until the draw drops. That IS the
        enforcement — no new actuation, only a reason to command it."""
        d = decide(_view(pause_min=45.0))
        assert d.intent is ChargerIntent.DISABLE, d.reason
        assert d.commanded_amps == 0
        assert "paused" in d.reason

    def test_the_reason_says_how_much_longer(self):
        assert "45 min" in decide(_view(pause_min=45.0)).reason

    def test_it_outranks_off(self):
        """Off RELEASES control. A user who arms a pause on a released
        charger means the pause — that is the entire request."""
        d = decide(_view(mode="off", pause_min=45.0))
        assert d.intent is ChargerIntent.DISABLE, d.reason

    def test_without_a_pause_off_is_still_hands_off(self):
        d = decide(_view(mode="off"))
        assert d.intent is ChargerIntent.RELEASE, d.reason

    def test_the_stability_bridge_cannot_bridge_over_it(self):
        """A pause is structural: the user said stop. Letting the
        anti-flap layer hold the charge through it would be the #461
        bridge doing the exact opposite of what it is for."""
        assert decide(_view(pause_min=45.0)).bridgeable is False

    @pytest.mark.parametrize("mode", [
        "solar_only", "min_plus_solar", "solar_plus_cheap", "always_max",
    ])
    def test_every_mode_honours_it(self, mode):
        assert decide(_view(mode=mode, pause_min=15.0)).intent is ChargerIntent.DISABLE

    def test_zero_changes_nothing(self):
        assert decide(_view(pause_min=0.0)).intent is not ChargerIntent.DISABLE


@pytest.mark.unit
class TestTheKnobIsWiredToTheDecision:
    """The pure layer above is only worth anything if something arms it.
    Testing the helpers alone leaves the one line that persists the
    deadline free to be reverted with every test still green."""

    def _knob(self, chargers):
        from unittest.mock import MagicMock

        from homeassistant.components.number import NumberEntityDescription

        from custom_components.solar_energy_management.number import (
            SEMChargerPauseNumber,
        )
        coordinator = MagicMock()
        coordinator.config = {"ev_chargers": chargers}
        entry = MagicMock()
        entry.entry_id = "e"
        entry.options = {"ev_chargers": chargers}
        entry.data = {"ev_chargers": chargers}
        knob = SEMChargerPauseNumber.__new__(SEMChargerPauseNumber)
        knob.coordinator = coordinator
        knob._entry = entry
        knob._charger_id = "wallbox"
        knob.entity_description = NumberEntityDescription(
            key="charger_wallbox_pause_charging")
        knob.hass = MagicMock()
        knob.async_write_ha_state = MagicMock()   # not added to a platform
        return knob

    def test_it_reads_the_minutes_left_off_the_stored_deadline(self):
        import homeassistant.util.dt as dt_util
        armed = deadline_for_minutes(60, dt_util.now())
        knob = self._knob([{"id": "wallbox", "pause_charging_until": armed}])
        assert knob._remaining() == 60.0

    def test_no_deadline_reads_zero(self):
        assert self._knob([{"id": "wallbox"}])._remaining() == 0.0

    @pytest.mark.asyncio
    async def test_setting_it_persists_a_deadline_not_a_duration(self):
        from unittest.mock import patch
        knob = self._knob([{"id": "wallbox"}])
        with patch(
            "custom_components.solar_energy_management.persist_per_charger_option"
        ) as persist:
            await knob.async_set_native_value(60)
        assert persist.called
        _hass, _entry, _coord, cid, key, value = persist.call_args.args
        assert cid == "wallbox"
        assert key == "pause_charging_until"
        assert parse_deadline(value) is not None, value

    @pytest.mark.asyncio
    async def test_setting_it_to_zero_clears_the_stored_deadline(self):
        from unittest.mock import patch
        knob = self._knob([{"id": "wallbox"}])
        with patch(
            "custom_components.solar_energy_management.persist_per_charger_option"
        ) as persist:
            await knob.async_set_native_value(0)
        assert persist.call_args.args[5] is None
        assert knob._attr_native_value == 0.0

    def test_the_coordinator_resolves_it_for_the_view(self):
        """The ONE producer: three view builders would otherwise each have
        to ask the clock, and one of them would forget."""
        from unittest.mock import MagicMock
        import homeassistant.util.dt as dt_util

        from custom_components.solar_energy_management.coordinator import (
            SEMCoordinator,
        )
        armed = deadline_for_minutes(30, dt_util.now())
        coord = SEMCoordinator.__new__(SEMCoordinator)
        coord.config = {"ev_chargers": [
            {"id": "wallbox", "pause_charging_until": armed},
            {"id": "keba"},
        ]}
        assert coord._charger_pause_remaining_min("wallbox") == 30.0
        assert coord._charger_pause_remaining_min("keba") == 0.0
        assert coord._charger_pause_remaining_min("nobody") == 0.0
