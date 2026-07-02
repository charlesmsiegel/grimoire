import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import CampaignsView from "./routes/CampaignsView";
import CampaignWizard from "./routes/CampaignWizard";
import CampaignView from "./routes/CampaignView";
import WorldsView from "./routes/WorldsView";
import WorldView from "./routes/WorldView";
import ConfigView from "./routes/ConfigView";

export default function App() {
  const [theme, setTheme] = useState<string | null>(null);
  const [keySet, setKeySet] = useState(false);

  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setTheme(c.theme);
        setKeySet(c.key_set);
      })
      .catch(() => setTheme(DEFAULT_THEME));
  }, []);

  if (theme === null) return null;

  return (
    <ThemeProvider initial={theme}>
      <header className="topbar">
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
        </nav>
        <div className="topbar-right">
          <span className="status">
            <span className="dot">●</span> OPENROUTER · {keySet ? "CONNECTED" : "NO KEY"}
          </span>
          <span className="divider" />
          <NavLink to="/config" className={({ isActive }) => "config-link" + (isActive ? " active" : "")}>
            Config
          </NavLink>
        </div>
      </header>
      <Routes>
        <Route path="/" element={<CampaignsView />} />
        <Route path="/campaigns/new" element={<CampaignWizard keySet={keySet} />} />
        <Route path="/campaigns/:cid" element={<CampaignView keySet={keySet} />} />
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
        <Route path="/worlds" element={<WorldsView />} />
        <Route path="/worlds/:wid" element={<WorldView />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
