import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type ProviderHealth } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_MODE } from "./theme/themes";
import AppHeader from "./components/AppHeader";
import AppRail from "./components/AppRail";
import PhoneTabs from "./components/PhoneTabs";
import AppPaletteSource from "./components/AppPaletteSource";
import CommandPalette, { usePaletteHotkey } from "./components/CommandPalette";
import { useHotkeys } from "./shortcuts/useHotkeys";
import { SEARCH_CHORD } from "./shell/rail";
import { FocusProvider, FocusRestore, useFocus } from "./components/focus";
import { PaletteProvider } from "./components/palette";
import { ShellStatusProvider } from "./components/ShellStatus";
import ShortcutsHelp from "./shortcuts/ShortcutsHelp";
import { onConfigChanged, onStoreMovedElsewhere } from "./appEvents";
import { RAIL_PX, railless } from "./shell/rail";
import { useOpenCampaign } from "./shell/useOpenCampaign";
import { useShellPayload } from "./shell/useShellPayload";
import { ShellPayloadProvider } from "./shell/ShellPayloadContext";
import AppRoutes from "./AppRoutes";
// Re-exported: the redirect lived here before the route table moved out, and
// `CharacterPage.test.tsx` mounts it by this path.
export { LegacyCharacterRedirect } from "./AppRoutes";

/** The shell's own body: header, palette, routes. Split out from `App` only so
 *  the ⌘K hotkey can live *under* `PaletteProvider` — a hook cannot read a
 *  context its own component renders.
 *
 *  Memoized because everything under it is the whole app, and `App` re-renders
 *  for reasons that change none of these props: a `configChanged` bumps its
 *  revision counter before the read it asks for has even answered. Every prop
 *  is a primitive, the provider health object (held stable across reads that
 *  did not change it -- see `App`), or a state setter, so the comparison is
 *  exact. What Shell reads for itself -- the location, focus, the palette --
 *  still re-renders it through its own hooks. */
