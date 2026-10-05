import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, type SamplerImportReport, type SamplerParams,
  type SamplerParamSpec, type SamplerPreset,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "./Field";

/** Named sampler presets: temperature, top-p and the rest, saved as JSON under
 *  the data directory and attached to a connection or a route elsewhere.
 *
 *  The list/detail editor CLAUDE.md asks of every record list: a rail of
 *  presets, a read-only view with an Edit step, and `+ New` straight to the
 *  form. Saves itself, like the pricing table beside it on the Settings page:
 *  it writes its own files through its own routes, and folding it into the
 *  config draft would make one Save mean two writes. */

/** A stop string as the form shows it: control characters spelled as escapes,
 *  so `\nYou:` — the most common stop string there is — survives a one-per-line
 *  textarea. Backslash first, or the escapes it introduces would be doubled. */
export function encodeStop(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\t/g, "\\t");
}

export function decodeStop(s: string): string {
  return s.replace(/\\(\\|n|t)/g, (_m, c: string) => (c === "n" ? "\n" : c === "t" ? "\t" : "\\"));
}

/** The form's state: every number as the string typed, so an empty box stays
 *  empty (unset) instead of becoming a 0 nobody meant. */
type Draft = { name: string; notes: string; values: Record<string, string> };

const BLANK: Draft = { name: "", notes: "", values: {} };

function toDraft(p: SamplerPreset): Draft {
  const values: Record<string, string> = {};
  for (const [k, v] of Object.entries(p.params)) {
    values[k] = Array.isArray(v) ? v.map(encodeStop).join("\n") : String(v);
  }
  return { name: p.name, notes: p.notes, values };
}

/** The draft as the params the server stores. Blank means unset. A number that
 *  is not one is sent as typed, so the server's 400 names the parameter. */
function toParams(d: Draft, table: SamplerParamSpec[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const spec of table) {
    const typed = (d.values[spec.name] ?? "").trim();
    if (!typed) continue;
    if (spec.kind === "stop") {
      const lines = (d.values[spec.name] ?? "").split("\n").filter((l) => l !== "");
      if (lines.length) out[spec.name] = lines.map(decodeStop);
    } else {
      const n = Number(typed);
      out[spec.name] = Number.isFinite(n) ? n : typed;
    }
  }
  return out;
}

function shown(v: unknown): string {
  return Array.isArray(v) ? v.map((s) => JSON.stringify(s)).join(", ") : String(v);
}

