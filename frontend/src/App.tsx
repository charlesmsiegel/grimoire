import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
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
      <div className="topbar">
        <Link to="/" style={{ fontWeight: 600 }}>
          ✦ grimoire
        </Link>
        <nav>
          <Link to="/">Campaigns</Link>
          <Link to="/worlds">Worlds</Link>
          <Link to="/config">Config</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<CampaignsView />} />
        <Route path="/campaigns/new" element={<CampaignWizard keySet={keySet} />} />
        <Route path="/campaigns/:cid" element={<CampaignView keySet={keySet} />} />
        <Route path="/worlds" element={<WorldsView />} />
        <Route path="/worlds/:wid" element={<WorldView />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
