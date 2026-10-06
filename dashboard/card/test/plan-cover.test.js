import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { homeCover, GRID_EPS_W } from '../src/util/plan-cover.js';

// #1063 — a home with no battery saw its sunny hours in the battery colour,
// under a "battery covers home" legend. Slots below are the shapes the
// backend publishes (coordinator._slot_rows → sensor._energy_plan_attrs).

test('the meter carrying the house is grid', () => {
    assert.equal(homeCover({ home_grid_w: 450 }), 'grid');
    assert.equal(homeCover({ home_grid_w: 450, batt: true }), 'grid');
    assert.equal(homeCover({ home_grid_w: GRID_EPS_W + 0.1 }), 'grid');
});

test('the battery colour needs the plan to say it drew the battery', () => {
    assert.equal(homeCover({ home_grid_w: 0, batt: true }), 'batt');
    assert.equal(homeCover({ home_grid_w: 0.4, batt: true }), 'batt');
});

test('nothing on the meter and no battery drawn is the sun', () => {
    // A sun slot: the ledger sets the net home draw to 0.
    assert.equal(homeCover({ home_grid_w: 0 }), 'sun');
    assert.equal(homeCover({ home_grid_w: 0.9 }), 'sun');
    // The reporter's home: no battery, so no slot ever carries `batt`.
    for (const w of [0, 0.5, 1.0]) {
        assert.notEqual(homeCover({ home_grid_w: w }), 'batt');
    }
});

test('a missing draw is not a battery', () => {
    assert.equal(homeCover({}), 'sun');
    assert.equal(homeCover(null), 'sun');
    assert.equal(homeCover({ home_grid_w: null }), 'sun');
});

test('the plan card colours the Home row through homeCover', () => {
    const src = readFileSync(
        new URL('../src/cards/sem-energy-plan-card.js', import.meta.url), 'utf8');
    assert.match(src, /this\._runs\(slots, t0, span, homeCover\)/);
    // The old rule — "under a watt on the meter means battery" — is gone.
    assert.doesNotMatch(src, /<=\s*GRID_EPS_W/);
    assert.doesNotMatch(src, /r\.v \? 'batt' : 'grid'/);
    // No battery: no battery icon on the Home row, no hand-over time.
    assert.match(src, /hasBatt \? 'mdi:home-battery' : 'mdi:home'/);
    assert.match(src, /const takeoverCell = !hasBatt \? nothing/);
    // Legend keys only for colours that are drawn.
    assert.match(src, /drawn\.has\('batt'\)/);
    assert.match(src, /drawn\.has\('sun'\)/);
    // Tomorrow: no battery row, no "battery charging" key.
    assert.match(src, /curve\.length > 1 \? html`<span class="key"><i class="sw" style="background:#f06292">/);
});
