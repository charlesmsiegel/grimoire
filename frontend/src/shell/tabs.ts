import type { ShellPayload } from "../api/types";
import { APP_ROWS, CAMPAIGN_ROWS, type RailCtx, type RailRow } from "./rail";

/** Below this the rail is not a drawer you open, it is a bar you land on.
 *
 *  The rail already becomes a drawer below `RAIL_PX` (1180), which is the
 *  right answer for a tablet: there is room for the page, and navigation is
 *  something you ask for. On a 375px phone that same drawer is the *only* way
 *  to move, and every move costs a deliberate open — so the design gives the
 *  phone a permanent bottom bar instead and leaves the drawer underneath it
 *  for everything the bar has no room for.
 *
 *  720, matching `PageShell`'s `PHONE_PX` and `index.css`, because these are
 *  one decision seen from three places: below it a 274px column cannot share a
 *  row with content, and below it navigation cannot afford to be modal. Three
 *  numbers that must agree are better written once, but the CSS cannot import
 *  and the module cannot be read from a media query, so what holds them level
 *  is that they are the same literal and a test that says so. */
export const PHONE_PX = 720;

export type PhoneTab = {
  id: string;
  label: string;
  icon: string;
  /** Where it goes, or `null` when it goes nowhere — an unreachable tab is
   *  dropped rather than rendered dead, exactly as a rail row is. */
  to: string | null;
  /** The count on the tab, or `undefined` for none. Read through the rail's
   *  own `tail`, so a badge cannot say something the rail contradicts. */
  badge?: string;
  /** What a screen reader hears after the label, so the badge is never the
   *  only carrier. */
  badgeLabel?: string;
  match: (pathname: string, ctx: RailCtx) => boolean;
  /** The one tab that navigates nowhere: it opens the rail drawer. */
  opensRail?: boolean;
};

const byId = (rows: RailRow[], id: string) => rows.find((r) => r.id === id);

/** The five destinations a phone gets, in the design's order.
 *
 *  Four of them are rail rows read straight out of the rail's table rather
 *  than restated here. That is the whole point: the design's note beside this
 *  bar is "each badged from the same live counts the desktop rail uses — a
 *  phone tab bar that lags the app is worse than no badge", and two tables of
 *  the same routes is precisely how one comes to lag. A row that grows a count
 *  grows it here for free; a row whose page does not exist yet returns `null`
 *  from its own `to` and drops out of both surfaces at once.
 *
 *  `Play` is the exception and is not a rail row, because the rail does not
 *  have one either — it renders an indented row per open scene, which is a
 *  shape a 75px-wide tab cannot take. It resolves to the first open scene, and
 *  disappears when there is none rather than landing on a list that the Scenes
 *  tab already is.
 *
 *  `Scenes` keeps a permanent slot even though `More` could hold it. On a
 *  phone it is the only way to reach a scene that is not the current one, and
 *  a destination that is the sole route to a whole class of content is not one
 *  to put two taps away. */
export function phoneTabs(ctx: RailCtx, payload: ShellPayload | null): PhoneTab[] {
  const fromRow = (rows: RailRow[], id: string, over: Partial<PhoneTab>): PhoneTab | null => {
    const row = byId(rows, id);
    if (!row) return null;
    const to = row.to(ctx, payload);
    if (!to) return null;
    return {
      id: row.id, label: row.label, icon: row.icon, to,
      badge: row.tail?.(payload),
      badgeLabel: row.tailLabel?.(payload),
      match: row.match,
      ...over,
    };
  };

  const open = payload?.campaign?.open ?? [];
  const playTo = ctx.cid && open.length
    ? `/campaigns/${ctx.cid}/scenes/${open[0].sid}`
    : null;
  const play: PhoneTab | null = playTo
    ? {
      id: "play", label: "Play", icon: "▶", to: playTo,
      // Exactly its own destination, not "any scene". Reading scene 9 while
      // Play would resume scene 15 is not being on Play — Scenes is where
      // you are, and it is what should be lit.
      match: (p) => p === playTo,
    }
    : null;

  // Scenes is `isUnder(.../scenes)` in the rail, which is right there because
  // the rail has no Play row — it lists the open scenes as its own indented
  // rows instead. Here Play is a tab, so the two overlap on exactly one path,
  // and the more specific of them takes it.
  const scenes = fromRow(CAMPAIGN_ROWS, "scenes", {});
  if (scenes && play) {
    const under = scenes.match;
    scenes.match = (p, c) => under(p, c) && !play.match(p, c);
  }

  // With a campaign open the three campaign slots are worth more than any
  // app-wide row: they are where play happens, and Campaigns and Library are a
  // tap away under More. With none open they are empty, and the bar falls back
  // to the app's own front doors rather than shrinking to To do and a drawer —
  // "the same app, not a cut-down one" is the design's note on this screen, and
  // a phone that can only reach two places is the cut-down one.
  const lead = ctx.cid
    ? [
      fromRow(CAMPAIGN_ROWS, "overview", { label: "Hub" }),
      scenes,
      play,
    ]
    : [
      fromRow(APP_ROWS, "campaigns", {}),
      fromRow(APP_ROWS, "library", {}),
    ];

  const tabs = [
    ...lead,
    fromRow(APP_ROWS, "todo", {}),
    {
      // Not a route. The drawer is already a dialog with focus entry, Tab
      // containment and restoration, so "everything else" is a surface the app
      // has rather than one this bar has to invent — and the five slots stay
      // for the five things worth a permanent tap.
      id: "more", label: "More", icon: "⋯", to: null, opensRail: true,
      match: () => false,
    } satisfies PhoneTab,
  ];
  return tabs.filter((t): t is PhoneTab => t !== null);
}
