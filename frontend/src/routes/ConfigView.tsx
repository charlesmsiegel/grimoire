import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Config, type LLMConnection } from "../api/client";
import { ResponsePresetPicker } from "../components/ResponsePresetPicker";
import { StorageLocation } from "../components/StorageLocation";
import { ThemePicker } from "../components/ThemePicker";
import { useTheme } from "../theme/ThemeProvider";

export default function ConfigView() {
  const { setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeConnectionId, setActiveConnectionId] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userLabel, setUserLabel] = useState("");
  const [assistantLabel, setAssistantLabel] = useState("");
  const [llmTimeout, setLlmTimeout] = useState("");
  const [absorbBudget, setAbsorbBudget] = useState("");
  const [contextBudget, setContextBudget] = useState("");
  const [archiveDepth, setArchiveDepth] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setActiveConnectionId(c.active_connection_id);
      setSystemPrompt(c.system_prompt);
      setUserLabel(c.user_label);
      setAssistantLabel(c.assistant_label);
      setLlmTimeout(c.llm_timeout);
      setAbsorbBudget(c.absorb_budget);
      setContextBudget(c.context_budget);
      setArchiveDepth(c.archive_depth);
    });
    api.listConnections().then(setConnections).catch(() => setConnections([]));
  }, []);

  if (!config) return <div className="page page-narrow config">Loading…</div>;

  async function save(fields: Partial<{ theme: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; active_connection_id: string; llm_timeout: string; absorb_budget: string; context_budget: string; archive_depth: string }>) {
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
      <StorageLocation />

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

      <div className="section-label">Timeouts</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cfg-llm-timeout">No-reply timeout (seconds)</label>
          <input id="cfg-llm-timeout" type="text" inputMode="numeric" value={llmTimeout}
                 placeholder="120" onChange={(e) => setLlmTimeout(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-absorb-budget">Absorb budget (seconds)</label>
          <input id="cfg-absorb-budget" type="text" inputMode="numeric" value={absorbBudget}
                 placeholder="600" onChange={(e) => setAbsorbBudget(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        How long a generation may go without sending anything before it is abandoned, and
        how long one end-of-scene absorb (extraction, dossiers, mechanics audit) may take
        in total — past the budget, the remaining dossier refreshes are skipped and the
        audit reports as failed, leaving the absorb itself intact. Set either to
        <code> 0</code> to remove the limit, e.g. for a slow local endpoint.
      </p>

      <div className="section-label">Context</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cfg-context-budget">Context budget (tokens)</label>
          <input id="cfg-context-budget" type="text" inputMode="numeric" value={contextBudget}
                 placeholder="0" onChange={(e) => setContextBudget(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-archive-depth">Recalled scenes</label>
          <input id="cfg-archive-depth" type="text" inputMode="numeric" value={archiveDepth}
                 placeholder="3" onChange={(e) => setArchiveDepth(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        The token ceiling a scene's prompt is packed into. Over it, whole sections are
        dropped — recalled scenes first, then the older conversation, then the standing
        frame; the system prompts, the characters and the reply format are never dropped.
        The scene inspector shows what was cut. <code>0</code> means no ceiling, and
        nothing is ever dropped. Recalled scenes is how many older absorbed scenes a
        keyword match may pull back into context at once.
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

      <div className="section-label">Default response preset</div>
      <ResponsePresetPicker scope="global" />

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
      <ThemePicker value={config.theme} onPick={(theme) => save({ theme })} />

      <p style={{ marginTop: 24 }}>
        <button
          className="btn-accent"
          onClick={() => save({
            system_prompt: systemPrompt,
            user_label: userLabel, assistant_label: assistantLabel,
            llm_timeout: llmTimeout, absorb_budget: absorbBudget,
            context_budget: contextBudget, archive_depth: archiveDepth,
          })}
        >
          Save
        </button>
        {saved && <span className="save-flash" style={{ marginLeft: 12 }}>Saved ✓</span>}
      </p>
    </div>
  );
}
