import { useCallback, useEffect, useState } from "react";
import {
  api, STYLE_CLEAR, type LengthPreset, type ResponsePresetSummary,
  type ResponsePresetUsageEntry, type Style,
} from "../api/client";
import { Field } from "../components/Field";

type Knobs = {
  reply_words: string; blocks: string; paragraphs: string; speakers: string; blocks_per_speaker: string;
};

type FormState = {
  name: string; description: string; style_id: string;
  lengthMode: "preset" | "knobs";
  length_preset: string;
  knobs: Knobs;
};

const BLANK_KNOBS: Knobs = { reply_words: "", blocks: "", paragraphs: "", speakers: "", blocks_per_speaker: "" };
const BLANK: FormState = {
  name: "", description: "", style_id: "", lengthMode: "preset", length_preset: "", knobs: BLANK_KNOBS,
};

const KNOB_FIELDS: { key: keyof Knobs; label: string }[] = [
  { key: "reply_words", label: "Target words per reply" },
  { key: "blocks", label: "Max blocks per reply" },
  { key: "paragraphs", label: "Max paragraphs per block" },
  { key: "speakers", label: "Max speaking characters" },
  { key: "blocks_per_speaker", label: "Max blocks per character" },
];

function scopeNoun(scope: string): string {
  switch (scope) {
    case "global": return "The global default";
    case "campaign": return "Campaign";
    case "scene": return "Scene";
    default: return scope;
  }
}

/** What changed between an affected scope's before/after resolution, for the
 * delete-impact list — only the fields that actually differ, so a style-only
 * change doesn't get buried under five identical numbers. */
function describeChange(a: ResponsePresetUsageEntry): string {
  const parts: string[] = [];
  if (a.before.reply_words !== a.after.reply_words) {
    parts.push(`${a.before.reply_words ?? "—"} → ${a.after.reply_words ?? "—"} words per reply`);
  }
  if (a.before.blocks !== a.after.blocks) parts.push(`${a.before.blocks ?? "—"} → ${a.after.blocks ?? "—"} blocks`);
  if (a.before.paragraphs !== a.after.paragraphs) {
    parts.push(`${a.before.paragraphs ?? "—"} → ${a.after.paragraphs ?? "—"} paragraphs`);
  }
  if (a.before.speakers !== a.after.speakers) {
    parts.push(`${a.before.speakers ?? "—"} → ${a.after.speakers ?? "—"} speakers`);
  }
  if (a.before.blocks_per_speaker !== a.after.blocks_per_speaker) {
    parts.push(`${a.before.blocks_per_speaker ?? "—"} → ${a.after.blocks_per_speaker ?? "—"} blocks/speaker`);
  }
  if (a.before.style_id !== a.after.style_id) {
    parts.push(`style ${a.before.style_id || "no style"} → ${a.after.style_id || "no style"}`);
  }
  return parts.length ? parts.join(", ") : "no visible change";
}

const AFFECTED_CAP = 10;

