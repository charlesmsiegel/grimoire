import { useEffect, useState } from "react";
import { ApiError, api, type Config, type DataDirInfo } from "../api/client";
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

  const [dataDir, setDataDir] = useState<DataDirInfo | null>(null);
  const [dataDirInput, setDataDirInput] = useState("");
  const [dataDirMsg, setDataDirMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
      setSystemPrompt(c.system_prompt);
    });
    api.getDataDir().then((d) => {
      setDataDir(d);
      setDataDirInput(d.data_dir);
    });
  }, []);

  async function saveDataDir(value: string | null) {
    setDataDirMsg(null);
    try {
      const next = await api.putDataDir(value);
      setDataDir(next);
      setDataDirInput(next.data_dir);
      setDataDirMsg({ kind: "ok", text: `Storage now at ${next.data_dir}` });
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "Could not update storage location";
      setDataDirMsg({ kind: "err", text: detail });
    }
  }

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

      <label htmlFor="cfg-data-dir">Storage location</label>
      <p className="field-hint" style={{ marginTop: 0 }}>
        The folder where all worlds, campaigns, and settings live. Point it at a
        synced folder (Syncthing, Dropbox/Drive desktop, iCloud…) to share the
        same library across devices. Changes take effect immediately.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          id="cfg-data-dir"
          style={{ flex: 1 }}
          placeholder={dataDir?.default ?? "~/.grimoire"}
          value={dataDirInput}
          disabled={dataDir?.source === "env"}
          onChange={(e) => setDataDirInput(e.target.value)}
        />
        <button
          onClick={() => saveDataDir(dataDirInput)}
          disabled={dataDir?.source === "env" || dataDirInput.trim() === dataDir?.data_dir}
        >
          Move
        </button>
      </div>
      {dataDir?.source === "env" && (
        <p className="field-hint">
          Set by the <code>GRIMOIRE_HOME</code> environment variable — unset it to edit here.
        </p>
      )}
      {dataDir && dataDir.source !== "env" && !dataDir.is_default && (
        <p className="field-hint">
          <button className="link" onClick={() => saveDataDir(null)}>
            Reset to default ({dataDir.default})
          </button>
        </p>
      )}
      {dataDirMsg && (
        <p style={{ color: dataDirMsg.kind === "err" ? "var(--danger, crimson)" : "var(--accent)" }}>
          {dataDirMsg.text}
        </p>
      )}

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
