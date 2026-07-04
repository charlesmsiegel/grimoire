import { useState } from "react";
import type { CharacterSummary } from "../api/client";

// Picker for a greeting image's subjects: the greeting's present cast as
// one-click chips on top, a filterable list of all world characters below.
export function SubjectsPopover({ chars, present, value, onSave, onClose }: {
  chars: CharacterSummary[]; present: string[]; value: string[];
  onSave: (subjects: string[]) => void; onClose: () => void;
}) {
  const [sel, setSel] = useState<string[]>(value);
  const [q, setQ] = useState("");
  const toggle = (cid: string) =>
    setSel((s) => (s.includes(cid) ? s.filter((x) => x !== cid) : [...s, cid]));
  const presentChars = chars.filter((c) => present.includes(c.id));
  const others = chars.filter(
    (c) => !present.includes(c.id) && c.name.toLowerCase().includes(q.toLowerCase()));
  const chip = (c: CharacterSummary) => (
    <button key={c.id} className={"chip" + (sel.includes(c.id) ? " on" : "")}
            onClick={() => toggle(c.id)}>{c.name}</button>
  );
  return (
    <span className="subjects-popover" role="dialog" aria-label="Image subjects">
      {presentChars.length > 0 && (
        <>
          <span className="field-hint">Present in this greeting</span>
          <span className="chips">{presentChars.map(chip)}</span>
        </>
      )}
      <input type="text" placeholder="Search all characters…" value={q}
             aria-label="Search characters" onChange={(e) => setQ(e.target.value)} />
      {others.length > 0 && <span className="chips">{others.map(chip)}</span>}
      <span className="form-actions">
        <button className="subtle" onClick={onClose}>Cancel</button>
        <button className="primary" onClick={() => onSave(sel)}>Save</button>
      </span>
    </span>
  );
}
