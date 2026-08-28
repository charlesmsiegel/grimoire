import { money } from "../components/cost";
import { LIBRARY_SECTIONS, inLibrary, isUnder } from "../librarySections";
import type { ShellPayload } from "../api/types";

/** Below this the rail and a page's context column cannot share a row.
 *
 *  Distinct from `PageShell`'s `PHONE_PX` (720) on purpose: that one is where a
 *  *column and main* can no longer share a row. They are different questions
 *  about different pairs of elements, and collapsing them would be a
 *  coincidence rather than a simplification.
 *
 *  Duplicated as a literal in `index.css`, which cannot read a TypeScript
 *  constant — the same duplication `PHONE_PX` already has with the 720px rules.
 *  `rail.test.ts` reads the stylesheet and fails if the two drift. */
export const RAIL_PX = 1180;

/** The two pages the rail does not appear beside.
 *
 *  `PlainShell`'s docstring calls these "one centred question at a time" pages
 *  that would be answering *what am I navigating* with *nothing, finish this
 *  first*. On a first run the rail would otherwise offer Campaigns, Library and
 *  Configuration before setup has been answered at all. The header's drawer
 *  control goes with it — an opener for something that is not there is worse
 *  than no opener.
 *
 *  It is also why `useOpenCampaign` excludes these paths: `/campaigns/new`
 *  matches `/campaigns/:cid` as a route pattern, so a wizard abandoned halfway
 *  would otherwise leave `"new"` remembered as the open campaign. */
export const RAIL_LESS = ["/welcome", "/campaigns/new"];

export function railless(pathname: string): boolean {
  return RAIL_LESS.some((p) => isUnder(pathname, p));
}

/** What a row needs to know about the world outside the payload. */
export type RailCtx = { cid: string | null };

export type RailRow = {
  /** A stable key for the row. Deliberately NOT a path into the payload —
   *  several counts are nested (`sheets`, `ledger_open`) and a projection
   *  function is how those are read. */
  id: string;
  label: string;
  /** A literal character. There is no icon library, and the design's glyphs
   *  are literal characters too. */
  icon: string;
  /** Where the row goes, or `null` when it goes nowhere yet — the row is then
   *  not rendered at all, rather than rendered disabled.
   *
   *  It takes the payload as well as the campaign, because whether a row has
   *  anywhere to go can depend on what the campaign actually has -- Sheets is
   *  meaningless without a mechanics module bound.
   *
   *  This is what lets the rail ship complete in shape and sparse in fact.
   *  To be exact about what it buys, since it is easy to overclaim: a later
   *  slice still edits this table. It changes one `to` (and adds a `tail` if
   *  the row carries a count) rather than touching the rail's markup, its
   *  matching or its tests. */
  to: (ctx: RailCtx, payload: ShellPayload | null) => string | null;
  /** Whether this row is the one the current pathname belongs to.
   *
   *  Per row rather than one shared rule, because one shared rule is wrong in
   *  both directions and both are reachable today. `/library` is a redirect to
   *  `/worlds`, so a prefix test on `/library` goes dark the moment you use it.
   *  And `/campaigns/:cid/ledger` sits under `/campaigns/:cid` too, so a prefix
   *  test would light Play and Ledger at once. */
  match: (pathname: string, ctx: RailCtx) => boolean;
  /** The right-hand tail, or `undefined` for none. `undefined` and `0` are
   *  different answers: `0` means nothing is waiting, `undefined` means nobody
   *  computed it. */
  tail?: (p: ShellPayload | null) => string | undefined;
  /** What a screen reader hears appended to the row's name, so a count is
   *  never carried by visual position alone. */
  tailLabel?: (p: ShellPayload | null) => string | undefined;
};

/** The library section count is a frontend fact and stays one.
 *
 *  Read from the list that defines the sections, so adding a seventh is one
 *  edit. Answering it from Python as well would be one manifest in two
 *  languages with nothing holding them level, and a seventh section would ship
 *  a badge of six — which is why `GET /api/shell` carries no `library` field. */
const LIBRARY_SECTION_COUNT = LIBRARY_SECTIONS.length;

const campaignPath = (ctx: RailCtx, suffix = "") =>
  ctx.cid ? `/campaigns/${ctx.cid}${suffix}` : null;