export default function ResponsePresetsView() {
  const [presets, setPresets] = useState<ResponsePresetSummary[]>([]);
  const [styles, setStyles] = useState<Style[]>([]);
  const [lengthPresets, setLengthPresets] = useState<Record<string, LengthPreset>>({});
  const [pid, setPid] = useState<string | null>(null);
  const [builtIn, setBuiltIn] = useState(false);
  const [validity, setValidity] = useState<{ valid: boolean; issues: string[] } | null>(null);
  const [form, setForm] = useState<FormState>(BLANK);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // Three distinct states, and they must stay distinct: null = still checking,
  // "failed" = the lookup errored so the impact is UNKNOWN, an array = a real
  // answer (possibly empty). Collapsing "failed" into [] renders a confident
  // "nothing else changes" immediately before an irreversible delete.
  const [affected, setAffected] =
    useState<ResponsePresetUsageEntry[] | "failed" | null>(null);

  const reload = useCallback(() => api.listResponsePresets().then(setPresets).catch(() => setPresets([])), []);
  useEffect(() => {
    reload();
    api.listStyles().then((r) => setStyles(r ?? [])).catch(() => setStyles([]));
    api.listLengthPresets().then((r) => setLengthPresets(r ?? {})).catch(() => setLengthPresets({}));
  }, [reload]);

  function resetForm() {
    setPid(null);
    setBuiltIn(false);
    setValidity(null);
    setForm(BLANK);
    setConfirmingDelete(false);
    setAffected(null);
    setError(null);
    setMode("edit");
  }

  async function select(id: string) {
    setError(null);
    setConfirmingDelete(false);
    setAffected(null);
    let detail;
    try {
      detail = await api.getResponsePreset(id);
    } catch (err: any) {
      // An unreadable preset file must say so: without this the row click
      // does nothing visible and leaves an unhandled rejection behind.
      setError(err?.detail ?? String(err));
      return;
    }
    const { meta, validity: v } = detail;
    setPid(id);
    setBuiltIn(meta.built_in);
    setValidity(v);
    setForm({
      name: meta.name,
      description: meta.description ?? "",
      style_id: meta.style_id ?? "",
      lengthMode: meta.length_preset ? "preset" : "knobs",
      length_preset: meta.length_preset ?? "",
      knobs: {
        reply_words: meta.reply_words ?? "", blocks: meta.blocks ?? "", paragraphs: meta.paragraphs ?? "",
        speakers: meta.speakers ?? "", blocks_per_speaker: meta.blocks_per_speaker ?? "",
      },
    });
    setMode("view");
  }

  function setLengthMode(next: "preset" | "knobs") {
    // The record carries either a named preset or explicit knobs, never both —
    // switching modes clears the side that's no longer in play so a stale value
    // can't ride along into the next save.
    setForm((f) => next === "preset"
      ? { ...f, lengthMode: "preset", knobs: BLANK_KNOBS }
      : { ...f, lengthMode: "knobs", length_preset: "" });
  }

  function buildKnobs(k: Knobs): Record<string, number> {
    const out: Record<string, number> = {};
    for (const { key } of KNOB_FIELDS) {
      const raw = k[key].trim();
      const n = Number(raw);
      if (raw !== "" && Number.isFinite(n)) out[key] = n;
    }
    return out;
  }

  async function save() {
    if (!form.name.trim()) return;
    setError(null);
    const draft = {
      name: form.name,
      description: form.description,
      style_id: form.style_id,
      length_preset: form.lengthMode === "preset" ? form.length_preset : "",
      knobs: form.lengthMode === "knobs" ? buildKnobs(form.knobs) : null,
    };
    try {
      if (pid && !builtIn) {
        await api.updateResponsePreset(pid, draft);
        await reload();
        await select(pid);
      } else {
        const { id } = await api.createResponsePreset(draft);
        await reload();
        await select(id);
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function duplicate() {
    if (!pid) return;
    setError(null);
    try {
      const { id } = await api.duplicateResponsePreset(pid);
      await reload();
      await select(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  function startDelete() {
    if (!pid) return;
    setConfirmingDelete(true);
    setAffected(null);
    api.responsePresetUsage(pid)
      .then((r) => setAffected(r.affected ?? []))
      .catch(() => setAffected("failed"));
  }

  function cancelDelete() {
    setConfirmingDelete(false);
    setAffected(null);
  }

  async function confirmDelete() {
    if (!pid) return;
    setError(null);
    try {
      await api.deleteResponsePreset(pid);
      await reload();
      resetForm();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Response Presets</h1>
      </div>
      <div className="editor">
        <div className="editor-list">
          <button className="primary new" onClick={resetForm}>+ New preset</button>
          {presets.map((p) => (
            <button key={p.id} className={"row" + (pid === p.id ? " active" : "")} onClick={() => select(p.id)}>
              {p.name}
              {p.built_in && <span className="mark-badge" aria-hidden="true">built-in</span>}
            </button>
          ))}
        </div>

        <div className="editor-body">
          {error && <div className="banner">{error}</div>}

          {mode === "view" && pid ? (
            <div className="detail-view">
              <div className="detail-main">
                <h3>{form.name}</h3>
                {validity && !validity.valid && (
                  <div className="banner error-banner">This preset is broken — it supplies no fields.</div>
                )}
                {validity && validity.valid && validity.issues.length > 0 && (
                  <div className="field-hint">This preset is usable, but some fields are being skipped.</div>
                )}
                {form.description && <div className="detail-rendered">{form.description}</div>}
              </div>
              <aside className="detail-sidebar">
                <div className="form-actions">
                  {builtIn
                    ? <button className="subtle" onClick={duplicate}>Duplicate</button>
                    : <button className="subtle" onClick={() => setMode("edit")}>Edit</button>}
                </div>
                <div className="side-section">
                  <h4>Length</h4>
                  {form.length_preset
                    ? <span className="chip on">{form.length_preset}</span>
                    : <div className="field-hint">custom knobs</div>}
                </div>
                <div className="side-section">
                  <h4>Style</h4>
                  <span className="chip on">
                    {form.style_id === STYLE_CLEAR ? "no style (clears inherited)"
                      : form.style_id || "— none —"}
                  </span>
                </div>
                {validity && validity.issues.length > 0 && (
                  <div className="side-section">
                    <h4>Issues</h4>
                    <ul className="field-hint">
                      {validity.issues.map((issue, i) => <li key={i}>{issue}</li>)}
                    </ul>
                  </div>
                )}
              </aside>
            </div>
          ) : confirmingDelete ? (
            <div className="form">
              <h3>Delete "{form.name}"?</h3>
              {affected === null ? (
                <div className="field-hint">Checking affected scopes…</div>
              ) : affected === "failed" ? (
                <div className="banner error-banner" role="alert">
                  The impact of this delete could not be checked — campaigns or scenes
                  that inherit this preset may change, and this list is unknown.
                </div>
              ) : affected.length === 0 ? (
                <div className="field-hint">No campaigns or scenes inherit this preset — nothing else changes.</div>
              ) : (
                <div className="side-section">
                  <h4>This will change:</h4>
                  <ul>
                    {affected.slice(0, AFFECTED_CAP).map((a, i) => (
                      <li key={i}>{scopeNoun(a.scope)} <strong>{a.name}</strong>: {describeChange(a)}</li>
                    ))}
                  </ul>
                  {affected.length > AFFECTED_CAP && (
                    <div className="field-hint">…and {affected.length - AFFECTED_CAP} more.</div>
                  )}
                </div>
              )}
              <div className="form-actions">
                <button className="subtle" onClick={cancelDelete}>Cancel</button>
                <button className="primary" onClick={confirmDelete}>Confirm delete</button>
              </div>
            </div>
          ) : (
            <div className="form">
              <h3>{pid ? "Edit preset" : "New preset"}</h3>
              <Field label="Name">
                <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </Field>
              <Field label="Description">
                <input type="text" value={form.description}
                       onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </Field>
              <Field label="Style">
                <select value={form.style_id} onChange={(e) => setForm({ ...form, style_id: e.target.value })}>
                  <option value="">— none —</option>
                  <option value={STYLE_CLEAR}>— no style (clear inherited) —</option>
                  {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>

              <div className="field">
                <label>Length</label>
                <div className="joined">
                  <label>
                    <input type="radio" name="length-mode" checked={form.lengthMode === "preset"}
                           onChange={() => setLengthMode("preset")} />
                    Named preset
                  </label>
                  <label>
                    <input type="radio" name="length-mode" checked={form.lengthMode === "knobs"}
                           onChange={() => setLengthMode("knobs")} />
                    Custom knobs
                  </label>
                </div>
              </div>

              {form.lengthMode === "preset" ? (
                <Field label="Length preset">
                  <select value={form.length_preset}
                          onChange={(e) => setForm({ ...form, length_preset: e.target.value })}>
                    <option value="">— none —</option>
                    {Object.keys(lengthPresets).map((id) => <option key={id} value={id}>{id}</option>)}
                  </select>
                </Field>
              ) : (
                KNOB_FIELDS.map(({ key, label }) => (
                  <Field key={key} label={label}>
                    <input type="number" value={form.knobs[key]}
                           onChange={(e) => setForm({ ...form, knobs: { ...form.knobs, [key]: e.target.value } })} />
                  </Field>
                ))
              )}

              <div className="form-actions">
                {pid && !builtIn && <button className="subtle" onClick={startDelete}>Delete</button>}
                {pid && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
                <button className="primary" onClick={save} disabled={!form.name.trim()}>
                  {pid && !builtIn ? "Save preset" : "Create preset"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
