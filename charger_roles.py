"""(#1032, hardware wave) Charger roles read from ANY integration.

SEM does not cover hardware; it connects to integrations (Guido,
01.10.2026). This module is the crawler's reader for the roles a charger —
or a car that charges — exposes, by the integration's own words, never by
brand:

* R1 companion device — a device with no charger role on the same config
  entry as a charger (a Zaptec installation, an Easee equalizer) belongs to
  that charger; it is not "unknown hardware".
* R2 start/stop pair — a start and a stop button.
* R3 charge control on the car — the car's charging-amps number and its
  charge switch, on a device the vocabulary says is a vehicle.
* R4 read-only charger + controlling car — a charger that only reports,
  wired to the one car on the install that has R3 controls.
* R5 select by options — a charge mode (it can stop and it can charge) and
  a phase setting (1 / 3), whatever the select's key says.
* R6 service by fields — a per-phase current service; a site-current
  service on a companion device.

Everything here is REPORT DATA: an offer the user accepts, never a binding.
Pure over registry entries, a ``state_of`` callback and a
``{service: fields}`` map, so the rig can prove it on real integrations.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from .consts import role_lexicon as lex

#: platforms whose vocabulary is the user's, not an integration's
_SKIP_PLATFORMS = frozenset(lex.OPAQUE_PLATFORMS) | {"solar_energy_management"}


def _words(entry) -> List[str]:
    """The integration's own words for an entity: its translation key and
    its unique id."""
    return [w for w in (str(getattr(entry, "translation_key", "") or ""),
                        str(getattr(entry, "unique_id", "") or "")) if w]


def _matches(entry, rule: Dict[str, Any]) -> bool:
    eid = str(getattr(entry, "entity_id", ""))
    if not eid.startswith(f"{rule['platform']}."):
        return False
    words = _words(entry)
    if any(re.search(p, w, re.I) for p in rule.get("not", ()) for w in words):
        return False
    return any(re.search(p, w, re.I) for p in rule["any"] for w in words)


def _first(entries, rule) -> Optional[str]:
    hits = sorted(str(e.entity_id) for e in entries if _matches(e, rule))
    return hits[0] if hits else None


def _dc(entry) -> str:
    return str(getattr(entry, "original_device_class", "") or "")


def is_vehicle(entries) -> bool:
    """The vocabulary of a CAR: a marker no house declares, and none of the
    words only a building says (``HOUSE_MARKERS``)."""
    words = " ".join(w.lower() for e in entries for w in _words(e))
    return (any(m in words for m in lex.VEHICLE_MARKERS)
            and not any(m in words for m in lex.HOUSE_MARKERS))


def _options(entry, state_of) -> List[str]:
    opts = None
    if state_of is not None:
        st = state_of(str(entry.entity_id))
        if st is not None:
            opts = (getattr(st, "attributes", None) or {}).get("options")
    if not opts:
        opts = (getattr(entry, "capabilities", None) or {}).get("options")
    return [str(o) for o in (opts or [])]


def _pick_option(options: List[str], wanted: Iterable[str]) -> Optional[str]:
    low = {o.lower(): o for o in options}
    for w in wanted:
        if w in low:
            return low[w]
    return None


def _power_sensor(entries, *, vehicle: bool) -> Optional[str]:
    """The charging power reading: a ``power`` sensor that is not one phase
    leg or one CT clamp of several. A car's power sensor must say it is the
    CHARGER's (a car also reports drive power)."""
    cands = []
    for e in entries:
        eid = str(e.entity_id)
        if not eid.startswith("sensor.") or _dc(e) != "power":
            continue
        words = " ".join(_words(e)).lower() + " " + eid.lower()
        if vehicle and "charg" not in words:
            continue
        # a clamp on something else: the grid, the panels, a house
        # battery, a CT that only monitors
        if re.search(r"(reactive|export|import|generation|grid|battery|"
                     r"photovolt|solar|_pv_|\bpv\b|monitor)", words):
            continue
        leg = bool(re.search(r"(?:_|-)(l[123]|phase_?[123]|ct[1-9]|[123])$",
                             eid)) or bool(re.search(r"phase_[123]", eid))
        named = bool(re.search(r"charg|total|session", words))
        cands.append((leg, not named, eid))
    cands.sort()
    return cands[0][2] if cands else None