/** Where one scene's review lives. */
const wrapPath = (cid: string, sid: string) =>
  `/campaigns/${cid}/scenes/${sid}/wrap-up`;

/** Whether this pathname is a wrap-up in the open campaign.
 *
 *  A suffix test rather than a prefix one, because the scene id sits in the
 *  middle: `/campaigns/c/scenes/<any sid>/wrap-up`. Shared by two rows that
 *  must never both be lit -- `scenes` uses it to step aside and `wrap` to step
 *  forward, so there is one sentence deciding rather than two that can drift. */
function isWrapUp(p: string, ctx: RailCtx): boolean {
  if (!ctx.cid) return false;
  const head = `/campaigns/${ctx.cid}/scenes/`;
  // The trailing slash is stripped first so `/wrap-up/` answers the same as
  // `/wrap-up`; the router treats them as one route and the rail must too.
  const path = p.endsWith("/") && p.length > 1 ? p.slice(0, -1) : p;
  return path.startsWith(head) && path.endsWith("/wrap-up")
    // ...and the scene id is a real segment, so `/scenes/wrap-up` -- a scene
    // somebody managed to name that -- is a scene rather than a review.
    && path.slice(head.length, -"/wrap-up".length).length > 0;
}

export const APP_ROWS: RailRow[] = [
  {
    id: "campaigns", label: "Campaigns", icon: "◆",
    to: () => "/",
    match: (p) => p === "/",
    tail: (s) => (s ? String(s.campaigns) : undefined),
    tailLabel: (s) => (s ? `${s.campaigns} campaigns` : undefined),
  },
  {
    id: "library", label: "Library", icon: "▤",
    to: () => "/library",
    // The one caller `inLibrary`'s own comment has been promising since before
    // the rail it describes existed. A prefix test on `/library` would go dark
    // one click later, because `/library` is a `<Navigate to="/worlds">`.
    match: (p) => inLibrary(p),
    tail: () => String(LIBRARY_SECTION_COUNT),
    tailLabel: () => `${LIBRARY_SECTION_COUNT} sections`,
  },
  {
    id: "todo", label: "To do", icon: "✓",
    to: () => "/todo",
    match: (p) => isUnder(p, "/todo"),
    // The count of what has NOT been waved off, campaign open or not. It used
    // to be `null` outside one, because every chore the app could compute was
    // about a campaign; the library's own chores -- an undescribed image
    // backlog, a world whose cast has no taglines -- answer before a campaign
    // is chosen, which is exactly when a freshly imported world's backlog is
    // largest. A zero here is now a real zero rather than "cannot say".
    tail: (s) => num(s?.todo),
    tailLabel: (s) => lbl(s?.todo, "things noticed"),
  },
  {
    id: "search", label: "Search", icon: "⌕",
    to: () => "/search",
    match: (p) => isUnder(p, "/search"),
    // The design's tail reads ⌘⇧F. That chord cannot exist here: `chordOf`
    // folds shift into the character a printable key produces, so Cmd+Shift+F
    // and Cmd+F both normalize to `mod+f` — a `mod+shift+f` binding would never
    // fire and a `mod+f` one would take the browser's Find. Rather than ship a
    // tail advertising a key that does nothing, there is no tail and no
    // binding.
  },
  {
    // Scoped to the open campaign, which is what the design's figure is. With
    // none open there is nowhere for it to go, so it does not render.
    id: "costs", label: "Costs", icon: "$",
    to: (ctx) => campaignPath(ctx, "/costs"),
    match: (p, ctx) => !!ctx.cid && isUnder(p, `/campaigns/${ctx.cid}/costs`),
    // The all-time figure the design asks for, and it is all-time rather than
    // a bounded window on purpose: a 30-day number under an unlabelled `$4.82`
    // would mean something else than the page it links to. `store.usage_rollup`
    // is what made it affordable here — a byte bookmark into each month file,
    // so the rail pays for what has been played since the last navigation
    // instead of for the library's age.
    //
    // SPEND ONLY. One tail cannot carry three columns that may never be added,
    // so it carries the one that is money somebody was actually charged and
    // leaves the other two to the hub's card, which has room to keep them
    // apart. A campaign whose calls were all subscription-billed or all
    // unpriced therefore shows no tail rather than `$0.00` — which is the cost
    // rule itself, not an omission.
    tail: (s) => railMoney(s)?.[0],
    tailLabel: (s) => railMoney(s)?.[1],
  },
  {
    id: "stats", label: "Stats", icon: "▦",
    to: () => "/stats",
    match: (p) => isUnder(p, "/stats"),
  },
  {
    id: "config", label: "Configuration", icon: "⚙",
    to: () => "/config",
    match: (p) => isUnder(p, "/config"),
  },
];

