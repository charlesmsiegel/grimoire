import { useState } from "react";
import { api } from "../api/client";

export function TaglinePrompt({ wid, cid, name, onClose }:
  { wid: string; cid: string; name: string; onClose: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const { tagline } = await api.generateCharacterTagline(wid, cid);
      setText(tagline);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (text.trim()) {
      try {
        await api.setCharacterTagline(wid, cid, text.trim());
      } catch (err: any) {
        setError(err.detail ?? String(err));
        return;
      }
    }
    onClose();
  }

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Set tagline">
      <div className="tagline-modal">
        <h3>Tagline for {name}</h3>
        <p className="field-hint">
          A one-sentence identity for the off-scene cast. Type your own, or generate one.
        </p>
        <textarea aria-label="Tagline" value={text} rows={2}
                  onChange={(e) => setText(e.target.value)} />
        {error && <div className="banner">{error}</div>}
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
