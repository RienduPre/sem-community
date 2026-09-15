"""#964 — one physical unit, one bucket.

``device_id`` is the entity registry's own answer to "which box is this",
and it is OPTIONAL: KEBA's UDP integration registers no device, and
manually configured MQTT entities have none either. Two of the three
registry discovery sites used it as the WHOLE grouping key, so every
device-less box of a platform landed in ONE bucket — and every per-device
role pick (the old first/last-wins scan and the #962 sibling ranking
alike) then drew from a list that mixed two physical chargers.

The pins below run in both directions, because the fix has two ways to be
wrong: it must SEPARATE two device-less boxes, and it must not SHATTER one
box whose entities merely carry different name prefixes (the reason this
was kept out of #962 — a split changes the charger COUNT).
"""
import ast
import pathlib
from types import SimpleNamespace

from custom_components.solar_energy_management.hardware_detection import (
    _entity_id_prefix,
    _shows_charger_shape,
    build_detection_report,
    discover_all_ev_chargers_from_registry,
    group_entities_by_unit,
    probe_charger_candidates,
)

_PKG = pathlib.Path(__file__).resolve().parent.parent


def _ent(entity_id, platform, device_class=None, device_id=None,
         config_entry_id=None):
    return SimpleNamespace(
        entity_id=entity_id, platform=platform, device_id=device_id,
        original_device_class=device_class, disabled_by=None,
        config_entry_id=config_entry_id,
        unique_id=entity_id.split(".", 1)[1],
        original_unit_of_measurement=None, unit_of_measurement=None,
    )


def _registry(entries):
    reg = SimpleNamespace()
    reg.entities = {e.entity_id: e for e in entries}
    return reg


def _hass(entries):
    """A hass whose entity registry holds ``entries`` (config path)."""
    return SimpleNamespace(_entries=entries)


def _discover(entries):
    from unittest.mock import patch
    with patch(
        "custom_components.solar_energy_management.hardware_detection"
        ".entity_registry.async_get",
        return_value=_registry(entries),
    ):
        return discover_all_ev_chargers_from_registry(_hass(entries))


def _roles(charger):
    return {k: v for k, v in charger.items() if not k.startswith("_")
            and str(v).count(".") == 1 and str(v).split(".", 1)[0] in
            ("sensor", "binary_sensor", "switch", "number")}


# ── Two boxes, no device ids: two chargers, never crossed ──────────────

def _two_keba_boxes():
    return [
        _ent("sensor.keba_garage_charging_power", "keba", "power"),
        _ent("binary_sensor.keba_garage_plug", "keba", "plug"),
        _ent("sensor.keba_garage_total_energy", "keba", "energy"),
        _ent("sensor.keba_carport_charging_power", "keba", "power"),
        _ent("binary_sensor.keba_carport_plug", "keba", "plug"),
        _ent("sensor.keba_carport_total_energy", "keba", "energy"),
    ]


class TestTwoDevicelessBoxes:

    def test_the_pre_fix_key_had_exactly_one_bucket(self):
        """Non-vacuity: spell out the rule this test exists to replace."""
        entries = _two_keba_boxes()
        assert {e.device_id for e in entries} == {None}
        pre_fix = {}
        for e in entries:
            pre_fix.setdefault(e.device_id, []).append(e)
        assert len(pre_fix) == 1          # ← the bug: two boxes, one bucket

    def test_two_deviceless_boxes_group_as_two_units(self):
        units = group_entities_by_unit(_two_keba_boxes())
        assert len(units) == 2
        for members in units.values():
            names = {str(e.entity_id).split(".", 1)[1].split("_")[1]
                     for e in members}
            assert len(names) == 1, f"a unit mixes boxes: {names}"

    def test_config_path_yields_two_chargers_with_uncrossed_roles(self):
        chargers = _discover(_two_keba_boxes())
        assert len(chargers) == 2
        for charger in chargers:
            roles = _roles(charger)
            assert "ev_charging_power_sensor" in roles
            assert "ev_connected_sensor" in roles
            boxes = {v.split(".", 1)[1].split("_")[1] for v in roles.values()}
            assert len(boxes) == 1, f"one charger's roles cross boxes: {roles}"
        power = {c["ev_charging_power_sensor"] for c in chargers}
        assert power == {"sensor.keba_garage_charging_power",
                         "sensor.keba_carport_charging_power"}

    def test_report_path_shows_two_units(self):
        rep = build_detection_report(registry=_registry(_two_keba_boxes()))
        keba = [c for c in rep["chargers"] if c["platform"] == "keba"]
        assert len(keba) == 2
        assert len({c["unit"] for c in keba}) == 2

    def test_roles_are_order_independent(self):
        """Not the partition (that is order-free by construction) but the
        ROLES the config path binds out of it — the thing #962 ranks."""
        import itertools
        entries = _two_keba_boxes()
        seen = set()
        for order in itertools.islice(itertools.permutations(entries), 12):
            chargers = _discover(list(order))
            seen.add(frozenset(
                (c["ev_charging_power_sensor"], c["ev_connected_sensor"])
                for c in chargers))
        assert len(seen) == 1, f"role binding depends on registry order: {seen}"


