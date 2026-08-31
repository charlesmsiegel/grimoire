import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

/** The one-line identity the off-scene cast is listed by.
 *
 *  World scope only, like the route behind it: a tagline is a property of the
 *  library's record, and a campaign's roster is a view of one. Sits under the
 *  name in the column rather than in a section of its own — it is read as part
 *  of the identity block, so it is laid out as part of it.
 */
export function TaglineSection(
  { wid, cid, onSaved, onError }: {
    wid: string;
    cid: string;
    onSaved?: (tagline: string) => void;
    onError: (err: unknown) => void;
  },
) {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const stored = useRef("");
  const live = useRef(true);
  // Set to true on mount, not only false on unmount: StrictMode mounts,
  // unmounts and mounts again in development, so a ref that is only ever
  // cleared stays cleared for the second mount's whole life -- and every read
  // this component makes is then discarded on arrival, leaving it reading
  // forever.
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  useEffect(() => {
    api.getCharacterTagline(wid, cid)
      .then((r) => { if (live.current) { stored.current = r.tagline; setText(r.tagline); } })
      .catch(() => { /* an unreadable tagline shows as none, which is what it looks like */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    setSaving(true);
    try {
      await api.setCharacterTagline(wid, cid, text.trim());
      if (!live.current) return;
      stored.current = text.trim();
      setEditing(false);
      onSaved?.(text.trim());
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setSaving(false);
    }
  }

  async function generate() {
    setBusy(true);
    try {
      const r = await api.generateCharacterTagline(wid, cid);
      if (!live.current) return;
      setText(r.tagline);
      setEditing(true);
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="identity-tagline">
        {text ? <>
          <p>{text}</p>
          {/* Named the way the dossier column names its sources: a file you can
              go and read, not prose the app made up. */}
          <span className="dossier-source">tagline.md</span>
        </> : <p className="field-hint">No tagline — the off-scene cast lists them by name alone.</p>}
        <div className="column-actions">
          <button className="subtle" type="button" onClick={() => setEditing(true)}>
            {text ? "Edit" : "Write one"}
          </button>
          <button className="subtle" type="button" disabled={busy} onClick={() => void generate()}>
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="identity-tagline editing">
      <textarea aria-label="Tagline" className="column-draft" rows={3} value={text}
                disabled={busy} onChange={(e) => setText(e.target.value)} />
      <div className="column-actions">
        <button className="primary" type="button" disabled={saving || busy} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="subtle" type="button" disabled={saving}
                onClick={() => { setText(stored.current); setEditing(false); }}>Cancel</button>
      </div>
    </div>
  );
}

export default TaglineSection;
