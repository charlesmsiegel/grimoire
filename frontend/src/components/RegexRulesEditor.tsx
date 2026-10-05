import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, type RegexBundle, type RegexEntry, type RegexLayer, type RegexRule, type RegexScope,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "./Field";
import { RegexImportDialog } from "./RegexImportDialog";
import { RegexTestPane } from "./RegexTestPane";

/** The four presets are settings of two axes, who a rule is for and where it
 *  runs (spec section 2). The checkboxes they set stay editable. */
const PRESETS: { label: string; targets: RegexRule["targets"]; applies: RegexRule["applies"] }[] = [
  { label: "Model output", targets: ["model"], applies: ["display", "prompt"] },
  { label: "User input", targets: ["user"], applies: ["display", "prompt"] },
  { label: "Display only", targets: ["model", "user"], applies: ["display"] },
  { label: "Prompt only", targets: ["model", "user"], applies: ["prompt"] },
];

const FLAGS: { key: string; label: string }[] = [
  { key: "g", label: "Every match" },
  { key: "i", label: "Ignore case" },
  { key: "m", label: "Multiline" },
  { key: "s", label: "Dot matches newline" },
  { key: "a", label: "ASCII \\w \\d \\b" },
];

/** A rule as the form holds it: depths and the trim list as the text typed. */
type Draft = {
  id: string | null; enabled: boolean; name: string; pattern: string; flags: string;
  replacement: string; trim: string; targets: RegexRule["targets"]; applies: RegexRule["applies"];
  rewrite_stored: boolean; min_depth: string; max_depth: string; imported: RegexRule["imported"];
};

const BLANK: Draft = {
  id: null, enabled: true, name: "", pattern: "", flags: "g", replacement: "", trim: "",
  targets: PRESETS[0].targets, applies: PRESETS[0].applies, rewrite_stored: false,
  min_depth: "", max_depth: "", imported: null,
};

const draftOf = (r: RegexRule): Draft => ({
  id: r.id, enabled: r.enabled, name: r.name, pattern: r.pattern, flags: r.flags,
  replacement: r.replacement, trim: r.trim.join("\n"), targets: r.targets, applies: r.applies,
  rewrite_stored: r.rewrite_stored, imported: r.imported,
  min_depth: r.min_depth === null ? "" : String(r.min_depth),
  max_depth: r.max_depth === null ? "" : String(r.max_depth),
});

/** The fields the form draws a note for. A refusal naming any other key (`id`,
 *  `enabled`, `imported`, `rules`) has nowhere to appear and goes to the banner. */
const FORM_FIELDS = new Set([
  "name", "pattern", "replacement", "flags", "trim", "targets", "applies",
  "rewrite_stored", "min_depth", "max_depth",
]);

const WHOLE = /^\d+$/;

/** Why the form cannot be sent, as the field it belongs to and the words. The
 *  server checks everything else; a depth typed as prose never reaches it as a
 *  number to check. */
function problemOf(d: Draft): { field: string; detail: string } | null {
  for (const field of ["min_depth", "max_depth"] as const) {
    if (d[field].trim() !== "" && !WHOLE.test(d[field].trim())) {
      return { field, detail: "Must be empty or a whole number, 0 or more." };
    }
  }
  return null;
}

const depthOf = (s: string) => (s.trim() === "" ? null : Number(s.trim()));

/** A new rule's id, minted here so the page can select it once it is saved. The
 *  server accepts any unused string and would mint the same shape. */
const newId = () => "r-" + Array.from(crypto.getRandomValues(new Uint8Array(4)),
  (b) => b.toString(16).padStart(2, "0")).join("");

function ruleOf(d: Draft): RegexRule {
  return {
    id: d.id ?? newId(), enabled: d.enabled, name: d.name.trim(), pattern: d.pattern, flags: d.flags,
    replacement: d.replacement, trim: d.trim.split("\n").filter((t) => t !== ""),
    targets: d.targets, applies: d.applies, rewrite_stored: d.rewrite_stored,
    min_depth: depthOf(d.min_depth), max_depth: depthOf(d.max_depth), imported: d.imported,
  };
}

