"""#971 — the one-tap grid-sign flip is bound to the meter it was tapped against.

Found on 17.09.2026 while simulating the export guard on the .175 rig: the
grid was moved from the combined Huawei meter to a declared split pair
(`grid_import_power_entity` / `grid_export_power_entity`, both synthetic,
both positive magnitudes) and SEM read **export 2000 W as 2000 W of import**.
`sensor.sem_diag_grid_sign` said `normal`, so no learned lock was flipping it.
The store carried `grid_sign_user_flip: true` — the #461 tap, made months
earlier to correct the combined meter's auto-lock — and `read_power()`
applied it "on top of whatever the path decided", declared pair included.

A declared pair's convention is fixed by declaration (`export − import`);
there is no lock for the tap to correct. The tap is a correction bound to a
source, and it must not outlive the source. So: the reader records which
branch read the meter (`_grid_source_key`), the flip service persists that
key beside the flip, and the flip applies only while the same source is in
use — a pre-binding flip (no key recorded) applies to the auto-derived
sources it was built for and never to a declared pair.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.solar_energy_management.coordinator.sensor_reader import (
    SensorReader,
)

from .ast_contracts import call_sites
from .test_split_grid_integration import (
    _make_energy_dashboard_config, _make_reader_with_states, _state,
)

IMP, EXP, COMBINED = "sensor.sim_grid_import", "sensor.sim_grid_export", "sensor.power_meter"


def _pair_reader(flip=None, flip_source="__unset__"):
    """A declared manual pair reading 2 kW of EXPORT, with the flip option."""
    ed = _make_energy_dashboard_config(
        solar_power="sensor.solar", grid_import_power=COMBINED,   # a combined sensor EXISTS too
        grid_import_energy="sensor.grid_import_total", grid_export_energy="sensor.grid_export_total",
        battery_power=None,
    )
    states = {
        "sensor.solar": _state(5000),
        COMBINED: _state(-999, device_class="power"),        # must NOT be read
        IMP: _state(0, device_class="power"),
        EXP: _state(2000, device_class="power"),
        "sensor.grid_import_total": _state(10, "kWh"),
        "sensor.grid_export_total": _state(20, "kWh"),
    }
    cfg = {"grid_import_power_entity": IMP, "grid_export_power_entity": EXP}
    if flip is not None:
        cfg["grid_sign_user_flip"] = flip
    if flip_source != "__unset__":
        cfg["grid_sign_user_flip_source"] = flip_source
    return _make_reader_with_states(MagicMock(), states, ed, extra_config=cfg)


def _combined_reader(flip=None, flip_source="__unset__"):
    """The combined meter reading +2000 (already SEM convention), autodetect locked 'normal'."""
    ed = _make_energy_dashboard_config(
        solar_power="sensor.solar", grid_import_power=COMBINED,
        grid_import_energy="sensor.grid_import_total", grid_export_energy="sensor.grid_export_total",
        battery_power=None,
    )
    states = {
        "sensor.solar": _state(5000),
        COMBINED: _state(2000, device_class="power"),
        "sensor.grid_import_total": _state(10, "kWh"),
        "sensor.grid_export_total": _state(20, "kWh"),
    }
    cfg = {}
    if flip is not None:
        cfg["grid_sign_user_flip"] = flip
    if flip_source != "__unset__":
        cfg["grid_sign_user_flip_source"] = flip_source
    r = _make_reader_with_states(MagicMock(), states, ed, extra_config=cfg)
    r._grid_sign_detected = True          # the auto-lock has spoken: no negation
    r._grid_sign_inverted = False
    return r


# ═══════════════════════════════════════════════════════════════════════
# The bug, then the binding
# ═══════════════════════════════════════════════════════════════════════

class TestADeclaredPairIsNeverFlippedByAnOldTap:
    def test_the_175_case_export_2kw_reads_as_export(self):
        """The pre-binding flip (no source recorded) on a declared pair."""
        r = _pair_reader(flip=True)
        p = r.read_power()
        assert p.grid_power == 2000, p.grid_power           # was −2000
        assert r._grid_source_key == f"manual:{IMP}|{EXP}"
        assert r.user_flip_applies() is False

    def test_without_any_flip_the_pair_reads_the_same(self):
        assert _pair_reader().read_power().grid_power == 2000

    def test_a_flip_recorded_against_the_combined_meter_does_not_follow(self):
        r = _pair_reader(flip=True, flip_source=f"combined:{COMBINED}")
        assert r.read_power().grid_power == 2000
        assert r.user_flip_applies() is False

    def test_a_flip_tapped_ON_the_pair_still_applies_to_the_pair(self):
        """The button keeps working where it was pressed (a swapped pair)."""
        r = _pair_reader(flip=True, flip_source=f"manual:{IMP}|{EXP}")
        assert r.read_power().grid_power == -2000
        assert r.user_flip_applies() is True


class TestTheAutoSourcesKeepTodaysBehaviour:
    def test_a_pre_binding_flip_still_corrects_the_combined_meter(self):
        r = _combined_reader(flip=True)
        assert r.read_power().grid_power == -2000
        assert r._grid_source_key == f"combined:{COMBINED}"
        assert r.user_flip_applies() is True

    def test_no_flip_no_change(self):
        assert _combined_reader().read_power().grid_power == 2000

    def test_a_flip_bound_to_a_pair_does_not_reach_the_combined_meter(self):
        r = _combined_reader(flip=True, flip_source=f"manual:{IMP}|{EXP}")
        assert r.read_power().grid_power == 2000
        assert r.user_flip_applies() is False


class TestTheFlipIsPersistedWithItsSource:
    def test_turning_on_records_the_current_source(self):
        r = _pair_reader(); r.read_power()
        out = r.user_flip_options({"other": 1}, True)
        assert out == {"other": 1, "grid_sign_user_flip": True,
                       "grid_sign_user_flip_source": f"manual:{IMP}|{EXP}"}

    def test_turning_off_drops_the_binding(self):
        r = _pair_reader(); r.read_power()
        out = r.user_flip_options({"grid_sign_user_flip": True,
                                   "grid_sign_user_flip_source": "combined:x"}, False)
        assert out["grid_sign_user_flip"] is False and out["grid_sign_user_flip_source"] is None

    def test_the_service_is_the_one_production_caller(self):
        sites = call_sites("user_flip_options")
        assert [s[0] for s in sites] == ["__init__.py"], sites

    def test_the_diagnostics_show_both_halves(self):
        r = _pair_reader(flip=True, flip_source=f"combined:{COMBINED}"); r.read_power()
        d = r.grid_sign_diagnostics()
        assert d["user_flip"] is True
        assert d["user_flip_source"] == f"combined:{COMBINED}"
        assert d["grid_source"] == f"manual:{IMP}|{EXP}"
        assert d["user_flip_applies"] is False


class TestItSaysSoOnce:
    def test_an_ignored_flip_logs_one_warning(self, caplog):
        import logging
        r = _pair_reader(flip=True)
        with caplog.at_level(logging.WARNING):
            r.read_power(); r.read_power(); r.read_power()
        hits = [m for m in caplog.messages if "one-tap user flip is bound" in m]
        assert len(hits) == 1, hits

    def test_no_flip_logs_nothing(self, caplog):
        import logging
        r = _pair_reader()
        with caplog.at_level(logging.WARNING):
            r.read_power()
        assert not [m for m in caplog.messages if "user flip" in m]


def test_every_grid_branch_tags_its_source():
    """The binding is only as good as the key: every place that assigns
    grid_power in the reader must name its source right after."""
    import ast
    from .ast_contracts import _tree_of          # dedents a method's source for ast.parse
    tree = _tree_of(SensorReader._read_from_energy_dashboard)
    assigns = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Assign) and any(
                   isinstance(t, ast.Attribute) and t.attr == "grid_power" for t in n.targets)]
    tags = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == "_grid_source_key" for t in n.targets)]
    assert assigns, "no grid_power assignment found?"
    untagged = [a for a in assigns if not any(0 < t - a <= 3 for t in tags)]
    assert not untagged, f"grid_power assigned without a source tag at lines {untagged}"
