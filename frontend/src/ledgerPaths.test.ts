import {
  CONTINUITY_GROUPS, GROUP_OF, LEDGER_SECTIONS, ledgerHref, parseLedgerTail,
  type LedgerTarget,
} from "./ledgerPaths";

/** The tail `LedgerView` hands the parser: everything after
 *  `/campaigns/<cid>/ledger`, read off the raw pathname. */
const tailOf = (href: string) =>
  href.split("/").filter(Boolean).slice(3).join("/");

const roundTrip = (target: LedgerTarget) =>
  parseLedgerTail(tailOf(ledgerHref("run", target)));

test("every section round-trips", () => {
  for (const section of LEDGER_SECTIONS) {
    const target: LedgerTarget = section === "continuity"
      ? { section, group: "overlaps" } : { section };
    expect(roundTrip(target)).toEqual(target);
  }
});

test("every group round-trips", () => {
  for (const group of CONTINUITY_GROUPS) {
    expect(roundTrip({ section: "continuity", group })).toEqual({ section: "continuity", group });
  }
});

test("a candidate id round-trips under its group", () => {
  const target: LedgerTarget = {
    section: "continuity", group: "overlaps",
    candidate: "possible_duplicate-0123456789abcdef",
  };
  expect(roundTrip(target)).toEqual(target);
});

test("a row id holding a slash and a colon stays one segment", () => {
  // Review Focus 1: a model-written plot id can be `mara/map` or `act:2`.
  for (const row of ["mara/map", "act:2", "mara/act:2 (draft)"]) {
    const href = ledgerHref("run", { section: "threads", row });
    expect(href.split("/")).toHaveLength(6);
    expect(roundTrip({ section: "threads", row })).toEqual({ section: "threads", row });
  }
});

test("the facts section with no row is the bare ledger", () => {
  expect(ledgerHref("run", { section: "facts" })).toBe("/campaigns/run/ledger");
  expect(parseLedgerTail("")).toEqual({ section: "facts" });
  // ...and the spelled-out address is accepted too.
  expect(parseLedgerTail("facts")).toEqual({ section: "facts" });
  expect(ledgerHref("run", { section: "facts", row: "f4" })).toBe("/campaigns/run/ledger/facts/f4");
});

test("the continuity section alone opens its first group", () => {
  expect(parseLedgerTail("continuity")).toEqual({ section: "continuity", group: "overlaps" });
});

test("the campaign id is encoded whole", () => {
  expect(ledgerHref("my run", { section: "threads" })).toBe("/campaigns/my%20run/ledger/threads");
});

test("an unknown section or group gives null", () => {
  expect(parseLedgerTail("nonsense")).toBeNull();
  expect(parseLedgerTail("continuity/nonsense")).toBeNull();
  expect(parseLedgerTail("continuity/nonsense/possible_duplicate-0123")).toBeNull();
});

test("empty segments are dropped and the tail is at most three deep", () => {
  expect(parseLedgerTail("/threads//mara-s-map/")).toEqual(
    { section: "threads", row: "mara-s-map" });
  expect(parseLedgerTail("continuity/closures/possible_thread_closure-01/extra")).toEqual(
    { section: "continuity", group: "closures", candidate: "possible_thread_closure-01" });
});

test("an undecodable segment gives null rather than throwing", () => {
  expect(parseLedgerTail("threads/%E0%A4%A")).toBeNull();
});

test("every candidate kind belongs to the group §6.2 names", () => {
  expect(GROUP_OF).toEqual({
    possible_duplicate: "overlaps", possible_relation: "overlaps",
    possible_thread_closure: "closures", possible_commitment_resolution: "resolutions",
  });
});