const labelOf = (r: RegexRule) => r.name || r.pattern || "(unnamed rule)";

/** A refusal the server pinned on one field of the open form. */
type Failure = { field: string; detail: string };

/** The output-processing rules for one level, as the list/detail editor every
 *  record page uses: a rail of rules (what this level sits on, then its own), a
 *  read-only view, and an explicit Edit.
 *
 *  Every write sends this level's whole file built from the bundle's `layer`,
 *  never from `inherited`: an inherited rule changes only by being switched off
 *  here, which is the `off` list, and to change one you switch it off and add
 *  your own. The global and connection levels sit on nothing, so they have no
 *  `off` and no switch. */
export function RegexRulesEditor({ scope }: { scope: RegexScope }) {
  const scopeKey = JSON.stringify(scope);
  const apiScope = useMemo(() => JSON.parse(scopeKey) as RegexScope, [scopeKey]);
  const canOff = scope.kind === "world" || scope.kind === "campaign";

  const [bundle, setBundle] = useState<RegexBundle | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [selId, setSelId] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  // The connections, for naming the connection rules a world or a campaign
  // inherits and for the test pane's choice of whose rules run first. A failed
  // read leaves the ids standing in for the names and the choice empty.
  const [connections, setConnections] = useState<{ id: string; name: string }[]>([]);
  const names = useMemo(() => Object.fromEntries(connections.map((c) => [c.id, c.name])), [connections]);
  const [importing, setImporting] = useState(false);
  // Which scope the screen belongs to, readable from a settled promise: a read
  // or a save landing after the reader moved on must not show the old one.
  const live = useRef(scopeKey);
  live.current = scopeKey;

  // Bound to the scope it was built for, not read off `live` when it is
  // called: an import dialog's `onDone` can land after the scope moved, and the
  // reload it calls is still this one, fetching the old level.
  const reload = useCallback(async () => {
    const mine = scopeKey;
    try {
      const b = await api.getRegex(apiScope);
      if (live.current === mine) { setBundle(b); setLoadFailed(false); }
    } catch {
      if (live.current === mine) setLoadFailed(true);
    }
  }, [apiScope, scopeKey]);

  useEffect(() => {
    setBundle(null); setLoadFailed(false); setSelId(null); setMode("view");
    setError(null); setFailure(null); setImporting(false);
    // A write still in flight belongs to the scope being left, and its `finally`
    // will not clear this once the scope has moved.
    setSaving(false);
    void reload();
  }, [reload]);

  // A connection's own level inherits nothing and tests only its own rules.
  const wantsConnections = scope.kind !== "connection";
  useEffect(() => {
    if (!wantsConnections) return;
    let alive = true;
    void (async () => {
      try {
        const list = await api.listConnections();
        if (alive) setConnections(list.map((c) => ({ id: c.id, name: c.name })));
      } catch { /* the ids stand in */ }
    })();
    return () => { alive = false; };
  }, [wantsConnections, scopeKey]);

  const layer: RegexLayer = bundle?.layer ?? { rules: [], off: [] };
  const inherited = bundle?.inherited ?? [];
  const own = layer.rules.find((r) => r.id === selId) ?? null;
  const entry = own ? null : inherited.find((e) => e.rule.id === selId) ?? null;
  const selected = own ?? entry?.rule ?? null;

  const levelTag = (e: RegexEntry) =>
    e.level === "connection" ? `connection: ${names[e.source] || e.source}` : e.level;
  /** A rule a campaign's world switched off: it never runs here, and nothing in
   *  this level's `off` list can turn it back on, so its switch is shown off
   *  and held. */
  const worldOff = (e: RegexEntry) => scope.kind === "campaign" && e.off_by === "world";

  function select(id: string) {
    setSelId(id); setMode("view"); setError(null); setFailure(null);
  }

  function startNew() {
    setSelId(null); setDraft(BLANK); setMode("edit"); setError(null); setFailure(null);
  }

  function startEdit(r: RegexRule) {
    setDraft(draftOf(r)); setMode("edit"); setError(null); setFailure(null);
  }

  /** Store `next` as this level's file and show the bundle the write answers
   *  with. `formIndex` is the place in the list of the rule the open form is
   *  sending, so a refusal that names it lands on its field; any other refusal
   *  is a banner. Reports whether it landed, and whether the screen is still
   *  the one that asked, with the stored layer when it landed. */
  async function commit(next: RegexLayer, formIndex?: number):
      Promise<{ ok: boolean; current: boolean; layer?: RegexLayer }> {
    const mine = live.current;
    setSaving(true); setError(null); setFailure(null);
    try {
      // The one place a body is built: the global and connection levels sit on
      // nothing, so the server refuses an `off` there, and a hand-edited or
      // synced file may still carry one that a read keeps.
      const saved = await api.putRegex(apiScope, canOff ? next : { ...next, off: [] });
      if (live.current === mine) setBundle(saved);
      return { ok: true, current: live.current === mine, layer: saved.layer };
    } catch (err) {
      if (live.current === mine) {
        const body = (err as { kind?: string; body?: { index?: number | null; field?: string } })?.body;
        const kind = (err as { kind?: string })?.kind;
        const index = body?.index;
        if (kind === "invalid_rule" && formIndex !== undefined && body?.field && FORM_FIELDS.has(body.field)
            && (index === undefined || index === null || index === formIndex)) {
          setFailure({ field: body.field, detail: errorText(err) });
        } else {
          setError(errorText(err));
        }
      }
      return { ok: false, current: live.current === mine };
    } finally {
      if (live.current === mine) setSaving(false);
    }
  }

  const problem = problemOf(draft);
  // The open form's rule, for the test pane. A form that cannot be sent (a depth
  // typed as prose) has no rule to test, so the pane falls back to what is saved.
  const testDraft = useMemo(
    () => (mode === "edit" && problem === null ? { ...ruleOf(draft), id: draft.id ?? "r-draft" } : undefined),
    [mode, draft, problem]);

  async function save() {
    if (problem !== null) return;
    const rule = ruleOf(draft);
    const at = layer.rules.findIndex((r) => r.id === rule.id);
    const rules = at === -1 ? [...layer.rules, rule] : layer.rules.map((r, i) => (i === at ? rule : r));
    const place = at === -1 ? layer.rules.length : at;
    const done = await commit({ ...layer, rules }, place);
    // Selected by its place in what was stored, not by the id sent: an id that
    // collides with an inherited rule's is re-minted on the server, and the
    // sent one would then select that inherited rule. Order is kept.
    if (done.ok && done.current) { setSelId(done.layer?.rules[place]?.id ?? rule.id); setMode("view"); }
  }

  function remove(r: RegexRule) {
    if (!window.confirm(`Delete rule '${labelOf(r)}'?`)) return;
    void commit({ ...layer, rules: layer.rules.filter((x) => x.id !== r.id) })
      .then((done) => { if (done.ok && done.current) setSelId(null); });
  }

  function setEnabled(r: RegexRule, enabled: boolean) {
    void commit({ ...layer, rules: layer.rules.map((x) => (x.id === r.id ? { ...x, enabled } : x)) });
  }

  function move(i: number, by: -1 | 1) {
    const rules = [...layer.rules];
    [rules[i], rules[i + by]] = [rules[i + by], rules[i]];
    void commit({ ...layer, rules });
  }

  /** "Use here" on an inherited rule: writes this level's `off` list. */
  function setUsed(e: RegexEntry, used: boolean) {
    const off = used ? layer.off.filter((id) => id !== e.rule.id) : [...layer.off, e.rule.id];
    void commit({ ...layer, off });
  }

  const set = (patch: Partial<Draft>) => {
    setDraft({ ...draft, ...patch });
    // The server's note was about what was typed there; typing again retires it.
    if (failure && failure.field in patch) setFailure(null);
  };
  const toggleTarget = (value: RegexRule["targets"][number], on: boolean) =>
    set({ targets: [...draft.targets.filter((v) => v !== value), ...(on ? [value] : [])] });
  const toggleApply = (value: RegexRule["applies"][number], on: boolean) =>
    set({ applies: [...draft.applies.filter((v) => v !== value), ...(on ? [value] : [])] });
  /** The server's words for a field of the open form, or the local check's. */
  const fieldNote = (field: string): string | undefined =>
    (failure?.field === field ? failure.detail : undefined)
    ?? (problem?.field === field ? problem.detail : undefined);

  function detail(r: RegexRule, e: RegexEntry | null) {
    const warnings = bundle?.warnings[r.id] ?? [];
    return (
      <div className="detail-view">
        <div className="detail-main">
          <h3>{labelOf(r)}</h3>
          <div className="field-hint">Pattern</div>
          <pre className="regex-code">{r.pattern}</pre>
          <div className="field-hint">Replacement</div>
          {r.replacement
            ? <pre className="regex-code">{r.replacement}</pre>
            : <p className="field-hint">Empty: what the pattern matches is deleted.</p>}
          {r.trim.length > 0 && (
            <div className="field-hint">
              Trimmed from the match before it is substituted: {r.trim.map((t) => JSON.stringify(t)).join(", ")}
            </div>
          )}
          {warnings.map((w) => <div key={w} className="field-hint">{w}</div>)}
        </div>
        <aside className="detail-sidebar">
          <div className="form-actions">
            {e === null ? (
              <button className="subtle" onClick={() => startEdit(r)} disabled={saving}>Edit</button>
            ) : (
              <span className="field-hint">
                {worldOff(e)
                  ? <>From {levelTag(e)}, and switched off by the world, so it does not run here.
                      Switch it back on in the world, or add your own here.</>
                  : <>From {levelTag(e)}. Change it there, or switch it off here and add your own.</>}
              </span>
            )}
          </div>
          <div className="side-section">
            <h4>Source</h4>
            <div className="chips">
              <span className="chip on">{e === null ? "this level" : levelTag(e)}</span>
              {!r.enabled && <span className="chip">disabled</span>}
            </div>
          </div>
          <div className="side-section">
            <h4>Flags</h4>
            <div className="chips">
              {r.flags
                ? [...r.flags].map((f) => <span key={f} className="chip on" title={FLAGS.find((x) => x.key === f)?.label}>{f}</span>)
                : <span className="field-hint">None: the first match only.</span>}
            </div>
          </div>
          <div className="side-section">
            <h4>Targets</h4>
            <div className="chips">
              {r.targets.map((t) => <span key={t} className="chip on">{t}</span>)}
            </div>
          </div>
          <div className="side-section">
            <h4>Applies</h4>
            <div className="chips">
              {r.applies.length > 0
                ? r.applies.map((a) => <span key={a} className="chip on">{a}</span>)
                : <span className="field-hint">Neither: it only rewrites stored text.</span>}
            </div>
          </div>
          <div className="side-section">
            <h4>Depth</h4>
            <span className="field-hint">
              {r.min_depth === null && r.max_depth === null
                ? "Every message."
                : `From ${r.min_depth ?? 0} to ${r.max_depth ?? "the oldest"}, 0 being the newest message.`}
            </span>
          </div>
          <div className="side-section">
            <h4>Stored text</h4>
            <span className="field-hint">
              {r.rewrite_stored
                ? "Rewritten when a reply lands; the original is kept."
                : "Left as written."}
            </span>
          </div>
          {r.imported && (
            <div className="side-section">
              <h4>Imported notes</h4>
              <div className="field-hint">From {r.imported.from}: <code>{r.imported.pattern}</code></div>
              {/* The server normalises this, but a hand-edited file read by an
                  older server could carry anything; a missing list draws nothing. */}
              {(Array.isArray(r.imported.notes) ? r.imported.notes : []).map((n) =>
                <div key={n} className="field-hint">{n}</div>)}
            </div>
          )}
          {e === null && (
            <div className="side-section">
              <div className="form-actions">
                <button className="subtle" disabled={saving} onClick={() => remove(r)}>Delete</button>
              </div>
            </div>
          )}
        </aside>
      </div>
    );
  }

  function form() {
    return (
      <div className="form">
        <h3>{draft.id && own ? "Edit rule" : "New rule"}</h3>
        <div className="side-section">
          <h4>Start from</h4>
          <div className="chips">
            {PRESETS.map((p) => (
              <button key={p.label} type="button" className="subtle"
                      onClick={() => set({ targets: p.targets, applies: p.applies })}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div role="group" aria-label="Targets" className="regex-checks">
          <span className="field-hint">Runs on</span>
          {(["model", "user"] as const).map((t) => (
            <label key={t} className="regex-check">
              <input type="checkbox" checked={draft.targets.includes(t)}
                     onChange={(e) => toggleTarget(t, e.target.checked)} />
              {t === "model" ? "Model posts" : "Player posts"}
            </label>
          ))}
          {fieldNote("targets") && <div className="field-hint">{fieldNote("targets")}</div>}
        </div>
        <div role="group" aria-label="Applies" className="regex-checks">
          <span className="field-hint">Applies to</span>
          {(["display", "prompt"] as const).map((a) => (
            <label key={a} className="regex-check">
              <input type="checkbox" checked={draft.applies.includes(a)}
                     onChange={(e) => toggleApply(a, e.target.checked)} />
              {a === "display" ? "What you read" : "What the model is sent"}
            </label>
          ))}
          <div className="field-hint">
            {fieldNote("applies") ?? "Leave both off only for a rule that rewrites stored text."}
          </div>
        </div>
        <Field label="Name" hint={fieldNote("name")}>
          <input type="text" value={draft.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="Pattern" hint={fieldNote("pattern") ?? "Python re syntax, checked when you save."}>
          <input type="text" className="regex-code" value={draft.pattern}
                 onChange={(e) => set({ pattern: e.target.value })} />
        </Field>
        <div className="field-hint">
          Python&apos;s re has no timeout: a pattern that backtracks catastrophically, such
          as (a+)+$ on a long line, can hang the server. Try it in the test pane first.
        </div>
        <Field label="Replacement"
               hint={fieldNote("replacement") ?? "$1, $<name>, $& or {{match}} for the match, $$ for a dollar sign."}>
          <input type="text" className="regex-code" value={draft.replacement}
                 onChange={(e) => set({ replacement: e.target.value })} />
        </Field>
        <div role="group" aria-label="Flags" className="regex-checks">
          <span className="field-hint">Flags</span>
          {FLAGS.map((f) => (
            <label key={f.key} className="regex-check">
              <input type="checkbox" checked={draft.flags.includes(f.key)}
                     onChange={(e) => set({ flags: FLAGS.map((x) => x.key).filter((k) =>
                       (k === f.key ? e.target.checked : draft.flags.includes(k))).join("") })} />
              {f.label}
            </label>
          ))}
          {fieldNote("flags") && <div className="field-hint">{fieldNote("flags")}</div>}
        </div>
        <Field label="Trim" hint={fieldNote("trim") ?? "One per line. Removed from the match before it is substituted for $& or {{match}}."}>
          <textarea rows={3} className="regex-code" value={draft.trim}
                    onChange={(e) => set({ trim: e.target.value })} />
        </Field>
        <Field label="Min depth" hint={fieldNote("min_depth") ?? "Empty for no limit. 0 is the newest message."}>
          <input type="number" min={0} value={draft.min_depth}
                 onChange={(e) => set({ min_depth: e.target.value })} />
        </Field>
        <Field label="Max depth" hint={fieldNote("max_depth")}>
          <input type="number" min={0} value={draft.max_depth}
                 onChange={(e) => set({ max_depth: e.target.value })} />
        </Field>
        <label className="regex-check">
          <input type="checkbox" checked={draft.enabled}
                 onChange={(e) => set({ enabled: e.target.checked })} />
          Enabled
        </label>
        <div className="field-hint">
          A disabled rule is kept but never runs, and may be saved with a pattern that does not compile yet.
        </div>
        <label className="regex-check">
          <input type="checkbox" checked={draft.rewrite_stored}
                 onChange={(e) => set({ rewrite_stored: e.target.checked })} />
          Also rewrite stored text
        </label>
        <div className="field-hint">
          {fieldNote("rewrite_stored") ?? "Rewrites the transcript once, as a reply lands. The original is kept and can be restored."}
        </div>
        <div className="form-actions">
          <button className="subtle" disabled={saving}
                  onClick={() => { setMode("view"); setError(null); setFailure(null); }}>Cancel</button>
          <button className="primary" disabled={saving || problem !== null} onClick={() => void save()}>
            Save
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="editor">
      <div className="editor-list">
        {inherited.length > 0 && (
          <>
            <div className="regex-group">Inherited</div>
            {/* Keyed by where the rule lives as well as its id: the server
                re-mints a new id that collides across levels, but two files
                can still share one from before that, or by a hand edit. */}
            {inherited.map((e) => (
              <div key={`${e.level}:${e.source}:${e.rule.id}`} className="regex-line">
                <button className={"row" + (selId === e.rule.id ? " active" : "")
                                   + (e.off || worldOff(e) ? " off" : "")}
                        aria-current={selId === e.rule.id ? "true" : undefined}
                        onClick={() => select(e.rule.id)}>
                  <span className="row-label">
                    {labelOf(e.rule)}
                    <span className="row-subtitle">
                      {levelTag(e)}{worldOff(e) && " · switched off by the world"}
                    </span>
                  </span>
                </button>
                {canOff && (
                  <input type="checkbox" title={worldOff(e) ? "Switched off by the world" : "Use here"}
                         aria-label={`Use ${labelOf(e.rule)} here`}
                         checked={!e.off && !worldOff(e)} disabled={saving || worldOff(e)}
                         onChange={(ev) => setUsed(e, ev.target.checked)} />
                )}
              </div>
            ))}
          </>
        )}
        <div className="regex-group">This level</div>
        {layer.rules.map((r, i) => (
          <div key={r.id} className="regex-line">
            <input type="checkbox" title="Enabled" aria-label={`Enable ${labelOf(r)}`}
                   checked={r.enabled} disabled={saving}
                   onChange={(ev) => setEnabled(r, ev.target.checked)} />
            <button className={"row" + (selId === r.id ? " active" : "") + (r.enabled ? "" : " off")}
                    aria-current={selId === r.id ? "true" : undefined}
                    onClick={() => select(r.id)}>
              <span className="row-label">{labelOf(r)}</span>
            </button>
            <span className="row-actions">
              <button type="button" aria-label={`Move ${labelOf(r)} up`}
                      disabled={saving || i === 0} onClick={() => move(i, -1)}>↑</button>
              <button type="button" aria-label={`Move ${labelOf(r)} down`}
                      disabled={saving || i === layer.rules.length - 1} onClick={() => move(i, 1)}>↓</button>
            </span>
          </div>
        ))}
        <button className="primary new" onClick={startNew}>+ New rule</button>
        {/* Held while a save is in flight: an import appends to the level as
            the server has it, and a whole-layer PUT landing after it would
            write the layer back without the rules it added. */}
        <button className="subtle new" disabled={saving} onClick={() => setImporting(true)}>Import…</button>
        <details className="regex-test-panel">
          <summary>Test rules</summary>
          <RegexTestPane key={scopeKey} scope={apiScope} draft={testDraft} connections={connections} />
        </details>
      </div>
      <div className="editor-body">
        {error && <div className="banner" role="alert">{error}</div>}
        {loadFailed && !bundle ? (
          <div className="editor-empty">
            The rules could not be read.{" "}
            <button className="subtle" onClick={() => void reload()}>Try again</button>
          </div>
        ) : !bundle ? (
          <div className="editor-empty">Loading rules…</div>
        ) : mode === "edit" ? (
          form()
        ) : selected ? (
          detail(selected, entry)
        ) : (
          <div className="editor-empty">Pick a rule to read it, or add one.</div>
        )}
      </div>
      {importing && (
        <RegexImportDialog scope={apiScope} held={saving} onClose={() => setImporting(false)}
                           onDone={() => { setImporting(false); void reload(); }} />
      )}
    </div>
  );
}
