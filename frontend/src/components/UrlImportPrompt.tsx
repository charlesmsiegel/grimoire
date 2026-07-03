import { useState } from "react";

export function UrlImportPrompt({ onSubmit, onClose }:
  { onSubmit: (urls: string[]) => void; onClose: () => void }) {
  const [text, setText] = useState("");

  function submit() {
    const urls = text.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!urls.length) return;
    onClose();
    onSubmit(urls);
  }

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Download from URL">
      <div className="tagline-modal">
        <h3>Download from URL</h3>
        <p className="field-hint">One URL per line — chub.ai links or direct card URLs.</p>
        <textarea aria-label="Card URLs" value={text} rows={6}
                  onChange={(e) => setText(e.target.value)} />
        <div className="form-actions">
          <button className="primary" type="button" onClick={submit}>Add</button>
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
