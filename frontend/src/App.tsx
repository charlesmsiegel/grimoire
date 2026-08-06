import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import CampaignsView from "./routes/CampaignsView";
import CampaignWizard from "./routes/CampaignWizard";
import CampaignView from "./routes/CampaignView";
import WorldsView from "./routes/WorldsView";
import WorldView from "./routes/WorldView";
import ModulesView from "./routes/ModulesView";
import StyleGuidesView from "./routes/StyleGuidesView";
import ResponsePresetsView from "./routes/ResponsePresetsView";
import ClimatesView from "./routes/ClimatesView";
import ConnectionsView from "./routes/ConnectionsView";
import ConfigView from "./routes/ConfigView";
import SetupWizard from "./routes/SetupWizard";

export default function App() {
  const [theme, setTheme] = useState<string | null>(null);
  // Settled by the same boot fetch that supplies the theme, and the theme
  // already gates the first render — so this needs no request of its own and
  // no route is ever chosen against a not-yet-known answer (#194). It is state
  // rather than a re-read because the wizard reports its own completion:
  // refetching to notice would race the navigation away from it.
  const [firstRun, setFirstRun] = useState(false);
  const [ready, setReady] = useState(false);
  const [activeLabel, setActiveLabel] = useState("NO CONNECTION");

  const location = useLocation();

  const [topbarCollapsed, setTopbarCollapsed] = useState(
    () => localStorage.getItem("grimoire.topbar.collapsed") === "1");
  // "new" satisfies [^/]+ exactly like a real campaign id would, so the
  // regex alone can't distinguish /campaigns/new (the wizard, not a
  // campaign detail page) from /campaigns/<cid> — exclude it explicitly.
  const isCampaignRoute = location.pathname !== "/campaigns/new" &&
    /^\/campaigns\/[^/]+$/.test(location.pathname);

  function toggleTopbar() {
    setTopbarCollapsed((v) => {
      localStorage.setItem("grimoire.topbar.collapsed", v ? "0" : "1");
      return !v;
    });
  }

  useEffect(() => {
    api.getConfig()
      .then((c) => { setTheme(c.theme); setFirstRun(c.first_run); })
      .catch(() => setTheme(DEFAULT_THEME));
  }, []);

  useEffect(() => {
    api.getConfig().then((c) => {
      setReady(c.ready);
      setActiveLabel(c.active_connection ? c.active_connection.name.toUpperCase() : "NO CONNECTION");
    });
  }, [location.pathname]);

  if (theme === null) return null;

  return (
    <ThemeProvider initial={theme}>
      <header className={"topbar" + (isCampaignRoute && topbarCollapsed ? " collapsed" : "")}>
        <NavLink to="/" className="brand">
          <img src="/grimoire-128.png" alt="" width={30} height={30} />
          <span>✦ GRIMOIRE</span>
        </NavLink>
        <nav>
          <NavLink to="/" end className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Campaigns
          </NavLink>
          <NavLink to="/worlds" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Worlds
          </NavLink>
          <NavLink to="/modules" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Modules
          </NavLink>
          <NavLink to="/styles" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Styles
          </NavLink>
          <NavLink to="/response-presets" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Response Presets
          </NavLink>
          <NavLink to="/climates" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Climates
          </NavLink>
          <NavLink to="/connections" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Connections
          </NavLink>
        </nav>
        <div className="topbar-right">
          <span className="status">
            <span className="dot">●</span> {activeLabel} · {ready ? "CONNECTED" : "NOT READY"}
          </span>
          <span className="divider" />
          <NavLink to="/config" className={({ isActive }) => "config-link" + (isActive ? " active" : "")}>
            Config
          </NavLink>
        </div>
      </header>
      <Routes>
        {/* A fresh install lands on the wizard instead of an empty campaigns
            list. Only `/` is redirected: every other route stays reachable, so
            the topbar is an escape hatch and a deep link is never hijacked. */}
        <Route path="/" element={firstRun ? <Navigate to="/welcome" replace /> : <CampaignsView />} />
        <Route path="/welcome" element={<SetupWizard onDone={() => setFirstRun(false)} />} />
        <Route path="/campaigns/new" element={<CampaignWizard ready={ready} />} />
        <Route path="/campaigns/:cid" element={
          <CampaignView ready={ready} topbarCollapsed={topbarCollapsed} onToggleTopbar={toggleTopbar} />} />
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
        <Route path="/worlds" element={<WorldsView />} />
        <Route path="/worlds/:wid" element={<WorldView />} />
        <Route path="/modules" element={<ModulesView />} />
        <Route path="/styles" element={<StyleGuidesView />} />
        <Route path="/response-presets" element={<ResponsePresetsView />} />
        <Route path="/climates" element={<ClimatesView />} />
        <Route path="/connections" element={<ConnectionsView />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
