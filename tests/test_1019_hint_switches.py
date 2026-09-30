"""#1019 — five hint categories, each a switch, all off until a person
flips one. A flip writes the entry option and the running config, like
every other persisted flag."""
import json
import os
from unittest.mock import MagicMock

import pytest
from homeassistant.components.switch import SwitchEntityDescription

from custom_components.solar_energy_management.persisted_flags import (
    PERSISTED_FLAG_DEFAULTS,
)
from custom_components.solar_energy_management.switch import (
    SEMSolarSwitch, SWITCH_TYPES,
)
from custom_components.solar_energy_management.coordinator.hints import (
    HINT_CATEGORIES, HINT_SWITCH_KEYS,
)

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheFive:
    def test_the_categories_and_their_switch_keys(self):
        assert HINT_CATEGORIES == (
            "silent_input", "night_load", "grid_rise", "cheap_now", "weekly_summary")
        assert HINT_SWITCH_KEYS == tuple(f"hint_{c}" for c in HINT_CATEGORIES)

    def test_every_category_is_a_switch_after_the_morning_window(self):
        keys = [s.key for s in SWITCH_TYPES]
        start = keys.index("ev_morning_window_enabled") + 1
        assert tuple(keys[start:start + 5]) == HINT_SWITCH_KEYS

    def test_all_five_default_off(self):
        for key in HINT_SWITCH_KEYS:
            assert PERSISTED_FLAG_DEFAULTS[key] is False, key

    def test_every_language_names_the_switches(self):
        with open(os.path.join(_HERE, "strings.json"), encoding="utf-8") as f:
            switches = json.load(f)["entity"]["switch"]
        for key in HINT_SWITCH_KEYS:
            assert switches[key]["name"], key
        for name in os.listdir(os.path.join(_HERE, "translations")):
            with open(os.path.join(_HERE, "translations", name), encoding="utf-8") as f:
                switches = json.load(f)["entity"]["switch"]
            for key in HINT_SWITCH_KEYS:
                assert switches[key]["name"], (name, key)

    def test_the_card_labels_exist_in_every_language(self):
        with open(os.path.join(_HERE, "dashboard", "translations.json"), encoding="utf-8") as f:
            data = json.load(f)
        for lang, strings in data.items():
            for key in HINT_SWITCH_KEYS:
                assert strings.get(key), (lang, key)
                assert strings.get(f"config_help_{key}"), (lang, key)


class TestFlip:
    @pytest.mark.asyncio
    async def test_a_flip_writes_the_option_and_the_running_config(self, mock_coordinator):
        mock_coordinator.config = dict(mock_coordinator.config)
        mock_coordinator.config_entry.options = {}
        mock_coordinator.async_request_refresh = MagicMock(return_value=None)
        switch = SEMSolarSwitch(
            mock_coordinator, SwitchEntityDescription(key="hint_night_load"), "e")
        switch.hass = MagicMock()
        switch.async_write_ha_state = MagicMock()
        assert switch.is_on is False

        await switch.async_turn_on()

        assert switch.is_on is True
        assert mock_coordinator.config["hint_night_load"] is True
        switch.hass.config_entries.async_update_entry.assert_called_once()
        _, kwargs = switch.hass.config_entries.async_update_entry.call_args
        assert kwargs["options"]["hint_night_load"] is True
