"""#1026 — the device registry is ASKED, never read as a mapping.

HA 2026.8 logs this once at every start (PROD, 28.09, beta.48):

    Detected that custom integration 'solar_energy_management' uses
    `device_registry.devices` as a mapping or calls its lookup methods, which
    is deprecated; ... This will stop working in Home Assistant 2027.9.0.

Four sites, all in the Huawei battery adapter: the inverter resolver behind
the feed-in cut (`reg.devices.get` twice, then a fleet walk) and the
zero-config battery autodetect (a second fleet walk).

Why it is bug class 48 and not a tidy-up. Both resolvers swallow their own
errors — one returns None "so resolution never breaks a cycle", the other
"so detection must never break setup". When 2027.9 stops answering, the raise
lands in those handlers and reads as *"there is no Huawei inverter on this
install"*. Forcible charge, forcible discharge and the export cut would all
go quiet on a working installation, with nothing in the log. That is the same
shape as #799, caught while HA is still only warning.

The supported lookups are `reg.async_get(device_id)` and
`device_registry.async_entries_for_config_entry(reg, entry_id)`. The second
is also the narrower question: only a `huawei_solar` device can ever match,
so there is no reason to walk the fleet at all.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator.battery_adapters import (
    huawei as hw,
)
from custom_components.solar_energy_management.coordinator.battery_adapters.huawei import (
    HuaweiBatteryAdapter,
)

from .ast_contracts import host_registry_mapping_reads

INV, BATT, OPT = "inv-dev-id", "batt-dev-id", "opt-dev-id"
ENTRY = "huawei-config-entry"


class _Registry:
    """Only what a custom integration may ask of HA's device registry: a
    lookup by id, and the per-config-entry list. There is deliberately NO
    ``devices`` attribute — touching one is the defect."""

    def __init__(self, devices):
        self._by_id = dict(devices)

    def async_get(self, device_id):
        return self._by_id.get(device_id)

    def for_config_entry(self, entry_id):
        return list(self._by_id.values()) if entry_id == ENTRY else []


def _prod_shaped():
    """PROD's actual shape: one inverter, one battery under it, an optimizer."""
    return {
        INV: SimpleNamespace(id=INV, identifiers={("huawei_solar", "BT2470369058")},
                             via_device_id=None),
        BATT: SimpleNamespace(
            id=BATT, identifiers={("huawei_solar", "BT2470369058/battery_1")},
            via_device_id=INV),
        OPT: SimpleNamespace(id=OPT, identifiers={("huawei_solar", "JV2339221485")},
                             via_device_id=INV),
    }


def _registry(devices, reg=None):
    reg = reg if reg is not None else _Registry(devices)
    return patch.multiple(
        "homeassistant.helpers.device_registry",
        async_get=MagicMock(return_value=reg),
        async_entries_for_config_entry=MagicMock(
            side_effect=lambda r, entry_id: r.for_config_entry(entry_id)),
    )


def _hass(entry_ids=(ENTRY,)):
    h = MagicMock()
    h.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id=e) for e in entry_ids]
    return h


def _adapter(battery_device=BATT, hass=None):
    a = HuaweiBatteryAdapter.__new__(HuaweiBatteryAdapter)
    a._hass = hass if hass is not None else _hass()
    a._config = {"export_control_readback_entity": "sensor.apc"}
    a._inverter_device_id = battery_device
    a._hass.states.get = MagicMock(
        return_value=SimpleNamespace(state="Unlimited", attributes={}))
    return a


