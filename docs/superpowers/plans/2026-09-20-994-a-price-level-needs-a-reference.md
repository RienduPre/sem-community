# #994 — a price level needs a reference, and may say it has none

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** every price level SEM publishes or acts on names the comparison behind it — and where no comparison exists, SEM says `unknown` instead of `cheap`, and every consumer that would have waited acts now.

**Architecture:** restore the two things SEM's borrowed vocabulary lost (an explicit reference, and an absence state); make the flat test relative so it holds in any currency and any tariff model; put the six hand-typed level tuples behind one module; give each comparative consumer the third branch.

**Tech stack:** Python (`tariff/`, `coordinator/`), Lit cards, 16-language translations.

---

## What the research established

**The vocabulary is Tibber's, and SEM kept the words but dropped the definition.** Tibber classifies each hour against a **3-day moving average** — ≤60 % `VERY_CHEAP`, 60–90 % `CHEAP`, 90–115 % `NORMAL`, 115–140 % `EXPENSIVE`, ≥140 % `VERY_EXPENSIVE` — and its enum carries **`None` ("missing data")** as a first-class variant. Two consequences: by the source definition **a flat tariff is NORMAL, never CHEAP** (every hour is 100 % of the mean); and the absence state SEM needs is not an invention, it is the part that was left behind.

