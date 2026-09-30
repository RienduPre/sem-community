"""#809 / #869 — the generic battery adapter learns two more ways to write a
setpoint, through the ONE write path (``base._write_force_discharge``).

SEM's own sign is + = discharge, − = charge (#523). Victron's ESS grid
setpoint is the mirror (+ = import = charge), which #809's reporter bridged
with a sign-flipping template. Anker Solix (#869) has no signed setpoint at
all: a charge/discharge SELECT and an unsigned watt number.

| ``battery_setpoint_model`` | discharge W | charge W |
|---|---|---|
| ``signed`` (default, unchanged) | +W | −W |
| ``inverted`` | −W | +W |
| ``direction_select`` | select ← discharge, then W | select ← charge, then W |

The direction select follows the #978 rule: believe the entity — the number
is written only once the select READS the direction; until then the write
is withheld and retried next cycle, never sent blind.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
    GenericBatteryAdapter,
)

SP = "number.batt_setpoint"
DIR = "select.batt_direction"
SCALE = "custom_components.solar_energy_management.coordinator.power_control.native_power_scale"


def _state(value, lo=-5000.0, hi=5000.0):
    st = Mock()
    st.state = str(value)
    st.attributes = {"min": lo, "max": hi, "unit_of_measurement": "W"}
    return st


def _hass(states: dict) -> MagicMock:
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = Mock(side_effect=lambda eid: states.get(eid))
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    return hass


def _adapter(hass, **cfg) -> GenericBatteryAdapter:
    base = {"battery_force_discharge_control_entity": SP,
            "battery_max_discharge_power": 5000,
            "battery_max_charge_power": 5000}
    base.update(cfg)
    return GenericBatteryAdapter(hass, base)


def _writes(hass, domain="number"):
    return [c.args for c in hass.services.async_call.await_args_list
            if c.args and c.args[0] == domain]


class TestTheDefaultIsUnchanged:
    @pytest.mark.asyncio
    async def test_no_model_key_writes_sem_sign(self):
        hass = _hass({SP: _state(0)})
        with patch(SCALE, return_value=1.0):
            await _adapter(hass).command_force_discharge(1500, 20)
        assert _writes(hass) == [("number", "set_value",
                                  {"entity_id": SP, "value": 1500.0})]

    @pytest.mark.asyncio
    async def test_signed_charge_is_negative_when_bidirectional(self):
        hass = _hass({SP: _state(0)})
        with patch(SCALE, return_value=1.0):
            await _adapter(hass, battery_setpoint_bidirectional=True
                           ).command_force_charge(80, 1500, 60)
        assert _writes(hass)[-1][2]["value"] == -1500.0


class TestInverted:
    @pytest.mark.asyncio
    async def test_discharge_is_written_negative(self):
        hass = _hass({SP: _state(0)})
        with patch(SCALE, return_value=1.0):
            await _adapter(hass, battery_setpoint_model="inverted"
                           ).command_force_discharge(1500, 20)
        assert _writes(hass) == [("number", "set_value",
                                  {"entity_id": SP, "value": -1500.0})]

    @pytest.mark.asyncio
    async def test_charge_is_written_positive(self):
        hass = _hass({SP: _state(0)})
        with patch(SCALE, return_value=1.0):
            await _adapter(hass, battery_setpoint_model="inverted",
                           battery_setpoint_bidirectional=True
                           ).command_force_charge(80, 1500, 60)
        assert _writes(hass)[-1][2]["value"] == 1500.0

    @pytest.mark.asyncio
    async def test_the_clamp_applies_to_the_wire_value(self):
        """A mirrored entity with range [-2200, 2200]: 4000 W of discharge
        becomes −2200 on the wire, not 0 and not −4000."""
        hass = _hass({SP: _state(0, lo=-2200, hi=2200)})
        with patch(SCALE, return_value=1.0):
            await _adapter(hass, battery_setpoint_model="inverted"
                           ).command_force_discharge(4000, 20)
        assert _writes(hass)[-1][2]["value"] == -2200.0


class TestDirectionSelect:
    def _cfg(self):
        return dict(battery_setpoint_model="direction_select",
                    battery_power_direction_entity=DIR)

    @pytest.mark.asyncio
    async def test_first_cycle_sets_the_direction_and_withholds_the_number(self):
        hass = _hass({SP: _state(0, lo=0, hi=2200), DIR: _state("charge")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, **self._cfg())
            await a.command_force_discharge(1500, 20)
        assert _writes(hass, "select") == [("select", "select_option",
                                            {"entity_id": DIR, "option": "discharge"})]
        assert _writes(hass, "number") == []
        assert a._last_intent is None

    @pytest.mark.asyncio
    async def test_once_the_select_reads_it_the_magnitude_is_written(self):
        hass = _hass({SP: _state(0, lo=0, hi=2200), DIR: _state("discharge")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, **self._cfg())
            await a.command_force_discharge(1500, 20)
        assert _writes(hass, "select") == []
        assert _writes(hass, "number") == [("number", "set_value",
                                            {"entity_id": SP, "value": 1500.0})]

    @pytest.mark.asyncio
    async def test_charge_is_the_charge_direction_and_a_positive_magnitude(self):
        hass = _hass({SP: _state(0, lo=0, hi=2200), DIR: _state("charge")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, **self._cfg())
            await a.command_force_charge(80, 1500, 60)
        assert _writes(hass, "number")[-1][2]["value"] == 1500.0

    @pytest.mark.asyncio
    async def test_an_unsigned_entity_does_not_clamp_a_charge_to_zero(self):
        """The bug this model exists to avoid: SEM's −4400 against min 0."""
        hass = _hass({SP: _state(0, lo=0, hi=2200), DIR: _state("charge")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, **self._cfg())
            await a.command_force_charge(80, 4400, 60)
        assert _writes(hass, "number")[-1][2]["value"] == 2200.0

    @pytest.mark.asyncio
    async def test_the_direction_values_are_configurable(self):
        hass = _hass({SP: _state(0, lo=0, hi=2200), DIR: _state("Laden")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, battery_direction_discharge_value="Entladen",
                         battery_direction_charge_value="Laden", **self._cfg())
            await a.command_force_discharge(1000, 20)
        assert _writes(hass, "select")[0][2]["option"] == "Entladen"

    @pytest.mark.asyncio
    async def test_zero_needs_no_direction(self):
        hass = _hass({SP: _state(1000, lo=0, hi=2200), DIR: _state("discharge")})
        with patch(SCALE, return_value=1.0):
            a = _adapter(hass, **self._cfg())
            a._last_force_discharge_w = 1000.0
            await a.command_normal()
        assert _writes(hass, "select") == []
        assert any(w[2]["value"] == 0.0 for w in _writes(hass, "number"))

    def test_it_is_bidirectional_by_construction(self):
        hass = _hass({})
        assert _adapter(hass, **self._cfg()).supports_forced_charge is True

    def test_without_a_direction_entity_it_is_not(self):
        hass = _hass({})
        assert _adapter(hass, battery_setpoint_model="direction_select"
                        ).supports_forced_charge is False


class TestInvertedNeedsTheBidirectionalFlagLikeSigned:
    def test_flag_off_means_discharge_only(self):
        assert _adapter(_hass({}), battery_setpoint_model="inverted"
                        ).supports_forced_charge is False

    def test_flag_on_means_both(self):
        assert _adapter(_hass({}), battery_setpoint_model="inverted",
                        battery_setpoint_bidirectional=True
                        ).supports_forced_charge is True
