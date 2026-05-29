import { useState } from "react";

import { ApiError } from "../../../api/library";
import { mechanicsApi, type ManifestSpec } from "../../../api/library/mechanics";

export function ModuleCreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [spec, setSpec] = useState<ManifestSpec>({
    id: "",
    name: "",
    version: "1.0.0",
    api_version: "1",
    sheet_kinds: ["character"],
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await mechanicsApi.createModule(spec);
      onCreated(res.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="module-create-form">
      <label htmlFor="new-id">ID</label>
      <input
        id="new-id"
        value={spec.id}
        onChange={(e) => setSpec({ ...spec, id: e.target.value })}
      />
      <label htmlFor="new-name">Name</label>
      <input
        id="new-name"
        value={spec.name}
        onChange={(e) => setSpec({ ...spec, name: e.target.value })}
      />
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      <button onClick={submit} disabled={busy || !spec.id || !spec.name}>
        {busy ? "Creating…" : "Create module"}
      </button>
    </div>
  );
}
