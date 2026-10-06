/**
 * #1063 — who covers the house in one plan slot (the Home row's colour).
 *
 * The card used to read "no home draw on the meter" as "the battery covers
 * the house". But a sun slot has no net draw either, and a home without a
 * battery has only the sun and the grid. So a battery-less home saw its
 * sunny hours in the battery colour, under a "battery covers home" legend.
 *
 *   grid — the meter carries the house (more than GRID_EPS_W);
 *   batt — the plan drew the battery for the house (the slot's `batt`);
 *   sun  — nothing on the meter and no battery drawn.
 *
 * The battery colour needs the plan's own word for it, never a guess from
 * the other two: a battery the plan did not use is not drawn.
 */

// Below this the slot's home draw is not on the meter. The backend rounds
// home_grid_w to 1 decimal, so anything under a watt is noise.
export const GRID_EPS_W = 1.0;

export function homeCover(slot) {
    if (((slot && slot.home_grid_w) || 0) > GRID_EPS_W) return 'grid';
    return slot && slot.batt ? 'batt' : 'sun';
}
