import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, type Config, type ConfigUpdate, type LLMConnection, type PromptLayoutSection,
  type SceneContext,
} from "../api/client";
import { BackupsPanel } from "../components/BackupsPanel";
import { ContextBudgetBar } from "../components/ContextBudgetBar";
import { ColumnSection, PageShell } from "../components/PageShell";
import { PromptLayoutEditor } from "../components/PromptLayoutEditor";
import { ResponsePresetPicker } from "../components/ResponsePresetPicker";
import { StorageLocation } from "../components/StorageLocation";
import { StoreConflictNotice } from "../components/StoreConflictNotice";
import { ThemePicker } from "../components/ThemePicker";
import { normalizeMode } from "../theme/themes";
import { useTheme } from "../theme/ThemeProvider";

/** Every config field this page edits. One list, because it is what the draft
 *  is built from, what the dirty count is counted over, and what Save sends —
 *  three copies of the same list of names is how a field gets added to the
 *  form and quietly never saved. */
const DRAFT_FIELDS = [
  "active_connection_id", "fallback_connection_id", "llm_retries",
  "llm_timeout", "absorb_budget", "llm_call_budget",
  "context_budget", "archive_depth", "prompt_log_depth", "offscene_known_limit",
  "speaker_turn_taking", "prompt_layout_enabled",
  "turnstate_depth", "promote_streak",
  "embeddings_connection_id", "embeddings_model",
  "semantic_recall_depth", "semantic_recall_threshold",
  "system_prompt",
  "quote_color", "user_label", "assistant_label",
  "rolling_summary_every", "scene_break_every",
  "backup_enabled", "backup_interval_hours", "backup_keep", "backup_dir",
  "theme",
] as const;
type DraftField = (typeof DRAFT_FIELDS)[number];
type Draft = Record<DraftField, string>;

/** The saved state, in the shape the form edits. `??` guards a store written
 *  by an older build that has never heard of one of these keys: absent, it
 *  would make the field permanently dirty against `undefined` and send
 *  `undefined` back on every Save. */
function draftOf(c: Config): Draft {
  const d = {} as Draft;
  for (const f of DRAFT_FIELDS) d[f] = c[f] ?? "";
  // The one field that is not stored as it is edited. A store from the
  // three-theme era still holds `codex`/`manuscript`/`astral`, which matches no
  // segment of the picker — normalized here so the baseline the dirty count
  // compares against is the value the control can actually show, and an
  // untouched legacy theme does not read as an unsaved change.
  d.theme = normalizeMode(c.theme);
  return d;
}

type SectionId =
  | "storage" | "backups" | "connection" | "timeouts"
  | "context" | "layout" | "transient" | "semantic" | "system-prompt" | "response"
  | "transcript" | "playing" | "appearance";

/** The column, as data: three groups, twelve sections, and which draft fields
 *  each one owns — the last part is what lets a section carry an unsaved dot,
 *  so the footer's count is always findable rather than being a number about
 *  somewhere else. */
type SectionDef = { id: SectionId; group: string; label: string; fields: DraftField[] };
const SECTIONS: SectionDef[] = [
  { id: "storage", group: "The install", label: "Storage", fields: [] },
  { id: "backups", group: "The install", label: "Backups",
    fields: ["backup_enabled", "backup_interval_hours", "backup_keep", "backup_dir"] },
  { id: "connection", group: "The install", label: "Connection",
    fields: ["active_connection_id", "fallback_connection_id", "llm_retries"] },
  { id: "timeouts", group: "The install", label: "Timeouts",
    fields: ["llm_timeout", "absorb_budget", "llm_call_budget"] },
  { id: "context", group: "What the model sees", label: "Context",
    fields: ["context_budget", "archive_depth", "prompt_log_depth",
             "offscene_known_limit", "speaker_turn_taking"] },
  { id: "layout", group: "What the model sees", label: "Prompt layout",
    fields: ["prompt_layout_enabled"] },
  { id: "transient", group: "What the model sees", label: "Transient state",
    fields: ["turnstate_depth", "promote_streak"] },
  { id: "semantic", group: "What the model sees", label: "Semantic recall",
    fields: ["embeddings_connection_id", "embeddings_model",
             "semantic_recall_depth", "semantic_recall_threshold"] },
  { id: "system-prompt", group: "What the model sees", label: "System prompt",
    fields: ["system_prompt"] },
  { id: "response", group: "What the model sees", label: "Response preset", fields: [] },
  { id: "transcript", group: "What you see", label: "Transcript",
    fields: ["quote_color", "user_label", "assistant_label"] },
  { id: "playing", group: "What you see", label: "While playing",
    fields: ["rolling_summary_every", "scene_break_every"] },
  { id: "appearance", group: "What you see", label: "Appearance", fields: ["theme"] },
];
const GROUPS = SECTIONS.reduce<string[]>(
  (out, s) => (out.includes(s.group) ? out : [...out, s.group]), []);

