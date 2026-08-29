import { readFileSync } from "node:fs";
import { join } from "node:path";
import { APP_ROWS, CAMPAIGN_ROWS, RAIL_PX, SEARCH_CHORD, railless, titleFor } from "./rail";
import { formatChord } from "../shortcuts/keys";

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
  "/campaigns/c1/timeline", "/campaigns/c1/scenes/s1/wrap-up",
  "/welcome", "/campaigns/new",
  "/modules-of-my-own",
];

const ctx = { cid: "c1" };

/** A payload with a mechanics module bound, so rows gated on one are offered.
 *  `to` takes the payload because whether a row has anywhere to go can depend
 *  on what the campaign actually has. */
const WITH_MODULE = {
  campaigns: 1, todo: null,
  campaign: {
    id: "c1", name: "A Run", world_name: "Saltmarch", scenes: 1, open: [],
    ledger_open: 0, sheets: { sheeted: 1, total: 2 }, unreviewed: 0,
    pending: [], images_undescribed: null,
  },
} as any;

function activeIn(rows: typeof APP_ROWS, path: string) {
  return rows.filter((r) => r.to(ctx, WITH_MODULE) !== null && r.match(path, ctx))
             .map((r) => r.id);
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
  //
  // `images` is in this list for a different reason than `wrap`, and the two
  // are worth telling apart. Wrap-up has no page at all. Images has one — a
  // section of the world screen — and is only dark here because this fixture
  // predates `world` on the payload, which is exactly the state an older
  // server leaves the field in.
  const dead = [...APP_ROWS, ...CAMPAIGN_ROWS]
    .filter((r) => r.to(ctx, WITH_MODULE) === null).map((r) => r.id);
  expect(dead).toEqual(["wrap", "images"]);
});

describe("Wrap-up goes where the proposals are", () => {
  const PENDING = {
    ...WITH_MODULE,
    campaign: {
      ...WITH_MODULE.campaign, unreviewed: 8,
      pending: [{ sid: "s13", proposals: 8 }, { sid: "s11", proposals: 2 }],
    },
  };
  const wrap = () => CAMPAIGN_ROWS.find((r) => r.id === "wrap")!;

  test("the first scene holding proposals, at the review's own address", () => {
    expect(wrap().to(ctx, PENDING)).toBe("/campaigns/c1/scenes/s13/wrap-up");
  });

  test("the tail reaches a reader now, instead of being computed and dropped", () => {
    // It was always computed. `Row` returns null on a null `to` before it
    // draws any tail, so the count never left the table.
    expect(wrap().tail!(PENDING)).toBe("8");
    expect(wrap().tailLabel!(PENDING)).toBe("8 proposals undecided");
  });

  test("nothing pending, no row — not a wrap-up with nothing to wrap up", () => {
    expect(wrap().to(ctx, WITH_MODULE)).toBeNull();
  });

  test("the transcript is not the wrap-up", () => {
    // Reading a scene and judging one are different places to be, and the rail
    // has to say which.
    expect(wrap().match("/campaigns/c1/scenes/s13", ctx)).toBe(false);
    expect(wrap().match("/campaigns/c1/scenes/s13/wrap-up", ctx)).toBe(true);
  });

  test("any scene's wrap-up lights it, not only the one it offers", () => {
    // With two scenes waiting the row points at the first; a reader who
    // reached the second from the hub is still on Wrap-up.
    expect(wrap().match("/campaigns/c1/scenes/s11/wrap-up", ctx)).toBe(true);
  });

  test("a trailing slash is the same route", () => {
    expect(wrap().match("/campaigns/c1/scenes/s13/wrap-up/", ctx)).toBe(true);
  });

  test("another campaign's wrap-up is not this one's", () => {
    expect(wrap().match("/campaigns/c2/scenes/s13/wrap-up", ctx)).toBe(false);
  });

  test("Scenes stands aside so the two are never lit at once", () => {
    const scenes = CAMPAIGN_ROWS.find((r) => r.id === "scenes")!;
    expect(scenes.match("/campaigns/c1/scenes/s13", ctx)).toBe(true);
    expect(scenes.match("/campaigns/c1/scenes/s13/wrap-up", ctx)).toBe(false);
    // ...but a scene that merely ends in something like it is still a scene.
    expect(scenes.match("/campaigns/c1/scenes/wrap-up", ctx)).toBe(true);
  });

  test("Wrap-up is what the crumb calls that address", () => {
    expect(titleFor("/campaigns/c1/scenes/s13/wrap-up")).toBe("Wrap-up");
  });
});

