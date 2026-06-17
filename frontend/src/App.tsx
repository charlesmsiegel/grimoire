import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import ChatView from "./routes/ChatView";
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
        <Link to="/config">Config</Link>
      </div>
      <Routes>
        <Route path="/" element={<ChatView keySet={keySet} />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
