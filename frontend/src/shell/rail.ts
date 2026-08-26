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
   *  This is what lets the rail ship complete in shape and sparse in fact.
   *  To be exact about what it buys, since it is easy to overclaim: a later
   *  slice still edits this table. It changes one `to` (and adds a `tail` if
   *  the row carries a count) rather than touching the rail's markup, its
   *  matching or its tests. */
  to: (ctx: RailCtx) => string | null;
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
    // No page and no backend yet, so the row does not render. The To do slice
    // gives it a route and a tail off `payload.todo`.
    id: "todo", label: "To do", icon: "✓",
    to: () => null,
    match: (p) => isUnder(p, "/todo"),
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
    // No tail. The figure the design puts here is an all-time ledger rollup,
    // and `store.usage.lifetime_since` reserves that scan for the all-time view
    // — "nothing on the play path". The rail is the play path.
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
    match: (p, ctx) => !!ctx.cid && isUnder(p, `/campaigns/${ctx.cid}/scenes`),
    tail: (s) => (s?.campaign ? String(s.campaign.scenes) : undefined),
    tailLabel: (s) => (s?.campaign ? `${s.campaign.scenes} scenes` : undefined),
  },
  {
    id: "wrap", label: "Wrap-up", icon: "✦",
    to: () => null,   // review lives inside CampaignView; the wrap-up slice moves it
    match: () => false,
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
    id: "sheets", label: "Sheets", icon: "▣",
    to: (ctx) => campaignPath(ctx, "/sheets"),
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
    to: () => null,   // ImagesView is a WorldView section, not a route
    match: () => false,
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
  [(p) => isUnder(p, "/climates"), "Climates"],
  [(p) => isUnder(p, "/connections"), "Connections"],
  [(p) => isUnder(p, "/search"), "Search"],
  [(p) => isUnder(p, "/stats"), "Stats"],
  [(p) => isUnder(p, "/config"), "Configuration"],
  [(p) => isUnder(p, "/open"), "Opening"],
  [() => true, "Grimoire"],
];

export function titleFor(pathname: string): string {
  for (const [test, title] of TITLES) if (test(pathname)) return title;
  // Unreachable: the table's last entry matches everything. Kept so a future
  // edit that drops the catch-all fails loudly here rather than rendering "".
  return "Grimoire";
}
