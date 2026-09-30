"""#1024 — a finished charging session is stored with everything the
session list needs: which charger, when it ended, what it cost.

The record used to be written once per fleet disconnect, from the primary
charger's data, with five fields. Now it is written where the session ends
(``_update_session_tracking``), per charger, and the bound is a year of
daily sessions.
"""
import re
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util

from custom_components.solar_energy_management.coordinator.ev_control import (
    EVControlMixin,
)
from custom_components.solar_energy_management.coordinator.storage import (
    SEMStorage, SESSION_HISTORY_MAX,
)
from custom_components.solar_energy_management.coordinator.types import (
    PowerFlows, SessionData,
)


# ── storage ──────────────────────────────────────────────────────────

def _storage():
    hass = MagicMock()
    hass.config.config_dir = "/config"
    energy_store = MagicMock()
    energy_store.async_load = AsyncMock(return_value=None)
    daily_store = MagicMock()
    with patch(
        "custom_components.solar_energy_management.coordinator.storage.Store"
    ) as mock_store:
        mock_store.side_effect = [energy_store, daily_store]
        s = SEMStorage(hass, "entry")
    return s


class TestStorageBound:
    def test_the_bound_is_a_year_of_daily_sessions(self):
        assert SESSION_HISTORY_MAX == 400

    def test_the_oldest_record_drops_first(self):
        s = _storage()
        for i in range(SESSION_HISTORY_MAX + 1):
            s.add_session_to_history({"timestamp": f"t{i}", "energy_kwh": 1.0})
        history = s.get_session_history()
        assert len(history) == SESSION_HISTORY_MAX
        assert history[0]["timestamp"] == "t1"
        assert history[-1]["timestamp"] == f"t{SESSION_HISTORY_MAX}"

    def test_every_field_is_kept_as_given(self):
        s = _storage()
        record = {
            "timestamp": "2026-09-30T17:52:00+02:00",
            "end": "2026-09-30T18:20:00+02:00",
            "charger_id": "keba",
            "energy_kwh": 2.1,
            "solar_share_pct": 40.0,
            "cost": 0.38,
            "currency": "CHF",
            "duration_min": 28.0,
            "taper_detected": False,
        }
        s.add_session_to_history(dict(record))
        assert s.get_session_history() == [record]


# ── the record is written where the session ends ─────────────────────

def _host(*, cid="keba", session_kwh=6.0, full=True):
    dev = SimpleNamespace(device_id=cid, name="Garage")
    h = SimpleNamespace()
    h.config = {"update_interval": 10}
    h.hass = MagicMock()
    h.hass.config.currency = "CHF"
    h._boot_monotonic = time.monotonic() - 9999.0
    h._last_ev_connected = True
    h._ev_conn_confirmed = {"": True}
    h._ev_conn_streak = {}
    start = dt_util.now() - timedelta(minutes=28)
    h._session_data = SessionData(
        active=True, start_time=start.isoformat(),
        energy_kwh=session_kwh, solar_energy_kwh=session_kwh * 0.5,
        grid_energy_kwh=session_kwh * 0.5, solar_share_pct=50.0,
        cost_chf=0.9, duration_minutes=28.0)
    h._storage = MagicMock()
    h._ev_device = dev
    h._ev_devices = {cid: dev}
    h._ev_taper_detectors = {cid: SimpleNamespace(full_detected=full)}
    h._this_charger_power = lambda ev, p: float(getattr(p, "ev_power", 0.0))
    h._energy_calculator = SimpleNamespace(
        _import_rate=0.3, ev_battery_cost_rate=lambda: 0.0)
    return h


def _tick(h, connected, ev_power=0.0):
    p = SimpleNamespace(ev_connected=connected, ev_power=ev_power,
                        ev_connected_per_charger=None)
    EVControlMixin._confirm_ev_connection(h, p)
    EVControlMixin._update_session_tracking(h, p, PowerFlows())


def _unplug(h):
    for _ in range(3):
        _tick(h, connected=False)


class TestRecordOnSessionEnd:
    def test_the_record_names_the_charger_and_carries_cost_and_end(self):
        h = _host()
        _unplug(h)
        h._storage.add_session_to_history.assert_called_once()
        rec = h._storage.add_session_to_history.call_args.args[0]
        assert rec["charger_id"] == "keba"
        assert rec["timestamp"] == h._session_data.start_time
        assert re.match(r"\d{4}-\d{2}-\d{2}T", rec["end"])
        assert rec["energy_kwh"] == 6.0
        assert rec["solar_share_pct"] == 50.0
        assert rec["cost"] == 0.9
        assert rec["currency"] == "CHF"
        assert 27.5 <= rec["duration_min"] <= 29.5
        assert rec["taper_detected"] is True

    def test_the_record_is_written_once_per_session(self):
        h = _host()
        _unplug(h)
        _tick(h, connected=False)
        _tick(h, connected=False)
        assert h._storage.add_session_to_history.call_count == 1

    def test_an_empty_session_leaves_no_record(self):
        h = _host(session_kwh=0.0)
        _unplug(h)
        h._storage.add_session_to_history.assert_not_called()

    def test_a_legacy_single_charger_install_still_records(self):
        h = _host()
        h._ev_device = None
        h._ev_devices = {}
        h._ev_taper_detectors = {}
        _unplug(h)
        rec = h._storage.add_session_to_history.call_args.args[0]
        assert rec["charger_id"] is None
        assert rec["taper_detected"] is False


class TestOneWriter:
    def test_the_coordinator_no_longer_writes_the_record_itself(self):
        import inspect
        from custom_components.solar_energy_management.coordinator import (
            coordinator as coord_mod,
        )
        src = inspect.getsource(coord_mod)
        assert "add_session_to_history(" not in src, (
            "the session record has ONE writer: ev_control._update_session_tracking"
        )