def _plug_sensor(entries) -> Optional[str]:
    plugs = sorted(str(e.entity_id) for e in entries
                   if str(e.entity_id).startswith("binary_sensor.")
                   and _dc(e) == "plug")
    if plugs:
        return plugs[0]
    cables = sorted(str(e.entity_id) for e in entries
                    if str(e.entity_id).startswith("binary_sensor.")
                    and _dc(e) == "connectivity"
                    and re.search(r"cable|plug", " ".join(_words(e)), re.I))
    return cables[0] if cables else None


def _charging_sensor(entries) -> Optional[str]:
    """Charging NOW: a ``battery_charging`` binary, or a ``running`` one that
    says so (a car's dashcam and cabin protection also run)."""
    hits = sorted(
        str(e.entity_id) for e in entries
        if str(e.entity_id).startswith("binary_sensor.")
        and (_dc(e) == "battery_charging"
             or (_dc(e) == "running"
                 and re.search(r"charg|contactor", " ".join(_words(e)), re.I))))
    return hits[0] if hits else None


def _is_setting(entries, eid: Optional[str]) -> bool:
    """A number filed under the device's CONFIG category is a stored
    setting (a charger's own maximum), not a live control: writing it every
    cycle wears the box's memory and changes what the owner set."""
    for e in entries:
        if str(e.entity_id) == eid:
            return str(getattr(e, "entity_category", "") or "").endswith("config")
    return False


def _current_number(entries) -> Optional[str]:
    hits = []
    for e in entries:
        eid = str(e.entity_id)
        if not eid.startswith("number."):
            continue
        rule = lex.ROLE_RULES["ev_current_control"]
        for w in _words(e):
            key = w.rsplit("-", 1)[-1]
            if lex.role_for("number", key) == "ev_current_control" or \
                    lex.role_for("number", w) == "ev_current_control":
                hits.append(eid)
                break
            # the words that are a current only on a charger (``amp``,
            # ``charge_rate``) — this reader only acts on charger-shaped
            # devices
            if (any(re.search(p, key, re.I) for p in rule.get("charger_only_any", ()))
                    and not any(re.search(p, key, re.I) for p in rule.get("not", ()))):
                hits.append(eid)
                break
    return sorted(hits)[0] if hits else None


def unit_roles(entries, *, state_of: Optional[Callable] = None) -> Dict[str, Any]:
    """Every charger role ONE device carries, by its own words."""
    roles: Dict[str, Any] = {}
    vehicle = is_vehicle(entries)
    if vehicle:
        roles["vehicle"] = True
        for role, rule in lex.VEHICLE_CONTROL_RULES.items():
            hit = _first(entries, rule)
            if hit:
                roles[role] = hit
    else:
        cur = _current_number(entries)
        if cur:
            roles["current_number"] = cur
            if _is_setting(entries, cur):
                roles["current_is_setting"] = True
        start = _first(entries, lex.CHARGER_BUTTON_RULES["ev_start_button"])
        stop = _first(entries, lex.CHARGER_BUTTON_RULES["ev_stop_button"])
        if start and stop:
            roles["start_stop_buttons"] = [start, stop]
        for e in sorted(entries, key=lambda x: str(x.entity_id)):
            if not str(e.entity_id).startswith("select."):
                continue
            opts = _options(e, state_of)
            go = _pick_option(opts, lex.SELECT_CHARGE_OPTIONS)
            halt = _pick_option(opts, lex.SELECT_STOP_OPTIONS)
            if go and halt and "charge_mode" not in roles:
                roles["charge_mode"] = {"entity": str(e.entity_id),
                                        "start": go, "stop": halt}
            elif (all(p in opts for p in lex.SELECT_PHASE_OPTIONS)
                  and "phase_select" not in roles):
                roles["phase_select"] = {"entity": str(e.entity_id),
                                         "value_1p": "1", "value_3p": "3"}
    power = _power_sensor(entries, vehicle=vehicle)
    if power:
        roles["power"] = power
    plug = _plug_sensor(entries)
    if plug:
        roles["plug"] = plug
    charging = _charging_sensor(entries)
    if charging:
        roles["charging"] = charging
    return roles


#: the field a current-setting service takes the amperes in (#956)
_CURRENT_FIELDS = ("current", "max_current", "charging_current", "amps",
                   "ampere", "amp", "current_a")


