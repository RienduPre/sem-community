/**
 * (#967) The EV card's 12-h plan strip, as a pure state walk.
 *
 * `sem-ev-status-card._renderPlanStrip` used to hold this inline, with one
 * rule that lied: `night_open → charging` unless some start row carried the
 * private-selector detail `plan_ev_charge_tariff`. The joint plan's starts
 * (`plan_ev_charge_joint`) never counted, and by day the composer's fallback
 * row sat AT the window open — so @alexmc1510's strip painted "charging" from
 * 20:36, inside the punta band it painted pink itself, for a charge that
 * either waits for 00:00 or is only an estimate.
 *
 * The rule now reads the ROWS, not a detail string:
 *   - at `night_open` the state is `charging` only when an `ev_charge_start`
 *     sits at the open itself (the in-night reactive row) — a start LATER in
 *     the night, whatever produced it, means the car WAITS until then;
 *   - a start whose detail is `plan_ev_charge_estimate` (the daytime preview
 *     before the plan has seen the night) is drawn as `estimate`, never as a
 *     booked charge;
 *   - `ev_min_reached` / `ev_deadline` → `done`.
 * The fleet attribute `ev_tariff_waiting` is primary-charger-scoped and is
 * trusted only while the fleet fallback plan is being drawn.
 *
 * @param {Array<{kind:string, when:string, detail?:string}>} evRows
 *        the plan rows of kinds now / night_open / ev_charge_start /
 *        ev_min_reached / ev_deadline (any order)
 * @param {{now:number, end:number, usingPerPlan:boolean, fleetTariffWait:boolean}} opts
 * @returns {Array<{s:number, e:number, state:string}>}
 */
export const EV_STRIP_KINDS = ['now', 'night_open', 'ev_charge_start',
    'ev_min_reached', 'ev_deadline'];

const AT_OPEN_MS = 60 * 1000;   // the composer strips seconds; one minute is "at"

// `usingPerPlan` / `fleetTariffWait` are still passed by the card but no
// longer decide anything: a start AT the open is the only thing that makes
// the open "charging", so the fleet-scoped flag cannot mislabel a charger.
export function evStripSegments(evRows, { now, end }) {
    const rows = (evRows || [])
        .filter(r => r && EV_STRIP_KINDS.includes(r.kind))
        .map(r => ({ ...r, t: new Date(r.when).getTime() }))
        .filter(r => Number.isFinite(r.t))
        .sort((a, b) => a.t - b.t);
    const starts = rows.filter(r => r.kind === 'ev_charge_start');
    const startState = (r) =>
        r.detail === 'plan_ev_charge_estimate' ? 'estimate' : 'charging';

    const segments = [];
    let cursor = now;
    let state = 'idle';
    for (const r of rows) {
        // Clamp each transition to the horizon so the fill always covers the
        // visible window (a first event beyond it must leave a full idle bar).
        const segEnd = Math.min(r.t, end);
        if (segEnd > cursor) segments.push({ s: cursor, e: segEnd, state });
        cursor = Math.max(cursor, segEnd);
        if (r.kind === 'night_open') {
            const atOpen = starts.find(s => Math.abs(s.t - r.t) <= AT_OPEN_MS);
            // No start AT the open means the car is not charging at the
            // open — whether a later start exists (joint / tariff / estimate)
            // or the plan has not spoken yet. Either way: WAIT, never a bar.
            if (atOpen) state = startState(atOpen);
            else state = 'wait';
        } else if (r.kind === 'ev_charge_start') {
            state = startState(r);
        } else if (r.kind === 'ev_min_reached' || r.kind === 'ev_deadline') {
            state = 'done';
        }
    }
    if (cursor < end) segments.push({ s: cursor, e: end, state });
    return segments;
}
