import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    LEVEL_FLAT, LEVEL_NO_PRICES, isAbsence, priceLevelColor, priceLevelKey,
} from '../src/util/price-level.js';

// #994 — five cards render sensor.sem_tariff_price_level. Four passed the
// raw state to the translator (no key for a bare `flat`), and the fifth fell
// through its lookup to NORMAL's label and orange — a word SEM never chose.

test('a comparative level keeps its own key', () => {
    for (const l of ['cheap', 'very_cheap', 'normal', 'expensive',
                     'very_expensive', 'negative']) {
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