# ── One box, many prefixes: never shattered ────────────────────────────

def _one_keba_named_keba():
    """A KEBA whose device is called just "Keba": every entity id is
    ``keba_<what it measures>``, so the first-two-token prefix differs per
    entity. One box, three names."""
    return [
        _ent("sensor.keba_charging_power", "keba", "power"),
        _ent("binary_sensor.keba_plug", "keba", "plug"),
        _ent("sensor.keba_total_energy", "keba", "energy"),
    ]


class TestOneBoxIsNeverSplit:

    def test_the_prefixes_really_do_differ(self):
        """Non-vacuity: without this, the pin below proves nothing."""
        prefixes = {_entity_id_prefix(e.entity_id)
                    for e in _one_keba_named_keba()}
        assert len(prefixes) == 3

    def test_one_unit_despite_three_prefixes(self):
        units = group_entities_by_unit(_one_keba_named_keba())
        assert len(units) == 1
        assert len(next(iter(units.values()))) == 3

    def test_config_path_keeps_one_whole_charger(self):
        chargers = _discover(_one_keba_named_keba())
        assert len(chargers) == 1
        roles = _roles(chargers[0])
        assert roles["ev_charging_power_sensor"] == "sensor.keba_charging_power"
        assert roles["ev_connected_sensor"] == "binary_sensor.keba_plug"
        assert chargers[0]["ev_total_energy_sensor"] == "sensor.keba_total_energy"

    def test_a_prefix_split_is_refused_when_no_group_is_a_charger(self):
        """The evidence rule itself: neither half is a charger on its own."""
        power_only = [_one_keba_named_keba()[0]]
        plug_only = [_one_keba_named_keba()[1]]
        assert not _shows_charger_shape(power_only)
        assert not _shows_charger_shape(plug_only)
        assert _shows_charger_shape(_one_keba_named_keba())


# ── The identity a name cannot see ─────────────────────────────────────

class TestConfigEntrySeparatesIdenticalNames:

    def _entries(self):
        # Two identical boxes: Home Assistant disambiguates with a numeric
        # SUFFIX, which a first-two-token prefix cannot see.
        return [
            _ent("sensor.keba_p30_charging_power", "keba", "power",
                 config_entry_id="entry_a"),
            _ent("binary_sensor.keba_p30_plug", "keba", "plug",
                 config_entry_id="entry_a"),
            _ent("sensor.keba_p30_charging_power_2", "keba", "power",
                 config_entry_id="entry_b"),
            _ent("binary_sensor.keba_p30_plug_2", "keba", "plug",
                 config_entry_id="entry_b"),
        ]

    def test_the_prefix_alone_would_have_merged_them(self):
        assert len({_entity_id_prefix(e.entity_id)
                    for e in self._entries()}) == 1

    def test_the_config_entry_splits_them(self):
        units = group_entities_by_unit(self._entries())
        assert len(units) == 2
        for members in units.values():
            assert len({e.config_entry_id for e in members}) == 1

    def test_config_path_yields_two_uncrossed_chargers(self):
        chargers = _discover(self._entries())
        assert len(chargers) == 2
        pairs = {(c["ev_charging_power_sensor"], c["ev_connected_sensor"])
                 for c in chargers}
        assert pairs == {
            ("sensor.keba_p30_charging_power", "binary_sensor.keba_p30_plug"),
            ("sensor.keba_p30_charging_power_2",
             "binary_sensor.keba_p30_plug_2"),
        }


# ── Leftovers: a site total is not a third charger ─────────────────────

class TestUnattributedLeftovers:

    def _entries(self):
        return [
            _ent("sensor.openwb_lp1_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_lp1_plug", "openwb2mqtt", "plug"),
            _ent("sensor.openwb_lp2_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_lp2_plug", "openwb2mqtt", "plug"),
            # the site total — a real power sensor, no box of its own
            _ent("sensor.openwb_global_power", "openwb2mqtt", "power"),
        ]

    def test_two_loadpoints_and_no_third_unit(self):
        units = group_entities_by_unit(self._entries())
        assert len(units) == 2
        claimed = {str(e.entity_id) for g in units.values() for e in g}
        assert "sensor.openwb_global_power" not in claimed

    def test_the_report_names_what_it_dropped_and_nothing_else(self):
        rep = build_detection_report(registry=_registry(self._entries()))
        dropped = {u["entity"] for u in rep["unattributed"]}
        # exactly the site total — a mutant that lists every entity fails here
        assert dropped == {"sensor.openwb_global_power"}
        mapped = {v["entity"] for c in rep["chargers"]
                  for v in c["mapped"].values() if "entity" in v}
        assert not (mapped & dropped)


