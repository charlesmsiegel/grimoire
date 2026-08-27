import { readFileSync } from "node:fs";
import { join } from "node:path";
import { APP_ROWS, CAMPAIGN_ROWS, RAIL_PX, railless, titleFor } from "./rail";

/** Every pathname the app can actually be standing on.
 *
 *  One list, three tests. A test of one clever case proves the clever case; the
 *  defects this file exists for are the ordinary routes nobody thought to
 *  check — `/worlds` (which `Library` reaches by redirect) and every campaign
 *  child route (which sits under the Play row's path too). */
const PATHS = [
  "/", "/worlds", "/modules", "/styles", "/response-presets", "/climates",
  "/connections", "/library", "/search", "/stats", "/config", "/open",
  "/campaigns/c1", "/campaigns/c1/scenes/s1", "/campaigns/c1/ledger",
  "/campaigns/c1/sheets", "/campaigns/c1/costs", "/campaigns/c1/world",
  "/campaigns/c1/timeline",
  "/welcome", "/campaigns/new",
  "/modules-of-my-own",
];

const ctx = { cid: "c1" };

function activeIn(rows: typeof APP_ROWS, path: string) {
  return rows.filter((r) => r.to(ctx) !== null && r.match(path, ctx)).map((r) => r.id);
}

describe("at most one row lights per tier", () => {
  // Two active rows is the failure a single `isUnder` rule gives you, and it is
  // reachable on the most ordinary URL in the app: `/campaigns/c1/ledger` is
  // under `/campaigns/c1` as well as under itself.
  test.each(PATHS)("%s", (path) => {
    expect(activeIn(APP_ROWS, path).length).toBeLessThanOrEqual(1);
    expect(activeIn(CAMPAIGN_ROWS, path).length).toBeLessThanOrEqual(1);
  });
});

test("Library survives its own redirect", () => {
  // `/library` is a <Navigate to="/worlds">, so a prefix test on `/library`
  // goes dark one click after you use it. `inLibrary` is what knows better —
  // the caller its own comment has been promising since before the rail
  // existed.
  expect(activeIn(APP_ROWS, "/library")).toEqual(["library"]);
  expect(activeIn(APP_ROWS, "/worlds")).toEqual(["library"]);
  expect(activeIn(APP_ROWS, "/connections")).toEqual(["library"]);
  // ...without lighting on a route that merely shares a prefix.
  expect(activeIn(APP_ROWS, "/modules-of-my-own")).toEqual([]);
});

test("a campaign child lights its own row and not Overview", () => {
  expect(activeIn(CAMPAIGN_ROWS, "/campaigns/c1/ledger")).toEqual(["ledger"]);
  expect(activeIn(CAMPAIGN_ROWS, "/campaigns/c1/sheets")).toEqual(["sheets"]);
  // Overview is the hub and only the hub. Every other campaign page lives
  // under its path, so a prefix test would light it on all of them.
  expect(activeIn(CAMPAIGN_ROWS, "/campaigns/c1")).toEqual(["overview"]);
  expect(activeIn(CAMPAIGN_ROWS, "/campaigns/c1/scenes/s1")).toEqual(["scenes"]);
});

test("rows whose pages do not exist yet go nowhere", () => {
  // Not a wish-list: this is what keeps the rail from offering a destination
  // that is not there. Each id gets a route in its own slice.
  const dead = [...APP_ROWS, ...CAMPAIGN_ROWS]
    .filter((r) => r.to(ctx) === null).map((r) => r.id);
  expect(dead).toEqual(["wrap", "images"]);
});

test("Costs is absent with no campaign open, present with one", () => {
  const none = { cid: null };
  expect(APP_ROWS.find((r) => r.id === "costs")!.to(none)).toBeNull();
  expect(APP_ROWS.find((r) => r.id === "costs")!.to(ctx)).toBe("/campaigns/c1/costs");
});

test("Search advertises no shortcut", () => {
  // `chordOf` folds shift into the character a printable key produces, so the
  // design's ⌘⇧F and a plain ⌘F are the same chord — and that one is the
  // browser's Find. A tail naming a key that does nothing is worse than none.
  expect(APP_ROWS.find((r) => r.id === "search")!.tail).toBeUndefined();
});

test("no row carries a money tail", () => {
  // The figure the design puts on Costs is an all-time ledger rollup, which
  // `store.usage.lifetime_since` reserves for the all-time view rather than the
  // play path — and the rail is the play path, on every navigation.
  const payload = { campaigns: 3, campaign: null, todo: null } as const;
  for (const row of [...APP_ROWS, ...CAMPAIGN_ROWS]) {
    expect(row.tail?.(payload) ?? "").not.toMatch(/\$/);
  }
});

describe("tails tell 0 apart from unmeasured", () => {
  const withCampaign = (over: Record<string, unknown>) => ({
    campaigns: 1, todo: null,
    campaign: {
      id: "c1", name: "A Run", world_name: "Saltmarch", scenes: 2,
      open: [], ledger_open: 0, sheets: null,
      unreviewed: null, pending: [], images_undescribed: null, ...over,
    },
  } as any);

  test("0 renders as 0 — nothing is waiting is an answer", () => {
    expect(CAMPAIGN_ROWS.find((r) => r.id === "ledger")!.tail!(withCampaign({}))).toBe("0");
  });

  test("an unbound module renders no tail rather than 0 of 0", () => {
    // "This module keeps no sheets" is legal and is not a measurement of zero.
    expect(CAMPAIGN_ROWS.find((r) => r.id === "sheets")!.tail!(withCampaign({})))
      .toBeUndefined();
  });

  test("no payload at all renders no tails", () => {
    for (const row of [...APP_ROWS, ...CAMPAIGN_ROWS]) {
      const t = row.tail?.(null);
      // The library count is a frontend fact and needs no payload; everything
      // else must stay quiet until it has been told something.
      if (row.id !== "library") expect(t).toBeUndefined();
    }
  });
});

test("every route the app can be on gets a name for the pill", () => {
  // "Every screen names itself" is only a checkable claim if something walks
  // the routes. The table's last entry matches everything, so this fails only
  // if that catch-all is ever dropped.
  for (const p of PATHS) expect(titleFor(p)).not.toBe("");
});

test("the wizards are the rail-less pages", () => {
  expect(railless("/welcome")).toBe(true);
  expect(railless("/campaigns/new")).toBe(true);
  expect(railless("/campaigns/c1")).toBe(false);
  // `/campaigns/new` matching `/campaigns/:cid` is why this list is also what
  // `useOpenCampaign` consults: without it, starting and abandoning the wizard
  // would leave "new" remembered as the open campaign.
  expect(railless("/campaigns/newton")).toBe(false);
});

test("RAIL_PX and the stylesheet agree", () => {
  // A media query cannot read a TypeScript constant, so the number is written
  // twice — the same duplication PHONE_PX already has with the 720px rules.
  // This is what stops the pair drifting silently, which would put the rail and
  // the layout that makes room for it on different sides of the same viewport.
  // `process.cwd()` is `frontend/` -- vitest runs from there (see CLAUDE.md).
  const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf-8");
  expect(css).toContain(`@media (max-width: ${RAIL_PX}px)`);
});
