# Next hardware wave — brands with the most installs SEM cannot drive yet

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SEM drives five more charger and inverter setups that people
already run in Home Assistant, starting with the ones that have the most
installs and an open report. Each is built from the integration's own
source, ships inert, and becomes `tested-live` only when an owner proves it.

**Architecture:** No new mechanism. A brand is a data row (`_BRAND_HINTS`,
`_EV_CHARGER_PLATFORMS` in `hardware_detection.py`, a row in
`consts/hardware_matrix.py`) plus, where the brand controls in an unusual
way, one wiring helper next to `_wire_wattpilot` that `wire_current_entity`
calls. Control reuses what exists: a current `number`, a service
(`start_service` / `stop_service`), a charge-mode `select`
(`SESSION_START_CHARGE_MODE`, already used by go-e and openWB), and the
phase-switch hook (`ev_phase_switch_entity`, `validate_phase_switch_entity`).
No new user option: every choice is detected or fixed.

**Rule (Guido, 01.10.2026): "we just go to the integration".** SEM drives
each brand only through the entities and services its Home Assistant
integration exposes. No extra APIs, no cloud calls of our own, no
workarounds outside the integration.

**Tech stack:** Python 3.14 / HA 2026.8 (CI floor 3.13 / HA 2026.2),
`~/bin/semtest` with its own `SEM_ROOT`, worktree `/home/sem/sem-hw`,
branch `feature/hardware-wave` off develop.

