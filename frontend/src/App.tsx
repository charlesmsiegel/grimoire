import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_MODE } from "./theme/themes";
import AppHeader from "./components/AppHeader";
import AppPaletteSource from "./components/AppPaletteSource";
import CommandPalette, { usePaletteHotkey } from "./components/CommandPalette";
import { FocusProvider, FocusRestore, useFocus } from "./components/focus";
import { PaletteProvider } from "./components/palette";
import { ShellStatusProvider } from "./components/ShellStatus";
import ShortcutsHelp from "./shortcuts/ShortcutsHelp";
import { onConfigChanged } from "./appEvents";
import CampaignsView from "./routes/CampaignsView";
import CampaignWizard from "./routes/CampaignWizard";
import OpenScene from "./routes/OpenScene";
import CampaignView from "./routes/CampaignView";
import CostsView from "./routes/CostsView";
import LedgerView from "./routes/LedgerView";
import SheetsView from "./routes/SheetsView";
import TimelineView from "./routes/TimelineView";
import LibraryView from "./routes/LibraryView";
import SearchView from "./routes/SearchView";
import WorldsView from "./routes/WorldsView";
import WorldView from "./routes/WorldView";
import ModulesView from "./routes/ModulesView";
import StyleGuidesView from "./routes/StyleGuidesView";
import ResponsePresetsView from "./routes/ResponsePresetsView";
import ClimatesView from "./routes/ClimatesView";
import ConnectionsView from "./routes/ConnectionsView";
import ConfigView from "./routes/ConfigView";
import SetupWizard from "./routes/SetupWizard";

/** The shell's own body: header, palette, routes. Split out from `App` only so
 *  the ⌘K hotkey can live *under* `PaletteProvider` — a hook cannot read a
 *  context its own component renders. */
