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
  const [systemPrompt, setSystemPrompt] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
      setSystemPrompt(c.system_prompt);
    });
  }, []);

  if (!config) return <div className="config">Loading…</div>;

  async function save(fields: Partial<{ model: string; theme: string; openrouter_key: string; system_prompt: string; quote_color: string }>) {
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

      <label htmlFor="cfg-system-prompt">System prompt (sent with every scene)</label>
      <textarea
        id="cfg-system-prompt"
        rows={4}
        placeholder="e.g. Never speak or act for the player character."
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
      />

      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          aria-label="Color quoted dialogue"
          checked={config.quote_color === "on"}
          onChange={(e) => save({ quote_color: e.target.checked ? "on" : "off" })}
        />
        Color quoted dialogue
      </label>

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
          onClick={() => save({ model, system_prompt: systemPrompt, ...(key ? { openrouter_key: key } : {}) })}
        >
          Save
        </button>
        {saved && <span style={{ marginLeft: 12, color: "var(--accent)" }}>Saved</span>}
      </p>
    </div>
  );
}