@pytest.mark.unit
class TestNoProductionCodeReadsTheRegistryAsAMapping:
    """The guard that closes the class, not just these four lines."""

    def test_no_file_reads_the_host_device_registry_as_a_mapping(self):
        hits = host_registry_mapping_reads()
        assert hits == [], (
            "these read HA's device registry as a mapping — HA 2026.8 warns "
            f"and 2027.9 stops answering: {hits}")

    def test_the_contract_catches_the_shapes_it_is_written_for(self, tmp_path):
        """Not a vacuous pass: every form the defect took, plus the inline
        chain nobody wrote yet, is found."""
        (tmp_path / "offender.py").write_text(
            "from homeassistant.helpers import device_registry as dr\n"
            "def one(hass, did):\n"
            "    reg = dr.async_get(hass)\n"
            "    a = reg.devices.get(did)\n"
            "    b = list(reg.devices.values())\n"
            "    c = [d for d in reg.devices]\n"
            "    e = dr.async_get(hass).devices[did]\n"
            "    return a, b, c, e\n",
            encoding="utf-8")
        hits = host_registry_mapping_reads(root=tmp_path)
        assert [h[1] for h in hits] == [4, 5, 6, 7], hits

    #: Every way this codebase could reach the registry, one line each. A
    #: contract is only worth what it catches, so the list is the test.
    SHAPES = {
        "the bare-name import": (
            "from homeassistant.helpers.device_registry import async_get\n"
            "def f(hass, d):\n"
            "    reg = async_get(hass)\n"
            "    return reg.devices.get(d)\n"),
        "a parameter somebody handed the registry to": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass, reg):\n"
            "    return list(reg.devices.values())\n"),
        "a rename": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass):\n"
            "    reg = dr.async_get(hass)\n"
            "    r = reg\n"
            "    return list(r.devices.values())\n"),
        "the walrus": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass, d):\n"
            "    return (reg := dr.async_get(hass)).devices[d]\n"),
        "tuple unpacking": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass):\n"
            "    reg, x = dr.async_get(hass), 1\n"
            "    return len(reg.devices.keys()), x\n"),
        "getattr, which every callee-name contract is blind to": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass):\n"
            "    reg = dr.async_get(hass)\n"
            "    return getattr(reg, 'devices')\n"),
        "a for target": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass):\n"
            "    for reg in [dr.async_get(hass)]:\n"
            "        return list(reg.devices.values())\n"),
        "self._reg, read in another method": (
            "from homeassistant.helpers import device_registry as dr\n"
            "class C:\n"
            "    def a(self, hass):\n"
            "        self._reg = dr.async_get(hass)\n"
            "    def b(self, d):\n"
            "        return self._reg.devices.get(d)\n"),
        "deleted_devices, the same container": (
            "from homeassistant.helpers import device_registry as dr\n"
            "def f(hass):\n"
            "    reg = dr.async_get(hass)\n"
            "    return list(reg.deleted_devices.values())\n"),
    }

    def test_every_shape_the_registry_can_be_reached_by_is_caught(self, tmp_path):
        missed = []
        for name, src in self.SHAPES.items():
            d = tmp_path / name.replace(" ", "_").replace(",", "")
            d.mkdir()
            (d / "m.py").write_text(src, encoding="utf-8")
            if not host_registry_mapping_reads(root=d):
                missed.append(name)
        assert not missed, f"the contract does not catch: {missed}"

    def test_sems_own_registry_is_not_the_hosts(self, tmp_path):
        """SEM's ``UnifiedDeviceRegistry`` has a ``devices`` dict of its own
        (``__init__.py`` logs ``len(registry.devices)``). A contract that
        flagged that would be unusable, so it would be turned off."""
        (tmp_path / "ours.py").write_text(
            "from homeassistant.helpers import device_registry as dr\n"
            "def rename(registry):\n"
            "    pass\n"
            "def n(hass, unified):\n"
            "    reg = dr.async_get(hass)\n"
            "    return len(unified.devices), reg.async_get('x')\n",
            encoding="utf-8")
        assert host_registry_mapping_reads(root=tmp_path) == []

    def test_it_walks_the_real_tree(self):
        """Guard the guard: a root that finds no file would pass forever."""
        root = Path(__file__).resolve().parent.parent
        from .ast_contracts import _production_files
        assert sum(1 for _ in _production_files(root, ("tests", "scripts",
                                                       "__pycache__"))) > 20


