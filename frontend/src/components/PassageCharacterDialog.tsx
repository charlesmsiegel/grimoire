import { useEffect, useRef, useState } from "react";
import { api, type CharacterSummary, type PassageCharacterDraft } from "../api/client";
import { ErrorNote } from "./ErrorNote";
import { useHotkeys } from "../shortcuts/useHotkeys";

export function PassageCharacterDialog({ cid, sid, responseId, sourceText, onClose, onSaved }: {
  cid: string; sid: string; responseId: string; sourceText: string;
  onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [passage, setPassage] = useState(sourceText.slice(0, 16000));
  const [description, setDescription] = useState("");
  const [draft, setDraft] = useState<PassageCharacterDraft | null>(null);
  const [includeQuotes, setIncludeQuotes] = useState(true);
  const [existing, setExisting] = useState("");
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    let live = true;
    api.listCharacters({ kind: "campaign", id: cid }).then((rows) => {
      if (live) setCharacters(rows);
    }).catch((err) => { if (live) setError(err); });
    return () => { live = false; abort.current?.abort(); };
  }, [cid]);
  useHotkeys([{ keys: "escape", whileTyping: true, run: () => { if (!busy) onClose(); } }], { modal: true });
  function invalidate() { setDraft(null); setDescription(""); }
  async function suggest() {
    setBusy(true); setError(null);
    const controller = new AbortController(); abort.current = controller;
    try {
      const result = await api.draftPassageCharacter(cid, sid, responseId,
        { name, passage, source_text: sourceText }, controller.signal);
      if (!controller.signal.aborted) { setDraft(result); setDescription(result.description); }
    } catch (err) { if (!controller.signal.aborted) setError(err); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  async function save() {
    setBusy(true); setError(null);
    try {
      await api.savePassageCharacter(cid, sid, responseId, { name, passage, source_text: sourceText,
        description, mes_example: includeQuotes ? draft?.mes_example ?? "" : "",
        ...(existing ? { existing_ref: `characters:${existing}` } : {}) });
      onSaved();
    } catch (err) { setError(err); setBusy(false); }
  }
  return <div className="tagline-modal-backdrop" role="dialog" aria-modal="true" aria-label="Character from passage">
    <div className="tagline-modal import-dialog">
      <h2>Character from passage</h2>
      <p>Select the person and their evidence. Review the draft before adding it to this campaign.</p>
      <label>Name<input aria-label="Character name" value={name} disabled={busy} maxLength={200}
        onChange={(e) => { setName(e.target.value); invalidate(); }} /></label>
      <label>Selected passage<textarea aria-label="Selected passage" rows={5} value={passage} disabled={busy}
        onChange={(e) => { setPassage(e.target.value); invalidate(); }} /></label>
      <button disabled={busy || !name.trim() || !passage.trim()} onClick={() => void suggest()}>Draft description and examples</button>
      <label>Description<textarea aria-label="Character description" rows={5} value={description} disabled={busy}
        onChange={(e) => setDescription(e.target.value)} /></label>
      <p className="subtle">The draft is optional. Sparse evidence should leave a sparse card. Explicit authored facts and speech constraints take priority over inferred habits or example lines; review conflicting evidence in the character editor.</p>
      {draft && <>
        <label><input type="checkbox" checked={includeQuotes} disabled={busy}
          onChange={(e) => setIncludeQuotes(e.target.checked)} />Include evidenced dialogue examples</label>
        <pre>{draft.mes_example || "No unambiguous attributed dialogue was found."}</pre>
      </>}
      <label>Save to<select aria-label="Save character to" value={existing} disabled={busy}
        onChange={(e) => setExisting(e.target.value)}>
        <option value="">New campaign character</option>
        {characters.map((character) => <option key={character.id} value={character.id}>Append to {character.name}</option>)}
      </select></label>
      {error !== null && <p role="alert"><ErrorNote err={error} /></p>}
      <div className="form-actions">
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button disabled={busy || !name.trim() || !passage.trim()} onClick={() => void save()}>
          {busy ? "Working…" : existing ? "Attach reviewed evidence" : "Create reviewed character"}
        </button>
      </div>
    </div>
  </div>;
}
