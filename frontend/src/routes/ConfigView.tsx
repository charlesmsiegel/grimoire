import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, type Config, type DataDirInfo, type LLMConnection, type Style } from "../api/client";
import { themeList } from "../theme/themes";
import { useTheme } from "../theme/ThemeProvider";

export default function ConfigView() {
  const { setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeConnectionId, setActiveConnectionId] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [defaultStyleId, setDefaultStyleId] = useState("");
  const [styleOptions, setStyleOptions] = useState<Style[]>([]);
  const [userLabel, setUserLabel] = useState("");
  const [assistantLabel, setAssistantLabel] = useState("");
  const [saved, setSaved] = useState(false);

  const [dataDir, setDataDir] = useState<DataDirInfo | null>(null);
  const [dataDirInput, setDataDirInput] = useState("");
  const [dataDirMsg, setDataDirMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setActiveConnectionId(c.active_connection_id);
      setSystemPrompt(c.system_prompt);
      setUserLabel(c.user_label);
      setAssistantLabel(c.assistant_label);
      setDefaultStyleId(c.default_style_id);
    });
    api.getDataDir().then((d) => {
      setDataDir(d);
      setDataDirInput(d.data_dir);
    });
    api.listStyles().then(setStyleOptions).catch(() => setStyleOptions([]));
    api.listConnections().then(setConnections).catch(() => setConnections([]));
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

  if (!config) return <div className="page page-narrow config">Loading…</div>;

  async function save(fields: Partial<{ theme: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; default_style_id: string; active_connection_id: string }>) {
    const next = await api.putConfig(fields);
    setConfig(next);
    setSaved(true);
    if (fields.theme) setTheme(fields.theme);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="page page-narrow view-anim config">
      <div className="page-head">
        <h1 className="page-h1">Configuration</h1>
      </div>

      <div className="section-label">Storage location</div>
      <p className="field-hint" style={{ marginTop: 0 }}>
        The folder where all worlds, campaigns, and settings live. Point it at a
        synced folder (Syncthing, Dropbox/Drive desktop, iCloud…) to share the
        same library across devices. Changes take effect immediately.
      </p>
      <div className="joined">
        <input
          id="cfg-data-dir"
          aria-label="Storage location"
          className="mono-input"
          placeholder={dataDir?.default ?? "~/.grimoire"}
          value={dataDirInput}
          disabled={dataDir?.source === "env"}
          onChange={(e) => setDataDirInput(e.target.value)}
        />
        <button
          className="btn-accent"
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
        <p className={dataDirMsg.kind === "err" ? "config-msg err" : "config-msg save-flash"}>
          {dataDirMsg.text}
        </p>
      )}

      <div className="section-label">LLM connection</div>
      <select
        aria-label="LLM connection"
        value={activeConnectionId}
        onChange={(e) => {
          setActiveConnectionId(e.target.value);
          save({ active_connection_id: e.target.value });
        }}
      >
        {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <p className="field-hint">
        Manage connections (add a custom OpenAI-compatible endpoint, edit keys, pull a
        model list) on the <Link to="/connections">Connections</Link> page.
      </p>

      <div className="section-label">System prompt</div>
      <label className="sr-only" htmlFor="cfg-system-prompt">
        System prompt (sent with every scene)
      </label>
      <textarea
        id="cfg-system-prompt"
        rows={4}
        placeholder="e.g. Never speak or act for the player character."
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
      />

      <div className="section-label">Default prose style</div>
      <select
        aria-label="Default prose style"
        value={defaultStyleId}
        onChange={(e) => setDefaultStyleId(e.target.value)}
      >
        <option value="">— none —</option>
        {styleOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>

      <div className="section-label">Transcript</div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          aria-label="Color quoted dialogue"
          checked={config.quote_color === "on"}
          onChange={(e) => save({ quote_color: e.target.checked ? "on" : "off" })}
        />
        Color quoted dialogue
      </label>
      <div className="field-row" style={{ marginTop: 12 }}>
        <div className="field">
          <label htmlFor="cfg-user-label">Your label</label>
          <input id="cfg-user-label" type="text" value={userLabel} placeholder="You"
                 onChange={(e) => setUserLabel(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-assistant-label">Narrator label</label>
          <input id="cfg-assistant-label" type="text" value={assistantLabel} placeholder="Grimoire"
                 onChange={(e) => setAssistantLabel(e.target.value)} />
        </div>
      </div>

      <div className="section-label">Theme</div>
      <div className="theme-cards">
        {themeList.map((t) => (
          <button
            key={t.name}
            className={"theme-card" + (config.theme === t.name ? " active" : "")}
            onClick={() => save({ theme: t.name })}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p style={{ marginTop: 24 }}>
        <button
          className="btn-accent"
          onClick={() => save({
            system_prompt: systemPrompt,
            user_label: userLabel, assistant_label: assistantLabel,
            default_style_id: defaultStyleId,
          })}
        >
          Save
        </button>
        {saved && <span className="save-flash" style={{ marginLeft: 12 }}>Saved ✓</span>}
      </p>
    </div>
  );
}