**The defect** (#994): `StaticTariffProvider` and `CalendarTariffProvider` answer from a **clock**, never comparing the rates they hold; `get_price_level()` can never say `None` while `get_price_level_at()` can; and 18 comparative sites across 6 chains read the result as an instruction. Live cost: a flat 0.36/0.36 tariff read as `cheap` → house sink → **0 W discharge limit** → 3.66 kWh imported overnight from a 92–100 % battery.

**The history** says this is the sixth recurrence: #359 (six waves in four days), #728 (two), #524, #953, #879 — and every durable fix either stopped a silent fallback into another branch's semantics or published *which* branch produced the number. Neither addressed the vocabulary. This plan does.

## The model contract (the artifact this plan is really about)

`docs/TARIFF_MODELS.md`, new — what SEM claims per model, so the next consumer reads it before inventing a seventh opinion:

| model | reference for a level | SEM after this |
|---|---|---|
| **flat / single rate** | none exists | **no level** (`unknown`); nothing waits |
| **ToU, fixed blocks** (HT/NT, 2.0TD) | the configured rate table, *restricted to the hours that occur today* | clock mapping stays — **only when the rates differ** |
| **RTP / dynamic spot** | today's own curve (percentile) | unchanged; the three fallbacks now answer `unknown`, not `NORMAL` |
| **VPP** (peak price set daily) | today's curve | works — it is a curve |
| **CPP** (announced critical events) | not modelled | named in KNOWN_LIMITATIONS |
| **block / tiered by cumulative kWh** | not a time question at all | named in KNOWN_LIMITATIONS |
| **demand charge** (15-min peak) | not a price level | untouched — `#864` peak guard owns it |

## Files

- **Create** `coordinator/price_signal.py`, `docs/TARIFF_MODELS.md`, `tests/test_994_a_level_needs_a_reference.py`.
- Modify `tariff/tariff_provider.py`, `tariff/calendar_provider.py`, `coordinator/coordinator.py` (the `schedule = {}` wiring, the fleet-state level).
- Consumers: `sink_verdicts.py`, `day_ledger.py`, `today_plan.py`, `ev_tariff_planner.py`, `decide.py`, `surplus_controller.py` (×4), `analytics/energy_assistant.py`.
- Surface: `sensor.py`, `dashboard/translations.json` (×16), `sem-ev-status-card.js`, `sem-energy-plan-card.js`, `docs/TROUBLESHOOTING.md`, `docs/SETUP_GUIDE.md`, `docs/KNOWN_LIMITATIONS.md`.
- Rewrite `tests/test_tariff_provider.py:70-117` — it asserts the defect as spec.

---

### Task 1 — the vocabulary, with its reference back

- [ ] **Write the failing tests** — `TestTheVocabulary`: `spread()` reads the provider's own min/max; `variation_known()` is **relative** (a fraction of the day's mean) so it answers the same in CHF, in cents and in LKR at 50–70/kWh (#549); `comparative_level()` is `None` on a flat curve; `is_cheap()`/`is_expensive()` are **False** on `None`; `level_name()` publishes `unknown`.
- [ ] **Run them** — red.
- [ ] **Write `coordinator/price_signal.py`** — `FLAT_SPREAD_FRACTION = 0.005` (0.5 % of the mean: a 1 ct split on a 30 ct tariff is 3.3 % and survives; float noise does not), `CHEAP_LEVELS`/`EXPENSIVE_LEVELS` as the only copies, and a docstring carrying the Tibber definition and the incident.
- [ ] **Green. Commit.**

### Task 2 — the providers answer from prices, not from a clock

- [ ] **Failing tests, one per model**: Static with equal rates → `None` from **both** accessors; Static with different rates → unchanged HT/NT answer; **Static on a Saturday** → no variation (NT all day: the rate table says two rates, the *day* has one); Calendar with empty rules or the "Flat Rate" preset → `None`; Dynamic's `cache_empty` / `too_few_prices` / `flat_day` → `None` from both accessors.
- [ ] **Run them** — red.
- [ ] **Implement.** `today_min_price`/`today_max_price` must describe **the day that occurs**, not the configured table. Fix `coordinator.py:625`'s hardcoded `schedule = {}` so a Calendar install gets its own rules.
- [ ] **Rewrite `test_tariff_provider.py:70-117`**, which asserts CHEAP at night while asserting `today_min == today_max` in the same test.
- [ ] **Green. Commit.**

### Task 3 — one vocabulary, not six

- [ ] Replace `sink_verdicts._KEEP_LEVELS`, `today_plan.cheap_levels/expensive_levels`, `surplus_controller.price_is_cheap` (3 sites) **and the `== "expensive"` drift at `:507`**, `ev_tariff_planner._is_expensive/_LEVEL_RANK`, `decide._NOT_CHEAP_LEVELS/EXPENSIVE_LEVELS`, `day_ledger.tariff_cheap_at`, `energy_assistant._analyze_price` with `price_signal`.
- [ ] **AST pin**: no level-string tuple may exist outside `price_signal.py`.
- [ ] Pin the drift: `very_expensive` now damps the pool. **Green. Commit.**

### Task 4 — every comparative chain gains the third branch

One pin per chain, each naming the harm it prevents:

- [ ] **house sink** → flat ⇒ OPEN, **no 0 W clamp** (the incident, end to end through `decide_battery`).
- [ ] **cloud bridge** (`_idle_bridgeable`) → flat ⇒ a daytime dip is bridgeable, not structural. *Today an HT hour reads NORMAL and hard-stops the charger on every passing cloud.*
- [ ] **day grid top-up** (`SolarPlusCheapMode`) → flat weekend ⇒ no grid-funded top-up.
- [ ] **cheap-hours loads** (`day_ledger` → `energy_planner` → `ev_overlay`) ⇒ nothing defers for an hour that cannot come.
- [ ] **EV night plan** (`affordable_start`) ⇒ never books an hour it has no price for (`_rank`'s `.get(..., 3)` scores silence as NORMAL today).
- [ ] **surplus "Finish overnight from: Grid"** — both the desired-state clause and the imperative pass that actually runs on PROD.
- [ ] **plan strip** ⇒ no fake cheap/expensive rows every off-peak night.
- [ ] **energy assistant** ⇒ no "cheap electricity now" on identical rates.
- [ ] **Green. Commit.**

### Task 5 — say it on the surface, and say what SEM does not model

- [ ] `sensor.sem_tariff_price_level` publishes `unknown`; `classifier_path` keeps saying *how*, this says *whether*.
- [ ] `unknown` + `flat` in `dashboard/translations.json` ×16; the two cards render the absence rather than a cheap block.
- [ ] **`docs/TARIFF_MODELS.md`** — the table above, with the Tibber definition quoted and SEM's own reference per provider.
- [ ] `KNOWN_LIMITATIONS`: **CPP** and **block/tiered-by-consumption** are not modelled — a level cannot express a price that depends on the month's cumulative kWh.
- [ ] `TROUBLESHOOTING`: *"my battery never covers the house"* → a flat tariff, and what `unknown` means. `SETUP_GUIDE`: the equal-rates note beside the price-classification section.
- [ ] **Green. Commit.**

### Task 6 — close it

- [ ] CHANGELOG bug entry (the incident, the CHF cost, the models).
- [ ] `docs/BUG_CLASSES.md`: new class — *a word borrowed without its reference*. Sweep question: **what two numbers were compared to produce this word, and what would it say if nobody compared any?**
- [ ] Full suite, ruff, `npm test` + card build, CI both rungs.
- [ ] Challenge record: a reviewer must attack **"no consumer can still act on a level nobody computed, in any tariff model"**.

## Verification (once, at the end — build once, test many)

1. Every task's pins red before, green after; full suite and CI green.
2. **Incident replay**: flat rates + house sink on ⇒ the inverter's discharge limit is never written to 0.
3. **Model matrix**, as unit tests: flat / HT-NT weekday / HT-NT weekend / 3-tier ToU / dynamic curve / dynamic flat day / no curve — each asserting the level and that nothing waits when there is nothing to wait for.
4. On `.175`: set both rates equal ⇒ `tariff_price_level` reads `unknown`, no hold, no fake plan rows. Then set them apart ⇒ HT/NT behaviour returns unchanged.

## Out of scope

Renaming the enum or the sensor (compatibility); the percentile algorithm; #728's tier detection; export-side classification (already raw-rate and correct); implementing CPP or block-rate models — naming them as unmodelled is the deliverable here.
