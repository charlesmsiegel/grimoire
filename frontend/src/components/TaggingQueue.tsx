import { useState } from "react";
import { api, type Appearance, type CharacterSummary, type Greeting } from "../api/client";

// Stepper over greeting images with no subjects entry. Save / No-subjects PUT
// then advance; Skip advances only (the image stays unreviewed).
export function TaggingQueue({ wid, chars, greetings, queue, onClose, onSaved }: {
  wid: string; chars: CharacterSummary[]; greetings: Greeting[];
  queue: Appearance[]; onClose: () => void; onSaved: (gid: string) => void;
}) {
  const [items, setItems] = useState<Appearance[]>(queue);
  const [sel, setSel] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const total = queue.length;
  const cur = items[0];

  if (!cur) {
    return (
      <div className="tagging-queue">
        <p>All images tagged 🎉</p>
        <div className="form-actions"><button className="primary" onClick={onClose}>Close</button></div>
      </div>
    );
  }

  const pos = total - items.length + 1;
  const present = greetings.find((g) => g.id === cur.gid)?.present ?? [];
  const presentChars = chars.filter((c) => present.includes(c.id));
  const others = chars.filter(
    (c) => !present.includes(c.id) && c.name.toLowerCase().includes(q.toLowerCase()));
  const toggle = (cid: string) =>
    setSel((s) => (s.includes(cid) ? s.filter((x) => x !== cid) : [...s, cid]));
  const chip = (c: CharacterSummary) => (
    <button key={c.id} className={"chip" + (sel.includes(c.id) ? " on" : "")}
            onClick={() => toggle(c.id)}>{c.name}</button>
  );

  function advance() {
    setItems((it) => it.slice(1));
    setSel([]);
    setQ("");
    setError(null);
  }

  async function save(subjects: string[]) {
    try {
      await api.setImageSubjects(wid, cur.gid, cur.name, subjects);
      onSaved(cur.gid);
      advance();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="tagging-queue">
      {error && <div className="banner">{error}</div>}
      <div className="field-hint">Tagging {pos} / {total} — {cur.greeting_name}</div>
      <img className="queue-image" alt={`${cur.greeting_name} art`} src={cur.url} />
      {presentChars.length > 0 && (
        <>
          <div className="field-hint">Present in this greeting</div>
          <div className="chips">{presentChars.map(chip)}</div>
        </>
      )}
      <input type="text" placeholder="Search all characters…" value={q}
             aria-label="Search characters" onChange={(e) => setQ(e.target.value)} />
      {others.length > 0 && <div className="chips">{others.map(chip)}</div>}
      <div className="form-actions">
        <button className="subtle" onClick={onClose}>Close</button>
        <button className="subtle" onClick={advance}>Skip</button>
        <button className="subtle" onClick={() => save([])}>No subjects</button>
        <button className="primary" onClick={() => save(sel)} disabled={sel.length === 0}>Save & next</button>
      </div>
    </div>
  );
}
