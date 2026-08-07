import { useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import NavSidebar from "./components/NavSidebar";
import { ShellStatusProvider } from "./components/ShellStatus";
import { onConfigChanged } from "./appEvents";
import { GlobalStatusBar } from "./components/StatusBar";
import CampaignsView from "./routes/CampaignsView";
import CampaignWizard from "./routes/CampaignWizard";
import CampaignView from "./routes/CampaignView";
import LibraryView from "./routes/LibraryView";
import WorldsView from "./routes/WorldsView";
import WorldView from "./routes/WorldView";
import ModulesView from "./routes/ModulesView";
import StyleGuidesView from "./routes/StyleGuidesView";
import ResponsePresetsView from "./routes/ResponsePresetsView";
import ClimatesView from "./routes/ClimatesView";
import ConnectionsView from "./routes/ConnectionsView";
import ConfigView from "./routes/ConfigView";
import SetupWizard from "./routes/SetupWizard";

const RAIL_KEY = "grimoire.nav.rail";
// Below this, a 208px sidebar is more than a third of the viewport. The
// Android shell packages this same frontend in a full-screen WebView, so a
// 360px portrait phone is a real target, not a hypothetical narrow desktop:
// there the expanded rail leaves ~150px of main column, which .page's own
// padding all but consumes.
const NARROW_PX = 640;

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

  const [topbarCollapsed, setTopbarCollapsed] = useState(
    () => localStorage.getItem("grimoire.topbar.collapsed") === "1");
  // Absent, not "0", is the third state: the user has never expressed a
  // preference, so the shell picks per route (below). Reading it as a string
  // rather than a boolean is what keeps that distinction.
  const [railPref, setRailPref] = useState<string | null>(() => localStorage.getItem(RAIL_KEY));
  // innerWidth rather than matchMedia so the reading is the same one the CSS
  // gets and jsdom needs no shim. Event-driven either way -- no polling.
  const [narrow, setNarrow] = useState(() => window.innerWidth <= NARROW_PX);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth <= NARROW_PX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  // "new" satisfies [^/]+ exactly like a real campaign id would, so the
  // regex alone can't distinguish /campaigns/new (the wizard, not a
  // campaign detail page) from /campaigns/<cid> — exclude it explicitly.
  // The scene segment is part of the same page (#87), so it collapses the
  // topbar too; without it the play view grew a topbar back the moment it
  // redirected itself to a scene URL.
  const isCampaignRoute = location.pathname !== "/campaigns/new" &&
    /^\/campaigns\/[^/]+(\/scenes\/[^/]+)?$/.test(location.pathname);

  // Two independent controls, because they answer different questions. The
  // rail is "how wide should the nav be", and a campaign page is the one place
  // the transcript is worth the width, so that is the untouched default there.
  // Hiding is "get the nav out of the way entirely" — CampaignView's own
  // ▴ Nav toggle, unchanged, and still campaign-only.
  // A narrow viewport joins the campaign route as a reason to *default* to the
  // rail, so the app does not open crushed on a phone before the reader has
  // found the toggle. It does not override a stated preference: someone who
  // expanded it asked for that, and quietly re-collapsing on every render
  // would be the control not working.
  const rail = railPref === null ? (isCampaignRoute || narrow) : railPref === "1";
  const navHidden = isCampaignRoute && topbarCollapsed;

  function toggleTopbar() {
    setTopbarCollapsed((v) => {
      localStorage.setItem("grimoire.topbar.collapsed", v ? "0" : "1");
      return !v;
    });
  }

  function toggleRail() {
    const next = rail ? "0" : "1";
    localStorage.setItem(RAIL_KEY, next);
    setRailPref(next);
  }

  useEffect(() => {
    api.getConfig()
      .then((c) => { setTheme(c.theme); setFirstRun(c.first_run); setDataDir(c.data_dir); })
      .catch(() => setTheme(DEFAULT_THEME));
  }, []);

  // The routed page is swapped inside a scroll port that itself never
  // unmounts, so without this a click on the persistent sidebar opens the
  // destination at the offset the previous page was left at -- heading off
  // screen, often near the bottom. Keyed to pathname only: a search/hash
  // change is the same page.
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => { mainRef.current?.scrollTo(0, 0); }, [location.pathname]);

  // Navigation is not the only thing that changes what the bar should say:
  // /config switches the active connection and /connections edits its model,
  // both without moving the pathname. Leaving the bar naming the old
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
    // one, so without this the older response lands second and reverts the bar
    // to the connection it just stopped describing -- and, now that first-run
    // rides along, could re-arm the wizard redirect from a stale read. Same
    // guard the sidebar's fetch uses, for the same reason.
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
        <header className={"topbar" + (navHidden ? " collapsed" : "")}>
          <NavLink to="/" className="brand">
            <img src="/grimoire-128.png" alt="" width={30} height={30} />
            <span>✦ GRIMOIRE</span>
          </NavLink>
          <div className="topbar-right">
            <NavLink to="/config" className={({ isActive }) => "config-link" + (isActive ? " active" : "")}>
              Config
            </NavLink>
          </div>
        </header>
        <div className="shell">
          {!navHidden && <NavSidebar rail={rail} onToggleRail={toggleRail} />}
          <main className="shell-main" ref={mainRef}>
            <Routes>
              {/* A fresh install lands on the wizard instead of an empty campaigns
                  list. Only `/` is redirected: every other route stays reachable, so
                  the sidebar is an escape hatch and a deep link is never hijacked.
                  The two guards are exact opposites of one `inSetup`, which is what
                  keeps them from bouncing a redirect back and forth. Gating
                  `/welcome` too is what stops a reload part-way through the wizard —
                  after a world exists, so the server no longer calls it a first run —
                  from restarting at step one and creating a second world. */}
              <Route path="/" element={inSetup ? <Navigate to="/welcome" replace /> : <CampaignsView />} />
              <Route path="/welcome" element={
                inSetup
                  ? <SetupWizard onDone={(dir) => setLeftSetupFor(dir ?? dataDir)} />
                  : <Navigate to="/" replace />} />
              <Route path="/campaigns/new" element={<CampaignWizard ready={ready} />} />
              {/* Keyed so a campaign→campaign move remounts. The sidebar's
                  Recent list made that transition reachable for the first
                  time: it stays on this route and only changes the param, so
                  React reuses the component, and CampaignView's [cid] effect
                  refetches without synchronously dropping the scene list,
                  transcript or activeId. Until those land -- or forever, if
                  they fail -- it would show campaign A's scene while its
                  handlers carry B's cid, and scene ids repeat across
                  campaigns. The key is the campaign segment alone and NOT the
                  whole pathname: this route matches deeper now (#87, below),
                  and keying on the full path would remount on every scene
                  jump -- exactly what the nested child exists to prevent. */}
              {/* The play view answers to two paths — with and without a scene
                  (#87) — and they have to resolve to the SAME element
                  instance. Sibling routes would remount CampaignView on
                  /campaigns/A/scenes/s1 → /campaigns/B but not on
                  A/scenes/s1 → B/scenes/s2, so the stale-response guards the
                  view is built around (cidRef, the window token) would hold in
                  one direction and be bypassed in the other. Nesting keeps one
                  instance for every combination. The child renders nothing —
                  CampaignView has no <Outlet /> — and exists only to put
                  `:sid` in the matched path, where useMatch can read it. */}
              <Route path="/campaigns/:cid" element={
                <CampaignView key={location.pathname.split("/").slice(0, 3).join("/")}
                              ready={ready}
                              topbarCollapsed={topbarCollapsed} onToggleTopbar={toggleTopbar} />}>
                <Route path="scenes/:sid" element={null} />
              </Route>
              <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
              <Route path="/library" element={<LibraryView />} />
              <Route path="/worlds" element={<WorldsView />} />
              <Route path="/worlds/:wid" element={<WorldView />} />
              <Route path="/modules" element={<ModulesView />} />
              <Route path="/styles" element={<StyleGuidesView />} />
              <Route path="/response-presets" element={<ResponsePresetsView />} />
              <Route path="/climates" element={<ClimatesView />} />
              <Route path="/connections" element={<ConnectionsView />} />
              <Route path="/config" element={<ConfigView />} />
            </Routes>
          </main>
        </div>
        <GlobalStatusBar connection={connection} ready={ready} model={model} />
      </ShellStatusProvider>
    </ThemeProvider>
  );
}
