import { describe, expect, test } from "vitest";
import type { ShellPayload } from "../api/types";
import { phoneTabs, PHONE_PX } from "./tabs";
import { APP_ROWS, CAMPAIGN_ROWS, RAIL_PX } from "./rail";

const payload = (over: Partial<ShellPayload> = {}): ShellPayload => ({
  campaigns: 3, library: 6, todo: 14,
  campaign: {
    id: "run", name: "A Campaign", world_name: "Realm", scenes: 15,
    open: [{ sid: "s15", title: "The lower step", turns: null }],
    unreviewed: 8, ledger_open: 4, sheets: { sheeted: 4, total: 7 },
    images_undescribed: 3,
  },
  ...over,
} as ShellPayload);

const ids = (ctx: { cid: string | null }, p: ShellPayload | null) =>
  phoneTabs(ctx, p).map((t) => t.id);

test("the phone breakpoint sits below the rail's", () => {
  // They are one decision seen twice: the rail becomes a drawer first, and
  // only further down does the drawer stop being enough on its own. A phone
  // breakpoint at or above the rail's would put a bar on screen beside a
  // docked rail, which is two navigation surfaces arguing.
  expect(PHONE_PX).toBeLessThan(RAIL_PX);
});

describe("what the bar offers", () => {
  test("five destinations with a campaign open, in the design's order", () => {
    expect(ids({ cid: "run" }, payload())).toEqual(
      ["overview", "scenes", "play", "todo", "more"],
    );
  });

  test("Play disappears when no scene is open rather than landing on a list", () => {
    // The Scenes tab already is that list, and a tab that silently becomes a
    // duplicate of its neighbour is a slot spent saying nothing.
    const p = payload({ campaign: { ...payload().campaign!, open: [] } });
    expect(ids({ cid: "run" }, p)).not.toContain("play");
  });

  test("Play goes to the first open scene", () => {
    const t = phoneTabs({ cid: "run" }, payload()).find((x) => x.id === "play");
    expect(t?.to).toBe("/campaigns/run/scenes/s15");
  });

  test("with no campaign open the bar falls back to the app's front doors", () => {
    // Not a two-tab stub. "The same app, not a cut-down one" is the design's
    // note on this screen, and a phone that can only reach To do and a drawer
    // is the cut-down one.
    const p = payload({ campaign: null });
    expect(ids({ cid: null }, p)).toEqual(["campaigns", "library", "todo", "more"]);
  });
});

describe("badges come from the rail, not from a second table", () => {
  test("a tab's badge is the rail row's own tail", () => {
    const p = payload();
    const tabs = phoneTabs({ cid: "run" }, p);
    for (const t of tabs) {
      const row = [...APP_ROWS, ...CAMPAIGN_ROWS].find((r) => r.id === t.id);
      if (!row) continue;   // `play` and `more` are the bar's own
      expect(t.badge).toBe(row.tail?.(p));
      expect(t.label === "Hub" || t.label === row.label).toBe(true);
    }
  });

  test("a count nobody computed is no badge, not a zero", () => {
    // The rail's rule, inherited rather than restated. `0` means nothing is
    // waiting; absent means nobody could say.
    const p = payload({ todo: null });
    const todo = phoneTabs({ cid: "run" }, p).find((t) => t.id === "todo");
    expect(todo?.badge).toBeUndefined();
  });

  test("a zero is a badge", () => {
    const p = payload({ todo: 0 });
    const todo = phoneTabs({ cid: "run" }, p).find((t) => t.id === "todo");
    expect(todo?.badge).toBe("0");
  });
});

test("More opens the rail rather than navigating", () => {
  const more = phoneTabs({ cid: "run" }, payload()).find((t) => t.id === "more");
  expect(more?.opensRail).toBe(true);
  expect(more?.to).toBeNull();
});

test("at most one tab is active for any pathname", () => {
  // The rail holds itself to this and the bar has to as well: two lit tabs is
  // two answers to "where am I".
  const ctx = { cid: "run" };
  const tabs = phoneTabs(ctx, payload());
  const paths = [
    "/campaigns/run", "/campaigns/run/scenes", "/campaigns/run/scenes/s15",
    "/campaigns/run/ledger", "/todo", "/library", "/config", "/",
  ];
  for (const p of paths) {
    const lit = tabs.filter((t) => t.match(p, ctx));
    expect(lit.length, `${p} lit ${lit.map((t) => t.id).join()}`).toBeLessThanOrEqual(1);
  }
});

test("a row whose page does not exist yet is not a tab", () => {
  // `to` returning null is how the rail ships complete in shape and sparse in
  // fact, and the bar reads the same signal rather than keeping its own list
  // of what is built.
  const p = payload({ campaign: null });
  expect(phoneTabs({ cid: null }, p).every((t) => t.to !== null || t.opensRail)).toBe(true);
});
