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