function Shell(
  { inSetup, dataDir, ready, connection, model, onLeftSetup }: {
    inSetup: boolean; dataDir: string; ready: boolean;
    connection: string; model: string; onLeftSetup: (dir: string) => void;
  },
) {
  const location = useLocation();
  usePaletteHotkey();
  const { focus } = useFocus();

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
             : <AppHeader model={model} connection={connection} ready={ready} />}
      <AppPaletteSource />
      <CommandPalette />
      {/* `?`, and the sheet that lists whatever is bound where you are
          standing. Mounted beside the palette because they are the same
          promise from two directions: everything is one keystroke away, and
          the keystrokes are discoverable without a manual. */}
      <ShortcutsHelp />
      {/* Every route renders its own `PageShell`, so the 274px column belongs
          to the page rather than to the chrome. That is the whole reason the
          nav sidebar could be retired: a column that changes with the page can
          answer "what am I navigating" precisely, where one shared list had to
          answer it for all of them at once and answered it for none. */}
      <Routes>
        {/* A fresh install lands on the wizard instead of an empty campaigns
            list. Only `/` is redirected: every other route stays reachable, so
            a deep link is never hijacked. The two guards are exact opposites
            of one `inSetup`, which is what keeps them from bouncing a redirect
            back and forth. Gating `/welcome` too is what stops a reload
            part-way through the wizard — after a world exists, so the server no
            longer calls it a first run — from restarting at step one and
            creating a second world. */}
        <Route path="/" element={inSetup ? <Navigate to="/welcome" replace /> : <CampaignsView />} />
        <Route path="/welcome" element={
          inSetup
            ? <SetupWizard onDone={(dir) => onLeftSetup(dir ?? dataDir)} />
            : <Navigate to="/" replace />} />
        <Route path="/campaigns/new" element={<CampaignWizard ready={ready} />} />
        {/* Where a completion-notification tap lands; see `OpenScene`. */}
        <Route path="/open" element={<OpenScene />} />
        {/* Keyed so a campaign→campaign move remounts. The palette made that
            transition reachable: it stays on this route and only changes the
            param, so React reuses the component, and CampaignView's [cid]
            effect refetches without synchronously dropping the scene list,
            transcript or activeId. Until those land -- or forever, if they
            fail -- it would show campaign A's scene while its handlers carry
            B's cid, and scene ids repeat across campaigns. The key is the
            campaign segment alone and NOT the whole pathname: this route
            matches deeper now (#87, below), and keying on the full path would
            remount on every scene jump -- exactly what the nested child exists
            to prevent. */}
        {/* The play view answers to two paths — with and without a scene
            (#87) — and they have to resolve to the SAME element instance.
            Sibling routes would remount CampaignView on
            /campaigns/A/scenes/s1 → /campaigns/B but not on
            A/scenes/s1 → B/scenes/s2, so the stale-response guards the view is
            built around (cidRef, the window token) would hold in one direction
            and be bypassed in the other. Nesting keeps one instance for every
            combination. The child renders nothing — CampaignView has no
            <Outlet /> — and exists only to put `:sid` in the matched path,
            where useMatch can read it. */}
        <Route path="/campaigns/:cid" element={
          <CampaignView key={location.pathname.split("/").slice(0, 3).join("/")}
                        ready={ready} />}>
          <Route path="scenes/:sid" element={null} />
        </Route>
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
        {/* The ledger is a room, not a drawer over the transcript (4e): it is a
            table read top to bottom, and the supersession chains it exists to
            show do not fit in a panel wedged above the scene. */}
        <Route path="/campaigns/:cid/ledger" element={<LedgerView />} />
        <Route path="/campaigns/:cid/costs" element={<CostsView />} />
        {/* The timeline is the ledger's other half and a room for the same
            reason (#198): the ledger says what is still open, this says what
            happened, and a play history read end to end is not a drawer over
            the scene it is a history of. */}
        <Route path="/campaigns/:cid/timeline" element={<TimelineView />} />
        {/* Sheet coverage across the cast (#201). A room for the ledger's
            reason: the play view's mechanics panel binds the module in six
            lines, but "who among forty characters has a sheet" is a list read
            top to bottom, and a drawer over the transcript is not where a list
            like that goes. */}
        <Route path="/campaigns/:cid/sheets" element={<SheetsView />} />
        <Route path="/library" element={<LibraryView />} />
        {/* Search keeps its query in the URL, so a result page is a link and
            the back button returns to it after following a hit. */}
        <Route path="/search" element={<SearchView />} />
        <Route path="/worlds" element={<WorldsView />} />
        <Route path="/worlds/:wid" element={<WorldView />} />
        <Route path="/modules" element={<ModulesView />} />
        <Route path="/styles" element={<StyleGuidesView />} />
        <Route path="/response-presets" element={<ResponsePresetsView />} />
        <Route path="/climates" element={<ClimatesView />} />
        <Route path="/connections" element={<ConnectionsView />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </>
  );
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

  const location = useLocation();

  useEffect(() => {
    api.getConfig()
      .then((c) => { setTheme(c.theme); setFirstRun(c.first_run); setDataDir(c.data_dir); })
      .catch(() => setTheme(DEFAULT_MODE));
  }, []);

  // Navigation is not the only thing that changes what the header should say:
  // /config switches the active connection and /connections edits its model,
  // both without moving the pathname. Leaving the header naming the old
  // connection during the connection-management workflow is the worst possible
  // moment for it to be wrong, since that workflow exists to change it.
  const [configRev, setConfigRev] = useState(0);
  useEffect(() => onConfigChanged(() => setConfigRev((n) => n + 1)), []);

  useEffect(() => {
    // `fresh`: the cached config is only invalidated by this tab's own writes,
    // so a library populated in another tab or by a sync client would leave
    // `firstRun` — and the connection and model beside it — stale indefinitely.
    //
    // Guarded because this effect can be in flight twice at once -- two quick
    // connection edits, or a store move during a slow read -- and nothing
    // orders the responses. `fresh` stops a *new* caller joining a
    // pre-mutation read; it cannot unsubscribe a `.then` already attached to
    // one, so without this the older response lands second and reverts the
    // header to the connection it just stopped describing -- and, now that
    // first-run rides along, could re-arm the wizard redirect from a stale
    // read.
    let live = true;
    api.getConfig({ fresh: true }).then((c) => {
      if (!live) return;
      setReady(c.ready);
      setFirstRun(c.first_run);
      setDataDir(c.data_dir);
      setConnection(c.active_connection ? c.active_connection.name.toUpperCase() : "");
      setModel(c.active_connection?.model ?? "");
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
                   connection={connection} model={model}
                   onLeftSetup={setLeftSetupFor} />
          </PaletteProvider>
        </FocusProvider>
      </ShellStatusProvider>
    </ThemeProvider>
  );
}