export const CAMPAIGN_ROWS: RailRow[] = [
  {
    // The campaign's front door. Exact, because every other campaign row lives
    // underneath this path -- a prefix test here would light Overview on every
    // one of them and give two active rows at once.
    id: "overview", label: "Overview", icon: "◈",
    to: (ctx) => campaignPath(ctx),
    match: (p, ctx) => !!ctx.cid && p === `/campaigns/${ctx.cid}`,
  },
  {
    id: "scenes", label: "Scenes", icon: "☰",
    to: (ctx) => campaignPath(ctx, "/scenes"),
    // Everything under `/scenes` EXCEPT a wrap-up, which is its own row now.
    // Without the exclusion both light at once, which the rail's own test
    // forbids and which would leave a reader unable to tell from the chrome
    // whether they are reading a scene or judging one.
    match: (p, ctx) => !!ctx.cid && isUnder(p, `/campaigns/${ctx.cid}/scenes`)
                       && !isWrapUp(p, ctx),
    tail: (s) => (s?.campaign ? String(s.campaign.scenes) : undefined),
    tailLabel: (s) => (s?.campaign ? `${s.campaign.scenes} scenes` : undefined),
  },
  {
    id: "wrap", label: "Wrap-up", icon: "✦",
    // The review's own address (`App.tsx`). It used to point at the scene
    // itself, which was a real destination but not a distinguishable one --
    // `scenes` owns every path under `/scenes`, so the row could never light.
    // It has a path of its own now, and `scenes` excludes it above.
    //
    // Null when nothing is pending, so the row is absent rather than offering
    // a wrap-up with nothing to wrap up. `unreviewed` and `pending` come from
    // one `_pending` call, so they cannot disagree about whether to render.
    to: (ctx, s) => {
      const first = s?.campaign?.pending?.[0];
      return ctx.cid && first ? wrapPath(ctx.cid, first.sid) : null;
    },
    // Lit on any scene's wrap-up, not only the one the row points at: with two
    // scenes waiting the row offers the first, and a reader who reached the
    // second from the hub is still on Wrap-up.
    match: (p, ctx) => isWrapUp(p, ctx),
    // The one badge that is an alert rather than a count: proposals nobody has
    // decided are holding the world back, and the hub says so in words.
    tail: (s) => num(s?.campaign?.unreviewed),
    tailLabel: (s) => lbl(s?.campaign?.unreviewed, "proposals undecided"),
  },
  {
    id: "ledger", label: "Ledger & timeline", icon: "≡",
    to: (ctx) => campaignPath(ctx, "/ledger"),
    match: (p, ctx) => !!ctx.cid && isUnder(p, `/campaigns/${ctx.cid}/ledger`),
    tail: (s) => num(s?.campaign?.ledger_open),
    tailLabel: (s) => lbl(s?.campaign?.ledger_open, "open threads"),
  },
  {
    // Only where the campaign binds a mechanics module. `sheets` is null when
    // it does not, and a Sheets row on a campaign with no mechanics is an
    // offer to look at a page that can only say "nothing here" -- the rail
    // should not send anyone somewhere to be told that.
    id: "sheets", label: "Sheets", icon: "▣",
    to: (ctx, s) => (s?.campaign?.sheets ? campaignPath(ctx, "/sheets") : null),
    match: (p, ctx) => !!ctx.cid && isUnder(p, `/campaigns/${ctx.cid}/sheets`),
    tail: (s) => {
      const k = s?.campaign?.sheets;
      return k ? `${k.sheeted} of ${k.total}` : undefined;
    },
    tailLabel: (s) => {
      const k = s?.campaign?.sheets;
      return k ? `${k.sheeted} of ${k.total} sheeted` : undefined;
    },
  },
  {
    id: "images", label: "Images", icon: "▨",
    // `ImagesView` is a section of `WorldView` rather than a route of its own,
    // but the section is addressable — `WorldView` opens whatever
    // `?section=` names — so the row has somewhere real to go after all. It
    // needs the world's id, not its name, which is why `world` travels beside
    // `world_name` in the payload.
    to: (_ctx, s) => (s?.campaign?.world ? `/worlds/${s.campaign.world}?section=images` : null),
    // Never lit, deliberately. `match` is given the pathname alone, and the
    // section this row points at lives in the query string — so the honest
    // options are "never active" or "active on every screen of that world,
    // including its Characters and its Lore". A row that lights while you are
    // somewhere else is worse than one that never lights, and the day Images
    // earns a real route this becomes a one-line `isUnder`.
    match: () => false,
    tail: (s) => num(s?.campaign?.images_undescribed),
    tailLabel: (s) => lbl(s?.campaign?.images_undescribed, "images undescribed"),
  },
];