**Ship path (Guido's rules):** build every task, suite green, ruflo told to
refute, one deploy to .175 with the compressed sun sim (observer ON), then
a soak on Guido's PROD. Merge to develop only on his word after the soak.

---

## Why these brands (Home Assistant analytics, 01.10.2026)

| brand | HA installs | SEM today | open report |
|---|---|---|---|
| Tesla Wall Connector + a Tesla car integration | 6 730 (core) + Fleet 5 076 / Tessie 1 855 / Teslemetry 1 112 | not detected | none — needs an owner |
| myenergi Zappi | 3 810 (`myenergi`) | not detected | none — needs an owner |
| Easee | 3 656 (`easee`) | `tested-live`, but no phase switching | #1015 |
| Zaptec | 2 112 (`zaptec`) | `implemented`; one install reports "no role matched" | #1032 (OddmarDam) |
| EG4 / Luxpower | 257 (`eg4_web_monitor`) + 370 (`lxp_modbus`) | `requested` | #810 (reporter has the box) |

Checked and left out:

- **#812 Messkonzept 8** is a second tariff for the heat pump, not
  hardware. It stays in the sensor-input batch.
- **#849 analytics** needs no SEM code. The census drops domains missing
  from the frozen brands list; the fix is upstream (analytics#1128).
- **#917 NRGkick, #808 ABL** are built and wait for their reporters.
- **#886 Juicebox** is fixed and confirmed (closed).
- **Pod Point** (334 installs) is next after this wave.

---

## Task 0: Baseline

**Files:** none.

- [ ] **Step 1: Suite green before anything**

```bash
SEM_SRC=/home/sem/sem-hw SEM_ROOT=/tmp/ha-hwfull ~/bin/semtest tests -rf > ~/claude-jobs/suite-hw-baseline.txt 2>&1; tail -2 ~/claude-jobs/suite-hw-baseline.txt
```
Expected: `0 failed`. Record the count.

- [ ] **Step 2: Lint**

```bash
cd /home/sem/sem-hw && ~/.venvs/sem-314/bin/python -m ruff check . -q
```
Expected: no output.

---

## Task 1: Zaptec — the installation device and a control that exists (#1032)

**What the user sees today.** OddmarDam's Zaptec install files "no role
matched" for a device named `abbastova` with only installation entities:
`installation`, `authorization_required`, `3_to_1_phase_switch_current`,
`max_current`, `authentication_type`, `installation_type`, `network_type`.

**What upstream does** (`custom-components/zaptec`, master):

- Two devices per site: an **installation** and a **charger**
  ([manager.py](https://github.com/custom-components/zaptec/blob/master/custom_components/zaptec/manager.py)).
- The current limit `available_current` sits on the installation
  ([number.py](https://github.com/custom-components/zaptec/blob/master/custom_components/zaptec/number.py),
  `INSTALLATION_ENTITIES`). `create_entities_from_descriptions` **skips an
  entity whose data key the API does not return** — so on an account that
  cannot set the limit, `available_current` is never built. That is this
  device: the limit is missing, not misnamed.
- The charger device has `charger_min_current` / `charger_max_current`
  (charger **settings**, written with `set_settings`), buttons
  `resume_charging` / `stop_charging_final`, and a charging switch
  ([button.py](https://github.com/custom-components/zaptec/blob/master/custom_components/zaptec/button.py),
  [switch.py](https://github.com/custom-components/zaptec/blob/master/custom_components/zaptec/switch.py)).
- Services: `limit_current`, `stop_charging`, `resume_charging`, …
  ([services.yaml](https://github.com/custom-components/zaptec/blob/master/custom_components/zaptec/services.yaml)).
- `has_entity_name = True`, unique id `<zaptec object id>_<key>`. Entity
  ids follow the device name (`abbastova_…`), **not** `zaptec_…` — the old
  `zaptec_*` patterns in SEM's Zaptec profile (`hardware_detection.py:180`)
  never match a current install. Detection must key on platform + unique-id
  suffix, as the roster path already does.

**Control SEM will use**, in order:

1. `number.<installation>_available_current` when it exists (today's path).
2. Otherwise start/stop only, through the integration's
   `resume_charging` / `stop_charging_final` buttons, and a Repair in plain
   words: "Your Zaptec account cannot set the current. SEM can only start
   and stop." SEM writes nothing else on a Zaptec.

**Also:** an installation device whose sibling charger device matched is
a known companion, not news — no "please report" for it. (Strictly that
false report is a small bug; it is fixed here because the same rows decide
it. Tell the autopilot via a `.claimed` marker on #1032 while this runs.)

**Files:**
- Modify: `hardware_detection.py` (Zaptec profile ~180, near-miss report ~2489, `_BRAND_HINTS`)
- Modify: `consts/hardware_matrix.py` (Zaptec row ~259: evidence #1032)
- Create: `tests/test_1032_zaptec_installation_device.py`
- Modify: `tests/test_split_grid_integration.py` only if the Zaptec pipeline test needs the new ids

- [ ] **Step 1: Failing tests from upstream's own shape** — two devices
  built like upstream (`has_entity_name`, ids from the device name):
  (a) with `available_current` on the installation → it is the current
  control; (b) without it → start/stop via the charger buttons, a Repair is
  raised, no near-miss report for the installation; (c) entity ids without
  any `zaptec_` prefix are still found.
- [ ] **Step 2:** run, see them fail.
- [ ] **Step 3:** implement in the rows above; no new module.
- [ ] **Step 4:** run, see them pass; run the Zaptec pipeline test.
- [ ] **Step 5:** commit `feat(#1032): Zaptec installation and charger devices, with or without a current limit`.

**Risk:** a Zaptec owner who can set the limit loses nothing (path 1 is
unchanged). **Who proves it:** OddmarDam — ask for the charger device's
entity list and whether the account owns the installation.

---

## Task 2: Tesla Wall Connector, controlled through the car

**What the user sees today.** Nothing: `tesla_wall_connector` is not a
charger to SEM. 6 730 installs.

**What upstream exposes** (`home-assistant/core`, dev):

- Wall Connector is **read-only**: `vehicle_connected`, `contactor_closed`
  ([binary_sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/tesla_wall_connector/binary_sensor.py))
  and power/energy sensors. It has no control.
- The car integrations control charging:
  `number.*_charge_state_charge_current_request` (0–32 A, max from
  `charge_state_charge_current_request_max`) and the charge switch with
  unique id `charge_state_user_charge_enable_request`
  ([tesla_fleet/number.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/tesla_fleet/number.py),
  [switch.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/tesla_fleet/switch.py)).
  Teslemetry and Tessie publish the same keys.

**Control SEM will use:** sense with the Wall Connector (plug = vehicle
connected, charging = contactor closed, power), steer with the car's amp
number and charge switch. SEM already steers a car's own amp number behind
an Easee (#752, `tesla_ble`), so this is a pairing, not a new kind.

Every car integration gets the same treatment — Tesla Fleet, Teslemetry,
Tessie, BLE — through its own amps number and charge switch, at SEM's
normal write pacing. No Fleet-specific rule.

**Risk:** a car asleep ignores commands — read back the amp number and
report a refused write the #820 way.

**Files:**
- Modify: `hardware_detection.py` (pairing: a Wall Connector device + one Tesla car device on the same install)
- Modify: `consts/hardware_matrix.py` (new charger row, `implemented`)
- Create: `tests/test_tesla_wall_connector_pairing.py`
- Modify: `tests/test_split_grid_integration.py` (a pipeline test, current via the car)

- [ ] **Step 1:** failing tests — pairing found from upstream-shaped entities
  for each of Fleet / Teslemetry / Tessie / BLE; no pairing with two cars
  (ask, never guess); a refused write is reported.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** commit `feat: Tesla Wall Connector charges through the car's own controls`.

**Who proves it:** a Tesla owner — ask in Discussions; praun drives a Tesla
(BLE today).

---

## Task 3: myenergi Zappi — a charger SEM steers by mode

**What the user sees today.** Nothing; `myenergi` (3 810 installs) is not
detected.

**What upstream exposes** (`CJNE/ha-myenergi`, main):

- Zappi charge mode `select` with pymyenergi's `CHARGE_MODES`
  (Fast, Eco, Eco+, Stopped) and a phase `select` with options `1`, `3`,
  `auto` ([select.py](https://github.com/CJNE/ha-myenergi/blob/main/custom_components/myenergi/select.py)).
- Boost services (`myenergi_boost`, `myenergi_smart_boost`,
  `myenergi_stop_boost`) ([services.yaml](https://github.com/CJNE/ha-myenergi/blob/main/custom_components/myenergi/services.yaml)).
- No amp number: a Zappi sets its own current in Eco/Eco+.

**Control SEM will use:** the integration's charge-mode select, through
the existing charge-mode path
(`SESSION_START_CHARGE_MODE`): start = `Fast` (grid allowed) or `Eco+`
(solar only), stop = `Stopped`; the phase select feeds
`ev_phase_switch_entity`. SEM decides *whether* and *how*; the Zappi
follows the sun itself. Eddi (the water diverter) waits for #880; libbi
(battery) is a later wave.

**Risk:** myenergi is a cloud API with slow updates — write a mode only on
change (idempotent, #538 rule), never per cycle.

**Files:**
- Modify: `hardware_detection.py`, `consts/hardware_matrix.py`
- Create: `tests/test_myenergi_zappi.py`
- Modify: `tests/test_split_grid_integration.py` (pipeline test, mode-controlled)

- [ ] **Step 1:** failing tests — Zappi found from upstream-shaped entities;
  start/stop write the right option once; phase select wired; no amp writes.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** commit `feat: myenergi Zappi is steered by its charge mode`.

**Who proves it:** a Zappi owner — ask in Discussions.

---

## Task 4: Easee phase switching (#1015)

**What the user sees today.** SEM drives Easee current but cannot switch
between one and three phases.

**What upstream exposes** (`nordicopen/easee_hass`, master,
[services.yaml](https://github.com/nordicopen/easee_hass/blob/master/custom_components/easee/services.yaml)):

- `set_circuit_dynamic_limit` with `current_p1`, `current_p2`,
  `current_p3` and `time_to_live` — kodesmoelfen's path in #1015.
- `set_charger_phase_mode` with `1_phase` / `auto_phase` / `3_phase` — a
  charger **setting**.

**Control SEM will use:** the Easee integration's own
`set_circuit_dynamic_limit` service (3-phase = all three at the amps;
1-phase = P1 at the amps, P2/P3 at 0), through a brand wiring helper beside
`_wire_wattpilot`, so the existing phase-switch logic drives it. Nothing
outside the integration.

**Risk:** a circuit with two chargers shares the limit — refuse to phase-
switch when the circuit has more than one charger (detected, Repair).

**Files:**
- Modify: `hardware_detection.py` (`_wire_easee_phases`), `coordinator/phase_switch*.py` only through its existing hook
- Create: `tests/test_1015_easee_phases.py`

- [ ] **Step 1:** failing tests — 3→1 and 1→3 send the right three
  currents once; `time_to_live` set; two chargers on one circuit → no
  phase switching, Repair raised.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** commit `feat(#1015): Easee switches phases through the circuit limit`.

**Who proves it:** kodesmoelfen (#1015).

---

## Task 5: EG4 / Luxpower battery control (#810)

**What the user sees today.** `requested`. The matching rules are already
settled in #810 with the reporter: `ac_charge_soc_limit` is the charge
target; `system_charge_soc_limit`, `soc_cutoff`, `ac_couple_*_soc` and
`quick_charge` are excluded on his word.

**What upstream exposes:** `joyfulhouse/eg4_web_monitor` (257 installs)
and `ant0nkr/luxpower-ha-integration` (`lxp_modbus`, 370) — read both
sources at build time for the AC-charge enable switch, the charge-rate
number and the SOC limit; the roster (`consts/integration_roster.py`)
already lists both.

**Control SEM will use:** force-charge = AC charge on + `ac_charge_soc_limit`
+ charge rate; release = AC charge off. Same adapter shape as the other
"charge to a target" brands in `coordinator/battery_adapters/`.

**Risk:** a FlexBoss is grid-forming; a wrong write can drain or overcharge.
Ship with control **off** until the reporter turns it on.

**Files:**
- Create: `coordinator/battery_adapters/eg4.py`
- Modify: `hardware_detection.py`, `consts/hardware_matrix.py` (EG4 row → `implemented`)
- Create: `tests/test_810_eg4_adapter.py`; pipeline test in `tests/test_split_grid_integration.py`

- [ ] **Step 1:** confirm on #810 that the reporter's box is commissioned
  (the 2.2 plan held this task for that). Stop here if not.
- [ ] **Step 2–5:** failing tests (force-charge/release writes, excluded
  keys never written, sign pattern), implement, pass, commit
  `feat(#810): EG4 and Luxpower batteries`.

**Who proves it:** the #810 reporter (FlexBoss 21 + two EG4 wall-mount packs).

---

## Task 6: Prove it is inert, then soak

- [ ] Full suite on `/tmp/ha-hwfull`; ruff; CI on a draft PR.
- [ ] ruflo reviewer, told to refute: "on an install without these brands
  nothing changes; on one with them SEM writes only the controls named in
  this plan."
- [ ] `SRC=/home/sem/sem-hw ~/bin/sem-deploy-175.sh`, then
  `~/bin/sem-sim-compress.sh 10.10.20.175 18.0 30` (observer ON, stop it
  with `timeout`). Expect no SEM errors and no new entity on .175.
- [ ] PROD soak on Guido's word (soak marker `BRANCH:` + `LADDER: hands-off`).
- [ ] CHANGELOG `[Unreleased]`, one line per brand (≤ 25 words); README
  brand list; matrix rows stay `implemented` until an owner confirms.
- [ ] Short posts: #1032, #1015, #810; one Discussions call for a Tesla
  Wall Connector owner and a Zappi owner.

---

## Decision for Guido

**Order:** Tesla and Zappi have the most installs but no owner to prove
them yet; Zaptec, Easee and EG4 have reporters waiting. Keep this order
(installs first) or build the three with reporters first?
