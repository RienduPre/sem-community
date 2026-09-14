"""#801 follow-up — a SG-Ready CONTACT is not always a switch.

The reporter's Buderus sits behind EMS-ESP, which carries the two SG-Ready
inputs as ``text`` entities holding a bit string (``010000000000000``), not
as a relay pair. The service path shipped first, but it cannot express his
payload: ``{state}`` renders 1–4 and ``{relay1}`` renders true/false, while
his pump needs a per-contact STRING. So the contact itself generalised —
same truth table, same inversion, same verify-after-write, only the write
service and its payload vary by the contact entity's domain.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class _Services:
    def __init__(self):
        self.calls = []

    def has_service(self, *_):
        return True

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((f"{domain}.{service}", dict(data)))


class _State:
    def __init__(self, state):
        self.state = state
        self.attributes = {}


def _controller(states=None, **over):
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        HeatPumpController,
    )
    table = dict(states or {})
    hass = SimpleNamespace(
        services=_Services(),
        states=SimpleNamespace(get=lambda e: table.get(e)),
    )
    kw = dict(hass=hass)
    kw.update(over)
    return HeatPumpController(hass=hass, **{k: v for k, v in kw.items() if k != "hass"}), hass


# ── the EMS-ESP shape the issue is about ──────────────────────────────

ON_1, OFF_1 = "100000000000000", "010000000000000"
ON_4, OFF_4 = "100000000000", "000000100000"


def _ems_esp(**over):
    kw = dict(
        relay1_entity_id="text.ems_esp_boiler_input_1_options",
        relay2_entity_id="text.ems_esp_boiler_input_4_options",
        relay1_on_value=ON_1, relay1_off_value=OFF_1,
        relay2_on_value=ON_4, relay2_off_value=OFF_4,
    )
    kw.update(over)
    return kw


@pytest.mark.asyncio
async def test_a_text_contact_is_written_with_its_configured_value():
    """BOOST is (relay1=off, relay2=on) — each contact gets ITS OWN value,
    through text.set_value rather than homeassistant.turn_on."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(**_ems_esp())
    assert await c._set_sg_ready_state(SGReadyState.BOOST) is True
    assert hass.services.calls == [
        ("text.set_value", {"entity_id": "text.ems_esp_boiler_input_1_options",
                            "value": OFF_1}),
        ("text.set_value", {"entity_id": "text.ems_esp_boiler_input_4_options",
                            "value": ON_4}),
    ]
    assert c._last_relay_path == "both_relays"


@pytest.mark.asyncio
async def test_the_two_contacts_do_not_share_one_value():
    """The reporter's two inputs carry bit strings of DIFFERENT widths (15
    and 12), so a single shared on/off pair would write nonsense to one of
    them. FORCE_ON closes both — each with its own string."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(**_ems_esp())
    await c._set_sg_ready_state(SGReadyState.FORCE_ON)
    written = [d["value"] for _, d in hass.services.calls]
    assert written == [ON_1, ON_4]
    assert len(set(len(w) for w in written)) == 2


@pytest.mark.asyncio
async def test_normal_writes_the_off_value_not_an_empty_string():
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(**_ems_esp())
    await c._set_sg_ready_state(SGReadyState.NORMAL)
    assert [d["value"] for _, d in hass.services.calls] == [OFF_1, OFF_4]


@pytest.mark.asyncio
async def test_nc_inversion_still_applies_to_a_value_contact():
    """#523's invert flag flips the truth table, not the write mechanism."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(**_ems_esp(invert_sg_ready=True))
    await c._set_sg_ready_state(SGReadyState.BOOST)
    # BOOST is (False, True); inverted → (True, False)
    assert [d["value"] for _, d in hass.services.calls] == [ON_1, OFF_4]


@pytest.mark.asyncio
async def test_a_value_contact_without_values_refuses_to_write():
    """A contact SEM cannot drive must FAIL, not silently write "on" into a
    bit-string field — a write the pump would ignore while SEM believed it
    had boosted and credited rated_power to the surplus pool (#508 C3)."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(relay1_entity_id="text.input_1", relay2_entity_id="text.input_4")
    assert await c._set_sg_ready_state(SGReadyState.BOOST) is False
    assert hass.services.calls == []
    assert c._last_relay_path == "relay1_failed"


@pytest.mark.asyncio
async def test_a_switch_contact_is_untouched_by_any_of_this():
    """Every existing install: no values configured, turn_on/turn_off."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(relay1_entity_id="switch.r1", relay2_entity_id="switch.r2")
    assert await c._set_sg_ready_state(SGReadyState.FORCE_ON) is True
    assert hass.services.calls == [
        ("homeassistant.turn_on", {"entity_id": "switch.r1"}),
        ("homeassistant.turn_on", {"entity_id": "switch.r2"}),
    ]


