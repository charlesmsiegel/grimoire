import { agingLabel } from "./aging";

/** The wording the ledger and the advance digest share (#103). Pinned here
 *  rather than in either view, because the point of the helper is that the two
 *  cannot phrase the same claim differently. */
const aging = (over: Partial<Parameters<typeof agingLabel>[0]> = {}) =>
  ({ state: "ok", days_since: null, days_over: null, due_in: null, ...over } as never);

test("a record inside the campaign's patience carries no badge", () => {
  expect(agingLabel(aging())).toBe("");
});

test("an unaged record carries no badge either", () => {
  // No clock, no dated scene, a calendar that will not load — "cannot tell"
  // renders as nothing at all, which is what the ledger showed before #103.
  expect(agingLabel(undefined)).toBe("");
});

test("overdue leads with how far past the deadline it is", () => {
  expect(agingLabel(aging({ state: "overdue", days_over: 12, days_since: 40 })))
    .toBe("OVERDUE BY 12 DAYS");
});

test("a day is singular", () => {
  expect(agingLabel(aging({ state: "overdue", days_over: 1 }))).toBe("OVERDUE BY 1 DAY");
  expect(agingLabel(aging({ state: "stale", days_since: 1 }))).toBe("STALE · 1 DAY UNTOUCHED");
});

test("overdue with no number still says overdue", () => {
  // Reachable only from a hand-edited response, but the badge must not read
  // "OVERDUE BY null DAYS".
  expect(agingLabel(aging({ state: "overdue" }))).toBe("OVERDUE");
});

test("stale says how long it has been", () => {
  expect(agingLabel(aging({ state: "stale", days_since: 45 })))
    .toBe("STALE · 45 DAYS UNTOUCHED");
  expect(agingLabel(aging({ state: "stale" }))).toBe("STALE");
});