/** A count's tail, keeping `0` and "nobody asked" apart.
 *
 *  `0` is an answer — nothing is waiting. `null`/`undefined` means the field
 *  was not computed, and the rail draws nothing rather than a zero somebody
 *  would read as a measurement. Same sentence as the cost rule, one domain
 *  over. */
function num(v: number | null | undefined): string | undefined {
  return v === null || v === undefined ? undefined : String(v);
}

function lbl(v: number | null | undefined, noun: string): string | undefined {
  return v === null || v === undefined ? undefined : `${v} ${noun}`;
}

/** The Costs tail and what a screen reader hears for it, or `undefined`.
 *
 *  Four ways to have nothing to say, and each is the cost rule rather than a
 *  missing case: no campaign open, an aggregate that could not be brought up
 *  to date (`partial`), a campaign that has run no calls at all, and — the one
 *  worth reading twice — a campaign whose calls were real but whose spend
 *  column is zero because every one of them billed to a subscription or came
 *  back with no price. Rendering `$0.00` there would be the app asserting that
 *  a played campaign was free.
 *
 *  Returned as a pair rather than computed twice, so the tail and its label
 *  cannot disagree about whether there is one. */
function railMoney(s: ShellPayload | null): [string, string] | undefined {
  const m = s?.campaign?.money;
  if (!m || m.partial || !m.calls || !(m.cost_usd > 0)) return undefined;
  return [money(m.cost_usd), `${money(m.cost_usd)} spent`];
}

/** What the ⌘K pill calls the screen you are on.
 *
 *  Matched in order, first match wins, catch-all last — so no route can fall
 *  through to a blank crumb silently. A page that publishes its own context
 *  (every campaign page does, through `ShellStatus`) wins over this table: the
 *  router knows the cid but not the campaign's name, so only the page can
 *  answer there.
 *
 *  `/library` resolves to what it redirects *to*, because the reader never sees
 *  `/library` for longer than a frame. */
export const TITLES: [(p: string) => boolean, string][] = [
  [(p) => p === "/", "Campaigns"],
  [(p) => isUnder(p, "/welcome"), "Setup"],
  [(p) => isUnder(p, "/campaigns/new"), "New campaign"],
  [(p) => isUnder(p, "/library") || isUnder(p, "/worlds"), "The Library"],
  [(p) => isUnder(p, "/modules"), "Modules"],
  [(p) => isUnder(p, "/styles"), "Styles"],
  [(p) => isUnder(p, "/response-presets"), "Response presets"],
  [(p) => isUnder(p, "/calendars"), "Calendars"],
  [(p) => isUnder(p, "/climates"), "Climates"],
  [(p) => isUnder(p, "/connections"), "Connections"],
  [(p) => isUnder(p, "/search"), "Search"],
  [(p) => isUnder(p, "/stats"), "Stats"],
  [(p) => isUnder(p, "/config"), "Configuration"],
  [(p) => isUnder(p, "/open"), "Opening"],
  // Last of the campaign-scoped entries, and only reachable when the page
  // itself publishes nothing: `CampaignView` names the campaign and the scene,
  // which this table cannot -- the router knows a cid, not a name.
  [(p) => /\/wrap-up\/?$/.test(p), "Wrap-up"],
  [() => true, "Grimoire"],
];

export function titleFor(pathname: string): string {
  for (const [test, title] of TITLES) if (test(pathname)) return title;
  // Unreachable: the table's last entry matches everything. Kept so a future
  // edit that drops the catch-all fails loudly here rather than rendering "".
  return "Grimoire";
}
