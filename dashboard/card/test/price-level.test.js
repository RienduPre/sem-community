import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    LEVEL_FLAT, LEVEL_NO_PRICES, isAbsence, priceLevelColor, priceLevelKey,
} from '../src/util/price-level.js';

// #994 — five cards render sensor.sem_tariff_price_level. Four passed the
// raw state to the translator (no key for a bare `flat`), and the fifth fell
// through its lookup to NORMAL's label and orange — a word SEM never chose.

test('a comparative level keeps its own key', () => {
    // `negative` is the exception and has its own test below: its
    // translation key is `price_negative`, not a bare `negative`.
    for (const l of ['cheap', 'very_cheap', 'normal', 'expensive',
                     'very_expensive']) {
        assert.equal(priceLevelKey(l), l);
    }
});

test('the two absences get their own keys', () => {
    assert.equal(priceLevelKey(LEVEL_FLAT), 'price_level_flat');
    assert.equal(priceLevelKey(LEVEL_NO_PRICES), 'price_level_no_prices');
});

test('an absence is never painted as normal', () => {
    const normal = priceLevelColor('normal');
    assert.notEqual(priceLevelColor(LEVEL_FLAT), normal);
    assert.notEqual(priceLevelColor(LEVEL_NO_PRICES), normal);
    assert.equal(priceLevelColor(LEVEL_FLAT), priceLevelColor(LEVEL_NO_PRICES));
});

test('an unrecognised state takes the caller fallback, not a level colour', () => {
    assert.equal(priceLevelColor('sideways', '#123456'), '#123456');
    assert.equal(priceLevelColor(undefined, '#123456'), '#123456');
});

test('isAbsence answers for exactly the two words', () => {
    assert.ok(isAbsence(LEVEL_FLAT));
    assert.ok(isAbsence(LEVEL_NO_PRICES));
    for (const l of ['cheap', 'normal', 'expensive', '', undefined]) {
        assert.ok(!isAbsence(l));
    }
});

test('case and whitespace do not change the key', () => {
    assert.equal(priceLevelKey('FLAT'), 'price_level_flat');
    assert.equal(priceLevelKey('No_Prices'), 'price_level_no_prices');
});

// Second review: `negative`'s key is `price_negative`, a fact only the price
// card knew. The other four cards passed the raw state to the translator,
// which has no bare `negative` key, so they printed the untranslated word.

test('negative keeps the key the translations actually carry', () => {
    assert.equal(priceLevelKey('negative'), 'price_negative');
});

test('every comparative level maps to a key the translations define', () => {
    // The set the sensor can publish, and the keys translations.json has.
    const defined = new Set([
        'price_negative', 'very_cheap', 'cheap', 'normal', 'expensive',
        'very_expensive', 'price_level_flat', 'price_level_no_prices',
    ]);
    for (const l of ['negative', 'very_cheap', 'cheap', 'normal', 'expensive',
                     'very_expensive', LEVEL_FLAT, LEVEL_NO_PRICES]) {
        assert.ok(defined.has(priceLevelKey(l)), `${l} -> ${priceLevelKey(l)}`);
    }
});

test('an absence never takes a comparative colour', () => {
    const comparative = ['negative', 'very_cheap', 'cheap', 'normal',
                         'expensive', 'very_expensive'].map(l => priceLevelColor(l));
    for (const a of [LEVEL_FLAT, LEVEL_NO_PRICES]) {
        assert.ok(!comparative.includes(priceLevelColor(a)), a);
    }
});

test('a state that is not a level at all takes the caller fallback', () => {
    // '' and 'unknown' are ordinary Home Assistant states for an entity
    // that has not loaded. They used to take NORMAL's orange on the grid card.
    for (const s of ['', 'unknown', 'unavailable', '—', undefined, null]) {
        assert.equal(priceLevelColor(s, '#888'), '#888');
    }
});
