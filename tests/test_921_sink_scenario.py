"""arc #921 — a negative hour through the REAL cycle (#873's ``run_cycle``).

The assembly, not the parts: every switch OFF reproduces today's cycle; a
dynamic tariff whose price is negative produces a CLOSED grid verdict when the
guard is on; the guard never engages on the first cycle (hysteresis); the
verdicts and the guard state are published where the cards read them.
"""
import pytest

from .test_873_cycle_executes import WIRED, _sensors, run_cycle, sealed_nights

NEGATIVE_HOUR = {
    "tariff_mode": "dynamic",
    "dynamic_tariff_entity": "sensor.price",
    "dynamic_feedin_entity": "sensor.feedin",
}


def _states(export_w=3000.0, price=-0.02, feedin=-0.05):
    st = _sensors(6000, export_w, 0, 60)
    st["sensor.price"] = price
    st["sensor.feedin"] = feedin
    return st


@pytest.mark.asyncio
async def test_everything_off_is_todays_cycle():
    before = await run_cycle(config={**WIRED, "currency": "EUR"},
                             states=_sensors(6000, 3000, 0, 60), nights=sealed_nights())
    after = await run_cycle(config={**WIRED, "currency": "EUR", "export_guard_enabled": False,
                                    "battery_house_sink_enabled": False,
                                    "ev_morning_window_enabled": False},
                            states=_sensors(6000, 3000, 0, 60), nights=sealed_nights())
    for k in ("home_consumption_power", "grid_power", "charging_state"):
        assert before[k] == after[k], k
    assert after["export_guard_state"] == "idle"
    assert {v["state"] for v in after["sink_verdicts"].values()} == {"open"}


@pytest.mark.asyncio
async def test_a_negative_hour_closes_the_grid_and_holds_before_engaging():
    out = await run_cycle(config={**WIRED, **NEGATIVE_HOUR, "currency": "EUR",
                                  "export_guard_enabled": True},
                          states=_states(), nights=sealed_nights())
    assert out["sink_verdicts"]["grid_export"]["state"] == "closed", out["sink_verdicts"]
    assert out["export_guard_state"] == "holding"        # never 'engaged' on cycle one


@pytest.mark.asyncio
async def test_a_negative_hour_with_the_guard_off_is_open():
    out = await run_cycle(config={**WIRED, **NEGATIVE_HOUR, "currency": "EUR"},
                          states=_states(), nights=sealed_nights())
    assert out["sink_verdicts"]["grid_export"]["state"] == "open"
    assert out["export_guard_state"] == "idle"


@pytest.mark.asyncio
async def test_an_unreadable_feedin_price_never_closes_the_grid():
    st = _states(); st["sensor.feedin"] = "unavailable"
    out = await run_cycle(config={**WIRED, **NEGATIVE_HOUR, "currency": "EUR",
                                  "export_guard_enabled": True},
                          states=st, nights=sealed_nights())
    # The dynamic provider falls back to the STATIC export rate when the
    # feed-in entity is unreadable — known and positive — so the grid stays
    # open on a read price, not on a guess.
    assert out["sink_verdicts"]["grid_export"]["state"] == "open"
    assert out["export_guard_state"] == "idle"


@pytest.mark.asyncio
async def test_the_house_sink_holds_the_pack_in_a_negative_import_hour():
    """A negative import hour is a keep-the-pack hour — let the house import."""
    out = await run_cycle(config={**WIRED, **NEGATIVE_HOUR, "currency": "EUR",
                                  "battery_house_sink_enabled": True},
                          states=_states(price=-0.02, feedin=0.02), nights=sealed_nights())
    assert out["sink_verdicts"]["house"]["state"] == "held"
    assert out["sink_verdicts"]["grid_export"]["state"] == "open"   # feed-in still earns
