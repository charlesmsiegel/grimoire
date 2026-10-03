import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type TrackerLayerBundle, type TrackerScope } from "../../api/client";
import { errorText } from "../../api/errors";
import { FieldForm } from "./FieldForm";
import {
  BLANK_DRAFT, draftProblem, fieldOf, fullLayer, keyFromLabel, removeAdded, switchOff, switchOn,
  upsertAdded, type FieldDraft, type FullLayer,
} from "./fieldLayer";

/** What the tracker follows in ONE scene.
 *
 *  A scene may switch an inherited field off or add one of its own, and nothing
 *  else: redefining an inherited field mid-scene would strand the snapshots
 *  already stored against the old definition. So this panel never sends a
 *  `change`, and its only edit to an inherited field is the checkbox. */
export function SceneTrackerPanel({ cid, sid }: { cid: string; sid: string }) {
  const scope: TrackerScope = useMemo(() => ({ kind: "scene", cid, sid }), [cid, sid]);
  const [bundle, setBundle] = useState<TrackerLayerBundle | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<FieldDraft>(BLANK_DRAFT);
  const [keyTouched, setKeyTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(`${cid}:${sid}`);
  live.current = `${cid}:${sid}`;

  const reload = useCallback(async () => {
    const mine = live.current;
    try {
      const b = await api.getTrackerFields(scope);
      if (live.current === mine) { setBundle(b); setLoadFailed(false); }
    } catch {
      if (live.current === mine) setLoadFailed(true);
    }
  }, [scope]);

  useEffect(() => {
    setBundle(null); setLoadFailed(false); setAdding(false); setError(null);
    void reload();
  }, [reload]);

  const layer = fullLayer(bundle?.layer);
  const ownKeys = new Set(layer.fields.map((f) => f.key));
  // What the campaign hands this scene. The scene's own additions are listed
  // apart, with Remove rather than a checkbox.
  const inherited = (bundle?.inherited ?? []).filter((f) => !ownKeys.has(f.key));
  const taken = [...(bundle?.effective ?? []).map((f) => f.key), ...layer.fields.map((f) => f.key)];

  /** Store `next` as the scene's layer. `change` is left out on purpose. */
  async function commit(next: FullLayer): Promise<boolean> {
    const mine = live.current;
    setSaving(true); setError(null);
    try {
      await api.setTrackerFields(scope, { fields: next.fields, off: next.off });
      await reload();
      return true;
    } catch (err) {
      if (live.current === mine) setError(errorText(err));
      return false;
    } finally {
      if (live.current === mine) setSaving(false);
    }
  }

  function changeDraft(next: FieldDraft, touched?: boolean) {
    const follows = !keyTouched && !touched;
    setDraft(follows ? { ...next, key: keyFromLabel(next.label) } : next);
    if (touched) setKeyTouched(true);
  }

  function openForm() {
    setDraft(BLANK_DRAFT); setKeyTouched(false); setAdding(true); setError(null);
  }

  const problem = draftProblem(draft, { keyFixed: false, taken });

  async function add() {
    if (problem !== null) return;
    if (await commit(upsertAdded(layer, fieldOf(draft)))) setAdding(false);
  }

  function remove(key: string, label: string) {
    if (!window.confirm(`Remove field '${label}' from this scene? Values already tracked for it will have no label.`)) return;
    void commit(removeAdded(layer, key));
  }

  return (
    <section className="scene-tracker" aria-label="Scene tracker">
      <h3>Tracker for this scene</h3>
      {error && <div className="banner" role="alert">{error}</div>}
      {loadFailed && !bundle ? (
        <div className="editor-empty">
          The tracker fields could not be read.{" "}
          <button className="subtle" onClick={() => void reload()}>Try again</button>
        </div>
      ) : !bundle ? (
        <div className="editor-empty">Loading fields…</div>
      ) : (
        <>
          <div className="field-hint">
            Untick a field to stop tracking it in this scene only. The campaign's own list is unchanged.
          </div>
          <div className="side-section">
            {inherited.map((f) => {
              // A field a layer below switched off is not this scene's to turn back on.
              const offBelow = f.off === true;
              return (
                <div key={f.key} className="scene-tracker-row">
                  <label>
                    <input type="checkbox" disabled={saving || offBelow}
                           checked={!offBelow && !layer.off.includes(f.key)}
                           onChange={(e) => void commit(
                             e.target.checked ? switchOn(layer, f.key) : switchOff(layer, f.key))} />
                    {" "}{f.label}
                  </label>
                  {offBelow && <span className="field-hint"> switched off for the campaign</span>}
                </div>
              );
            })}
          </div>
          {layer.fields.length > 0 && (
            <div className="side-section">
              <h4>Scene-only fields</h4>
              {layer.fields.map((f) => (
                <div key={f.key} className="scene-tracker-row">
                  <span>{f.label}</span>{" "}
                  <span className="field-hint">{f.type}</span>{" "}
                  <button className="subtle" disabled={saving}
                          aria-label={`Remove ${f.label}`}
                          onClick={() => remove(f.key, f.label)}>Remove</button>
                </div>
              ))}
            </div>
          )}
          {adding ? (
            <FieldForm draft={draft} onChange={changeDraft} keyFixed={false} problem={problem}
                       saving={saving} saveLabel="Add field" onSave={() => void add()}
                       onCancel={() => { setAdding(false); setError(null); }} />
          ) : (
            <button className="subtle" onClick={openForm}>+ Scene-only field</button>
          )}
        </>
      )}
    </section>
  );
}