describe("Images points at the world section that holds it", () => {
  const WITH_WORLD = {
    ...WITH_MODULE,
    campaign: { ...WITH_MODULE.campaign, world: "saltmarch", images_undescribed: 3 },
  };
  const images = () => CAMPAIGN_ROWS.find((r) => r.id === "images")!;

  test("the id, not the name: `world_name` cannot address anything", () => {
    expect(images().to(ctx, WITH_WORLD))
      .toBe(`/worlds/saltmarch?section=images&for=${WITH_WORLD.campaign.id}`);
  });

  test("the campaign rides along, so the gallery can offer its cast as a filter", () => {
    // This is a CAMPAIGN row pointing at a world view: the reader arriving is
    // usually asking about one game's cast while looking at every record the
    // world has. Carried in the URL rather than inferred, so the link is
    // shareable and survives a reload.
    const to = images().to(ctx, WITH_WORLD)!;
    expect(to).toContain(`for=${WITH_WORLD.campaign.id}`);
  });

  test("no campaign id is no `for=`, not an empty one", () => {
    // An empty `for=` would reach the gallery as a campaign to look up, and
    // the appearance read behind it would 404 rather than simply not happen.
    const anon = { ...WITH_WORLD, campaign: { ...WITH_WORLD.campaign, id: "" } };
    expect(images().to(ctx, anon)).toBe("/worlds/saltmarch?section=images");
  });

  test("the backlog rides along", () => {
    expect(images().tail!(WITH_WORLD)).toBe("3");
    expect(images().tailLabel!(WITH_WORLD)).toBe("3 images undescribed");
  });

  test("nothing computed is no tail, and zero is a tail", () => {
    const none = { ...WITH_WORLD, campaign: { ...WITH_WORLD.campaign, images_undescribed: null } };
    expect(images().tail!(none)).toBeUndefined();
    const zero = { ...WITH_WORLD, campaign: { ...WITH_WORLD.campaign, images_undescribed: 0 } };
    expect(images().tail!(zero)).toBe("0");
  });

  test("it never lights, because the section is in the query string", () => {
    // `match` is handed the pathname alone. Lighting on `/worlds/saltmarch`
    // would mean lighting while the reader is in that world's Characters or
    // Lore, which is a worse lie than never lighting at all.
    for (const p of ["/worlds/saltmarch", "/worlds/saltmarch?section=images", "/campaigns/c1"]) {
      expect(images().match(p, ctx)).toBe(false);
    }
  });
});

test("Costs is absent with no campaign open, present with one", () => {
  const none = { cid: null };
  expect(APP_ROWS.find((r) => r.id === "costs")!.to(none, WITH_MODULE)).toBeNull();
  expect(APP_ROWS.find((r) => r.id === "costs")!.to(ctx, WITH_MODULE))
    .toBe("/campaigns/c1/costs");
});

test("Search advertises the chord the app actually answers", () => {
  // ⌘⇧F used to be unexpressible: `chordOf` folded shift into the character a
  // printable key produces even with a modifier held, so ⌘⇧F and ⌘F were one
  // chord — and that one is the browser's Find. Shift is named whenever
  // another modifier is present now, so the tail is a key that works.
  const tail = APP_ROWS.find((r) => r.id === "search")!.tail!(null);
  expect(tail).toBe(formatChord(SEARCH_CHORD));
  // Printed from the constant the binding uses, so the rail cannot advertise
  // one key while the app answers another.
  expect(SEARCH_CHORD).toBe("mod+shift+f");
});

