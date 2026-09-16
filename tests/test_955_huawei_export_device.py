"""#955 — the feed-in verbs go to the INVERTER, not the battery.

`huawei_solar` splits its services by device TYPE. `forcible_charge` resolves
a BATTERY device; `set_zero_power_grid_connection` and
`reset_maximum_feed_grid_power` go through `get_inverter_data`, which raises
`wrong_device_type` for anything that is not the inverter.

`_inverter_device_id` is — despite the name — the battery device:
`_autodetect_battery_device` looks for `connected_energy_storage` or
`/battery` on purpose (#523). Handing that to the feed-in verbs refuses on
EVERY zero-config Huawei install, which is the common one.

Nothing caught it: the other tests stub `_inverter_device_id = "dev"`, and the
rig runs in observer mode where the call is never made. It was found by
reading PROD's device registry — `Inverter` is `('huawei_solar','BT2470369058')`
with no `via_device_id`, `Battery 1` is `('huawei_solar','BT2470369058/battery_1')`
with `via_device_id` pointing at the inverter. This file is that registry.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator.battery_adapters.huawei import (
    HuaweiBatteryAdapter,
)

INV, BATT, OPT = "inv-dev-id", "batt-dev-id", "opt-dev-id"


def _registry(devices):
    reg = SimpleNamespace(devices=devices)
    return patch(
        "homeassistant.helpers.device_registry.async_get", return_value=reg)


def _prod_shaped():
    """PROD's actual shape: one inverter, one battery under it, an optimizer."""
    return {
        INV: SimpleNamespace(id=INV, identifiers={("huawei_solar", "BT2470369058")},
                             via_device_id=None),
        BATT: SimpleNamespace(id=BATT,
                              identifiers={("huawei_solar", "BT2470369058/battery_1")},
                              via_device_id=INV),
        OPT: SimpleNamespace(id=OPT, identifiers={("huawei_solar", "JV2339221485")},
                             via_device_id=INV),
    }


def _adapter(battery_device=BATT, config=None):
    a = HuaweiBatteryAdapter.__new__(HuaweiBatteryAdapter)
    a._hass = MagicMock()
    a._config = config or {}
    a._inverter_device_id = battery_device
    return a


class TestTheExportVerbsTargetTheInverter:
    def test_the_battery_id_is_not_what_the_feed_in_verbs_get(self):
        """The bug, stated as the thing that must never come back."""
        a = _adapter()
        with _registry(_prod_shaped()):
            assert a._export_device_id() == INV
            assert a._export_device_id() != a._inverter_device_id

    def test_it_follows_the_battery_up_to_its_inverter(self):
        a = _adapter()
        with _registry(_prod_shaped()):
            assert a._export_device_id() == INV

    def test_a_solar_only_install_finds_the_root_device(self):
        """No battery at all — the inverter is still the only root huawei
        device whose identifier carries no sub-part."""
        devs = _prod_shaped(); devs.pop(BATT)
        a = _adapter(battery_device="")
        with _registry(devs):
            assert a._export_device_id() == INV

    def test_an_optimizer_is_never_the_target(self):
        """Optimizers are huawei_solar devices too — 27 of them on PROD."""
        devs = {OPT: _prod_shaped()[OPT]}
        a = _adapter(battery_device="")
        with _registry(devs):
            assert a._export_device_id() == ""   # a sub-device is not the inverter

    def test_an_explicit_config_wins(self):
        a = _adapter(config={"export_device_id": "chosen-by-hand"})
        with _registry(_prod_shaped()):
            assert a._export_device_id() == "chosen-by-hand"

    def test_no_huawei_devices_at_all_is_a_refusal_not_a_crash(self):
        a = _adapter(battery_device="")
        with _registry({}):
            assert a._export_device_id() == ""

    def test_a_registry_that_raises_is_a_refusal_not_a_crash(self):
        a = _adapter()
        with patch("homeassistant.helpers.device_registry.async_get",
                   side_effect=RuntimeError("no registry")):
            assert a._export_device_id() == ""


@pytest.mark.asyncio
class TestTheWriteUsesIt:
    async def test_the_cut_is_addressed_to_the_inverter(self):
        a = _adapter()
        a._hass.services.async_call = MagicMock()

        async def _call(*args, **kw):
            return None
        a._hass.services.async_call = MagicMock(side_effect=_call)
        with _registry(_prod_shaped()):
            await a.command_limit_export(0.0)
        sent = a._hass.services.async_call.call_args.args
        assert sent[0] == "huawei_solar"
        assert sent[2]["device_id"] == INV, "the cut went to the battery again"

    async def test_the_release_is_addressed_to_the_inverter(self):
        a = _adapter()

        async def _call(*args, **kw):
            return None
        a._hass.services.async_call = MagicMock(side_effect=_call)
        with _registry(_prod_shaped()):
            await a.command_release_export()
        assert a._hass.services.async_call.call_args.args[2]["device_id"] == INV

    async def test_no_inverter_found_refuses_and_says_so(self):
        a = _adapter(battery_device="")
        with _registry({}):
            with pytest.raises(NotImplementedError):
                await a.command_limit_export(0.0)


# ── (#908) the release must restore the mode SEM FOUND, not "Unlimited" ──

class _State:
    def __init__(self, state, watt=None, percent=None):
        self.state = state
        self.attributes = {"maximum_power_watt": watt, "maximum_power_percent": percent}


def _with_mode(mode_state):
    a = _adapter(config={"export_device_id": INV,
                         "export_control_readback_entity": "sensor.apc",
                         "export_guard_override_external": True})
    a._hass.states.get = MagicMock(return_value=mode_state)
    calls = []

    async def _call(domain, service, data, *args, **kw):
        calls.append((domain, service, dict(data)))
    a._hass.services.async_call = MagicMock(side_effect=_call)
    return a, calls