@pytest.mark.asyncio
async def test_a_number_contact_writes_a_float_not_a_string():
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(
        relay1_entity_id="number.sg1", relay2_entity_id="number.sg2",
        relay1_on_value="1", relay1_off_value="0",
        relay2_on_value="1", relay2_off_value="0",
    )
    await c._set_sg_ready_state(SGReadyState.BOOST)
    assert hass.services.calls == [
        ("number.set_value", {"entity_id": "number.sg1", "value": 0.0}),
        ("number.set_value", {"entity_id": "number.sg2", "value": 1.0}),
    ]


@pytest.mark.asyncio
async def test_zero_is_a_real_off_value_not_an_empty_field():
    """``0`` is falsy; a truthiness check would have read it as "unset" and
    refused the write. Emptiness is decided on the string."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(
        relay1_entity_id="number.sg1", relay2_entity_id="number.sg2",
        relay1_on_value=1, relay1_off_value=0,
        relay2_on_value=1, relay2_off_value=0,
    )
    assert await c._set_sg_ready_state(SGReadyState.NORMAL) is True
    assert [d["value"] for _, d in hass.services.calls] == [0.0, 0.0]


@pytest.mark.asyncio
async def test_a_select_contact_uses_select_option():
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, hass = _controller(
        relay1_entity_id="select.sg1", relay2_entity_id="select.sg2",
        relay1_on_value="Ein", relay1_off_value="Aus",
        relay2_on_value="Ein", relay2_off_value="Aus",
    )
    await c._set_sg_ready_state(SGReadyState.BOOST)
    assert hass.services.calls == [
        ("select.select_option", {"entity_id": "select.sg1", "option": "Aus"}),
        ("select.select_option", {"entity_id": "select.sg2", "option": "Ein"}),
    ]


# ── read-back (#914 restart adoption) ─────────────────────────────────

def test_restart_adoption_reads_the_state_off_a_text_contact():
    """#914 re-owns a boost SEM left on across a restart by reading the
    contacts back. A value contact's boolean comes off its own value."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, _ = _controller(
        states={"text.ems_esp_boiler_input_1_options": _State(OFF_1),
                "text.ems_esp_boiler_input_4_options": _State(ON_4)},
        **_ems_esp())
    assert c._read_sg_ready_state() == (True, SGReadyState.BOOST)


def test_an_unmapped_third_value_reads_as_i_cannot_tell():
    """The pump's own menu can leave a bit string SEM never wrote. That is
    not BOOST and not NORMAL — it is no verdict, so nothing is adopted."""
    c, _ = _controller(
        states={"text.ems_esp_boiler_input_1_options": _State("000000000000001"),
                "text.ems_esp_boiler_input_4_options": _State(ON_4)},
        **_ems_esp())
    readable, observed = c._read_sg_ready_state()
    assert readable is True and observed is None


def test_a_switch_pair_still_reads_back_the_old_way():
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, _ = _controller(
        states={"switch.r1": _State("on"), "switch.r2": _State("on")},
        relay1_entity_id="switch.r1", relay2_entity_id="switch.r2")
    assert c._read_sg_ready_state() == (True, SGReadyState.FORCE_ON)


# ── the config flow refuses a contact it cannot drive ─────────────────

def test_the_flow_refuses_a_value_contact_with_no_values():
    """The #437 rule — never ship a half-configured heat pump that silently
    does nothing — extended to the contact's values."""
    from custom_components.solar_energy_management.config_flow import (
        _contact_values_missing,
    )
    form = {
        "heat_pump_relay1_entity": "text.input_1",
        "heat_pump_relay2_entity": "text.input_4",
        "heat_pump_relay1_on_value": ON_1,
        "heat_pump_relay1_off_value": OFF_1,
        "heat_pump_relay2_on_value": ON_4,
        # relay2 OFF missing
    }
    assert _contact_values_missing(form.get) is True
    form["heat_pump_relay2_off_value"] = OFF_4
    assert _contact_values_missing(form.get) is False


