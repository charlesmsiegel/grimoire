import { useState } from "react";
import { api, type EntityKind, type EntityScope, type UndescribedImage } from "../api/client";

/** A stepper over every stored image nobody has described yet.
 *
 *  `TaggingQueue`'s shape, for `TaggingQueue`'s reason: a library that has been
 *  collecting art since before descriptions existed starts with all of it
 *  undescribed, and working through that one editor tab at a time is how it
 *  never gets done.
 *
 *  Three buttons, and they are three different answers — which is exactly what
 *  the store's absent-vs-`""` distinction exists to record:
 *
 *  - **Save** writes the text.
 *  - **No description** writes `""`. The image is reviewed, so it leaves this
 *    queue for good, and it is never offered to the model.
 *  - **Skip** writes nothing. The image stays undescribed and comes back next
 *    time — which is the right answer for "not now", and the wrong one to
 *    conflate with "nothing to say". */
export function DescribeQueue({ wid, queue, onClose, onSaved }: {
  wid: string;
  queue: UndescribedImage[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [items, setItems] = useState<UndescribedImage[]>(queue);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"save" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Captured once. `onSaved` re-reads the backlog, so the `queue` PROP shrinks
  // under us while `items` does not -- and "Describing 2 / 3" would jump back
  // to "1 / 2" the moment the first save landed. The denominator is how much
  // work this sitting started with.
  const [total] = useState(queue.length);
  const cur = items[0];

  if (!cur) {
    return (
      <div className="tagging-queue">
        <p>Every image is described 🎉</p>
        <div className="form-actions">
          <button className="primary" onClick={onClose}>Close</button>
        </div>
      </div>
    );
  }

  const pos = total - items.length + 1;
  const scope: EntityScope = { kind: "world", id: wid };

  function advance() {
    setItems((it) => it.slice(1));
    setText("");
    setError(null);
  }

  /** One write, dispatched on the surface the image belongs to. The three
   *  endpoints differ only in their URL shape — an actor's art is per version,
   *  an entity's is keyed on a fixed "default". */
  function write(description: string) {
    if (cur.kind === "characters") {
      return api.setCharacterImageDescription(scope, cur.id, cur.vid, cur.name, description);
    }
    if (cur.kind === "pcs") {
      return api.setPCImageDescription(scope, cur.id, cur.vid, cur.name, description);
    }
    return api.setEntityImageDescription(scope, cur.kind as EntityKind, cur.id,
                                         cur.name, description);
  }

  async function save(description: string) {
    setBusy("save");
    setError(null);
    try {
      await write(description);
      onSaved();
      advance();
    } catch (err: unknown) {
      setError((err as { detail?: string })?.detail ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  async function draft() {
    setBusy("draft");
    setError(null);
    try {
      // Characters only: that is the surface the draft route serves today, and
      // the button is hidden elsewhere rather than offered and failing.
      const r = await api.draftCharacterImageDescription(wid, cur.id, cur.vid, cur.name);
      setText(r.description);
    } catch (err: unknown) {
      setError((err as { detail?: string })?.detail ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="tagging-queue">
      {error != null && <div className="banner">{error}</div>}
      <div className="field-hint">
        Describing {pos} / {total} — {cur.record_name} · {cur.name}
      </div>
      <img className="queue-image" alt={`${cur.record_name} art`} src={cur.url} />
      <textarea value={text} rows={4} aria-label="Description"
                placeholder="What does this picture show?"
                onChange={(e) => setText(e.target.value)} />
      <div className="form-actions">
        <button className="primary" disabled={busy !== null}
                onClick={() => { void save(text); }}>
          {busy === "save" ? "Saving…" : "Save"}
        </button>
        <button className="subtle" disabled={busy !== null}
                onClick={() => { void save(""); }}>No description</button>
        {cur.kind === "characters" && (
          <button className="subtle" disabled={busy !== null}
                  onClick={() => { void draft(); }}>
            {busy === "draft" ? "Looking…" : "Describe it for me"}
          </button>
        )}
        <button className="subtle" disabled={busy !== null} onClick={advance}>Skip</button>
        <button className="subtle" disabled={busy !== null} onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
