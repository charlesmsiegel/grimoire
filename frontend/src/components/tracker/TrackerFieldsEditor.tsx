import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type TrackerField, type TrackerLayerBundle, type TrackerScope } from "../../api/client";
import { errorText } from "../../api/errors";
import { FieldForm } from "./FieldForm";
import {
  BLANK_DRAFT, changeOf, draftOf, draftProblem, fieldOf, fullLayer, keyFromLabel, removeAdded,
  revert, switchOff, switchOn, upsertAdded, withChange, withoutShadowed, type FieldDraft, type FullLayer,
} from "./fieldLayer";

type EditorScope = Extract<TrackerScope, { kind: "world" | "campaign" }>;

/** The tracker's field list for a world or a campaign, as the list/detail
 *  editor every record page uses: a rail of fields, a read-only view, and an
 *  explicit Edit.
 *
 *  What this layer holds is what the bundle's `layer` says it stores; every
 *  write below is built from that and never from `effective`, which would
 *  freeze the definitions it inherits into this layer. The bundle names only
 *  this layer and what sits under it, so an inherited field is "built-in" on
 *  the world page and "inherited" on a campaign's, rather than a lookup of
 *  which of two layers below it came from. */
export function TrackerFieldsEditor({ scope }: { scope: EditorScope }) {
  const id = scope.kind === "world" ? scope.wid : scope.cid;
  const apiScope: TrackerScope = useMemo(
    () => (scope.kind === "world" ? { kind: "world", wid: id } : { kind: "campaign", cid: id }),
    // `scope` is usually an object literal in the parent, new on every render;
    // what it names is its kind and id.
    [scope.kind, id]);
  const inheritedLabel = scope.kind === "world" ? "built-in" : "inherited";

  const [bundle, setBundle] = useState<TrackerLayerBundle | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [selKey, setSelKey] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<FieldDraft>(BLANK_DRAFT);
  const [keyTouched, setKeyTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which scope the screen belongs to, readable from a settled promise: a read
  // or a save that lands after the reader moved to another campaign must not
  // put the old one's fields on screen.
  const live = useRef(id + scope.kind);
  live.current = id + scope.kind;

  /** Read the layer again. Used to load, and to retry a failed load. */
  const reload = useCallback(async () => {
    const mine = live.current;
    try {
      const b = await api.getTrackerFields(apiScope);
      if (live.current === mine) { setBundle(b); setLoadFailed(false); }
    } catch {
      if (live.current === mine) setLoadFailed(true);
    }
  }, [apiScope]);

  useEffect(() => {
    setBundle(null); setLoadFailed(false); setSelKey(null); setMode("view"); setError(null);
    void reload();
  }, [reload]);

  const inherited = bundle?.inherited ?? [];
  const effective = bundle?.effective ?? [];
  const inheritedByKey = new Map(inherited.map((f) => [f.key, f]));
  // What gets written: the stored layer minus additions a lower layer has since
  // taken over, which the server would refuse on every save.
  const layer: FullLayer = withoutShadowed(fullLayer(bundle?.layer), new Set(inheritedByKey.keys()));
  /** Added by this layer: its key is in `fields` and nothing below owns it. */
  const addedHere = (key: string) => layer.fields.some((f) => f.key === key) && !inheritedByKey.has(key);
  const selected = effective.find((f) => f.key === selKey) ?? null;
  const taken = [...effective.map((f) => f.key), ...layer.fields.map((f) => f.key)];

  function select(key: string) {
    setSelKey(key); setMode("view"); setError(null);
  }

  function startNew() {
    setSelKey(null); setDraft(BLANK_DRAFT); setKeyTouched(false); setMode("edit"); setError(null);
  }

  function startEdit(f: TrackerField) {
    setDraft(draftOf(f)); setKeyTouched(true); setMode("edit"); setError(null);
  }

  function changeDraft(next: FieldDraft, touched?: boolean) {
    // A new field's key follows its label until the reader types one.
    const follows = selected === null && !keyTouched && !touched;
    setDraft(follows ? { ...next, key: keyFromLabel(next.label) } : next);
    if (touched) setKeyTouched(true);
  }

  /** Store `next` as this layer and show the bundle the write answers with.
   *  Reports whether it landed, and whether the screen is still the one that
   *  asked: a save settling after a campaign switch must not touch the new one. */
  async function commit(next: FullLayer): Promise<{ ok: boolean; current: boolean }> {
    const mine = live.current;
    setSaving(true); setError(null);
    try {
      const saved = await api.setTrackerFields(apiScope, next);
      if (live.current === mine) setBundle(saved);
      return { ok: true, current: live.current === mine };
    } catch (err) {
      if (live.current === mine) setError(errorText(err));
      return { ok: false, current: live.current === mine };
    } finally {
      if (live.current === mine) setSaving(false);
    }
  }

  const problem = draftProblem(draft, { keyFixed: selected !== null, taken });

  async function save() {
    if (problem !== null) return;
    let next: FullLayer;
    if (selected === null || addedHere(selected.key)) {
      next = upsertAdded(layer, fieldOf(draft));
    } else {
      const base = inheritedByKey.get(selected.key);
      if (!base) return;
      next = withChange(layer, selected.key, changeOf(base, draft));
    }
    const done = await commit(next);
    if (done.ok && done.current) { setSelKey(draft.key); setMode("view"); }
  }

  function remove(f: TrackerField) {
    if (!window.confirm(`Remove field '${f.label}'? Values already tracked for it will have no label.`)) return;
    void commit(removeAdded(layer, f.key)).then((done) => { if (done.ok && done.current) setSelKey(null); });
  }

  function sourceChips(f: TrackerField) {
    const here = addedHere(f.key);
    return (
      <>
        <span className="chip on">{here ? "this layer" : inheritedLabel}</span>
        {!here && f.key in layer.change && <span className="chip on">changed here</span>}
      </>
    );
  }

  function detail(f: TrackerField) {
    const here = addedHere(f.key);
    const offBelow = inheritedByKey.get(f.key)?.off === true;
    const offHere = layer.off.includes(f.key);
    return (
      <div className="detail-view">
        <div className="detail-main">
          <h3>{f.label}</h3>
          {f.hint ? <p>{f.hint}</p> : <p className="field-hint">No hint.</p>}
          <div className="field-hint">Key: <code>{f.key}</code></div>
        </div>
        <aside className="detail-sidebar">
          <div className="form-actions">
            <button className="subtle" onClick={() => startEdit(f)} disabled={saving}>Edit</button>
          </div>
          <div className="side-section">
            <h4>Field</h4>
            <div className="chips">
              <span className="chip on">type: {f.type}</span>
              <span className="chip on">
                known to: {f.aware === "present" ? "everyone present" : "the character only"}
              </span>
            </div>
          </div>
          {f.type === "enum" && (
            <div className="side-section">
              <h4>Options</h4>
              <div className="chips">
                {(f.options ?? []).map((o) => <span key={o} className="chip on">{o}</span>)}
              </div>
            </div>
          )}
          <div className="side-section">
            <h4>Source</h4>
            <div className="chips">{sourceChips(f)}</div>
          </div>
          <div className="side-section">
            <h4>Availability</h4>
            {here ? (
              <>
                <div className="field-hint">Added by this layer; removing it deletes the definition.</div>
                <div className="form-actions">
                  <button className="subtle" disabled={saving} onClick={() => remove(f)}>Remove</button>
                </div>
              </>
            ) : offBelow ? (
              <div className="field-hint">
                Switched off by a layer below this one, which only that layer can switch back on.
              </div>
            ) : (
              <div className="form-actions">
                {offHere ? (
                  <button className="subtle" disabled={saving}
                          onClick={() => void commit(switchOn(layer, f.key))}>Switch on</button>
                ) : (
                  <button className="subtle" disabled={saving}
                          onClick={() => void commit(switchOff(layer, f.key))}>Switch off</button>
                )}
              </div>
            )}
            {!here && f.key in layer.change && (
              <>
                <div className="field-hint">This layer changes how the field is defined.</div>
                <div className="form-actions">
                  <button className="subtle" disabled={saving}
                          onClick={() => void commit(revert(layer, f.key))}>Revert</button>
                </div>
              </>
            )}
          </div>
        </aside>
      </div>
    );
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={startNew}>+ New field</button>
        {effective.map((f) => (
          <button key={f.key}
                  className={"row" + (selKey === f.key ? " active" : "") + (f.off ? " off" : "")}
                  aria-current={selKey === f.key ? "true" : undefined}
                  onClick={() => select(f.key)}>
            <span className="row-label">
              {f.label}
              {f.off && <span className="row-subtitle">switched off</span>}
            </span>
          </button>
        ))}
      </div>
      <div className="editor-body">
        {error && <div className="banner" role="alert">{error}</div>}
        {loadFailed && !bundle ? (
          <div className="editor-empty">
            The tracker fields could not be read.{" "}
            <button className="subtle" onClick={() => void reload()}>Try again</button>
          </div>
        ) : !bundle ? (
          <div className="editor-empty">Loading fields…</div>
        ) : mode === "edit" ? (
          <div>
            <h3>{selected ? "Edit field" : "New field"}</h3>
            <FieldForm draft={draft} onChange={changeDraft} keyFixed={selected !== null}
                       problem={problem} saving={saving} onSave={() => void save()}
                       onCancel={() => { setMode("view"); setError(null); }} />
          </div>
        ) : selected ? (
          detail(selected)
        ) : (
          <div className="editor-empty">Pick a field to read it, or add one.</div>
        )}
      </div>
    </div>
  );
}
