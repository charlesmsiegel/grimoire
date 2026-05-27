import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, fetchWorldDependents, libraryApi, type CampaignRef } from "../../api/library";
import { useResource } from "../../api/useResource";
import { CardFilters } from "../../components/CardFilters";
import { useCardFilters } from "../../hooks/useCardFilters";
import { markEnd } from "../../state/perf";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";

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
  const [refreshing, setRefreshing] = useState(false);
  const [refreshErr, setRefreshErr] = useState<string | null>(null);

  async function refresh() {
    setRefreshing(true);
    setRefreshErr(null);
    try {
      await libraryApi.rescanWorlds();
      reload();
    } catch (err) {
      setRefreshErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }

  const [deleting, setDeleting] = useState<{
    worldId: string;
    worldName: string;
    dependents: CampaignRef[] | undefined;
    busy: boolean;
    err: string | null;
  } | null>(null);

  async function openDelete(worldId: string, worldName: string) {
    setDeleting({ worldId, worldName, dependents: undefined, busy: false, err: null });
    try {
      const deps = await fetchWorldDependents(worldId);
      setDeleting((d) => (d && d.worldId === worldId ? { ...d, dependents: deps } : d));
    } catch {
      setDeleting((d) => (d && d.worldId === worldId ? { ...d, dependents: [] } : d));
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleting({ ...deleting, busy: true, err: null });
    try {
      await libraryApi.deleteWorld(deleting.worldId);
      setDeleting(null);
      reload();
    } catch (err) {
      setDeleting({
        ...deleting,
        busy: false,
        err: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

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
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
          title="Re-scan the library folder for changes made outside the UI"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
        <button onClick={() => setCreating((c) => !c)} aria-expanded={creating}>
          {creating ? "Cancel" : "+ New world"}
        </button>
      </header>
      {refreshErr && (
        <p className="library-error" role="alert">
          {refreshErr}
        </p>
      )}

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
        <WorldsListBody
          worlds={data ?? []}
          onDelete={openDelete}
        />
      </AsyncBoundary>

      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete world "${deleting.worldName}"?`}
          body={
            <p>
              This permanently removes the world directory and all its entities. Cannot be undone.
            </p>
          }
          dependents={deleting.dependents}
          typedConfirmation={{
            expected: deleting.worldId,
            label: `Type id "${deleting.worldId}" to confirm`,
          }}
          busy={deleting.busy}
          error={deleting.err}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

interface WorldsListBodyProps {
  worlds: Array<{
    id: string;
    name: string;
    description: string;
    tags: string[];
    genre: string;
    version: number;
  }>;
  onDelete: (id: string, name: string) => void;
}

function WorldsListBody({ worlds, onDelete }: WorldsListBodyProps) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(worlds, {
      text: (w) => [w.name, w.id, w.description, w.genre],
      tags: (w) => w.tags,
    });

  return (
    <>
      <CardFilters
        search={search}
        onSearch={setSearch}
        availableTags={availableTags}
        selectedTags={selectedTags}
        onToggleTag={toggleTag}
        onClearTags={clearTags}
        searchPlaceholder="Search worlds by name, id, or description…"
        searchLabel="Search worlds"
        resultSummary={
          filtered.length === worlds.length
            ? `${worlds.length} world${worlds.length === 1 ? "" : "s"}`
            : `${filtered.length} of ${worlds.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="library-status">No worlds match the current filters.</p>
      ) : (
        <ul className="library-card-grid">
          {filtered.map((s) => (
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
              <div className="library-card-actions">
                <button
                  type="button"
                  aria-label="Delete world"
                  title="Delete world"
                  onClick={() => onDelete(s.id, s.name || s.id)}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    <line x1="10" y1="11" x2="10" y2="17" />
                    <line x1="14" y1="11" x2="14" y2="17" />
                  </svg>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