const Shell = memo(function Shell(
  { inSetup, dataDir, ready, connection, model, health, onLeftSetup }: {
    inSetup: boolean; dataDir: string; ready: boolean;
    connection: string; model: string; health: ProviderHealth | null;
    onLeftSetup: (dir: string) => void;
  },
) {
  const location = useLocation();
  /** Whether the reader asked for the wizard again, rather than being sent to
   *  it. Read off the query string because that is the only place the request
   *  can live: it has to survive the redirect this route otherwise is, and it
   *  must not become a stored setting — "run setup again" is one act, not a
   *  mode the app can get stuck in. */
  const rerunSetup = new URLSearchParams(location.search).get("again") === "1";
  usePaletteHotkey();
  useSearchHotkey();
  const { focus } = useFocus();
  const { shell, openCid, shellValue } = useShellChrome(dataDir);
  const { docked, railOpen, openRail, closeRail } = useRailDrawer();

  // The two wizards are one centred question at a time; `PlainShell`'s
  // docstring calls a navigation surface beside them an answer of "nothing,
  // finish this first". On a first run the rail would otherwise offer
  // Campaigns, Library and Configuration before setup has been answered.
  const noRail = railless(location.pathname) || focus;
  const storeGen = useStoreGeneration();

  return (
    <>
      {/* Focus mode swaps the 52px strip for the pill that undoes it, and that
          is a swap rather than a `display: none` on purpose: a hidden header is
          still nine tab stops between the reader and the composer, and the
          restore control has to be the FIRST of them. `PageShell` drops the
          context column and its phone toggle on the same flag, and
          `CampaignView` the scene bar and scene head — together those are the
          ~300px of chrome that, on a phone, left a transcript about half the
          viewport. */}
      {focus ? <FocusRestore />
             : <AppHeader model={model} connection={connection} ready={ready}
                          health={health} railDrawer={!docked} campaign={shell.payload?.campaign}
                          onOpenRail={openRail} />}
      <AppPaletteSource payload={shell.payload} />
      <CommandPalette />
      {/* `?`, and the sheet that lists whatever is bound where you are
          standing. Mounted beside the palette because they are the same
          promise from two directions: everything is one keystroke away, and
          the keystrokes are discoverable without a manual. */}
      <ShortcutsHelp />
      {/* Every route renders its own `PageShell`, and the 274px column still
          belongs to the page rather than to the chrome -- but it is no longer
          being asked to carry the app's navigation as well. "What am I
          navigating" was two questions: which page of the app am I on, whose
          answer is the same everywhere and now lives in the rail, and which of
          this page's records am I reading, which only the page can answer and
          which the column keeps. */}
      {/* The one shape the whole app sits in now: the rail beside the page.
          The rail navigates the app -- a question whose answer is the same on
          every page, so it is asked once in chrome that outlives every route.
          A page's own column still answers which of THAT page's records you are
          reading, which is why `PageShell` is untouched by this. */}
      <ShellPayloadProvider value={shellValue}>
      <div className="app-body">
        {!noRail && (
          <AppRail payload={shell.payload} status={shell.status} cid={openCid}
                   dataDir={dataDir} docked={docked} open={railOpen}
                   onClose={closeRail} onRetry={shell.retry} />
        )}
      <AppRoutes key={storeGen} inSetup={inSetup} rerunSetup={rerunSetup} ready={ready}
                 dataDir={dataDir} onLeftSetup={onLeftSetup} />
      </div>
      </ShellPayloadProvider>
      {/* The phone's half of the same decision, and a ROW of the shell rather
          than something floating over it: `#root` is already a column, so the
          bar taking its own height is what keeps a scrolling page from ending
          underneath it — no fixed positioning, no padding to keep in step with
          the bar's own size, and the safe-area inset is just its padding.
          `noRail` covers it too: the wizards and focus mode answer "nothing,
          finish this first" and "nothing, read this", and a permanent bar at
          the foot is the one surface that would keep arguing. CSS draws it
          only below `PHONE_PX`, where the rail has already become a drawer. */}
      {!noRail && (
        <PhoneTabs payload={shell.payload} cid={openCid}
                   onOpenRail={openRail} />
      )}
    </>
  );
});

/** The design's Search shortcut, expressible since `chordOf` stopped folding
 *  shift into a chord that already has a modifier in it.
 *
 *  `global`, like the palette's, because Search is the other thing that has
 *  to be reachable from anywhere -- an overlay must not be able to strand
 *  you. NOT `whileTyping`: the palette earns that by being the thing you
 *  reach for mid-sentence without losing the draft, and a chord that
 *  navigated away from a half-written post would lose it. */
function useSearchHotkey(): void {
  const navigate = useNavigate();
  useHotkeys([{
    keys: SEARCH_CHORD, label: "Search everything", group: "ANYWHERE",
    global: true, run: () => void navigate("/search"),
  }]);
}

/** The shell read the chrome draws from, and the two answers derived from it:
 *  which campaign the chrome is about, and the context value the pages that
 *  draw its numbers read. */
function useShellChrome(dataDir: string) {
  // Which campaign the rail's second tier is about. A hint the payload
  // validates -- what actually renders comes from `payload.campaign`, never
  // from storage, so a rename cannot leave a stale name in the chrome.
  const { cid, reconcile } = useOpenCampaign(dataDir);
  const shell = useShellPayload(dataDir, cid, reconcile);
  /** The campaign the chrome is actually about.
   *
   *  `cid` is what the browser remembers and what the read ASKS with; this is
   *  what came back. They differ in exactly one case, and it is the one the
   *  rail was previously blank for: a browser that has never opened a campaign
   *  asks with nothing, and `GET /api/shell` answers with the most recently
   *  played one. Every consumer of "which campaign" wants that answer rather
   *  than the empty question - the rail's rows build their hrefs from it (a
   *  row whose `to()` is null is not rendered at all, so the tier would draw
   *  its heading and nothing under it), and the Todo page must scope to the
   *  same campaign the rail's badge counted.
   *
   *  Deliberately not fed back into `useOpenCampaign`: nothing is written to
   *  storage, so opening a campaign explicitly still wins, and the default
   *  keeps tracking what was last played instead of freezing on whichever
   *  campaign it happened to pick first. */
  const openCid = shell.payload?.campaign?.id ?? cid;
  /** The same read, for the pages that draw its numbers (the hub, the scenes
   *  list). They used to issue their own copy of it beside this one and hold
   *  the page until it answered; `ShellPayloadContext` says why that was the
   *  slowest thing on either page. `cid` rides along because a status is only
   *  a statement about the campaign it asked about. */
  const shellValue = useMemo(
    () => ({ status: shell.status, payload: shell.payload, retry: shell.retry, cid }),
    [shell.status, shell.payload, shell.retry, cid]);
  return { shell, openCid, shellValue };
}

