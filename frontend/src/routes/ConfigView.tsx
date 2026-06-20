import { useEffect, useState } from "react";
import { api, type Config } from "../api/client";
import { themeList } from "../theme/themes";
import { useTheme } from "../theme/ThemeProvider";
import ModelCombobox from "./ModelCombobox";

export default function ConfigView() {
  const { setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
    });
  }, []);

  if (!config) return <div className="config">Loading…</div>;

  async function save(fields: Partial<{ model: string; theme: string; openrouter_key: string }>) {
    const next = await api.putConfig(fields);
    setConfig(next);
    setKey("");
    setSaved(true);
    if (fields.theme) setTheme(fields.theme);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="config">
      <h2>Configuration</h2>

      <label>OpenRouter API key</label>
      <input
        type="password"
        placeholder={config.key_set ? "A key is set — type to replace" : "sk-or-…"}
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />

      <label>Model</label>
      <ModelCombobox value={model} onChange={setModel} />

      <label>Theme</label>
      <div className="theme-cards">
        {themeList.map((t) => (
          <div
            key={t.name}
            className={"theme-card" + (config.theme === t.name ? " active" : "")}
            onClick={() => save({ theme: t.name })}
          >
            {t.label}
          </div>
        ))}
      </div>

      <p style={{ marginTop: 24 }}>
        <button
          className="primary"
          onClick={() => save({ model, ...(key ? { openrouter_key: key } : {}) })}
        >
          Save
        </button>
        {saved && <span style={{ marginLeft: 12, color: "var(--accent)" }}>Saved</span>}
      </p>
    </div>
  );
}