# ── The prober keeps what #814 proved on the rig ───────────────────────

class TestProberUnchanged:

    def test_a_template_sg_ready_switch_is_not_the_mock_chargers_control(self):
        reg = _registry([
            _ent("binary_sensor.mock_charger_2_plug", "template", "plug"),
            _ent("sensor.mock_charger_2_power", "template", "power"),
            _ent("switch.sg_ready_heating", "template"),
        ])
        cands = probe_charger_candidates(registry=reg)
        assert len(cands) == 1
        assert cands[0]["roles"].get("ev_start_stop_entity") != \
            "switch.sg_ready_heating"

    def test_a_deviceless_keba_is_still_one_candidate(self):
        reg = _registry([
            _ent("binary_sensor.keba_p30_plug", "keba", "plug"),
            _ent("sensor.keba_p30_charging_power", "keba", "power"),
            _ent("sensor.keba_p30_total_energy", "keba", "energy"),
        ])
        cands = probe_charger_candidates(registry=reg)
        assert len(cands) == 1
        assert cands[0]["roles"]["ev_charging_power_sensor"] == \
            "sensor.keba_p30_charging_power"


# ── The guard: nobody groups on device_id alone again ──────────────────

class TestGroupingChokePoint:
    """An AST lint, not a review habit: the class recurs by someone writing
    ``devices.setdefault(e.device_id, [])`` in the next discovery path."""

    def _package_files(self):
        for path in _PKG.rglob("*.py"):
            rel = path.relative_to(_PKG)
            if rel.parts[0] in ("tests", "tools", "scripts", "__pycache__"):
                continue
            yield path

    # The grouping function itself is the one place the key may BE a device id.
    _CHOKE_POINT = ("group_entities_by_unit", "_split_deviceless")


    @staticmethod
    def _is_device_id_read(node) -> bool:
        """``e.device_id`` / ``getattr(e, "device_id", None)`` — the OPTIONAL
        attribute of a registry entry. A bare ``device_id`` parameter (SEM's
        own surplus-device id) is a different thing and not this class, and
        so is ``getattr(self._ev_device, "device_id", "ev_charger")``: the
        receiver must be a plain NAME, which is what a registry entry is in
        every walk that groups one — a loop variable."""
        if (isinstance(node, ast.Attribute) and node.attr == "device_id"
                and isinstance(node.value, ast.Name)):
            return True
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "device_id")

    def _reads_a_registry_device_id(self, key, tainted) -> bool:
        if isinstance(key, ast.Name):
            return key.id in tainted
        return any(self._is_device_id_read(n) for n in ast.walk(key))

    def test_no_module_groups_registry_entries_by_device_id(self):
        offenders = []
        for path in self._package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            allowed = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef)
                        and node.name in self._CHOKE_POINT):
                    allowed.update(id(n) for n in ast.walk(node))
            # names that hold a registry entry's device_id — scoped to the
            # function that read it, so an unrelated ``device_id`` parameter
            # elsewhere in the module is not tainted by association.
            tainted_at = {}
            for scope in ast.walk(tree):
                if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = {t.id for n in ast.walk(scope)
                         if isinstance(n, ast.Assign)
                         and any(self._is_device_id_read(x)
                                 for x in ast.walk(n.value))
                         for t in n.targets if isinstance(t, ast.Name)}
                if names:
                    for n in ast.walk(scope):
                        tainted_at.setdefault(id(n), set()).update(names)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or id(node) in allowed:
                    continue
                tainted = tainted_at.get(id(node), set())
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                key = None
                if func.attr == "setdefault" and node.args:
                    # ``buckets.setdefault(<key>, []).append(e)``
                    key = node.args[0]
                elif func.attr == "append" and isinstance(func.value, ast.Subscript):
                    # the defaultdict form: ``buckets[<key>].append(e)``
                    key = func.value.slice
                if key is not None and self._reads_a_registry_device_id(
                        key, tainted):
                    offenders.append(
                        f"{path.relative_to(_PKG)}:{node.lineno}")
        assert not offenders, (
            "group registry entities with group_entities_by_unit(); "
            "device_id is optional and None is not an identity (#964): "
            + ", ".join(offenders))

    def test_all_three_discovery_sites_funnel_through_the_grouping(self):
        tree = ast.parse(
            (_PKG / "hardware_detection.py").read_text(encoding="utf-8"))
        wanted = {"discover_all_ev_chargers_from_registry",
                  "probe_charger_candidates", "build_detection_report"}
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                calls = {n.func.id for n in ast.walk(node)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)}
                if "group_entities_by_unit" in calls:
                    seen.add(node.name)
        assert seen == wanted, f"not funnelling: {sorted(wanted - seen)}"

    def test_the_lint_catches_the_shapes_it_is_written_for(self):
        """A lint that flags nothing is a lint that has stopped working."""
        offending = [
            "devices.setdefault(e.device_id, []).append(e)",
            'devices.setdefault(getattr(e, "device_id", None), []).append(e)',
            "did = e.device_id\n    devices.setdefault(did, []).append(e)",
            "devices[e.device_id].append(e)",
            # the natural way to "handle" the None key — and the recurrence
            'did = e.device_id or ""\n    devices.setdefault(did, []).append(e)',
            'did = getattr(e, "device_id", None) or ""\n'
            "    devices.setdefault(did, []).append(e)",
            'devices.setdefault(e.device_id or "", []).append(e)',
        ]
        innocent = [
            "self._device_goals.setdefault(device_id, {}).update(clean)",
            "units.setdefault(unit_key, []).append(e)",
            # SEM's own EV device, not a registry entry (coordinator.py)
            'cid = getattr(self._ev_device, "device_id", "ev_charger")\n'
            "    seen.setdefault(cid, {}).update(row)",
        ]
        for src in offending:
            assert self._flagged(f"def f(e, devices):\n    {src}\n"), src
        for src in innocent:
            assert not self._flagged(
                f"def f(e, device_id, unit_key, units, clean, seen, row):\n"
                f"    {src}\n"), src

    def _flagged(self, source: str) -> bool:
        tree = ast.parse(source)
        tainted_at = {}
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {t.id for n in ast.walk(scope)
                     if isinstance(n, ast.Assign)
                     and any(self._is_device_id_read(x)
                             for x in ast.walk(n.value))
                     for t in n.targets if isinstance(t, ast.Name)}
            if names:
                for n in ast.walk(scope):
                    tainted_at.setdefault(id(n), set()).update(names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            key = None
            if func.attr == "setdefault" and node.args:
                key = node.args[0]
            elif func.attr == "append" and isinstance(func.value, ast.Subscript):
                key = func.value.slice
            if key is not None and self._reads_a_registry_device_id(
                    key, tainted_at.get(id(node), set())):
                return True
        return False


# ── What an unproven split must NOT cost (the review of this fix) ───────

class TestAnUnprovenSplitCostsNothing:
    """A name axis that finds ONE box separates nothing — it only sheds the
    entities it left behind. Each case below is a single install where some
    prefix group happens to look like a charger on its own."""

    def test_a_keba_with_a_current_number_keeps_its_plug_and_target(self):
        # sensor.keba_charging_power + number.keba_charging_current share the
        # prefix `keba_charging` and ARE charger-shaped on their own; the plug
        # is under `keba_plug`. One box, and `keba.set_current` needs a target.
        entries = [
            _ent("sensor.keba_charging_power", "keba", "power"),
            _ent("number.keba_charging_current", "keba", "current"),
            _ent("binary_sensor.keba_plug", "keba", "plug"),
            _ent("sensor.keba_total_energy", "keba", "energy"),
        ]
        assert _shows_charger_shape(entries[:2])        # the tempting split
        assert len(group_entities_by_unit(entries)) == 1
        chargers = _discover(entries)
        assert len(chargers) == 1
        assert chargers[0]["ev_connected_sensor"] == "binary_sensor.keba_plug"
        assert chargers[0]["ev_charger_service_entity_id"] == \
            "binary_sensor.keba_plug"
        assert chargers[0]["ev_total_energy_sensor"] == "sensor.keba_total_energy"

    def test_an_unrelated_device_less_sibling_never_deletes_the_charger(self):
        # A YAML-MQTT JuiceBox beside a YAML-MQTT heat pump: the heat pump is
        # charger-shaped (power + a current number), the JuiceBox's own
        # entities each sit under a different prefix. The brand function's
        # identity gate — not the grouping — is what rejects the heat pump.
        entries = [
            _ent("sensor.juicebox_power", "mqtt", "power", config_entry_id="m"),
            _ent("sensor.juicebox_energy_lifetime", "mqtt", "energy",
                 config_entry_id="m"),
            _ent("number.juicebox_max_current", "mqtt", "current",
                 config_entry_id="m"),
            _ent("sensor.heat_pump_power", "mqtt", "power", config_entry_id="m"),
            _ent("number.heat_pump_current", "mqtt", "current",
                 config_entry_id="m"),
        ]
        chargers = _discover(entries)
        assert len(chargers) == 1
        assert chargers[0]["ev_charging_power_sensor"] == "sensor.juicebox_power"
        assert chargers[0]["ev_total_energy_sensor"] == \
            "sensor.juicebox_energy_lifetime"

    def test_disabling_one_entity_never_deletes_its_box(self):
        # lp2's plug disabled: lp2 is no longer charger-shaped, so the split
        # is unproven — that costs the SEPARATION, never lp2's entities.
        entries = [
            _ent("sensor.openwb_lp1_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_lp1_plug", "openwb2mqtt", "plug"),
            _ent("sensor.openwb_lp2_power", "openwb2mqtt", "power"),
        ]
        units = group_entities_by_unit(entries)
        claimed = {str(e.entity_id) for g in units.values() for e in g}
        assert claimed == {str(e.entity_id) for e in entries}

    def test_the_prober_keeps_its_name_split_when_nothing_is_proven(self):
        # #814's live rig, one layer nastier: PV production and an SG-Ready
        # switch beside a template charger whose own entities do not share a
        # prefix. Merging this platform into "one device" is what made the
        # prober call PV power a charger and SG-Ready its start/stop.
        reg = _registry([
            _ent("sensor.pv_power", "template", "power"),
            _ent("switch.sg_ready_heating", "template"),
            _ent("sensor.charger_power", "template", "power"),
            _ent("binary_sensor.charger_plug", "template", "plug"),
            _ent("switch.charger_start", "template"),
        ])
        assert probe_charger_candidates(registry=reg) == []


# ── The axes a two-token prefix cannot see ─────────────────────────────

class TestTheAxesAPrefixCannotSee:

    def test_home_assistants_own_numeric_suffix_separates_two_boxes(self):
        # Two JuiceBoxes on the ONE mqtt config entry: HA disambiguates the
        # second box's entities with a trailing _2, which no prefix can see.
        entries = [
            _ent("sensor.juicebox_power", "mqtt", "power", config_entry_id="m"),
            _ent("number.juicebox_max_current", "mqtt", "current",
                 config_entry_id="m"),
            _ent("sensor.juicebox_power_2", "mqtt", "power", config_entry_id="m"),
            _ent("number.juicebox_max_current_2", "mqtt", "current",
                 config_entry_id="m"),
        ]
        assert len({_entity_id_prefix(e.entity_id) for e in entries}) == 2
        units = group_entities_by_unit(entries)
        assert len(units) == 2
        for members in units.values():
            suffixed = {str(e.entity_id).endswith("_2") for e in members}
            assert len(suffixed) == 1, "a unit mixes the two boxes"

    def test_the_config_entry_separates_what_no_name_axis_can(self):
        # Two boxes whose entity ids were renamed so that every name axis
        # groups them TOGETHER (both powers under one prefix, both plugs
        # under another, no numeric suffix). Only the config entry — one
        # host-based box is one entry — still knows there are two.
        entries = [
            _ent("sensor.wallbox_power_garage", "wallbox", "power",
                 config_entry_id="entry_a"),
            _ent("binary_sensor.wallbox_plug_garage", "wallbox", "plug",
                 config_entry_id="entry_a"),
            _ent("sensor.wallbox_power_carport", "wallbox", "power",
                 config_entry_id="entry_b"),
            _ent("binary_sensor.wallbox_plug_carport", "wallbox", "plug",
                 config_entry_id="entry_b"),
        ]
        for axis in (1, 2, 3):
            by_name = {}
            for e in entries:
                by_name.setdefault(_entity_id_prefix(e.entity_id, axis),
                                   []).append(e)
            shaped = [g for g in by_name.values() if _shows_charger_shape(g)]
            assert len(shaped) < 2, f"name axis {axis} already separates them"
        units = group_entities_by_unit(entries)
        assert len(units) == 2
        for members in units.values():
            assert len({e.config_entry_id for e in members}) == 1

    def test_a_three_token_box_name_separates_two_loadpoints(self):
        entries = [
            _ent("sensor.openwb_chargepoint_1_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_chargepoint_1_plug", "openwb2mqtt", "plug"),
            _ent("sensor.openwb_chargepoint_2_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_chargepoint_2_plug", "openwb2mqtt", "plug"),
        ]
        # the two-token prefix sees ONE name here — this is the finer axis
        assert len({_entity_id_prefix(e.entity_id) for e in entries}) == 1
        units = group_entities_by_unit(entries)
        assert len(units) == 2
        for members in units.values():
            loadpoint = {str(e.entity_id).split(".", 1)[1].split("_")[2]
                         for e in members}
            assert loadpoint in ({"1"}, {"2"}), f"a unit mixes loadpoints: {loadpoint}"


# ── Two boxes that shatter the SAME way ────────────────────────────────

class TestLeftoversGoBackToTheirBox:
    """The two-box threshold refuses a split that finds ONE box. It does
    not, by itself, protect the entities an ADOPTED split leaves behind —
    and two boxes can shatter the same way at the very axis that separated
    them, so the shedding hits both owners at once."""

    def _entries(self):
        # `<box>_charging_power` + `<box>_charging_current` share a name and
        # are charger-shaped on their own; `<box>_plug_connected` is not.
        return [
            _ent("sensor.garage_charging_power", "keba", "power"),
            _ent("number.garage_charging_current", "keba", "current"),
            _ent("binary_sensor.garage_plug_connected", "keba", "plug"),
            _ent("sensor.garage_total_energy", "keba", "energy"),
            _ent("sensor.carport_charging_power", "keba", "power"),
            _ent("number.carport_charging_current", "keba", "current"),
            _ent("binary_sensor.carport_plug_connected", "keba", "plug"),
            _ent("sensor.carport_total_energy", "keba", "energy"),
        ]

    def test_the_loose_shape_would_have_orphaned_them(self):
        """Non-vacuity, and the reason the shape asks for a PLUG wherever
        the platform has one: on the two-token axis each box's power and
        current land together and look like a whole charger, while the plug
        that box is steered by sits in a name of its own."""
        by_name = {}
        for e in self._entries():
            by_name.setdefault(_entity_id_prefix(e.entity_id), []).append(e)
        loose = {k for k, g in by_name.items() if _shows_charger_shape(g)}
        assert loose == {"garage_charging", "carport_charging"}
        assert set(by_name) - loose == {
            "garage_plug", "garage_total", "carport_plug", "carport_total"}
        # with the plug required, that axis proves nothing and is refused
        assert not any(_shows_charger_shape(g, True) for g in by_name.values())

    def test_every_entity_lands_on_its_own_box(self):
        units = group_entities_by_unit(self._entries())
        assert len(units) == 2
        claimed = {str(e.entity_id) for g in units.values() for e in g}
        assert claimed == {str(e.entity_id) for e in self._entries()}
        for members in units.values():
            boxes = {str(e.entity_id).split(".", 1)[1].split("_")[0]
                     for e in members}
            assert len(boxes) == 1, f"a unit mixes boxes: {boxes}"

    def test_each_charger_keeps_its_own_plug_and_service_target(self):
        chargers = _discover(self._entries())
        assert len(chargers) == 2
        assert {(c["ev_charging_power_sensor"], c["ev_connected_sensor"],
                 c["ev_charger_service_entity_id"], c["ev_total_energy_sensor"])
                for c in chargers} == {
            ("sensor.garage_charging_power",
             "binary_sensor.garage_plug_connected",
             "binary_sensor.garage_plug_connected",
             "sensor.garage_total_energy"),
            ("sensor.carport_charging_power",
             "binary_sensor.carport_plug_connected",
             "binary_sensor.carport_plug_connected",
             "sensor.carport_total_energy"),
        }

    def test_a_leftover_equally_close_to_both_boxes_stays_out(self):
        # The rule is "exactly one box is closest", not "somebody takes it":
        # openWB's site total sits one token from either loadpoint.
        entries = [
            _ent("sensor.openwb_lp1_charging_power", "openwb2mqtt", "power"),
            _ent("number.openwb_lp1_charging_current", "openwb2mqtt", "current"),
            _ent("sensor.openwb_lp2_charging_power", "openwb2mqtt", "power"),
            _ent("number.openwb_lp2_charging_current", "openwb2mqtt", "current"),
            _ent("sensor.openwb_global_power", "openwb2mqtt", "power"),
        ]
        units = group_entities_by_unit(entries)
        assert len(units) == 2
        claimed = {str(e.entity_id) for g in units.values() for e in g}
        assert "sensor.openwb_global_power" not in claimed

    def test_a_reattached_leftover_keeps_its_registry_place(self):
        # A brand function that binds first- or last-wins reads the list in
        # order: a leftover must take its registry place, not the end.
        entries = list(reversed(self._entries()))
        order = {str(e.entity_id): i for i, e in enumerate(entries)}
        for members in group_entities_by_unit(entries).values():
            places = [order[str(e.entity_id)] for e in members]
            assert places == sorted(places), (
                f"a re-attached leftover jumped its registry place: {places}")


# ── One box is one box, whatever its sub-structure is named ────────────

class TestSubStructureIsNotASecondBox:
    """A current control is not unique to a box WITHIN one box: a per-phase
    leg carries one each, and so does a sub-meter. Found by the adversarial
    review of this fix — with the loose shape, three phase legs read as
    three chargers and shed the single plug they share."""

    def _per_phase(self):
        return [
            _ent("sensor.wb_l1_power", "keba", "power"),
            _ent("number.wb_l1_current", "keba", "current"),
            _ent("sensor.wb_l2_power", "keba", "power"),
            _ent("number.wb_l2_current", "keba", "current"),
            _ent("sensor.wb_l3_power", "keba", "power"),
            _ent("number.wb_l3_current", "keba", "current"),
            _ent("binary_sensor.wb_plug", "keba", "plug"),
            _ent("sensor.wb_total_energy", "keba", "energy"),
        ]

    def test_the_legs_really_do_look_like_chargers_without_the_plug_rule(self):
        """Non-vacuity: each leg passes the loose shape on its own."""
        legs = self._per_phase()
        assert _shows_charger_shape(legs[0:2])
        assert _shows_charger_shape(legs[2:4])
        assert not _shows_charger_shape(legs[0:2], True)

    def test_three_phase_legs_are_one_charger(self):
        assert len(group_entities_by_unit(self._per_phase())) == 1
        chargers = _discover(self._per_phase())
        assert len(chargers) == 1
        assert chargers[0]["ev_connected_sensor"] == "binary_sensor.wb_plug"
        assert chargers[0]["ev_charger_service_entity_id"] == \
            "binary_sensor.wb_plug"
        assert chargers[0]["ev_total_energy_sensor"] == "sensor.wb_total_energy"

    def test_a_total_beside_the_legs_is_not_a_fourth_box(self):
        # the plug sits INSIDE a shaped group here, so no leftover is
        # stranded — only the plug rule itself refuses this split.
        entries = [
            _ent("sensor.wb_total_power", "keba", "power"),
            _ent("binary_sensor.wb_total_plug", "keba", "plug"),
            _ent("sensor.wb_l1_power", "keba", "power"),
            _ent("number.wb_l1_current", "keba", "current"),
            _ent("sensor.wb_l2_power", "keba", "power"),
            _ent("number.wb_l2_current", "keba", "current"),
        ]
        assert len(group_entities_by_unit(entries)) == 1
        assert len(_discover(entries)) == 1

    def test_a_stranded_mark_refuses_the_axis(self):
        # A platform with no plug at all keeps the loose shape — and there
        # the cut is caught by what it leaves behind: the current control
        # that steers the box is not a leftover anybody may drop.
        entries = [
            _ent("sensor.juicebox_charging_power", "mqtt", "power"),
            _ent("sensor.juicebox_grid_power", "mqtt", "power"),
            _ent("number.juicebox_grid_current", "mqtt", "current"),
            _ent("number.juicebox_charging_current", "mqtt", "current"),
            _ent("number.juicebox_limit_current", "mqtt", "current"),
        ]
        units = group_entities_by_unit(entries)
        assert len(units) == 1
        claimed = {str(e.entity_id) for g in units.values() for e in g}
        assert claimed == {str(e.entity_id) for e in entries}


# ── The box number lives in the middle of the id ───────────────────────

class TestTheNumberInsideTheName:

    def _entries(self):
        # HA disambiguates the second box by its DEVICE name, so the digit
        # is a token in the middle — no fixed-width prefix and no trailing
        # _<n> can see it, and "garage" is a prefix of "garage_2".
        return [
            _ent("sensor.garage_charging_power", "keba", "power"),
            _ent("number.garage_charging_current", "keba", "current"),
            _ent("binary_sensor.garage_plug_connected", "keba", "plug"),
            _ent("sensor.garage_total_energy", "keba", "energy"),
            _ent("sensor.garage_2_charging_power", "keba", "power"),
            _ent("number.garage_2_charging_current", "keba", "current"),
            _ent("binary_sensor.garage_2_plug_connected", "keba", "plug"),
            _ent("sensor.garage_2_total_energy", "keba", "energy"),
        ]

    def test_no_other_axis_can_see_it(self):
        """Non-vacuity: every width of prefix, and the trailing number,
        either shatter the boxes or merge them."""
        for width in (1, 2, 3):
            by_name = {}
            for e in self._entries():
                by_name.setdefault(
                    _entity_id_prefix(e.entity_id, width), []).append(e)
            shaped = [g for g in by_name.values()
                      if _shows_charger_shape(g, True)]
            assert len(shaped) < 2, f"prefix width {width} already separates"

    def test_two_complete_boxes(self):
        chargers = _discover(self._entries())
        assert len(chargers) == 2
        assert {(c["ev_charging_power_sensor"], c["ev_connected_sensor"],
                 c["ev_charger_service_entity_id"], c["ev_total_energy_sensor"])
                for c in chargers} == {
            ("sensor.garage_charging_power",
             "binary_sensor.garage_plug_connected",
             "binary_sensor.garage_plug_connected",
             "sensor.garage_total_energy"),
            ("sensor.garage_2_charging_power",
             "binary_sensor.garage_2_plug_connected",
             "binary_sensor.garage_2_plug_connected",
             "sensor.garage_2_total_energy"),
        }


# ── A leftover that DOES belong to a box ───────────────────────────────

class TestALeftoverThatFindsItsBox:

    def _entries(self):
        # Each box's meter is named outside the prefix that separated the
        # boxes: `garage_energy_*` beside `garage_wb_*`.
        return [
            _ent("sensor.garage_wb_power", "keba", "power"),
            _ent("binary_sensor.garage_wb_plug", "keba", "plug"),
            _ent("sensor.garage_energy_total", "keba", "energy"),
            _ent("sensor.carport_wb_power", "keba", "power"),
            _ent("binary_sensor.carport_wb_plug", "keba", "plug"),
            _ent("sensor.carport_energy_total", "keba", "energy"),
        ]

    def test_the_meter_is_a_leftover_of_the_adopted_axis(self):
        """Non-vacuity: on the axis that separates the boxes, the meters
        are groups of their own and show no charger shape."""
        by_name = {}
        for e in self._entries():
            by_name.setdefault(_entity_id_prefix(e.entity_id), []).append(e)
        assert set(by_name) == {"garage_wb", "garage_energy",
                                "carport_wb", "carport_energy"}
        assert not _shows_charger_shape(by_name["garage_energy"], True)

    def test_each_meter_goes_back_to_its_own_box(self):
        chargers = _discover(self._entries())
        assert len(chargers) == 2
        assert {(c["ev_charging_power_sensor"], c["ev_total_energy_sensor"])
                for c in chargers} == {
            ("sensor.garage_wb_power", "sensor.garage_energy_total"),
            ("sensor.carport_wb_power", "sensor.carport_energy_total"),
        }


# ── What the diagnostics must NOT start saying ─────────────────────────

class TestTheProberComparisonStaysQuiet:

    def _one_deviceless_box(self):
        return [
            _ent("sensor.keba_p30_charging_power", "keba", "power"),
            _ent("binary_sensor.keba_p30_plug", "keba", "plug"),
            _ent("number.keba_p30_max_current", "keba", "current"),
            _ent("sensor.keba_p30_total_energy", "keba", "energy"),
        ]

    def test_the_two_sides_really_do_group_it_differently(self):
        """Non-vacuity: the prober keeps a name split the binding paths
        merge, so a comparison keyed on the GROUP would disagree here."""
        entries = self._one_deviceless_box()
        brand = group_entities_by_unit(entries)
        prober = group_entities_by_unit(entries, unproven_split="prefix")
        assert list(brand) != list(prober)

    def test_one_box_found_by_both_is_no_disagreement(self):
        rep = build_detection_report(registry=_registry(self._one_deviceless_box()))
        assert len(rep["chargers"]) == 1
        assert len(rep["prober_candidates"]) == 1
        assert rep["disagreements"] == []

    def test_a_shape_only_the_prober_knows_is_still_reported(self):
        """The section must not go silent: an unsupported brand the prober
        recognises is exactly what it exists to surface (#814)."""
        rep = build_detection_report(registry=_registry(
            self._one_deviceless_box() + [
                _ent("sensor.abl_power", "abl_emh1", "power", device_id="d9"),
                _ent("binary_sensor.abl_plug", "abl_emh1", "plug", device_id="d9"),
                _ent("number.abl_max_current", "abl_emh1", "current",
                     device_id="d9"),
            ]))
        assert [(d["kind"], d["platform"]) for d in rep["disagreements"]] == \
            [("prober_only", "abl_emh1")]

    def test_the_prober_is_never_coarser_than_its_own_prefix(self):
        # #814's rig: a garage door beside a template charger. An adopted
        # one-token axis would hand the door over as start/stop.
        reg = _registry([
            _ent("switch.garage_door", "template"),
            _ent("sensor.garage_power", "template", "power"),
            _ent("binary_sensor.garage_plug", "template", "plug"),
            _ent("switch.garage_start", "template"),
            _ent("sensor.carport_power", "template", "power"),
            _ent("binary_sensor.carport_plug", "template", "plug"),
            _ent("switch.carport_start", "template"),
        ])
        assert probe_charger_candidates(registry=reg) == []


# ── The order the axes are tried in, and the order units come back ─────

class TestOrderIsPartOfTheContract:

    def test_the_finest_axis_wins_over_a_coarser_one_that_also_fits(self):
        # Two openWB loadpoints and a KEBA on one device-less platform: the
        # one-token axis "fits" (openwb / keba) and merges the loadpoints.
        entries = [
            _ent("sensor.openwb_lp1_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_lp1_plug", "openwb2mqtt", "plug"),
            _ent("sensor.openwb_lp2_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.openwb_lp2_plug", "openwb2mqtt", "plug"),
            _ent("sensor.keba_p30_power", "openwb2mqtt", "power"),
            _ent("binary_sensor.keba_p30_plug", "openwb2mqtt", "plug"),
        ]
        units = group_entities_by_unit(entries)
        assert len(units) == 3, "a coarser axis merged the two loadpoints"

    def test_the_first_charger_is_the_first_box_in_the_registry(self):
        # `discover_ev_charger_from_registry` returns [0] and zero-config
        # setup stores it, so which box is primary is not an accident.
        entries = _two_keba_boxes()
        assert _discover(entries)[0]["ev_charging_power_sensor"] == \
            "sensor.keba_garage_charging_power"
        assert _discover(list(reversed(entries)))[0][
            "ev_charging_power_sensor"] == "sensor.keba_carport_charging_power"
