import { useEffect, useRef, useState } from "react";
import { Field } from "../Field";

/** The SillyTavern import form. It owns the file as parsed, the name to save
 *  it under and whether to bring the response length along (off: see the
 *  hint); the page owns the request and where the result is shown. */
export function PresetImport(
  { busy, newer, onImport, onCancel, onError }: {
    busy: boolean;
    newer: boolean;
    onImport: (name: string, data: unknown, withMax: boolean) => void;
    onCancel: () => void;
    onError: (message: string) => void;
  },
) {
  const [file, setFile] = useState<{ name: string; data: unknown } | null>(null);
  const [importName, setImportName] = useState("");
  const [withMax, setWithMax] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  /** Which file pick is current; a slower read of an earlier pick is ignored. */
  const picked = useRef(0);

  /** Retire any file read still in flight: its result belongs to an import
   *  session that has ended, and must not fill in the next one. */
  function retireReads() {
    picked.current += 1;
  }
  // Left any other way -- the column, the rail -- a read still in flight
  // belongs to no session at all, and its failure must not land as an error
  // on whatever page is open by then.
  useEffect(() => { const reads = picked; return () => { reads.current += 1; }; }, []);

  function pickFile(f: File | undefined) {
    onError("");
    // Only the latest pick may land: a large file read first can finish after
    // a second pick (or a cleared input), and without this it would overwrite
    // the selection the input now shows -- importing the wrong preset under
    // the right name.
    const pick = (picked.current += 1);
    if (!f) { setFile(null); return; }
    // FileReader rather than `Blob.text()`: the same result, and it is the one
    // every browser the Android WebView ships with has had for longest.
    const reader = new FileReader();
    reader.onload = () => {
      if (pick !== picked.current) return;
      try {
        setFile({ name: f.name,
                  data: JSON.parse(typeof reader.result === "string" ? reader.result : "") });
        setImportName((n) => n || f.name.replace(/\.json$/i, ""));
      } catch {
        setFile(null);
        onError(`${f.name} is not a JSON file`);
      }
    };
    reader.onerror = () => { if (pick === picked.current) onError(`Could not read ${f.name}`); };
    reader.readAsText(f);
  }

  return (
          <div className="sampler-form">
            <h3>Import a SillyTavern preset</h3>
            <p className="field-hint">
              Chat Completion and Text Completion presets both work. What maps is
              imported; everything else is listed afterwards so you can see what
              did not come across.
            </p>
            <Field label="Preset file">
              <input ref={fileInput} type="file" accept=".json,application/json"
                     onChange={(e) => pickFile(e.target.files?.[0])} />
            </Field>
            <Field label="Save as">
              <input type="text" value={importName}
                     onChange={(e) => setImportName(e.target.value)} />
            </Field>
            <label className="checkbox-row">
              <input type="checkbox" checked={withMax}
                     onChange={(e) => setWithMax(e.target.checked)} />
              {" "}Include the response length (max tokens)
            </label>
            <p className="field-hint">
              Off by default: SillyTavern writes a response length into every
              preset, and as a hard cap it would cut off absorb and dossiers as
              well as prose — and on a reasoning model the thinking may count
              against it too, leaving little or nothing for the reply.
            </p>
            <div className="form-actions">
              <button className="primary"
                      onClick={() => { if (file) onImport(importName.trim() || file.name, file.data, withMax); }}
                      disabled={busy || newer || !file}>Import</button>
              <button className="subtle"
                      onClick={() => { retireReads(); onCancel(); }}>
                Cancel
              </button>
            </div>
          </div>
  );
}