/** One numeric setting. A component rather than nineteen copies of the same
 *  five lines — and declared at module scope, not inside `ConfigView`, because
 *  a component defined during render is a new type every render and React
 *  would remount the input on each keystroke, taking the caret with it. */
function NumField(
  { id, label, value, placeholder, unit, caption, decimal = false, onChange }: {
    id: string; label: string; value: string; placeholder?: string;
    unit?: string; caption?: string; decimal?: boolean;
    onChange: (next: string) => void;
  },
) {
  return (
    <div className="config-field">
      <label htmlFor={id}>{label}</label>
      <div className="config-input">
        <input id={id} type="text" inputMode={decimal ? "decimal" : "numeric"}
               value={value} placeholder={placeholder}
               onChange={(e) => onChange(e.target.value)} />
        {unit && <span className="config-unit" aria-hidden>{unit}</span>}
      </div>
      {caption && <p className="config-caption">{caption}</p>}
    </div>
  );
}

/** Whether two layouts would store the same thing. Compared field by field
 *  rather than by JSON string so a key-order change in the API response cannot
 *  read as an edit the reader never made. */
function sameLayout(a: PromptLayoutSection[], b: PromptLayoutSection[]) {
  return a.length === b.length && a.every((row, i) =>
    row.id === b[i].id && row.label === b[i].label && row.enabled === b[i].enabled);
}

/** What the context bar is drawn from: a prompt some campaign actually built,
 *  and which campaign that was. */
type Probe = { ctx: SceneContext; campaign: string } | null;

/** Find one real prompt to draw the context bar against.
 *
 *  There is no campaign-independent source for this. Every context breakdown
 *  the store holds is scoped to a scene of a campaign — `GET
 *  /campaigns/{cid}/scenes/{sid}/context` for the live composition, and the
 *  frozen per-turn snapshots beside it — and Config is not inside a campaign.
 *  So the honest answer to "the last prompt" is the newest snapshot of the
 *  most recently played campaign's newest scene, NAMED as that campaign's, and
 *  nothing at all when there isn't one. It is never computed here: the numbers
 *  come out of the prompt log exactly as the packer wrote them.
 *
 *  Three chained reads, so it is deliberately not part of the page load: only
 *  the Context section shows the bar, and only that section pays for it. Any
 *  failure — no campaigns, a store with `prompt_log_depth` at 0, an api double
 *  that never heard of these routes — is the same answer as no data, because
 *  the alternative to a bar is no bar, never a made-up one. */
async function lastPrompt(): Promise<Probe> {
  try {
    const campaigns = await api.listCampaigns();
    // `activity` folds in the newest scene; `updated` alone only moves on
    // metadata writes. Same ranking the campaigns shelf uses.
    const stamp = (c: { activity?: string; updated: string }) => c.activity || c.updated || "";
    const newest = [...campaigns].sort((a, b) => stamp(b).localeCompare(stamp(a)))[0];
    if (!newest) return null;
    const scenes = await api.listScenes(newest.id);   // newest first
    if (!scenes.length) return null;
    const { entries } = await api.listScenePrompts(newest.id, scenes[0].id);   // newest first
    if (!entries.length) return null;
    const snapshot = await api.getScenePrompt(newest.id, scenes[0].id, entries[0].id);
    return { ctx: snapshot, campaign: newest.name };
  } catch {
    return null;
  }
}