@pytest.mark.asyncio
class TestTheReleaseRestoresWhatWasFound:
    """PROD's SUN2000 has sat in DI Active Scheduling — the grid operator's
    ripple-control receiver on the dry contacts — for its whole history.
    ACTIVE_POWER_CONTROL_MODE is ONE register with five mutually exclusive
    values, and `reset_maximum_feed_grid_power` is documented by the
    integration as *"Set Active Power Control to 'Unlimited'"*. So the old
    release would have taken the inverter OUT of the operator's scheme and
    left it there, with nothing in SEM aware of it."""

    async def test_di_scheduling_is_put_back_not_unlimited(self):
        a, calls = _with_mode(_State("DI Active Scheduling", watt=0, percent=0.0))
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert calls[-1][1] == "set_di_active_power_scheduling", calls
        assert calls[-1][1] != "reset_maximum_feed_grid_power"

    async def test_an_unlimited_inverter_still_gets_the_plain_reset(self):
        a, calls = _with_mode(_State("Unlimited"))
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert calls[-1][1] == "reset_maximum_feed_grid_power"

    async def test_a_watt_cap_is_put_back_at_its_own_number(self):
        a, calls = _with_mode(_State("Limited to 7000W", watt=7000, percent=70.0))
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert calls[-1][1] == "set_maximum_feed_grid_power"
        assert calls[-1][2]["power"] == 7000

    async def test_a_percent_cap_is_put_back_at_its_own_number(self):
        a, calls = _with_mode(_State("Limited to 60.0%", watt=0, percent=60.0))
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert calls[-1][1] == "set_maximum_feed_grid_power_percent"
        assert calls[-1][2]["power_percentage"] == 60.0

    async def test_an_unreadable_mode_falls_back_to_the_reset(self):
        """Unknown is not a licence to invent: 'Unlimited' can only ever ALLOW
        more export than SEM's cut, never less."""
        a, calls = _with_mode(None)
        await a.command_limit_export(0.0)
        await a.command_release_export()
        assert calls[-1][1] == "reset_maximum_feed_grid_power"

    async def test_the_prior_is_captured_before_the_cut_not_after(self):
        """Capture after the write would record SEM's own 'Zero Power'."""
        a, calls = _with_mode(_State("DI Active Scheduling"))
        await a.command_limit_export(0.0)
        assert a._export_prior_mode[0] == "DI Active Scheduling"
        assert calls[0][1] == "set_zero_power_grid_connection"

    async def test_the_recipe_persisted_for_a_restart_carries_the_same_mode(self):
        """A restart replays the RECIPE, so it must name the prior mode too —
        otherwise the cut outlives SEM and the hand-back is still wrong."""
        a, _ = _with_mode(_State("DI Active Scheduling"))
        await a.command_limit_export(0.0)
        assert a.export_release_recipe()["service"] == "set_di_active_power_scheduling"


@pytest.mark.asyncio
class TestTheRestoreSurvivesARestart:
    """The gap the first version of the capture left open, caught by Guido
    asking whether the claim was even right.

    `_export_prior_mode` lives in memory. After a restart, `adopt_export_prior`
    takes over the cut from the store — and the base dropped the recipe on the
    floor, so `export_release_recipe()` had nothing to go on and fell back to
    `reset_maximum_feed_grid_power`, i.e. Unlimited. Re-deriving it is not an
    option either: by then the inverter reports SEM's own "Zero Power".
    The previous lifetime's capture is the only record of what was found."""

    def _restarted(self, stored_service):
        a = _adapter(config={"export_device_id": INV})
        a.adopt_export_prior({"domain": "huawei_solar", "service": stored_service,
                              "data": {"device_id": INV}})
        return a

    def test_an_adopted_di_cut_is_released_back_into_di(self):
        a = self._restarted("set_di_active_power_scheduling")
        assert a.export_release_recipe()["service"] == "set_di_active_power_scheduling"

    def test_it_does_not_fall_back_to_unlimited(self):
        """The defect in one line."""
        a = self._restarted("set_di_active_power_scheduling")
        assert a.export_release_recipe()["service"] != "reset_maximum_feed_grid_power"

    def test_an_adopted_watt_cap_keeps_its_number(self):
        a = _adapter(config={"export_device_id": INV})
        a.adopt_export_prior({"domain": "huawei_solar",
                              "service": "set_maximum_feed_grid_power",
                              "data": {"device_id": INV, "power": 7000}})
        r = a.export_release_recipe()
        assert r["service"] == "set_maximum_feed_grid_power" and r["data"]["power"] == 7000

    def test_an_adopted_cut_still_counts_as_held(self):
        a = self._restarted("set_di_active_power_scheduling")
        assert a.holds_export_cut() is True

    async def test_the_release_writes_the_adopted_mode(self):
        a = self._restarted("set_di_active_power_scheduling")
        calls = []

        async def _call(domain, service, data, *args, **kw):
            calls.append((domain, service, dict(data)))
        a._hass.services.async_call = MagicMock(side_effect=_call)
        await a.command_release_export()
        assert calls[-1][1] == "set_di_active_power_scheduling", calls

    async def test_a_released_adapter_forgets_the_adopted_recipe(self):
        """Otherwise the NEXT cut would restore a mode from two lifetimes ago."""
        a = self._restarted("set_di_active_power_scheduling")

        async def _call(*args, **kw):
            return None
        a._hass.services.async_call = MagicMock(side_effect=_call)
        await a.command_release_export()
        assert a._adopted_recipe is None
        assert a.holds_export_cut() is False