@pytest.mark.unit
class TestTheInverterResolverStillAnswers:
    """The behaviour the four lines existed for, now through the helpers."""

    def test_the_battery_is_followed_up_to_its_inverter(self):
        a = _adapter()
        with _registry(_prod_shaped()):
            assert a._export_device_id() == INV

    def test_a_solar_only_install_finds_the_root_device(self):
        devs = _prod_shaped()
        devs.pop(BATT)
        a = _adapter(battery_device="")
        with _registry(devs):
            assert a._export_device_id() == INV

    def test_an_optimizer_is_never_the_target(self):
        a = _adapter(battery_device="")
        with _registry({OPT: _prod_shaped()[OPT]}):
            assert a._export_device_id() == ""

    def test_only_the_huawei_config_entry_is_asked(self):
        """The narrower question: SEM no longer walks every device in the
        house to find three Huawei ones. Asked on the scan path — a known
        battery is followed by id and never gets this far."""
        a = _adapter(battery_device="")
        with _registry(_prod_shaped()):
            a._export_device_id()
        assert a._hass.config_entries.async_entries.call_args.args == (
            "huawei_solar",)

    def test_a_device_on_two_config_entries_is_listed_once(self):
        """Asked of the lister itself. Through the resolver it is invisible —
        the first match returns — so dropping the de-duplication would leave
        every other test in this file green."""
        reg = _Registry(_prod_shaped())
        reg.for_config_entry = lambda entry_id: list(reg._by_id.values())
        hass = _hass(entry_ids=("a", "b"))
        with _registry(None, reg=reg):
            devs = hw._huawei_solar_devices(hass, reg)
        assert [d.id for d in devs] == [INV, BATT, OPT], devs

    def test_the_order_does_not_move_when_a_device_is_updated(self):
        """A per-config-entry index re-orders on every device update, and the
        old walk of the whole registry did not. Two batteries and no combined
        storage device: an unstable order would move forcible charge from
        battery 1 to battery 2 after the inverter reports new firmware."""
        reg = _Registry(_prod_shaped())
        hass = _hass()
        with _registry(None, reg=reg):
            first = [d.id for d in hw._huawei_solar_devices(hass, reg)]
            reg._by_id = dict(reversed(list(reg._by_id.items())))
            second = [d.id for d in hw._huawei_solar_devices(hass, reg)]
        assert first == second, "the order follows the registry's index"

    def test_a_sub_device_with_no_parent_is_not_the_inverter(self):
        """#955's rule, which nothing pinned: a `SN/battery_1` identifier is a
        sub-device even when its `via_device_id` is missing. Chosen as the
        inverter, every feed-in command fails `wrong_device_type`."""
        a = _adapter(battery_device="")
        orphan = SimpleNamespace(
            id="orphan", via_device_id=None,
            identifiers={("huawei_solar", "BT2470369058/battery_1")})
        with _registry({"orphan": orphan}):
            assert a._export_device_id() == ""

    def test_a_registry_that_raises_is_a_refusal_not_a_crash(self):
        a = _adapter()
        with patch("homeassistant.helpers.device_registry.async_get",
                   side_effect=RuntimeError("no registry")):
            assert a._export_device_id() == ""