export default function ConfigView() {
  const { mode: themeMode, setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [section, setSection] = useState<SectionId>("storage");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<Probe>(null);
  // Bumped when the store pointer moves, to remount anything describing the
  // old library rather than leave it showing a report about a folder the app
  // is no longer using.
  const [storeEpoch, setStoreEpoch] = useState(0);
  // A ref, not state: as state it would be a dependency of the effect that sets
  // it, so flipping it would re-run the effect and its cleanup would cancel the
  // read it had just started — the bar would never arrive.
  const probeStarted = useRef(false);
  /** The prompt layout is edited HERE rather than inside `PromptLayoutEditor`,
   *  which is presentational. The panel used to own its own draft and its own
   *  Save button, and that made the page lie: reorder a section, and the footer
   *  still read "no unsaved changes" while Save — the page's one Save — wrote
   *  everything except the reordering the reader had just done. One dirty
   *  count, one Save, or the affordance is a trap. */
  const [layout, setLayout] = useState<PromptLayoutSection[] | null>(null);
  const [layoutSaved, setLayoutSaved] = useState<PromptLayoutSection[] | null>(null);
  const [layoutFailed, setLayoutFailed] = useState(false);
  const layoutStarted = useRef(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setDraft(draftOf(c));
    });
    api.listConnections().then(setConnections).catch(() => setConnections([]));
  }, []);

  // Fetched when the Context section is first opened, not on mount: it is
  // three round trips into a campaign for one bar, and ten of the eleven
  // sections never show it.
  //
  // No liveness guard on the way back: this is a one-shot read of a record
  // that cannot change while the page is open, so there is no later response
  // for a stale one to install over, and a late `setProbe` on an unmounted
  // component is a no-op.
  useEffect(() => {
    if (section !== "context" || probeStarted.current) return;
    probeStarted.current = true;
    lastPrompt().then(setProbe);
  }, [section]);

  // Same lazy rule as the probe: thirty rows fetched when the Prompt layout
  // section is first opened, never on mount.
  useEffect(() => {
    if (section !== "layout" || layoutStarted.current) return;
    layoutStarted.current = true;
    api.getPromptLayout()
      .then((l) => { setLayout(l.sections); setLayoutSaved(l.sections); })
      .catch(() => setLayoutFailed(true));
  }, [section]);

  const saved = config ? draftOf(config) : null;
  const dirty = draft && saved ? DRAFT_FIELDS.filter((f) => draft[f] !== saved[f]) : [];
  /** The layout counts as ONE unsaved change however many rows moved — it is a
   *  single stored document, and a count of moved rows would be a number the
   *  reader cannot act on row by row. */
  const layoutDirty = !!layout && !!layoutSaved && !sameLayout(layout, layoutSaved);
  const dirtyCount = dirty.length + (layoutDirty ? 1 : 0);
  const dirtyIn = (s: SectionDef) =>
    s.fields.some((f) => dirty.includes(f)) || (s.id === "layout" && layoutDirty);

  function edit(field: DraftField, value: string) {
    setDraft((d) => (d ? { ...d, [field]: value } : d));
  }

  /** The theme is the one field that has to take effect before it is saved —
   *  a look you cannot see until you commit it is a control you cannot use.
   *  So it is applied to the DOM and held in the draft, and only the draft is
   *  what Save writes; Revert puts the preview back with everything else. */
  function pickTheme(mode: string) {
    edit("theme", mode);
    setTheme(mode);
  }

  function revert() {
    if (!saved) return;
    setDraft(saved);
    setTheme(saved.theme);
    setLayout(layoutSaved);
    setError(null);
  }

  async function save() {
    if (!draft || !dirtyCount || busy) return;
    setBusy(true);
    setError(null);
    // Only the fields that actually changed: a whole-form PUT would carry
    // nineteen values into an unlocked read-modify-write of one file and
    // overwrite anything another tab (or the Connections page) moved while
    // this form sat open.
    const patch: ConfigUpdate = {};
    for (const f of dirty) patch[f] = draft[f];
    const sent = draft;
    // The layout first, and only when it changed: it is a separate document at
    // a separate route, and PUTting it on every Save would rewrite thirty rows
    // because someone edited a timeout.
    //
    // Its own try, deliberately: the settings catch below puts the theme
    // preview back, which is the right repair for a failed config write and a
    // non-sequitur for a failed layout one — the theme is not in this request.
    if (layoutDirty && layout) {
      try {
        const stored = await api.putPromptLayout(layout.map(
          (r) => ({ id: r.id, label: r.label, enabled: r.enabled })));
        setLayout(stored.sections);
        setLayoutSaved(stored.sections);
      } catch (e: any) {
        setError(e?.detail ?? "Could not save the prompt layout");
        setBusy(false);
        return;   // the settings are not written either: one Save, one outcome
      }
    }
    // Nothing more to do when only the layout moved: an empty patch is a
    // read-modify-write of config.md that stores no field.
    if (!dirty.length) {
      setBusy(false);
      return;
    }
    try {
      const next = await api.putConfig(patch);
      setConfig(next);
      // The new baseline is what the server says it stored — but only the
      // fields nobody touched while the write was in flight. Save disables its
      // own buttons, not the inputs, so a keystroke landing during the request
      // is a real edit, and adopting the response wholesale would silently
      // throw it away.
      setDraft((d) => {
        const merged = draftOf(next);
        if (d) for (const f of DRAFT_FIELDS) if (d[f] !== sent[f]) merged[f] = d[f];
        return merged;
      });
      // Reconcile the preview with what was actually stored: if the server
      // normalized or refused the theme, the screen must stop showing a look
      // nothing on disk agrees with.
      setTheme(normalizeMode(next.theme));
    } catch (e: any) {
      setError(e?.detail ?? "Could not save these settings");
      // Same reason in the other direction — a theme left applied after a
      // failed write looks chosen for the session and is gone at reload.
      if (saved) setTheme(saved.theme);
    } finally {
      setBusy(false);
    }
  }

  const current = SECTIONS.find((s) => s.id === section)!;
  const recallOff = !draft?.embeddings_connection_id || draft.semantic_recall_depth === "0";
  /** The fallback as the picker can show it: a saved id equal to the active
   *  connection is no longer offered as an option, and the backend drops it
   *  anyway, so it reads as None. */
  const fallbackShown =
    draft && draft.fallback_connection_id !== draft.active_connection_id
      ? draft.fallback_connection_id : "";

  const column = (
    <>
      <div className="column-head">
        <div className="eyebrow">Configuration</div>
        {config && <div className="column-head-sub">{config.data_dir}</div>}
      </div>
      {GROUPS.map((group) => (
        <ColumnSection key={group} label={group}>
          {SECTIONS.filter((s) => s.group === group).map((s) => (
            <button key={s.id} className={"column-row" + (s.id === section ? " active" : "")}
                    onClick={() => setSection(s.id)}>
              <span className="column-row-label">
                {s.label}
                {/* The dot is the state, so the state is also spelled out: a
                    colour is not a label, and this row is the only place the
                    connection reports itself on this page. */}
                {s.id === "connection" && config && (
                  <>
                    <span className={"conn-dot " + (config.ready ? "ok" : "off")} aria-hidden> ●</span>
                    <span className="sr-only">{config.ready ? " ready" : " no key set"}</span>
                  </>
                )}
                {s.id === "semantic" && recallOff && <span className="column-row-off"> off</span>}
                {s.id === "backups" && draft?.backup_enabled !== "on" &&
                  <span className="column-row-off"> off</span>}
              </span>
              {dirtyIn(s) && (
                <>
                  <span className="sr-only">unsaved</span>
                  <span className="column-row-dirty" aria-hidden>●</span>
                </>
              )}
            </button>
          ))}
        </ColumnSection>
      ))}
    </>
  );

  // The only theme control on the page, and it is pinned rather than living in
  // the Appearance section: it is the one setting whose effect is the whole
  // screen, so it wants to be reachable — and visible — from whichever section
  // you happen to be reading. A second copy inside Appearance would be the
  // same state rendered twice, one of them always off screen.
  const footer = <ThemePicker value={draft?.theme ?? themeMode} onPick={pickTheme} disabled={busy} />;

  // `config-shell`, not the old `config`: this page's CSS is written against the
  // redesign's tokens, and the legacy `.config …` block is a whole set of input
  // rules from the long-scroll era that would out-specify half of it.
  return (
    <PageShell column={column} footer={footer} columnLabel="Settings" className="config-shell">
      <div className="page-wide view-anim config-page">
        <div className="eyebrow">{current.group}</div>
        <h1 className="screen-title">{current.label}</h1>

        {error && <div className="banner error-banner">{error}</div>}
        {!draft && <p className="empty-state">Loading…</p>}

        {draft && section === "storage" && (
          <>
            <StorageLocation onMoved={() => setStoreEpoch((n) => n + 1)} />
            <p className="field-hint">
              The one setting on this page that does not wait for Save: moving the
              library moves the file this form writes to, so a draft held back for
              it would land in whichever store the pointer named by the time you
              pressed Save.
            </p>
            {/* Remounted on a move: the notice describes one library, and the
                pointer now names a different one. */}
            <StoreConflictNotice key={storeEpoch} />
          </>
        )}

        {draft && section === "backups" && (
          <>
            <p className="config-copy">
              A backup is the whole library zipped into one file — worlds, campaigns,
              scenes, settings. Everything grimoire knows is plain files under the
              storage location, so an archive is a complete restore point and nothing
              else has to be running to use it. The rebuildable thumbnail cache and the
              backups folder itself are left out.
            </p>
            <p className="config-copy">
              Automatic backups happen while the app is running and nowhere else: it
              checks hourly whether the newest archive is older than the interval. They
              are <strong>off</strong> until you turn them on, because each archive is a
              second copy of your library and the count below multiplies it — and if your
              storage location is a synced folder, every archive is uploaded whole. Point
              the folder somewhere outside the library to avoid that.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Back up automatically"
                checked={draft.backup_enabled === "on"}
                onChange={(e) => edit("backup_enabled", e.target.checked ? "on" : "off")}
              />
              Back up automatically
            </label>
            <div className="config-fields">
              <NumField id="cfg-backup-interval" label="Every" unit="hours"
                        placeholder="24" caption="checked against the newest archive"
                        value={draft.backup_interval_hours}
                        onChange={(v) => edit("backup_interval_hours", v)} />
              <NumField id="cfg-backup-keep" label="Keep" unit="archives"
                        placeholder="7" caption="0 = keep every one"
                        value={draft.backup_keep}
                        onChange={(v) => edit("backup_keep", v)} />
              <div className="config-field">
                <label htmlFor="cfg-backup-dir">Backup folder</label>
                <div className="config-input">
                  <input id="cfg-backup-dir" type="text" className="mono-input"
                         value={draft.backup_dir}
                         placeholder={`${config?.data_dir ?? ""}/backups`}
                         onChange={(e) => edit("backup_dir", e.target.value)} />
                </div>
                <p className="config-caption">blank = inside the library</p>
              </div>
            </div>
            {/* The saved value, not the draft: the list below is the folder the
                server is actually writing to, and re-reading it against a field
                nobody has saved yet would show an empty directory as if it were
                the state of your backups. */}
            <BackupsPanel dir={config?.backup_dir ?? ""} />
          </>
        )}

        {draft && section === "connection" && (
          <>
            <p className="config-copy">
              Which connection every scene, absorb and one-shot call goes to. Manage
              connections (add a custom OpenAI-compatible endpoint, edit keys, pull a
              model list) on the <Link to="/connections">Connections</Link> page.
            </p>
            <p className="config-copy">
              A call that fails for a passing reason — a rate limit, a dropped
              connection — is re-sent up to the retry count, with a growing pause
              between tries. Only ever <em>before</em> the reply starts arriving: once
              text is on screen it is never re-requested, because a second attempt
              would repeat what you have already read. <code>0</code> retries sends
              once and reports the failure.
            </p>
            <p className="config-copy">
              If the connection still cannot answer, the fallback gets one attempt.
              It is a whole connection, not just another model name, so it can be an
              entirely different provider — which also means it can be a bad key or a
              deleted connection, and a fallback that cannot send is simply not used.
            </p>
            <div className="config-fields">
              <div className="config-field">
                <label htmlFor="cfg-connection">LLM connection</label>
                <select id="cfg-connection" aria-label="LLM connection"
                        value={draft.active_connection_id}
                        onChange={(e) => edit("active_connection_id", e.target.value)}>
                  {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                {/* The SELECTED connection's key, not `config.ready`: the
                    latter describes the one on disk, which is the wrong one to
                    report the moment this select has been changed and not yet
                    saved — exactly when someone is looking at this line. */}
                <p className="config-caption">
                  {connections.find((c) => c.id === draft.active_connection_id)?.key_set
                    ? "key set"
                    : "no key set — scenes will not send"}
                </p>
              </div>
              <NumField id="cfg-llm-retries" label="Retries" placeholder="2"
                        caption="0 = send once, then report the failure"
                        value={draft.llm_retries}
                        onChange={(v) => edit("llm_retries", v)} />
              <div className="config-field">
                <label htmlFor="cfg-fallback-connection">Fallback connection</label>
                <select id="cfg-fallback-connection" aria-label="Fallback connection"
                        value={fallbackShown}
                        onChange={(e) => edit("fallback_connection_id", e.target.value)}>
                  <option value="">None</option>
                  {connections.filter((c) => c.id !== draft.active_connection_id)
                              .map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                {/* The active connection is filtered out of the list rather
                    than merely ignored by the backend: offering it would look
                    like a working setting, and falling back to the connection
                    that just failed is a third attempt wearing a different
                    name.

                    Hence `fallbackShown` rather than the draft value: a saved
                    fallback that has since been promoted to active matches no
                    option, and a <select> whose value matches no option
                    renders *blank* — not "None", not the stale name, nothing.
                    Shown as None, which is what it now behaves as. The draft
                    keeps the id, so it is neither silently rewritten on disk
                    nor lost if the active connection changes back. */}
                <p className="config-caption">
                  {fallbackShown
                    ? "tried once when the connection above is exhausted"
                    : "no fallback — an exhausted connection is an error"}
                </p>
              </div>
            </div>
          </>
        )}

        {draft && section === "timeouts" && (
          <>
            <p className="config-copy">
              How long a generation may go without sending anything before it is abandoned, and
              how long one end-of-scene absorb (extraction, dossiers, mechanics audit) may take
              in total — past the budget, the remaining dossier refreshes are skipped and the
              audit reports as failed, leaving the absorb itself intact. Set either to
              <code> 0</code> to remove the limit, e.g. for a slow local endpoint.
            </p>
            <p className="config-copy">
              The call ceiling bounds one whole one-shot generation — a tagline, a voice
              anchor, scene suggestions — because a reply that keeps trickling in never trips
              the no-reply timeout above. Scene prose is deliberately exempt (a long reply you
              are already reading must not be cut off mid-sentence), and so is absorb, which
              the budget beside it already covers. <code>0</code> removes it.
            </p>
            <div className="config-fields">
              <NumField id="cfg-llm-timeout" label="No-reply timeout" unit="seconds"
                        placeholder="120" value={draft.llm_timeout}
                        onChange={(v) => edit("llm_timeout", v)} />
              <NumField id="cfg-absorb-budget" label="Absorb budget" unit="seconds"
                        placeholder="600" value={draft.absorb_budget}
                        onChange={(v) => edit("absorb_budget", v)} />
              <NumField id="cfg-llm-call-budget" label="One-shot call ceiling" unit="seconds"
                        placeholder="300" value={draft.llm_call_budget}
                        onChange={(v) => edit("llm_call_budget", v)} />
            </div>
          </>
        )}

        {draft && section === "context" && (
          <>
            <p className="config-copy">
              The token ceiling a scene's prompt is packed into. Over it, whole sections are
              dropped — recalled scenes first, then the older conversation, then the standing
              frame; the system prompts, the characters and the reply format are never dropped.
              The scene inspector shows what was cut. <code>0</code> means no ceiling, and
              nothing is ever dropped. Recalled scenes is how many older absorbed scenes a
              keyword match may pull back into context at once.
            </p>
            <p className="config-copy">
              Kept turn prompts is how many past turns each campaign keeps a frozen copy of
              the exact prompt for, readable from the scene inspector's Turn history. They
              hold whole prompts, so the count is per campaign rather than per scene — playing
              one scene for long enough ages out another's. <code>0</code> records none.
            </p>
            <p className="config-copy">
              Named off-scene characters bounds the one-line directory of characters the
              campaign can see but has never cast. It grows with the world rather than with
              the story, so on a large library it quietly eats the budget everything below it
              is competing for. Past the ceiling, the characters the present cast's own cards
              mention are kept first and the rest are left out — the scene inspector prices
              the tier on its own row, so you can see what it costs before changing this.{" "}
              <code>0</code> names every one of them.
            </p>
            <div className="config-fields">
              <NumField id="cfg-context-budget" label="Context budget" unit="tokens"
                        placeholder="0" caption="0 = no ceiling" value={draft.context_budget}
                        onChange={(v) => edit("context_budget", v)} />
              <NumField id="cfg-archive-depth" label="Recalled scenes" placeholder="3"
                        caption="older scenes a keyword match may pull back"
                        value={draft.archive_depth}
                        onChange={(v) => edit("archive_depth", v)} />
              <NumField id="cfg-prompt-log-depth" label="Kept turn prompts" placeholder="50"
                        caption="per campaign, not per scene" value={draft.prompt_log_depth}
                        onChange={(v) => edit("prompt_log_depth", v)} />
              <NumField id="cfg-offscene-known-limit" label="Named off-scene characters"
                        placeholder="40" caption="0 = name every one of them"
                        value={draft.offscene_known_limit}
                        onChange={(v) => edit("offscene_known_limit", v)} />
            </div>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Name an active speaker in group scenes"
                checked={draft.speaker_turn_taking === "on"}
                onChange={(e) => edit("speaker_turn_taking", e.target.checked ? "on" : "off")}
              />
              Name an active speaker in group scenes
            </label>
            <p className="config-copy">
              With three or more characters in a scene, every card goes into the prompt and the
              model decides for itself who answers — which is how one character monologues for
              three turns while the others stand there. This names one to carry each turn: whoever
              the last post spoke to, or failing that whoever has been quiet longest. It is worked
              out from the transcript each turn and stored nowhere, so a reroll picks the same
              character rather than advancing a rotation you never saw. It adds a short section to
              every group-scene prompt, and does nothing in a scene with fewer than two characters.
            </p>
            {probe && (
              <>
                <ContextBudgetBar
                  ctx={probe.ctx}
                  label={`LAST TURN IN ${probe.campaign.toUpperCase()}, AGAINST THIS BUDGET`}
                />
                <p className="config-copy">
                  The bar is the last prompt that campaign actually built — the numbers
                  already exist in the context builder, and reading them here is what makes
                  the budget field mean something before you spend a turn finding out.
                  {probe.ctx.budget_tokens !== Number(draft.context_budget) && (
                    <> It was packed to a ceiling of{" "}
                      {probe.ctx.budget_tokens > 0
                        ? probe.ctx.budget_tokens.toLocaleString()
                        : "no"}{" "}
                      tokens, which is not what the field above says now.</>
                  )}
                </p>
              </>
            )}
          </>
        )}

        {draft && section === "layout" && (
          <>
            <p className="config-copy">
              The system message is assembled from about thirty sections in a fixed order — the
              system prompts, the character cards, the world info, the reply format. This is that
              order, and yours to change: move a section, or switch one off to stop sending it at
              all. Everything here is off until you turn it on, and turning it off again keeps
              the layout, so you can put a change back without rebuilding it.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Use my section order"
                checked={draft.prompt_layout_enabled === "on"}
                onChange={(e) => edit("prompt_layout_enabled", e.target.checked ? "on" : "off")}
              />
              Use my section order
            </label>
            <PromptLayoutEditor
              rows={layout} failed={layoutFailed} busy={busy}
              onChange={setLayout}
              onReset={async () => {
                setBusy(true);
                try {
                  const stored = await api.putPromptLayout([]);
                  setLayout(stored.sections);
                  setLayoutSaved(stored.sections);
                } catch {
                  setLayoutFailed(true);
                } finally {
                  setBusy(false);
                }
              }} />
            <p className="config-copy">
              The tier beside each section is the order the budget packer drops things in when a
              prompt will not fit, and it is not editable — recalled lore sits below the recalled
              scenes on purpose, so that a semantic hit can only ever add to a prompt and never
              push something else out of it. Where a section sits in the message and what gives
              way under pressure are two different questions.
            </p>
            <p className="config-copy">
              To change what a section <em>says</em>, edit its template in <code>templates/</code>
              — they are read from disk, so a saved edit is live on the next turn.
            </p>
          </>
        )}

        {draft && section === "transient" && (
          <>
            <p className="config-copy">
              Asks the narrator to record each character's mood, intent and posture at the end of
              every reply — stripped from the transcript, never shown in the scene — and feeds the
              last few posts' worth back into the prompt. Tracked posts is how far back that reaches;
              <code> 0</code> turns the whole thing off, which is the default. Promote after is how
              many replies running a value has to hold before ending a scene offers it for the
              character's standing state, alongside the other proposed edits.
            </p>
            <div className="config-fields">
              <NumField id="cfg-turnstate-depth" label="Tracked posts" placeholder="0"
                        caption="0 = off" value={draft.turnstate_depth}
                        onChange={(v) => edit("turnstate_depth", v)} />
              <NumField id="cfg-promote-streak" label="Promote after" unit="replies"
                        placeholder="3" value={draft.promote_streak}
                        onChange={(v) => edit("promote_streak", v)} />
            </div>
          </>
        )}

        {draft && section === "semantic" && (
          <>
            <p className="config-copy">
              World info activates on keywords. Semantic recall adds a second pass over the
              entries the keywords missed, picking the ones closest in meaning to what has just
              been said — so the lore about a character's inherited sword can surface when the
              scene talks about the blade her mother left her. It only ever adds, never removes,
              and lore owned by an absent character stays hidden either way. Leave the connection
              blank, or set entries to <code>0</code>, to turn it off.
            </p>
            <div className="config-fields">
              <div className="config-field">
                <label htmlFor="cfg-embeddings-connection">Embeddings connection</label>
                <select id="cfg-embeddings-connection" value={draft.embeddings_connection_id}
                        onChange={(e) => edit("embeddings_connection_id", e.target.value)}>
                  <option value="">Off</option>
                  {connections.filter((c) => c.kind === "openai_compatible").map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                <p className="config-caption">openai-compatible endpoints only</p>
              </div>
              <div className="config-field">
                <label htmlFor="cfg-embeddings-model">Embedding model</label>
                <div className="config-input">
                  <input id="cfg-embeddings-model" type="text" value={draft.embeddings_model}
                         placeholder="text-embedding-3-small"
                         onChange={(e) => edit("embeddings_model", e.target.value)} />
                </div>
              </div>
              <NumField id="cfg-semantic-depth" label="Recalled entries" placeholder="0"
                        caption="0 = off" value={draft.semantic_recall_depth}
                        onChange={(v) => edit("semantic_recall_depth", v)} />
              <NumField id="cfg-semantic-threshold" label="Similarity threshold" decimal
                        placeholder="0.4" caption="0 to 1" value={draft.semantic_recall_threshold}
                        onChange={(v) => edit("semantic_recall_threshold", v)} />
            </div>
            <p className="config-copy">
              Only custom OpenAI-compatible connections can be used — OpenRouter and Claude serve
              no embeddings endpoint. What counts as "close enough" differs between embedding
              models, so tune the threshold (0 to 1) against the scene inspector, which shows what
              actually activated.
            </p>
            <p className="config-copy">
              <strong>This sends text to the endpoint above.</strong> Turning it on means recent
              scene text, and the world info being searched, go to that embeddings provider as
              well as to your LLM connection — a second place your campaign is read. Point it at
              a local endpoint to keep it on your machine.
            </p>
          </>
        )}

        {draft && section === "system-prompt" && (
          <>
            <p className="config-copy">
              Sent with every scene, ahead of the characters and the reply format — the
              standing instruction the narrator is never allowed to forget.
            </p>
            <label className="sr-only" htmlFor="cfg-system-prompt">
              System prompt (sent with every scene)
            </label>
            <textarea
              id="cfg-system-prompt"
              rows={6}
              className="config-textarea"
              placeholder="e.g. Never speak or act for the player character."
              value={draft.system_prompt}
              onChange={(e) => edit("system_prompt", e.target.value)}
            />
          </>
        )}

        {draft && section === "response" && (
          <>
            <p className="config-copy">
              The default length and style every campaign inherits, and what a campaign,
              a scene or a single turn overrides. This block writes its own record rather
              than a config field, so — unlike everything else here — it saves as you set it.
            </p>
            <ResponsePresetPicker scope="global" />
          </>
        )}

        {draft && section === "transcript" && (
          <>
            <p className="config-copy">
              How the scene reads back to you. None of this reaches the model: the labels
              name the two voices in the transcript you are looking at.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Color quoted dialogue"
                checked={draft.quote_color === "on"}
                onChange={(e) => edit("quote_color", e.target.checked ? "on" : "off")}
              />
              Color quoted dialogue
            </label>
            <div className="config-fields">
              <div className="config-field">
                <label htmlFor="cfg-user-label">Your label</label>
                <div className="config-input">
                  <input id="cfg-user-label" type="text" value={draft.user_label} placeholder="You"
                         onChange={(e) => edit("user_label", e.target.value)} />
                </div>
              </div>
              <div className="config-field">
                <label htmlFor="cfg-assistant-label">Narrator label</label>
                <div className="config-input">
                  <input id="cfg-assistant-label" type="text" value={draft.assistant_label}
                         placeholder="Grimoire"
                         onChange={(e) => edit("assistant_label", e.target.value)} />
                </div>
              </div>
            </div>
          </>
        )}

        {draft && section === "playing" && (
          <>
            <p className="config-copy">
              The scene inspector keeps a running summary of the scene you are playing, refolded
              in the background once this many posts have landed since the last one. Each refresh
              is one extra model call, so this is what the feature costs. <code>0</code> turns the
              automatic refresh off — the inspector's own <em>Refresh</em> button still works. The
              summary is a reading aid only: it is never added to what the model is told.
            </p>
            <p className="config-copy">
              It also watches for the moment a scene has run its course — a long stretch of
              posts, a move, a jump in the clock — and asks the model whether that is really
              where the scene ends. The number below is only how often it may look; the
              signals still have to agree before anything reaches the model, so it costs
              well under one call per that many posts. It never ends or splits a scene: the
              answer is a suggestion in the inspector, and yours to take or wave off.
              <code>0</code> turns it off, panel and all.
            </p>
            <div className="config-fields">
              <NumField id="cfg-rolling-every" label="Summarize the scene every"
                        unit="posts" placeholder="10" caption="0 = only on demand"
                        value={draft.rolling_summary_every}
                        onChange={(v) => edit("rolling_summary_every", v)} />
              <NumField id="cfg-break-every" label="Scene-break check, at most every"
                        unit="posts" placeholder="20" caption="0 = off"
                        value={draft.scene_break_every}
                        onChange={(v) => edit("scene_break_every", v)} />
            </div>
          </>
        )}

        {draft && section === "appearance" && (
          <p className="config-copy">
            One theme in two modes. <strong>System</strong> follows whatever the machine
            says it is right now, and changes with it; Light and Dark hold. The control is
            pinned at the foot of the list beside this — it applies as you click it so you
            can see what you are choosing, and like everything else here it is not written
            to disk until you Save.
          </p>
        )}
      </div>

      <div className="config-bar">
        <span className={"config-dirty" + (dirtyCount ? " on" : "")}>
          {dirtyCount
            ? `${dirtyCount} unsaved ${dirtyCount === 1 ? "change" : "changes"}`
            : "No unsaved changes"}
        </span>
        <div className="config-bar-actions">
          <button className="btn-outline" onClick={revert} disabled={!dirtyCount || busy}>
            Revert
          </button>
          <button className="btn-accent" onClick={save} disabled={!dirtyCount || busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </PageShell>
  );
}
