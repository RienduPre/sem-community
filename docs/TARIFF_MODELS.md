# Tariff models — what SEM claims, per model

SEM's price **level** (`sensor.sem_tariff_price_level`) answers one question:
*is this hour better or worse than the others?* It is a **comparison**, and a
comparison needs two prices that differ.

The vocabulary is Tibber's. Tibber classifies each hour against a **3-day
moving average** — ≤ 60 % `very_cheap`, 60–90 % `cheap`, 90–115 % `normal`,
115–140 % `expensive`, ≥ 140 % `very_expensive` — and its own enum carries a
**"missing data"** state beside the five words. SEM had kept the words and
dropped both the reference and the absence, which is how a flat tariff came
to be published as `cheap` and a battery came to be held all night for an
expensive hour that could not arrive (#994).

So: **when nothing was compared, SEM says `unknown`.** Everything that would
have waited for a better hour acts now instead — the same rule the export
side has followed since #921: *an unknown price is OPEN, never CLOSED*.

## What each model gets

| model | what SEM compares | the level you get |
|---|---|---|
| **Flat / single rate** | nothing — there is one price | **`unknown`**. Nothing waits, nothing holds, no cheap blocks are drawn. |
| **HT/NT (two rates, a clock)** | the two configured rates, *on a day that contains both* | `cheap` in NT, `normal` in HT — **only when the rates differ**. A weekend, which is NT all day, has one price and therefore no level. |
| **Multi-tier ToU** (Spanish 2.0TD, US "Nighttime Savers") | the distinct tier prices (#728) | the tier's own level; tier detection is unchanged. |
| **Dynamic / spot** (Nord Pool, Tibber, EPEX, aWATTar…) | today's own curve, by percentile | the five words; `unknown` when the curve is missing, has fewer than four points, or is flat. |
| **Variable peak (VPP)** | today's curve — it *is* a curve | as dynamic. |
| **Critical peak (CPP)** | **not modelled** | an announced critical event is not a percentile of today. See *Known limitations*. |
| **Block / tiered by consumption** (e.g. 31 ct → 42 ct past a monthly baseline) | **not modelled** | the price depends on the month's cumulative kWh, not on the hour. A level cannot express it. |
| **Demand charge** (highest 15-min average) | not a price level at all | untouched — the peak guard (#864) owns that axis, on its own units. |

## Why "flat" is measured relatively

A flat test written as "the two rates differ by less than 0.001/kWh" is the
#359 defect in miniature: an absolute cutoff calibrated for one currency. It
was re-fixed twice on the configuration surface alone — for a Slovak tariff
at 1.69/kWh (#417) and a Sri Lankan one three orders of magnitude away
(#549). SEM therefore asks whether the spread is at least **0.5 % of the
day's own average**, which keeps a 1 ct HT/NT split on a 30 ct tariff (3.3 %)
and rejects float noise, in any currency.

## Where the level comes from, and how to check

`sensor.sem_tariff_price_level` carries `classifier_path` — *how* the level
was reached (`percentile_active`, `tou_tiers(...)`, `static_ht_nt`,
`calendar_schedule`, `negative_price_shortcircuit`, and the fallbacks). Since
#994 it also reports when there was nothing to classify:
`static_no_comparison`, `calendar_no_comparison`,
`percentile_fallback_flat_day`, `percentile_fallback_cache_empty`,
`percentile_fallback_too_few_prices`.

If the sensor reads **`unknown`**, that is SEM saying your tariff gives it no
hour to prefer. On a flat contract that is the correct and final answer.

## Negative prices are not a comparison

A negative price is an **absolute** fact and is handled as one: the export
guard closes the meter on the *export* rate's own sign (#921/#955), and the
battery's negative-price force charge reads the raw import price. Neither
goes through the comparative path, so a flat tariff changes neither.