def service_roles(domain: str, services: Dict[str, List[str]]) -> Dict[str, Any]:
    """R6 — the integration's services read by their FIELDS, plus the #956
    rule: a service named like a current setter with a current field is a
    CONTROL (KEBA's ``set_current``), so a charger driven by services is
    never "read-only"."""
    out: Dict[str, Any] = {}
    rule = lex.SERVICE_ROLE_RULES.get("ev_current_control") or {}
    for name in sorted(services or {}):
        fields = list(services[name] or [])
        full = f"{domain}.{name}"
        param = next((f for f in _CURRENT_FIELDS if f in fields), None)
        if (rule and param
                and any(re.search(p, full, re.I) for p in rule.get("any", ()))
                and not any(re.search(p, full, re.I) for p in rule.get("not", ()))):
            out.setdefault("current_service", {"service": full, "param": param,
                                               "fields": fields})
        if all(f in fields for f in lex.SERVICE_PHASE_FIELDS):
            out.setdefault("phase_current_service",
                           {"service": f"{domain}.{name}", "fields": fields})
        cur = [f for f in fields if f in lex.SERVICE_SITE_CURRENT_FIELDS]
        if cur:
            out.setdefault("site_current_service",
                           {"service": f"{domain}.{name}", "param": cur[0],
                            "fields": fields})
    return out


def _has_control(roles: Dict[str, Any]) -> bool:
    return any(k in roles for k in ("current_number", "start_stop_buttons",
                                    "charge_mode", "vehicle_charge_current",
                                    "vehicle_charge_switch", "current_service"))


def _offer(roles: Dict[str, Any]) -> Dict[str, Any]:
    """The charger config these roles fill — the same keys the charger
    pickers and the coordinator already use."""
    o: Dict[str, Any] = {}
    if roles.get("power"):
        o["ev_charging_power_sensor"] = roles["power"]
    if roles.get("plug"):
        o["ev_connected_sensor"] = roles["plug"]
    if roles.get("charging"):
        o["ev_charging_sensor"] = roles["charging"]
    if roles.get("current_number"):
        o["ev_current_control_entity"] = roles["current_number"]
    if roles.get("vehicle_charge_current"):
        o["ev_current_control_entity"] = roles["vehicle_charge_current"]
    if roles.get("vehicle_charge_switch"):
        o["ev_start_stop_entity"] = roles["vehicle_charge_switch"]
    if roles.get("charge_mode"):
        cm = roles["charge_mode"]
        o["ev_charge_mode_entity"] = cm["entity"]
        o["ev_charge_mode_start"] = cm["start"]
        o["ev_charge_mode_stop"] = cm["stop"]
    if roles.get("start_stop_buttons"):
        start, stop = roles["start_stop_buttons"]
        o["ev_start_service"] = "button.press"
        o["ev_start_service_data"] = json.dumps({"entity_id": start})
        o["ev_stop_service"] = "button.press"
        o["ev_stop_service_data"] = json.dumps({"entity_id": stop})
    if roles.get("current_service") and "ev_current_control_entity" not in o:
        o["ev_charger_service"] = roles["current_service"]["service"]
        o["ev_service_param_name"] = roles["current_service"]["param"]
    if roles.get("phase_select"):
        o["_suggested_phase_switch"] = dict(roles["phase_select"])
    return o


