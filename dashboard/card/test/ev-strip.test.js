/**
 * (#967) The EV strip paints the window open as "charging" only when a
 * start row sits AT the open. @alexmc1510's card painted 20:36–21:41 as a
 * booked charge inside his punta band: the composer's daytime fallback put a
 * start row at the open, and the old inline rule turned `night_open` into
 * `charging` unless a start carried the private-selector detail — a joint
 * or held-back start later in the night never counted.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { evStripSegments } from '../src/util/ev-strip.js';

const T = (h, m = 0, dayOffset = 0) =>
    new Date(Date.UTC(2026, 8, 16 + dayOffset, h, m)).toISOString();
const ms = (iso) => new Date(iso).getTime();
const NOW = ms(T(14, 6));
const END = NOW + 12 * 3600 * 1000;           // 02:06 next day
const OPEN = T(20, 36);

const states = (segs) => segs.map(s => s.state);
const at = (segs, iso) => segs.find(s => s.s <= ms(iso) && ms(iso) < s.e)?.state;

test('a start AT the open is charging from the open (the in-night reactive row)', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_charge_start', when: OPEN, detail: 'plan_ev_charge_night' },
        { kind: 'ev_min_reached', when: T(1, 23, 1) },
    ], { now: NOW, end: END });
    assert.deepEqual(states(segs), ['idle', 'charging', 'done']);
    assert.equal(at(segs, OPEN), 'charging');
});

test('the daytime preview at the open is an ESTIMATE, never a booked charge', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_charge_start', when: OPEN, detail: 'plan_ev_charge_estimate' },
        { kind: 'ev_min_reached', when: T(21, 41) },
    ], { now: NOW, end: END });
    assert.equal(at(segs, OPEN), 'estimate');
    assert.ok(!states(segs).includes('charging'));
});

test('a joint-plan start at 00:00 means WAIT from the open — not charging', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_charge_start', when: T(0, 0, 1), detail: 'plan_ev_charge_joint' },
        { kind: 'ev_min_reached', when: T(5, 0, 1) },
    ], { now: NOW, end: END });
    assert.equal(at(segs, OPEN), 'wait', 'the old rule painted charging here');
    assert.equal(at(segs, T(21, 0)), 'wait');
    assert.equal(at(segs, T(0, 30, 1)), 'charging');
});

test('a tariff-held start behaves the same, whatever produced it', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_charge_start', when: T(1, 12, 1), detail: 'plan_ev_charge_tariff' },
    ], { now: NOW, end: END });
    assert.equal(at(segs, OPEN), 'wait');
    assert.equal(at(segs, T(1, 30, 1)), 'charging');
});

test('no start at all after the open is WAIT — the plan has not spoken', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_deadline', when: T(6, 0, 1) },
    ], { now: NOW, end: END });
    assert.equal(at(segs, OPEN), 'wait');
});

test('the fleet tariff flag can no longer turn another charger into a bar', () => {
    const rows = [
        { kind: 'night_open', when: OPEN },
        { kind: 'ev_charge_start', when: T(0, 0, 1), detail: 'plan_ev_charge_joint' },
    ];
    const a = evStripSegments(rows, { now: NOW, end: END, usingPerPlan: false, fleetTariffWait: false });
    const b = evStripSegments(rows, { now: NOW, end: END, usingPerPlan: true, fleetTariffWait: true });
    assert.deepEqual(states(a), states(b));
    assert.equal(at(a, OPEN), 'wait');
});

test('a first event beyond the horizon leaves one full idle bar, never an empty strip', () => {
    const segs = evStripSegments([
        { kind: 'night_open', when: T(21, 35, 1) },
        { kind: 'ev_charge_start', when: T(21, 35, 1), detail: 'plan_ev_charge_night' },
    ], { now: NOW, end: END });
    assert.deepEqual(segs, [{ s: NOW, e: END, state: 'idle' }]);
});

test('deadline and min-reached both end the charge as done', () => {
    for (const kind of ['ev_min_reached', 'ev_deadline']) {
        const segs = evStripSegments([
            { kind: 'ev_charge_start', when: T(22, 0), detail: 'plan_ev_charge_night' },
            { kind, when: T(1, 0, 1) },
        ], { now: NOW, end: END });
        assert.equal(at(segs, T(1, 30, 1)), 'done', kind);
    }
});

test('rows of other kinds and unparsable times are ignored, order does not matter', () => {
    const segs = evStripSegments([
        { kind: 'ev_min_reached', when: T(1, 0, 1) },
        { kind: 'cheap_start', when: T(0, 0, 1) },
        { kind: 'ev_charge_start', when: 'not a date' },
        { kind: 'ev_charge_start', when: T(22, 0), detail: 'plan_ev_charge_night' },
    ], { now: NOW, end: END });
    assert.deepEqual(states(segs), ['idle', 'charging', 'done']);
});