/** Whether the rail is docked beside the page or is a drawer, and the
 *  drawer's open state. */
function useRailDrawer() {
  // `innerWidth` rather than `matchMedia`, and event-driven rather than polled
  // -- the same reading `PageShell` takes for its own breakpoint, and the one
  // the CSS gets. The two breakpoints stay distinct: RAIL_PX is where the rail
  // and a page's column can no longer share a row, PHONE_PX is where a column
  // and main cannot.
  const [docked, setDocked] = useState(() => window.innerWidth > RAIL_PX);
  const [railOpen, setRailOpen] = useState(false);
  useEffect(() => {
    const onResize = () => setDocked(window.innerWidth > RAIL_PX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  // Widening past the breakpoint docks the rail, so the drawer must not be left
  // "open" underneath it -- and leaving focus mode must not restore one either.
  useEffect(() => { if (docked) setRailOpen(false); }, [docked]);
  const openRail = useCallback(() => setRailOpen(true), []);
  const closeRail = useCallback(() => setRailOpen(false), []);
  return { docked, railOpen, openRail, closeRail };
}

/** Bumped when another tab moves the store, and the routed page's key. The
 *  page open here was read from the library that just stopped being served,
 *  and its handlers would write into the new one under the old one's ids --
 *  so it is remounted rather than left to notice, which it has no way to
 *  do. The chrome around it is not: the rail and the header refresh through
 *  their own signals and keep their scroll and drawer state. */
function useStoreGeneration(): number {
  const [storeGen, setStoreGen] = useState(0);
  useEffect(() => onStoreMovedElsewhere(() => setStoreGen((n) => n + 1)), []);
  return storeGen;
}

/** The fields of a provider verdict the header draws. `at` is left out on
 *  purpose: the server restamps it on every real turn, so comparing it would
 *  make nearly every read after play look like news to a dot that says the
 *  same thing. */
function sameHealth(a: ProviderHealth | null, b: ProviderHealth | null): boolean {
  // Falsy rather than `=== null`: this runs inside a state updater, so a
  // payload that left the key out would otherwise throw during render and
  // take the whole tree down over a status dot.
  if (!a || !b) return a === b;
  return a.state === b.state && a.kind === b.kind && a.detail === b.detail;
}

export default function App() {
  const [theme, setTheme] = useState<string | null>(null);
  // The server's verdict, refreshed with the rest of the config on every
  // navigation — a world created from WorldsView, or a data dir repointed at
  // another library, both change the answer without this component hearing
  // about it otherwise (#194).
  const [firstRun, setFirstRun] = useState(false);
  // ...and a latch for "setup has been left", scoped to the store it was left
  // in. Unscoped it is a trap in the other direction: `finish()` treats its
  // `setup_done` write as best-effort so a failure can't strand anyone, and on
  // a store that cannot record the flag the next refresh would answer first-run
  // again and redirect straight back into the wizard, forever. Keyed to the
  // data dir it still guarantees one exit is enough — while letting a *different*
  // library, pointed at from Config later in the same session, get its own
  // first run rather than inheriting this one's dismissal.
  const [dataDir, setDataDir] = useState("");
  const [leftSetupFor, setLeftSetupFor] = useState<string | null>(null);
  const inSetup = firstRun && leftSetupFor !== dataDir;
  const [ready, setReady] = useState(false);
  const [connection, setConnection] = useState("");
  const [model, setModel] = useState("");
  // What the active provider last did (#146). It rides the config read the
  // header already makes rather than a poll of its own: every navigation
  // refreshes it, and the server updates it from real turns and from an
  // explicit Test connection, so the dot tracks the thing it claims to.
  const [health, setHealth] = useState<ProviderHealth | null>(null);

  const location = useLocation();

  // Navigation is not the only thing that changes what the header should say:
  // /config switches the active connection and /providers edits a provider,
  // both without moving the pathname. Leaving the header naming the old
  // connection during the connection-management workflow is the worst possible
  // moment for it to be wrong, since that workflow exists to change it.
  const [configRev, setConfigRev] = useState(0);
  useEffect(() => onConfigChanged(() => setConfigRev((n) => n + 1)), []);

  /** What the last run of the effect below asked about. */
  const asked = useRef<string | null>(null);

  // ONE read serves the theme and the header. They used to be two effects, a
  // cached read for the theme and a fresh one for everything else, which on
  // mount were two requests for the same bytes -- three under StrictMode.
  useEffect(() => {
    // `fresh`: the cached config is only invalidated by this tab's own writes,
    // so a library populated in another tab or by a sync client would leave
    // `firstRun` — and the connection and model beside it — stale indefinitely.
    // The exception is a re-run for inputs already asked about, which only
    // StrictMode's rehearsed mount produces: that joins the read those inputs
    // started (`getConfig` caches the promise) instead of issuing a second.
    //
    // Guarded because this effect can be in flight twice at once -- two quick
    // connection edits, or a store move during a slow read -- and nothing
    // orders the responses. `fresh` stops a *new* caller joining a
    // pre-mutation read; it cannot unsubscribe a `.then` already attached to
    // one, so without this the older response lands second and reverts the
    // header to the connection it just stopped describing -- and, now that
    // first-run rides along, could re-arm the wizard redirect from a stale
    // read.
    const inputs = `${location.pathname}\n${configRev}`;
    const fresh = asked.current !== inputs;
    asked.current = inputs;
    let live = true;
    api.getConfig(fresh ? { fresh: true } : undefined).then((c) => {
      if (!live) return;
      // The theme is taken once: `ThemeProvider` owns it from then on, and a
      // change made in Configuration reaches it through that provider rather
      // than through here.
      setTheme((t) => t ?? c.theme);
      // Primitives, so an unchanged value is a bail-out rather than a render.
      setReady(c.ready);
      setFirstRun(c.first_run);
      setDataDir(c.data_dir);
      setConnection(c.active_connection ? c.active_connection.name.toUpperCase() : "");
      setModel(c.active_connection?.model ?? "");
      // ...but health is an object, new on every read. Installed wholesale it
      // re-rendered the whole tree once per navigation, after the read landed,
      // to draw the same dot.
      setHealth((h) => (sameHealth(h, c.health) ? h : c.health));
    }).catch(() => {
      // A failed read must not leave the app blank: with no theme nothing
      // renders at all. Later failures change nothing -- the header keeps
      // saying what the last good read said.
      if (live) setTheme((t) => t ?? DEFAULT_MODE);
    });
    return () => { live = false; };
  }, [location.pathname, configRev]);

  if (theme === null) return null;

  return (
    <ThemeProvider initial={theme}>
      <ShellStatusProvider>
        <FocusProvider>
          <PaletteProvider>
            <Shell inSetup={inSetup} dataDir={dataDir} ready={ready}
                   connection={connection} model={model} health={health}
                   onLeftSetup={setLeftSetupFor} />
          </PaletteProvider>
        </FocusProvider>
      </ShellStatusProvider>
    </ThemeProvider>
  );
}
