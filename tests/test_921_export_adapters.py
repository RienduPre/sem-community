"""#955 — each brand's dialect for 'stop feeding the grid' and 'put it back'.

The decision layer never names a brand: it emits LIMIT_EXPORT / RELEASE_EXPORT
and the adapter speaks the inverter's language. Huawei publishes the control
as SERVICES addressed by device_id (and ships the restore itself); Deye as
the #827 System Work Mode select (prior captured, restored); anything else as
a writable export-limit number when it has one — and a sensor-domain limit is
observable only, so the base REFUSES rather than pretends.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.battery_adapters.base import (
    BatteryControlAdapter,
)
from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
    GenericBatteryAdapter,
)
from custom_components.solar_energy_management.coordinator.battery_adapters.huawei import (
    HuaweiBatteryAdapter,
)
from custom_components.solar_energy_management.coordinator.charger_types import BatteryIntent


def _hass(states=None):
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states.get = MagicMock(side_effect=lambda e: (states or {}).get(e))
    return hass


def _calls(hass):
    return [(c.args[0], c.args[1], c.args[2]) for c in hass.services.async_call.await_args_list]


@pytest.mark.asyncio
class TestTheBaseRefuses:
    async def test_base_verbs_raise_not_implemented(self):
        class Bare(BatteryControlAdapter):
            async def command_normal(self): ...
            async def command_limit_discharge(self, watts): ...
            async def command_force_charge(self, *a): ...
            async def command_stop_force_charge(self): ...
            @property
            def max_charge_power_w(self): return 5000.0
            @property
            def max_discharge_power_w(self): return 5000.0
            @property
            def supports_forced_charge(self): return False
        b = Bare(_hass(), {})
        with pytest.raises(NotImplementedError):
            await b.command_limit_export(0.0)
        with pytest.raises(NotImplementedError):
            await b.command_release_export()


@pytest.mark.asyncio
class TestHuawei:
    def _adapter(self, states=None, override=False):
        hass = _hass(states)
        cfg = {"inverter_device_id": "dev-huawei",
               "export_guard_override_external": override,
               "export_control_readback_entity": "sensor.inverter_active_power_control"}
        return HuaweiBatteryAdapter(hass, cfg), hass

    async def test_zero_export_is_one_service_call_by_device_id(self):
        a, hass = self._adapter({"sensor.inverter_active_power_control": SimpleNamespace(state="Unlimited")})
        await a.command_limit_export(0.0)
        assert ("huawei_solar", "set_zero_power_grid_connection", {"device_id": "dev-huawei"}) in _calls(hass)
        assert a._last_intent is BatteryIntent.LIMIT_EXPORT

    async def test_release_is_the_integrations_own_reset(self):
        a, hass = self._adapter()
        await a.command_release_export()
        assert ("huawei_solar", "reset_maximum_feed_grid_power", {"device_id": "dev-huawei"}) in _calls(hass)
        assert a._last_intent is BatteryIntent.RELEASE_EXPORT

    async def test_refuses_under_external_scheduling(self):
        a, hass = self._adapter({"sensor.inverter_active_power_control": SimpleNamespace(state="DI Active Scheduling")})
        with pytest.raises(NotImplementedError, match="external scheduling"):
            await a.command_limit_export(0.0)
        assert _calls(hass) == []

    async def test_the_override_lets_it_act_under_external_scheduling(self):
        a, hass = self._adapter({"sensor.inverter_active_power_control": SimpleNamespace(state="DI Active Scheduling")}, override=True)
        await a.command_limit_export(0.0)
        assert len(_calls(hass)) == 1

    async def test_a_cap_in_watts_uses_the_watt_service(self):
        a, hass = self._adapter()
        await a.command_limit_export(1500.0)
        assert ("huawei_solar", "set_maximum_feed_grid_power", {"device_id": "dev-huawei", "power": 1500}) in _calls(hass)

    async def test_repeat_is_not_rewritten(self):
        """#538 — an identical command every cycle is a modbus write-storm."""
        a, hass = self._adapter()
        await a.command_limit_export(0.0)
        await a.command_limit_export(0.0)
        assert len(_calls(hass)) == 1

    async def test_no_device_id_is_a_refusal(self):
        a = HuaweiBatteryAdapter(_hass(), {})
        with pytest.raises(NotImplementedError, match="inverter_device_id"):
            await a.command_limit_export(0.0)


@pytest.mark.asyncio
class TestGeneric:
    async def test_a_writable_number_is_captured_written_and_restored(self):
        hass = _hass({"number.inv_export_limit": SimpleNamespace(state="11000")})
        a = GenericBatteryAdapter(hass, {"export_limit_entity": "number.inv_export_limit"})
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert _calls(hass) == [
            ("number", "set_value", {"entity_id": "number.inv_export_limit", "value": 0.0}),
            ("number", "set_value", {"entity_id": "number.inv_export_limit", "value": 11000.0}),
        ]

    async def test_the_prior_is_captured_once_not_overwritten_by_our_own_cap(self):
        hass = _hass({"number.inv_export_limit": SimpleNamespace(state="11000")})
        a = GenericBatteryAdapter(hass, {"export_limit_entity": "number.inv_export_limit"})
        await a.command_limit_export(0.0)
        hass.states.get = MagicMock(return_value=SimpleNamespace(state="0"))   # our own write reads back
        await a.command_limit_export(500.0)
        await a.command_release_export()
        assert _calls(hass)[-1] == ("number", "set_value", {"entity_id": "number.inv_export_limit", "value": 11000.0})

    async def test_a_sensor_domain_limit_is_observable_only(self):
        a = GenericBatteryAdapter(_hass(), {"export_limit_entity": "sensor.inv_export_limit"})
        with pytest.raises(NotImplementedError, match="read-only"):
            await a.command_limit_export(0.0)

    async def test_no_entity_no_control(self):
        a = GenericBatteryAdapter(_hass(), {})
        with pytest.raises(NotImplementedError):
            await a.command_limit_export(0.0)

    async def test_an_unreadable_prior_refuses_rather_than_restores_nothing(self):
        hass = _hass({"number.inv_export_limit": SimpleNamespace(state="unavailable")})
        a = GenericBatteryAdapter(hass, {"export_limit_entity": "number.inv_export_limit"})
        with pytest.raises(NotImplementedError, match="unreadable"):
            await a.command_limit_export(0.0)
        assert _calls(hass) == []
