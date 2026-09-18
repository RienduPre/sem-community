"""#978 — a power-strategy flip SEM never verified was cached as done.

@RienduPre (2× Sessy, 2.1.0-beta.29, discussion #958): while
``select.sessy_N_power_strategy`` reads ``nom`` the firmware refuses every
write to ``number.sessy_N_power_setpoint``; SEM's own flip to ``api`` never
landed (HA: *"Referenced entities … are missing or not currently
available"* — a WARNING, and a normal return), yet ``_set_strategy`` cached
``_last_strategy = "api"`` on that return. The de-dup then blocked every
retry for the life of the process, the setpoint went into a battery still
on ``nom``, and three refusals withdrew battery-to-grid — blaming the
setpoint entity.

Pinned here:

* a flip is cached only once the select READS it; nothing is sent when it
  already does;
* a flip that has not landed after ``STRATEGY_RETRY_S`` is a MISS on the
  #915 read-back ledger (entity, wanted, seen; a Repair after three), said
  once, and re-sent;
* the setpoint is WITHHELD — and the device's strikes untouched — until
  the strategy reads active; a laggy select costs one cycle, no noise;
* unreadable is its own state, not "did not land" (#925).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_energy_management.coordinator.battery_adapters.generic import (
    GenericBatteryAdapter,
)

SEL = "select.sessy_1_power_strategy"
SP = "number.sessy_1_power_setpoint"
CFG = {
    "battery_force_discharge_control_entity": SP,
    "battery_strategy_control_entity": SEL,
    "battery_strategy_active_value": "api",
    "battery_strategy_idle_value": "eco",
    "battery_max_discharge_power": 1700,
    "battery_max_charge_power": 2200,
    "battery_setpoint_bidirectional": True,
}


def _rig(*, lands=True, select_state="nom", lag_reads=0, select_present=True):
    """A Sessy-shaped hass: a strategy select and a W setpoint.

    ``lands=False`` — the select IGNORES ``select_option`` (Rien's case).
    ``lag_reads=n`` — the new option shows only after ``n`` further reads.
    ``select_present=False`` — the select has no state at all (wrong id)."""
    hass = MagicMock()
    st = {SP: SimpleNamespace(state="0", attributes={"unit_of_measurement": "W",
                                                     "min": -2200, "max": 1700})}
    if select_present:
        st[SEL] = SimpleNamespace(state=select_state, attributes={})
    lag = {}

    async def _call(domain, service, data=None, **kw):
        if service == "select_option":
            if not lands:
                return
            if lag_reads:
                lag.update(opt=data["option"], left=lag_reads)
            else:
                st[SEL] = SimpleNamespace(state=data["option"], attributes={})
        elif service == "set_value":
            st[SP] = SimpleNamespace(state=str(data["value"]), attributes=st[SP].attributes)

    def _get(eid):
        if eid == SEL and lag:
            lag["left"] -= 1
            if lag["left"] <= 0:
                st[SEL] = SimpleNamespace(state=lag.pop("opt"), attributes={}); lag.clear()
        return st.get(eid)

    hass.services.async_call = AsyncMock(side_effect=_call)
    hass.states.get = MagicMock(side_effect=_get)
    return hass, st


def _calls(hass, service=None):
    out = [(c.args[0], c.args[1], dict(c.args[2])) for c in hass.services.async_call.await_args_list]
    return [c for c in out if service is None or c[1] == service]


# ═══════════════════════════════════════════════════════════════════════
# The flip is believed from the entity
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAFlipIsCachedOnlyOnceTheSelectReadsIt:
    async def test_a_flip_that_lands_is_cached_and_the_setpoint_follows(self):
        hass, st = _rig(lands=True)
        gen = GenericBatteryAdapter(hass, CFG)
        await gen.command_force_discharge(1700, 20.0)
        assert gen._last_strategy == "api"
        assert [c[2]["value"] for c in _calls(hass, "set_value")] == [1700.0]
        assert gen._force_discharge_failures == 0

    async def test_nothing_is_sent_when_the_select_already_reads_it(self):
        hass, st = _rig(select_state="api")
        gen = GenericBatteryAdapter(hass, CFG)
        gen._took_control = False
        await gen.command_force_discharge(1700, 20.0)
        assert _calls(hass, "select_option") == []
        assert [c[2]["value"] for c in _calls(hass, "set_value")] == [1700.0]

    async def test_a_second_command_does_not_resend_a_landed_flip(self):
        hass, st = _rig(lands=True)
        gen = GenericBatteryAdapter(hass, CFG)
        await gen.command_force_discharge(1700, 20.0)
        await gen.command_force_discharge(1500, 20.0)
        assert len(_calls(hass, "select_option")) == 1

    async def test_a_silently_dropped_flip_is_not_cached(self):
        """The bug: the service call returned, the select stayed ``nom``."""
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        await gen.command_force_discharge(1700, 20.0)
        assert len(_calls(hass, "select_option")) == 1
        assert gen._last_strategy is None          # NOT "api"

    async def test_an_unreadable_select_is_unknown_not_landed(self):
        hass, st = _rig(select_present=False, lands=False)   # the id names nothing
        gen = GenericBatteryAdapter(hass, CFG)
        await gen.command_force_discharge(1700, 20.0)
        assert gen._last_strategy is None
        assert _calls(hass, "set_value") == []


# ═══════════════════════════════════════════════════════════════════════
# The setpoint is withheld, the device is not blamed
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestTheSetpointWaitsForTheStrategy:
    async def test_no_setpoint_write_and_no_strike_while_the_select_reads_nom(self, caplog):
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        with caplog.at_level(logging.INFO):
            for _ in range(5):
                await gen.command_force_discharge(1700, 20.0)
        assert _calls(hass, "set_value") == []
        assert gen._force_discharge_failures == 0
        assert gen.supports_forced_discharge is True   # NOT withdrawn
        assert SEL in (gen._last_error or "") and "nom" in gen._last_error
        assert len([m for m in caplog.messages if "not writing it" in m]) == 1

    async def test_the_bidirectional_charge_is_gated_the_same_way(self):
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        await gen.command_force_charge(target_soc=100.0, charge_power_w=1500, duration_min=60)
        assert _calls(hass, "set_value") == []
        assert "charge setpoint withheld" in (gen._last_error or "")

    async def test_a_laggy_select_costs_one_cycle_and_no_noise(self, caplog):
        hass, st = _rig(lands=True, lag_reads=5)     # the flip shows a cycle later
        gen = GenericBatteryAdapter(hass, CFG)
        with caplog.at_level(logging.WARNING):
            await gen.command_force_discharge(1700, 20.0)      # sent, not yet reflected
            assert _calls(hass, "set_value") == []
            await gen.command_force_discharge(1700, 20.0)      # now it reads api
        assert [c[2]["value"] for c in _calls(hass, "set_value")] == [1700.0]
        assert gen.write_not_taken_strikes == 0
        assert not [m for m in caplog.messages if "did not land" in m]

    async def test_no_strategy_select_means_nothing_gates_the_setpoint(self):
        hass, st = _rig()
        cfg = {k: v for k, v in CFG.items() if not k.startswith("battery_strategy")}
        gen = GenericBatteryAdapter(hass, cfg)
        await gen.command_force_discharge(1700, 20.0)
        assert [c[2]["value"] for c in _calls(hass, "set_value")] == [1700.0]


# ═══════════════════════════════════════════════════════════════════════
# The reporter's night: retried, named, on the #915 ledger — never silent
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAStuckFlipIsAMissNotASuccess:
    async def test_resent_after_the_gate_and_counted_once_per_gate(self, caplog):
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        t = [1000.0]
        verdicts = []
        with patch("time.monotonic", side_effect=lambda: t[0]), caplog.at_level(logging.WARNING):
            for _ in range(20):                       # 200 s of 10 s cycles
                await gen.command_force_discharge(1700, 20.0)
                verdicts.append(gen.verify_pending_write())
                t[0] += 10.0
        sends = _calls(hass, "select_option")
        assert len(sends) == 4, sends                 # t=0, 60, 120, 180
        assert gen.write_not_taken_strikes == 3
        assert (gen.last_unverified_entity, gen.last_unverified_wanted,
                gen.last_unverified_seen) == (SEL, "api", "nom")
        assert verdicts.count(False) == 3 and True not in verdicts
        said = [m for m in caplog.messages if "did not land" in m]
        assert len(said) == 1 and SEL in said[0] and "'api'" in said[0] and "nom" in said[0]

    async def test_a_landing_after_misses_clears_the_ledger(self):
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        t = [1000.0]
        with patch("time.monotonic", side_effect=lambda: t[0]):
            for _ in range(13):                       # two misses (t=60, 120)
                await gen.command_force_discharge(1700, 20.0); t[0] += 10.0
            assert gen.write_not_taken_strikes == 2
            gen.verify_pending_write()
            st[SEL] = SimpleNamespace(state="api", attributes={})   # the select finally moves
            await gen.command_force_discharge(1700, 20.0)
        assert gen._last_strategy == "api"
        assert gen.write_not_taken_strikes == 0
        assert gen.last_unverified_entity == "" and gen.last_verified_entity == SEL
        assert gen.verify_pending_write() is True
        assert [c[2]["value"] for c in _calls(hass, "set_value")] == [1700.0]

    async def test_a_missing_select_is_reported_as_missing(self):
        hass, st = _rig(select_present=False, lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        t = [1000.0]
        with patch("time.monotonic", side_effect=lambda: t[0]):
            for _ in range(8):
                await gen.command_force_discharge(1700, 20.0); t[0] += 10.0
        assert gen.write_not_taken_strikes >= 1
        assert gen.last_unverified_seen == "missing"

    async def test_an_interleaved_normal_does_not_erase_the_evidence(self, caplog):
        """The reviewer's repro (challenge record): the intent flaps
        FORCE_DISCHARGE / NORMAL every cycle while the select is stuck on
        ``nom``. NORMAL asks for ``nom`` — already there — and the first cut
        took that landing as "nothing pending", so ``api`` was re-sent as a
        fresh attempt every cycle and no miss was ever counted."""
        hass, st = _rig(lands=False)
        gen = GenericBatteryAdapter(hass, CFG)
        t = [1000.0]
        verdicts = []
        with patch("time.monotonic", side_effect=lambda: t[0]), caplog.at_level(logging.WARNING):
            for i in range(22):                       # 220 s of 10 s cycles
                if i % 2 == 0:
                    await gen.command_force_discharge(1700, 20.0)
                else:
                    await gen.command_normal()
                verdicts.append(gen.verify_pending_write())
                t[0] += 10.0
        api_sends = [c for c in _calls(hass, "select_option") if c[2]["option"] == "api"]
        assert len(api_sends) <= 4, len(api_sends)     # gated, not every cycle
        assert gen.write_not_taken_strikes >= 3
        assert verdicts.count(False) >= 3
        # NORMAL's #523 mutual-exclusion zero is fine; no DISCHARGE setpoint went out
        assert all(c[2]["value"] == 0.0 for c in _calls(hass, "set_value"))
        assert len([m for m in caplog.messages if "did not land" in m]) == 1

