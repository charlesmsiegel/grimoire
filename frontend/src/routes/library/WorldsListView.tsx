import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { markEnd } from "../../state/perf";
import { AsyncBoundary } from "./AsyncBoundary";

export function WorldsListView() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.listWorlds(), []),
  );

  // End the `library:render` span started in `LibraryLayout` the first time
  // the worlds list completes loading without error. Reloads don't restart
  // the measurement — the budget targets initial render to first content.
  const measuredRef = useRef(false);
  useEffect(() => {
    if (!measuredRef.current && !loading && !error && data) {
      measuredRef.current = true;
      markEnd("library:render");
    }
  }, [loading, error, data]);

  const [creating, setCreating] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const created = await libraryApi.createWorld(newId.trim(), { name: newName.trim() });
      setCreating(false);
      setNewId("");
      setNewName("");
      navigate(`/library/worlds/${encodeURIComponent(created.id)}`);
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="library-section">
      <header className="library-section-header">
        <h3>Worlds</h3>
        <button onClick={() => setCreating((c) => !c)} aria-expanded={creating}>
          {creating ? "Cancel" : "+ New world"}
        </button>
      </header>

      {creating && (
        <form onSubmit={submit} className="library-form" aria-label="Create world">
          <label>
            <span>ID</span>
            <input
              required
              value={newId}
              pattern="[a-z0-9][a-z0-9-]*"
              title="lowercase letters, digits, and hyphens"
              onChange={(e) => setNewId(e.target.value)}
            />
          </label>
          <label>
            <span>Name</span>
            <input required value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          {submitErr && (
            <p className="library-error" role="alert">
              {submitErr}
            </p>
          )}
        </form>
      )}

      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No worlds yet. Create one to get started."
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {data?.map((s) => (
            <li key={s.id} className="library-card">
              <Link to={`/library/worlds/${encodeURIComponent(s.id)}`}>
                <h4>{s.name || s.id}</h4>
                <small>{s.id}</small>
                {s.genre && <p className="library-card-genre">{s.genre}</p>}
                {s.description && <p className="library-card-desc">{s.description}</p>}
                <p className="library-card-meta">
                  v{s.version} · {s.tags.length} tag{s.tags.length === 1 ? "" : "s"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </div>
  );
}
