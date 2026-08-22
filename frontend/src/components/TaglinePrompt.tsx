import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ErrorNote } from "./ErrorNote";
import { otherModalOpen, watchHotkeys } from "../shortcuts/registry";
import { useHotkeys } from "../shortcuts/useHotkeys";

export function TaglinePrompt({ wid, cid, name, onClose, onSaved }:
  { wid: string; cid: string; name: string; onClose: () => void; onSaved?: (text: string) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // The raw rejection, not its text: `kind` is what says the model could
  // not be reached at all, and stringifying here would throw it away (#210).
  const [error, setError] = useState<unknown>(null);

  // This is the one dialog in the app that appears without the reader asking:
  // it arrives when an import finishes, which can be while they have the avatar
  // crop, the URL prompt or a sheet takeover open. Every one of those shares or
  // beats its z-index, so it would sit UNDER what they are looking at while
  // taking Escape from it -- and its Escape skips a queue entry, so they would
  // silently lose a character's turn.
  //
  // So it waits, and it asks the registry rather than being told which siblings
  // to avoid: a list would have to be kept by whoever adds the next dialog, and
  // could not name the sheet takeover at all, whose open state lives inside
  // `SheetPanel` (PR #400 review). Nothing is lost by waiting -- the queue is
  // untouched, so this gets its turn the moment the screen is clear.
  //
  // The initial read takes no `self` because this scope is not registered until
  // the effect below runs, and every later read excludes it -- without that,
  // being modal would make it see itself and defer forever.
  const [waiting, setWaiting] = useState(() => otherModalOpen());
  const self = useHotkeys(
    [{ keys: "escape", label: "Skip the tagline", group: "THIS PANEL",
       // Escape is Skip: the same dismissal that button offers, and
       // unconditional because Skip is. `busy` disables Generate, not the way
       // out (PR #400 review).
       enabled: !waiting, whileTyping: true, run: onClose }],
    { modal: !waiting },
  );
  useEffect(() => {
    const recheck = () => setWaiting(otherModalOpen(self));
    recheck();
    return watchHotkeys(recheck);
  }, [self]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const { tagline } = await api.generateCharacterTagline(wid, cid);
      setText(tagline);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (text.trim()) {
      try {
        await api.setCharacterTagline(wid, cid, text.trim());
      } catch (err: unknown) {
        setError(err);
        return;
      }
      onSaved?.(text.trim());
    }
    onClose();
  }

  if (waiting) return null;

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Set tagline">
      <div className="tagline-modal">
        <h3>Tagline for {name}</h3>
        <p className="field-hint">
          A one-sentence identity for the off-scene cast. Type your own, or generate one.
        </p>
        <textarea aria-label="Tagline" value={text} rows={2}
                  onChange={(e) => setText(e.target.value)} />
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        <div className="form-actions">
          <button className="subtle" type="button" disabled={busy} onClick={generate}>
            {busy ? "Generating…" : "Generate"}
          </button>
          <button className="primary" type="button" onClick={save}>Save</button>
          <button className="subtle" type="button" onClick={onClose}>Skip</button>
        </div>
      </div>
    </div>
  );
}