def build_role_offers(units: Dict[Any, List[Any]], *,
                      device_of: Callable[[Any], Optional[str]],
                      entry_of: Callable[[Any], Optional[str]],
                      services_of: Optional[Callable[[str], Dict[str, list]]] = None,
                      state_of: Optional[Callable] = None,
                      claimed: Iterable[Optional[str]] = (),
                      claimed_entities: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """One offer per charger-shaped unit, with its companions (R1), the car
    that drives a read-only charger (R4) and the service roles (R6).

    ``units`` maps a unit key to its registry entries; ``device_of`` /
    ``entry_of`` give a unit's device id and config entry id; ``claimed`` is
    the device ids a brand path already turned into a charger, and
    ``claimed_entities`` the entities SEM already binds or has configured — a
    unit holding any of them is left alone (a device-less box like KEBA has
    no device id to match on).
    """
    claimed = {c for c in claimed if c}
    claimed_entities = {str(e) for e in claimed_entities if e}
    rows = []
    for key, entries in units.items():
        entries = [e for e in entries
                   if str(getattr(e, "platform", "") or "") not in _SKIP_PLATFORMS]
        if not entries:
            continue
        if claimed_entities & {str(e.entity_id) for e in entries}:
            continue
        platform = str(entries[0].platform or "")
        roles = unit_roles(entries, state_of=state_of)
        if services_of and not roles.get("vehicle"):
            srv = service_roles(platform, services_of(platform))
            if srv.get("current_service"):
                roles["current_service"] = srv["current_service"]
        rows.append({"key": key, "platform": platform,
                     "device_id": device_of(key), "entry_id": entry_of(key),
                     "roles": roles, "size": len(entries)})

    vehicles = [r for r in rows if r["roles"].get("vehicle")
                and r["roles"].get("vehicle_charge_current")]
    offers: List[Dict[str, Any]] = []
    for r in rows:
        roles = r["roles"]
        if r["device_id"] in claimed:
            continue
        if roles.get("vehicle"):
            if not roles.get("vehicle_charge_current"):
                continue
            kind = "vehicle"
        elif roles.get("power") and (roles.get("plug") or _has_control(roles)):
            kind = "charger" if _has_control(roles) else "read_only_charger"
        else:
            continue
        offer = _offer(roles)
        row: Dict[str, Any] = {"platform": r["platform"],
                               "device_id": r["device_id"], "kind": kind,
                               "roles": sorted(k for k in roles
                                               if k not in ("vehicle", "current_is_setting")),
                               "offer": offer, "companions": [],
                               "evidence": []}
        # R1 — devices of the same config entry with no charger shape
        for c in rows:
            if (c is r or c["entry_id"] is None or c["entry_id"] != r["entry_id"]
                    or c["roles"].get("vehicle")):
                continue
            croles = c["roles"]
            # a companion carries no power reading of its own: a device that
            # measures is something else (an energy site, a heat diverter)
            if croles.get("power"):
                continue
            row["companions"].append({"device_id": c["device_id"],
                                      "entities": c["size"]})
        # R1 — a charger whose own current number is a stored setting takes
        # the live current control of its companion (the installation's
        # available current), or else starts and stops instead of rewriting
        # the setting
        if roles.get("current_is_setting"):
            comp_cur = [c["roles"].get("current_number") for c in rows
                        if c["device_id"] in {x["device_id"] for x in row["companions"]}
                        and c["roles"].get("current_number")
                        and not c["roles"].get("current_is_setting")]
            if comp_cur:
                offer["ev_current_control_entity"] = sorted(comp_cur)[0]
                row["evidence"].append(
                    f"{roles['current_number']} is a stored setting; the "
                    f"companion's {sorted(comp_cur)[0]} is the live control")
            elif "ev_start_service" in offer or "ev_charge_mode_entity" in offer:
                offer.pop("ev_current_control_entity", None)
                row["evidence"].append(
                    f"{roles['current_number']} is a stored setting; SEM "
                    "starts and stops instead of rewriting it")
        # R6 — services by their fields
        srv = service_roles(r["platform"], services_of(r["platform"])) \
            if services_of else {}
        if srv.get("phase_current_service"):
            row["phase_service"] = srv["phase_current_service"]
        if srv.get("site_current_service"):
            # Report data, not a control: a site-current service may need a
            # right the account lacks (#1032's installation had no
            # available_current entity, which is how the integration says so)
            row["site_service"] = srv["site_current_service"]
        # R4 — a charger that only reports, driven through the one car
        if kind == "read_only_charger":
            if len(vehicles) == 1:
                car = vehicles[0]["roles"]
                offer["ev_current_control_entity"] = car["vehicle_charge_current"]
                if car.get("vehicle_charge_switch"):
                    offer["ev_start_stop_entity"] = car["vehicle_charge_switch"]
                row["paired_vehicle"] = vehicles[0]["device_id"]
                row["kind"] = "charger_via_vehicle"
            elif len(vehicles) > 1:
                row["choose_vehicle"] = sorted(str(v["device_id"]) for v in vehicles)
        missing = []
        if "ev_charging_power_sensor" not in offer:
            missing.append("power reading")
        if not any(k in offer for k in ("ev_current_control_entity",
                                         "ev_start_stop_entity",
                                         "ev_charge_mode_entity",
                                         "ev_start_service",
                                         "ev_charger_service")):
            missing.append("control")
        row["complete"] = not missing
        if missing:
            row["missing"] = missing
        offers.append(row)
    return offers