def test_the_flow_asks_nothing_extra_of_a_switch_contact():
    from custom_components.solar_energy_management.config_flow import (
        _contact_values_missing,
    )
    form = {"heat_pump_relay1_entity": "switch.r1",
            "heat_pump_relay2_entity": "input_boolean.r2"}
    assert _contact_values_missing(form.get) is False


def test_every_writable_domain_the_picker_offers_can_be_written():
    """The picker and the write table are one definition — a domain offered
    in the config that SEM cannot drive would be a dead choice."""
    from custom_components.solar_energy_management.consts.devices import (
        CONTACT_VALUE_SERVICES, SG_READY_CONTACT_DOMAINS,
    )
    assert set(SG_READY_CONTACT_DOMAINS) == (
        {"switch", "input_boolean"} | set(CONTACT_VALUE_SERVICES))


# ── what the ruflo review refuted ─────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_stand_down_does_not_report_the_pump_idle():
    """REFUTED claim (c): ``deactivate`` discarded the write's verdict.

    The pump is physically left in FORCE_ON while SEM records IDLE / 0 W and
    hands that power to the next device — the mirror of the #508 C3 rule it
    already honours on the way up. Reproduced by the reviewer on a value
    contact with no OFF value, which #801 turns from a freak service
    exception into an ordinary misconfiguration.
    """
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    from custom_components.solar_energy_management.devices.base import DeviceState
    c, hass = _controller(
        relay1_entity_id="text.in1", relay2_entity_id="text.in4",
        relay1_on_value=ON_1, relay2_on_value=ON_4)   # no OFF values
    await c.activate(5000)
    assert c._status.state is DeviceState.ACTIVE
    hass.services.calls.clear()

    await c.deactivate()
    assert c._status.state is DeviceState.ACTIVE, (
        "the contacts never moved — SEM must not believe the pump stood down")
    assert c._status.current_consumption_w > 0
    assert c._last_deactivation_path == "relay_failed"


@pytest.mark.asyncio
async def test_a_successful_stand_down_still_reports_idle():
    from custom_components.solar_energy_management.devices.base import DeviceState
    c, _ = _controller(relay1_entity_id="switch.r1", relay2_entity_id="switch.r2")
    await c.activate(5000)
    await c.deactivate()
    assert c._status.state is DeviceState.IDLE
    assert c._status.current_consumption_w == 0.0
    assert c._last_deactivation_path == "normal"


def test_a_number_contact_reads_back_through_its_own_formatting():
    """REFUTED claim (b): a number entity reports ``"1.0"`` for a contact
    configured ``1``, so string comparison made every number contact
    unreadable and #914 never re-owned a boost across a restart."""
    from custom_components.solar_energy_management.devices.heat_pump_controller import (
        SGReadyState,
    )
    c, _ = _controller(
        states={"number.sg1": _State("0.0"), "number.sg2": _State("1.0")},
        relay1_entity_id="number.sg1", relay2_entity_id="number.sg2",
        relay1_on_value="1", relay1_off_value="0",
        relay2_on_value="1", relay2_off_value="0")
    assert c._read_sg_ready_state() == (True, SGReadyState.BOOST)


def test_a_bit_strings_leading_zeros_are_still_the_meaning():
    """The numeric comparison must NOT reach text contacts: ``010000`` and
    ``10000`` are different inputs on EMS-ESP, and both parse as 10000.0."""
    c, _ = _controller(
        states={"text.ems_esp_boiler_input_1_options": _State("10000000000000"),
                "text.ems_esp_boiler_input_4_options": _State(ON_4)},
        **_ems_esp())
    readable, observed = c._read_sg_ready_state()
    assert readable is True and observed is None


def test_the_dashboard_path_is_covered_by_a_repair_not_by_the_form():
    """REFUTED claim (b): ``_contact_values_missing`` is only reachable from
    the config flow, while the dashboard Config card saves every field
    through ``set_option`` one at a time. The live-config Repair is what
    covers that surface, so it must exist and be raiseable."""
    from custom_components.solar_energy_management.coordinator import repair_issues as ri
    assert hasattr(ri, "raise_heat_pump_contact_values_missing")
    assert hasattr(ri, "clear_heat_pump_contact_values_missing")
    assert "heat_pump_contact_values_missing" in ri._DOCS_ANCHORS