/** What an import did with the file, one list per outcome. */
function ImportReport({ report }: { report: SamplerImportReport }) {
  return (
    <div className="sampler-import-report" aria-label="Import report">
      {report.mapped.length > 0 && (
        <p className="field-hint">
          <strong>Imported:</strong>{" "}
          {report.mapped.map((m) => `${m.param} ← ${m.from}`).join(", ")}
        </p>
      )}
      {report.neutral.length > 0 && (
        <p className="field-hint">
          <strong>Left unset (the sampler's off position):</strong>{" "}
          {report.neutral.map((m) => m.from).join(", ")}
        </p>
      )}
      {report.skipped.map((m) => (
        <p className="field-hint" key={m.from}>
          <strong>Skipped {m.from}</strong> ({shown(m.value)}): {m.why}.
        </p>
      ))}
      {report.invalid.map((m) => (
        <p className="field-hint" key={`${m.key}:${m.why}`}>
          <strong>Not imported, {m.key}:</strong> {m.why}
        </p>
      ))}
      {report.unmapped.length > 0 && (
        <p className="field-hint">
          <strong>No grimoire equivalent:</strong> {report.unmapped.join(", ")}
        </p>
      )}
      {report.notes.map((n) => <p className="field-hint" key={n}>{n}</p>)}
    </div>
  );
}

export function SamplerPresetEditor() {
  const [presets, setPresets] = useState<SamplerPreset[]>([]);
  const [table, setTable] = useState<SamplerParamSpec[]>([]);
  const [pid, setPid] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit" | "import">("view");
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<SamplerImportReport | null>(null);
  // The import form: the file as parsed, the name to save it under, and
  // whether to bring its response length along (off: see the hint).
  const [file, setFile] = useState<{ name: string; data: unknown } | null>(null);
  const [importName, setImportName] = useState("");
  const [withMax, setWithMax] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  /** Which file pick is current; a slower read of an earlier pick is ignored. */
  const picked = useRef(0);

  const reload = useCallback(() => api.listSamplerPresets().then((r) => {
    setPresets(r.presets);
    setTable(r.params);
    return r.presets;
  }), []);

  useEffect(() => {
    reload().catch((err: unknown) => setError(errorText(err)));
  }, [reload]);

  const current = presets.find((p) => p.id === pid) ?? null;

  function open(id: string) {
    setError(null);
    setReport(null);
    setPid(id);
    setMode("view");   // records open read-only
  }

  function startNew() {
    setError(null);
    setReport(null);
    setPid(null);
    setDraft(BLANK);
    setMode("edit");   // `+ New` goes straight to the form
  }

  /** Retire any file read still in flight: its result belongs to an import
   *  session that has ended, and must not fill in the next one. */
  function retireReads() {
    picked.current += 1;
  }

  function startImport() {
    retireReads();
    setError(null);
    setReport(null);
    setPid(null);
    setFile(null);
    setImportName("");
    setWithMax(false);
    setMode("import");
  }

  function startEdit() {
    if (!current) return;
    setDraft(toDraft(current));
    setMode("edit");
  }

  async function save() {
    if (!draft.name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body = { name: draft.name, notes: draft.notes,
                     params: toParams(draft, table) as SamplerParams };
      const saved = pid ? await api.updateSamplerPreset(pid, body)
                        : await api.createSamplerPreset(body);
      await reload();
      open(saved.id);
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current) return;
    if (!window.confirm(`Delete the sampler preset “${current.name}”?`)) return;
    setBusy(true);
    try {
      await api.deleteSamplerPreset(current.id);
      setPid(null);
      await reload();
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function pickFile(f: File | undefined) {
    setError(null);
    // Only the latest pick may land: a large file read first can finish after
    // a second pick (or a cleared input), and without this it would overwrite
    // the selection the input now shows -- importing the wrong preset under
    // the right name.
    const pick = (picked.current += 1);
    if (!f) { setFile(null); return; }
    // FileReader rather than `Blob.text()`: the same result, and it is the one
    // every browser the Android WebView ships with has had for longest.
    const reader = new FileReader();
    reader.onload = () => {
      if (pick !== picked.current) return;
      try {
        setFile({ name: f.name,
                  data: JSON.parse(typeof reader.result === "string" ? reader.result : "") });
        setImportName((n) => n || f.name.replace(/\.json$/i, ""));
      } catch {
        setFile(null);
        setError(`${f.name} is not a JSON file`);
      }
    };
    reader.onerror = () => { if (pick === picked.current) setError(`Could not read ${f.name}`); };
    reader.readAsText(f);
  }

  async function runImport() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const got = await api.importSamplerPreset({
        name: importName.trim() || file.name, data: file.data, include_max_tokens: withMax });
      await reload();
      setPid(got.preset.id);
      setMode("view");
      setReport(got.report);   // after `open`'s reset, so it stays on screen
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={startNew}>+ New preset</button>
        <button className="subtle new" onClick={startImport}>Import from SillyTavern…</button>
        {presets.map((p) => (
          <button key={p.id} className={"row" + (pid === p.id ? " active" : "")}
                  onClick={() => open(p.id)}>
            {p.name}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}

        {mode === "view" && !current && (
          <p className="empty-state">
            {presets.length ? "Pick a preset to read it." : "No sampler presets yet."}
          </p>
        )}

        {mode === "view" && current && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{current.name}</h3>
              {report && <ImportReport report={report} />}
              <div className="detail-rendered">
                {Object.keys(current.params).length === 0 ? (
                  <p>Sets nothing: every parameter is left at the provider's default.</p>
                ) : (
                  <table className="sampler-params">
                    <tbody>
                      {table.filter((t) => t.name in current.params).map((t) => (
                        <tr key={t.name}>
                          <th scope="row">{t.label}</th>
                          <td>{shown(current.params[t.name])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {current.notes && (
                  <Markdown remarkPlugins={[remarkGfm]}>{current.notes}</Markdown>
                )}
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={startEdit}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Source</h4>
                <span className="chip on">
                  {current.source === "sillytavern" ? "Imported from SillyTavern" : "Made here"}
                </span>
              </div>
              <div className="side-section">
                <h4>Where it applies</h4>
                <span className="field-hint">
                  Attach it to a connection on the Connections page, or to a job
                  under Model routing — globally here, or per campaign in the scene
                  inspector.
                </span>
              </div>
              <div className="side-section">
                <button className="subtle danger" onClick={() => void remove()}
                        disabled={busy}>Delete</button>
              </div>
            </aside>
          </div>
        )}

        {mode === "edit" && (
          <div className="sampler-form">
            <Field label="Name">
              <input type="text" value={draft.name}
                     onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <p className="field-hint">
              Leave a box blank to leave that parameter at the provider's default.
              A parameter the connection's backend cannot take is not sent, and the
              routing picker and scene inspector say which.
            </p>
            {table.map((spec) => spec.kind === "stop" ? (
              <Field key={spec.name} label={spec.label}
                     hint={`One per line, at most ${spec.max_entries}. Write a line break as \\n.`}>
                <textarea rows={3} value={draft.values[spec.name] ?? ""}
                          onChange={(e) => setDraft({ ...draft,
                            values: { ...draft.values, [spec.name]: e.target.value } })} />
              </Field>
            ) : (
              <Field key={spec.name} label={spec.label}
                     hint={spec.name === "max_tokens"
                       ? "Caps every call this preset reaches — absorb and dossiers included — and a reply cut off by it is kept as if complete. On a reasoning model, many providers count the model's thinking against this cap too, so a cap sized for the prose can be spent before the reply starts. Response targets (words and paragraphs) are the tool for prose length."
                       : `${spec.min} to ${spec.max}`}>
                <input type="number" inputMode="decimal"
                       step={spec.kind === "int" ? 1 : "any"}
                       min={spec.min} max={spec.max}
                       value={draft.values[spec.name] ?? ""}
                       onChange={(e) => setDraft({ ...draft,
                         values: { ...draft.values, [spec.name]: e.target.value } })} />
              </Field>
            ))}
            <Field label="Notes">
              <textarea rows={3} value={draft.notes}
                        onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            </Field>
            <div className="form-actions">
              <button className="primary" onClick={() => void save()}
                      disabled={busy || !draft.name.trim()}>Save</button>
              <button className="subtle" onClick={() => setMode("view")}>Cancel</button>
            </div>
          </div>
        )}

        {mode === "import" && (
          <div className="sampler-form">
            <h3>Import a SillyTavern preset</h3>
            <p className="field-hint">
              Chat Completion and Text Completion presets both work. What maps is
              imported; everything else is listed afterwards so you can see what
              did not come across.
            </p>
            <Field label="Preset file">
              <input ref={fileInput} type="file" accept=".json,application/json"
                     onChange={(e) => pickFile(e.target.files?.[0])} />
            </Field>
            <Field label="Save as">
              <input type="text" value={importName}
                     onChange={(e) => setImportName(e.target.value)} />
            </Field>
            <label className="checkbox-row">
              <input type="checkbox" checked={withMax}
                     onChange={(e) => setWithMax(e.target.checked)} />
              {" "}Include the response length (max tokens)
            </label>
            <p className="field-hint">
              Off by default: SillyTavern writes a response length into every
              preset, and as a hard cap it would cut off absorb and dossiers as
              well as prose — and on a reasoning model the thinking may count
              against it too, leaving little or nothing for the reply.
            </p>
            <div className="form-actions">
              <button className="primary" onClick={() => void runImport()}
                      disabled={busy || !file}>Import</button>
              <button className="subtle"
                      onClick={() => { retireReads(); setFile(null); setMode("view"); }}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
