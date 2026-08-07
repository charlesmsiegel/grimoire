import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Config, type ConfigUpdate, type LLMConnection } from "../api/client";
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
  const [llmCallBudget, setLlmCallBudget] = useState("");
  const [contextBudget, setContextBudget] = useState("");
  const [archiveDepth, setArchiveDepth] = useState("");
  const [promptLogDepth, setPromptLogDepth] = useState("");
  const [turnstateDepth, setTurnstateDepth] = useState("");
  const [promoteStreak, setPromoteStreak] = useState("");
  const [rollingEvery, setRollingEvery] = useState("");
  const [embeddingsConnectionId, setEmbeddingsConnectionId] = useState("");
  const [embeddingsModel, setEmbeddingsModel] = useState("");
  const [semanticDepth, setSemanticDepth] = useState("");
  const [semanticThreshold, setSemanticThreshold] = useState("");
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
      setLlmCallBudget(c.llm_call_budget);
      setContextBudget(c.context_budget);
      setArchiveDepth(c.archive_depth);
      setPromptLogDepth(c.prompt_log_depth);
      setTurnstateDepth(c.turnstate_depth);
      setPromoteStreak(c.promote_streak);
      setRollingEvery(c.rolling_summary_every);
      setEmbeddingsConnectionId(c.embeddings_connection_id);
      setEmbeddingsModel(c.embeddings_model);
      setSemanticDepth(c.semantic_recall_depth);
      setSemanticThreshold(c.semantic_recall_threshold);
    });
    api.listConnections().then(setConnections).catch(() => setConnections([]));
  }, []);

  if (!config) return <div className="page page-narrow config">Loading…</div>;

  async function save(fields: ConfigUpdate) {
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
        <div className="field">
          <label htmlFor="cfg-llm-call-budget">One-shot call ceiling (seconds)</label>
          <input id="cfg-llm-call-budget" type="text" inputMode="numeric" value={llmCallBudget}
                 placeholder="300" onChange={(e) => setLlmCallBudget(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        How long a generation may go without sending anything before it is abandoned, and
        how long one end-of-scene absorb (extraction, dossiers, mechanics audit) may take
        in total — past the budget, the remaining dossier refreshes are skipped and the
        audit reports as failed, leaving the absorb itself intact. Set either to
        <code> 0</code> to remove the limit, e.g. for a slow local endpoint.
      </p>
      <p className="field-hint">
        The call ceiling bounds one whole one-shot generation — a tagline, a voice
        anchor, scene suggestions — because a reply that keeps trickling in never trips
        the no-reply timeout above. Scene prose is deliberately exempt (a long reply you
        are already reading must not be cut off mid-sentence), and so is absorb, which
        the budget beside it already covers. <code>0</code> removes it.
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
        <div className="field">
          <label htmlFor="cfg-prompt-log-depth">Kept turn prompts</label>
          <input id="cfg-prompt-log-depth" type="text" inputMode="numeric" value={promptLogDepth}
                 placeholder="50" onChange={(e) => setPromptLogDepth(e.target.value)} />
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
      <p className="field-hint">
        Kept turn prompts is how many past turns each campaign keeps a frozen copy of
        the exact prompt for, readable from the scene inspector's Turn history. They
        hold whole prompts, so the count is per campaign rather than per scene — playing
        one scene for long enough ages out another's. <code>0</code> records none.
      </p>

      <div className="section-label">Transient state</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cfg-turnstate-depth">Tracked posts</label>
          <input id="cfg-turnstate-depth" type="text" inputMode="numeric" value={turnstateDepth}
                 placeholder="0" onChange={(e) => setTurnstateDepth(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-promote-streak">Promote after</label>
          <input id="cfg-promote-streak" type="text" inputMode="numeric" value={promoteStreak}
                 placeholder="3" onChange={(e) => setPromoteStreak(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        Asks the narrator to record each character's mood, intent and posture at the end of
        every reply — stripped from the transcript, never shown in the scene — and feeds the
        last few posts' worth back into the prompt. Tracked posts is how far back that reaches;
        <code> 0</code> turns the whole thing off, which is the default. Promote after is how
        many replies running a value has to hold before ending a scene offers it for the
        character's standing state, alongside the other proposed edits.
      </p>

      <div className="section-label">Semantic recall</div>
      <p className="field-hint" style={{ marginTop: 0 }}>
        World info activates on keywords. Semantic recall adds a second pass over the
        entries the keywords missed, picking the ones closest in meaning to what has just
        been said — so the lore about a character's inherited sword can surface when the
        scene talks about the blade her mother left her. It only ever adds, never removes,
        and lore owned by an absent character stays hidden either way. Leave the connection
        blank, or set entries to <code>0</code>, to turn it off.
      </p>
      <div className="field">
        <label htmlFor="cfg-embeddings-connection">Embeddings connection</label>
        <select
          id="cfg-embeddings-connection"
          value={embeddingsConnectionId}
          onChange={(e) => setEmbeddingsConnectionId(e.target.value)}
        >
          <option value="">Off</option>
          {connections.filter((c) => c.kind === "openai_compatible").map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>
      <div className="field-row" style={{ marginTop: 12 }}>
        <div className="field">
          <label htmlFor="cfg-embeddings-model">Embedding model</label>
          <input id="cfg-embeddings-model" type="text" value={embeddingsModel}
                 placeholder="text-embedding-3-small"
                 onChange={(e) => setEmbeddingsModel(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-semantic-depth">Recalled entries</label>
          <input id="cfg-semantic-depth" type="text" inputMode="numeric" value={semanticDepth}
                 placeholder="0" onChange={(e) => setSemanticDepth(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cfg-semantic-threshold">Similarity threshold</label>
          <input id="cfg-semantic-threshold" type="text" inputMode="decimal"
                 value={semanticThreshold} placeholder="0.4"
                 onChange={(e) => setSemanticThreshold(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        Only custom OpenAI-compatible connections can be used — OpenRouter and Claude serve
        no embeddings endpoint. What counts as "close enough" differs between embedding
        models, so tune the threshold (0 to 1) against the scene inspector, which shows what
        actually activated.
      </p>
      <p className="field-hint">
        <strong>This sends text to the endpoint above.</strong> Turning it on means recent
        scene text, and the world info being searched, go to that embeddings provider as
        well as to your LLM connection — a second place your campaign is read. Point it at
        a local endpoint to keep it on your machine.
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

      <div className="section-label">While playing</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cfg-rolling-every">Summarize the scene every (posts)</label>
          <input id="cfg-rolling-every" type="text" inputMode="numeric" value={rollingEvery}
                 placeholder="10" onChange={(e) => setRollingEvery(e.target.value)} />
        </div>
      </div>
      <p className="field-hint">
        The scene inspector keeps a running summary of the scene you are playing, refolded
        in the background once this many posts have landed since the last one. Each refresh
        is one extra model call, so this is what the feature costs. <code>0</code> turns the
        automatic refresh off — the inspector's own <em>Refresh</em> button still works. The
        summary is a reading aid only: it is never added to what the model is told.
      </p>

      <div className="section-label">Theme</div>
      <ThemePicker value={config.theme} onPick={(theme) => save({ theme })} />

      <p style={{ marginTop: 24 }}>
        <button
          className="btn-accent"
          onClick={() => save({
            system_prompt: systemPrompt,
            user_label: userLabel, assistant_label: assistantLabel,
            llm_timeout: llmTimeout, absorb_budget: absorbBudget,
            llm_call_budget: llmCallBudget,
            context_budget: contextBudget, archive_depth: archiveDepth,
            prompt_log_depth: promptLogDepth,
            turnstate_depth: turnstateDepth, promote_streak: promoteStreak,
            rolling_summary_every: rollingEvery,
            embeddings_connection_id: embeddingsConnectionId,
            embeddings_model: embeddingsModel,
            semantic_recall_depth: semanticDepth,
            semantic_recall_threshold: semanticThreshold,
          })}
        >
          Save
        </button>
        {saved && <span className="save-flash" style={{ marginLeft: 12 }}>Saved ✓</span>}
      </p>
    </div>
  );
}
