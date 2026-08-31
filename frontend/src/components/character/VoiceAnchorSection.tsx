import { useEffect, useRef, useState } from "react";
import { api, type EntityScope } from "../../api/client";
import { ColumnSection } from "../PageShell";

/** How a character SOUNDS: sent with the scene, and what absorb judges a played
 *  scene's drift against.
 *
 *  Mounted keyed on (scope, character), so switching character remounts it. The
 *  editor this came out of lived across every character on the screen and
 *  needed request tokens on the read, the save and the generation to keep one
 *  character's anchor out of another's textarea; a component that dies with the
 *  record it describes needs a liveness flag and nothing else.
 *
 *  What did NOT simplify is the destructive-blank guard. `PUT ""` REMOVES the
 *  anchor — that is how a character opts back out of drift detection — and ""
 *  is also what a failed read leaves behind. So Save stays disabled until a
 *  read actually succeeds, or a GET that failed for any reason would arm a
 *  one-click delete of an anchor the user never saw.
 */
export function VoiceAnchorSection(
  { scope, cid, cap, onError }: {
    scope: EntityScope;
    cid: string;
    /** The length the backend truncates at, or null if it could not be read —
     *  the warning is advisory and its absence must not block anything. */
    cap: number | null;
    onError: (err: unknown) => void;
  },
) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);      // generating
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const live = useRef(true);
  // Set to true on mount, not only false on unmount: StrictMode mounts,
  // unmounts and mounts again in development, so a ref that is only ever
  // cleared stays cleared for the second mount's whole life -- and every read
  // this component makes is then discarded on arrival, leaving it reading
  // forever.
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  /** What the server last confirmed, so Cancel has something to restore. The
   *  textarea edits `text` directly — a separate draft would have to be kept in
   *  step with a generation, which writes the box and is not a cancellable
   *  edit but the start of one. */
  const stored = useRef("");

  useEffect(() => {
    api.getCharacterVoiceAnchor(scope, cid)
      .then((r) => {
        if (!live.current) return;
        stored.current = r.voice_anchor;
        setText(r.voice_anchor);
        setState("ready");
      })
      .catch(() => { if (live.current) setState("error"); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function cancel() {
    setText(stored.current);
    setEditing(false);
  }

  async function save() {
    if (state !== "ready" || saving) return;
    setSaving(true);
    try {
      // Trimmed to blank on purpose: a blank anchor removes it. Only reachable
      // once the load succeeded, so the blank is the user's, not a placeholder.
      await api.setCharacterVoiceAnchor(scope, cid, text.trim());
      if (!live.current) return;
      stored.current = text;
      setEditing(false);
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setSaving(false);
    }
  }

  async function generate() {
    // `saving` too: a generation landing around an open PUT swaps the textarea
    // for a fresh draft while Save returns to its idle label, so the control
    // says "saved" over a value that never was.
    if (busy || saving) return;
    setBusy(true);
    try {
      const r = await api.generateCharacterVoiceAnchor(scope, cid);
      if (!live.current) return;
      // An empty completion is a failed generation, not a draft. Installing it
      // would arm the destructive save with a blank the user never wrote.
      if (!r.voice_anchor.trim()) {
        onError("The model returned an empty voice anchor — nothing was changed.");
        return;
      }
      setText(r.voice_anchor);
      setState("ready");
      setEditing(true);
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  // `[...text].length`, not `.length`: the backend counts CODE POINTS and
  // JavaScript counts UTF-16 units, so an anchor of astral characters reads as
  // double here and would warn at half the real cap. Advisory only.
  const over = cap !== null && [...text].length > cap;

  return (
    <ColumnSection label="Voice anchor">
      {!editing ? <>
        {state === "loading" ? <p className="field-hint">Reading…</p>
          : state === "error" ? (
            <p className="field-hint">Could not read the voice anchor — reload to try
              again. Editing is disabled so a failed read cannot overwrite the
              stored anchor with a blank.</p>
          ) : text.trim() ? <p className="column-prose">{text}</p>
            : <p className="field-hint">None — this character is not judged for voice drift.</p>}
        <div className="column-actions">
          <button className="subtle" type="button" disabled={state !== "ready"}
                  onClick={() => setEditing(true)}>{text.trim() ? "Edit" : "Write one"}</button>
          <button className="subtle" type="button" disabled={busy || saving} onClick={() => void generate()}>
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      </> : <>
        <p className="field-hint">
          How they sound. Sent with the scene, and absorb checks each played
          scene against it; clear it to opt out of both.
        </p>
        <textarea aria-label="Voice anchor" className="column-draft" value={text} rows={7}
                  disabled={busy}
                  onChange={(e) => setText(e.target.value)} />
        {over && <p className="field-hint">Over {cap} characters — the rest is not sent to the model.</p>}
        <div className="column-actions">
          <button className="primary" type="button" disabled={saving || busy} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="subtle" type="button" disabled={saving} onClick={cancel}>
            Cancel
          </button>
        </div>
      </>}
    </ColumnSection>
  );
}

export default VoiceAnchorSection;