@pytest.mark.unit
class TestAGoneLookupIsSaidOutLoud:
    """Class 48's other half: the swallow must not be able to report a
    missing host API as a missing inverter."""

    #: What 2027.9 does: `DeviceRegistry.devices` is removed, so the helper
    #: that used to be reached through it is gone. Both shapes, because a
    #: removed attribute and a raising one land in different places.
    GONE = (("removed", lambda *a, **k: (_ for _ in ()).throw(
                AttributeError("'DeviceRegistry' object has no attribute")),),
            ("raising", lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("uses `device_registry.devices` as a mapping")),))

    def test_the_export_resolver_warns_instead_of_reporting_no_inverter(
            self, caplog):
        for what, boom in self.GONE:
            caplog.clear()
            a = _adapter(battery_device="")
            with patch("homeassistant.helpers.device_registry.async_get",
                       return_value=_Registry(_prod_shaped())), \
                    patch("homeassistant.helpers.device_registry"
                          ".async_entries_for_config_entry", boom):
                with caplog.at_level("WARNING"):
                    assert a._export_device_id() == ""
            assert "cannot resolve the inverter device" in caplog.text, what

    def test_the_battery_autodetect_warns_too(self, caplog):
        """The handler that mattered most. It said "detection must never break
        setup" and covered the registry read as well, so a gone lookup read as
        "this install has no Huawei battery" — and forcible charge and
        discharge would have stopped with nothing in the log."""
        for what, boom in self.GONE:
            caplog.clear()
            a = HuaweiBatteryAdapter.__new__(HuaweiBatteryAdapter)
            a._hass, a._config = _hass(), {}
            with patch("homeassistant.helpers.device_registry.async_get",
                       return_value=_Registry(_prod_shaped())), \
                    patch("homeassistant.helpers.device_registry"
                          ".async_entries_for_config_entry", boom):
                with caplog.at_level("WARNING"):
                    assert a._autodetect_battery_device() is None
            assert "cannot find the battery device" in caplog.text, what

    def test_no_huawei_config_entry_is_quiet(self, caplog):
        """The benign case stays benign: a house with no huawei_solar at all
        must not warn on every adapter it builds."""
        reg = _Registry(_prod_shaped())
        with _registry(None, reg=reg):
            with caplog.at_level("WARNING"):
                assert hw._huawei_solar_devices(_hass(entry_ids=()), reg) == []
        assert caplog.text == ""


@pytest.mark.unit
class TestTheZeroConfigBatteryAutodetectStillAnswers:
    """#523's path, the other fleet walk."""

    def _detect(self, devices):
        a = HuaweiBatteryAdapter.__new__(HuaweiBatteryAdapter)
        a._hass = _hass()
        a._config = {}
        with _registry(devices):
            return a._autodetect_battery_device()

    def test_the_combined_storage_device_wins(self):
        devs = _prod_shaped()
        devs["combined"] = SimpleNamespace(
            id="combined",
            identifiers={("huawei_solar", "BT2470369058/connected_energy_storage")},
            via_device_id=INV)
        assert self._detect(devs) == "combined"

    def test_a_per_battery_device_is_the_fallback(self):
        assert self._detect(_prod_shaped()) == BATT

    def test_an_install_with_no_battery_detects_nothing(self):
        devs = _prod_shaped()
        devs.pop(BATT)
        assert self._detect(devs) is None

    def test_every_identifier_on_a_device_is_read_not_the_first_one(self):
        """`identifiers` is a SET, and the combined-storage one is what the
        forcible verbs want. Read only one identifier per device and the
        combined device looks like a plain battery, so a plain battery next
        door wins instead."""
        devs = {
            "plain": SimpleNamespace(
                id="plain", via_device_id=INV,
                identifiers={("huawei_solar", "BT1/battery_1")}),
            "both": SimpleNamespace(
                id="both", via_device_id=INV,
                identifiers={("huawei_solar", "BT1/battery_2"),
                             ("huawei_solar", "BT1/connected_energy_storage")}),
        }
        assert self._detect(devs) == "both"
        assert self._detect(dict(reversed(list(devs.items())))) == "both"

    def test_two_batteries_pick_the_same_one_every_time(self):
        devs = {
            "b2": SimpleNamespace(id="b2", via_device_id=INV,
                                  identifiers={("huawei_solar", "BT1/battery_2")}),
            "b1": SimpleNamespace(id="b1", via_device_id=INV,
                                  identifiers={("huawei_solar", "BT1/battery_1")}),
        }
        assert self._detect(devs) == "b1"
        assert self._detect(dict(reversed(list(devs.items())))) == "b1"