describe("the Costs tail", () => {
  const costs = () => APP_ROWS.find((r) => r.id === "costs")!;
  const withMoney = (over: Record<string, unknown>) => ({
    campaigns: 1, todo: null,
    campaign: {
      id: "c1", name: "A Run", world_name: "Saltmarch", scenes: 2,
      open: [], ledger_open: 0, sheets: null, unreviewed: null,
      pending: [], images_undescribed: null,
      money: {
        calls: 4, cost_usd: 4.82, estimated_usd: 0, modelled_usd: 0,
        unpriced_calls: 0, unmetered_calls: 0, subscription_calls: 0,
        modelled_calls: 0, priced_calls: 4, total_tokens: 900,
        partial: false, ...over,
      },
    },
  } as any);

  test("spend is what it carries", () => {
    expect(costs().tail!(withMoney({}))).toBe("$4.82");
    expect(costs().tailLabel!(withMoney({}))).toBe("$4.82 spent");
  });

  test("only spend — the other two columns are never added into it", () => {
    // Three separate claims about money. A tail summing any two of them is the
    // one number nobody can recover, so the estimate and the model stay on the
    // hub's card where they can be labelled.
    const tail = costs().tail!(withMoney(
      { cost_usd: 1, estimated_usd: 10, modelled_usd: 100 }));
    expect(tail).toBe("$1.00");
  });

  test("a subscription-billed campaign draws nothing rather than $0.00", () => {
    // Real usage, no money paid. Rendering the spend column as $0.00 here
    // would say a played campaign was free.
    expect(costs().tail!(withMoney(
      { cost_usd: 0, estimated_usd: 1.25, subscription_calls: 4, priced_calls: 4 })))
      .toBeUndefined();
  });

  test("an aggregate that could not be counted draws nothing", () => {
    expect(costs().tail!(withMoney({ partial: true, cost_usd: 4.82 })))
      .toBeUndefined();
  });

  test("a campaign that has run nothing draws nothing", () => {
    expect(costs().tail!(withMoney({ calls: 0, cost_usd: 0 }))).toBeUndefined();
  });

  test("a payload from before the field existed draws nothing", () => {
    const old = { campaigns: 1, todo: null,
                  campaign: { id: "c1", name: "A Run", world_name: "S",
                              scenes: 0, open: [], ledger_open: 0, sheets: null,
                              unreviewed: null, pending: [],
                              images_undescribed: null } } as any;
    expect(costs().tail!(old)).toBeUndefined();
  });
});

test("no OTHER row carries a money tail", () => {
  // Costs is the one row money belongs on. A count row that grew a dollar sign
  // would be a second figure with nothing saying which of the three it is.
  const payload = { campaigns: 3, campaign: null, todo: null } as const;
  for (const row of [...APP_ROWS, ...CAMPAIGN_ROWS]) {
    if (row.id === "costs" || row.id === "search") continue;
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
      // Two rows answer without a payload because neither is a COUNT: the
      // library's section total is a frontend fact, and Search's tail is the
      // chord that opens it. Everything that measures something must stay
      // quiet until it has been told what.
      if (row.id !== "library" && row.id !== "search") expect(t).toBeUndefined();
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


test("Sheets is offered only where a mechanics module is bound", () => {
  // `sheets` is null when none is. A Sheets row on a campaign with no
  // mechanics is an offer to visit a page that can only say "nothing here",
  // and the rail should not send anyone somewhere to be told that.
  const sheets = CAMPAIGN_ROWS.find((r) => r.id === "sheets")!;
  expect(sheets.to(ctx, WITH_MODULE)).toBe("/campaigns/c1/sheets");
  const noModule = {
    ...WITH_MODULE,
    campaign: { ...WITH_MODULE.campaign, sheets: null },
  };
  expect(sheets.to(ctx, noModule)).toBeNull();
  // ...and with no payload at all it stays quiet rather than guessing.
  expect(sheets.to(ctx, null)).toBeNull();
});
