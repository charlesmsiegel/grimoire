import { useEffect, useRef, useState } from "react";
import { api, type Card } from "../api/client";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { ErrorNote } from "./ErrorNote";
import { Field } from "./Field";

const FIELDS = [
  ["description", "Description"], ["personality", "Personality"],
  ["scenario", "Scenario"], ["first_mes", "First message"], ["mes_example", "Example dialogue"],
] as const;
const MAX_TEXT_CHARS = 64000;
// The server counts code points (Python len), so count the same way: a UTF-16
// `.length` would refuse emoji-heavy text the server accepts.
const codePoints = (value: string) => [...value].length;
// A saved character's folder is named from its name; the server refuses longer.
const MAX_NAME_CHARS = 200;

/** #136: parsing and refinement replace an in-memory proposal. Only Save
 * creates a character, through the ordinary creation API. A late answer may
 * never replace another dialog's edits or act in another world's roster. */
export function TextCharacterImport({ wid, onCancel, onSaved }: {
  wid: string;
  onCancel: () => void;
  onSaved: (result: { character: string; version: string }) => void;
}) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [busy, setBusy] = useState<"file" | "parse" | "refine" | "save" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const abort = useRef<AbortController | null>(null);
  const reader = useRef<FileReader | null>(null);
  useEffect(() => () => { abort.current?.abort(); reader.current?.abort(); }, []);

  function cancel() {
    if (busy === "save") return;
    abort.current?.abort(); reader.current?.abort(); onCancel();
  }
  useHotkeys([{ keys: "escape", whileTyping: true, enabled: busy !== "save", run: cancel }], { modal: true });

  function loadFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (!/\.(txt|md)$/i.test(file.name) || file.size > MAX_TEXT_CHARS * 4) {
      // Bytes, not characters: UTF-8 is at most four bytes per code point.
      setError(new Error("Choose a .txt or .md file with at most 64,000 characters.")); return;
    }
    setBusy("file");
    const controller = new AbortController(); abort.current = controller;
    const load = new FileReader(); reader.current = load;
    load.onload = () => {
      if (controller.signal.aborted) return;
      const value = typeof load.result === "string" ? load.result : "";
      if (codePoints(value) > MAX_TEXT_CHARS) {
        setError(new Error("Character text must be at most 64,000 characters."));
      } else {
        setText(value); setFilename(file.name);
      }
      setBusy(null);
    };
    load.onerror = () => {
      if (!controller.signal.aborted) { setError(new Error("Could not read the text file.")); setBusy(null); }
    };
    load.readAsText(file, "utf-8");
  }

  async function run(action: "parse" | "refine" | "save") {
    const controller = new AbortController(); abort.current = controller;
    setBusy(action); setError(null);
    try {
      if (action === "save" && card) {
        const result = await api.createCharacter({ kind: "world", id: wid }, { name: card.data.name, card });
        if (!controller.signal.aborted) onSaved(result);
      } else {
        const result = action === "refine" && card
          ? await api.refineCharacterText(wid, { text, card }, controller.signal)
          : await api.parseCharacterText(wid, { text, filename }, controller.signal);
        if (!controller.signal.aborted) setCard(result.card);
      }
    } catch (err) {
      // A failed refinement must leave the reviewer's current draft intact.
      if (!controller.signal.aborted) setError(err);
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  function edit(key: string, value: string) {
    if (card) setCard({ ...card, data: { ...card.data, [key]: value } });
  }

  return <div className="tagline-modal-backdrop" role="dialog" aria-modal="true" aria-label="Import character text">
    <div className="tagline-modal import-dialog text-character-import">
      <h3>Import character text</h3>
      {card ? <>
        <p>Review the fields before saving the character.</p>
        <Field key="review-name" label="Character name"><input value={card.data.name} disabled={busy !== null}
          maxLength={MAX_NAME_CHARS} onChange={(e) => edit("name", e.target.value)} /></Field>
        {FIELDS.map(([key, label]) => <Field key={key} label={label}>
          <textarea rows={3} value={card.data[key] ?? ""} disabled={busy !== null}
            onChange={(e) => edit(key, e.target.value)} />
        </Field>)}
        <p className="field-hint">Refine with AI sends the source text and edited draft to your configured model.
          It may cost a model call. Review its suggestions before saving.</p>
        <div className="form-actions">
          <button type="button" disabled={busy !== null || !card.data.name.trim()}
            onClick={() => void run("refine")}>{busy === "refine" ? "Refining…" : "Refine with AI"}</button>
          <button type="button" className="subtle" disabled={busy !== null}
            onClick={() => { setCard(null); setError(null); }}>Back to text</button>
        </div>
      </> : <>
        <p>Paste a character description or choose a text file. Parsing is offline and free.</p>
        <Field key="source-file" label="Text file"><input type="file" accept=".txt,.md" disabled={busy !== null}
          onChange={(e) => loadFile(e.target.files?.[0])} /></Field>
        <Field key="source-text" label="Character text"><textarea rows={12} value={text} disabled={busy !== null}
          onChange={(e) => setText(e.target.value)} /></Field>
        {codePoints(text) > MAX_TEXT_CHARS && <p role="alert">Character text must be at most 64,000 characters.</p>}
      </>}
      {error !== null && <div role="alert"><ErrorNote err={error} /></div>}
      <div className="form-actions">
        <button type="button" className="subtle" disabled={busy === "save"} onClick={cancel}>Cancel</button>
        {card
          ? <button type="button" className="primary" disabled={busy !== null || !card.data.name.trim()}
              onClick={() => void run("save")}>{busy === "save" ? "Saving…" : "Save character"}</button>
          : <button type="button" className="primary" disabled={busy !== null || !text.trim() || codePoints(text) > MAX_TEXT_CHARS}
              onClick={() => void run("parse")}>{busy === "parse" ? "Parsing…" : "Review draft"}</button>}
      </div>
    </div>
  </div>;
}
