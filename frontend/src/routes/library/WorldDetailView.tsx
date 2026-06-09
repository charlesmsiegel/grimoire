import { useCallback, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";

import { ApiError, fetchWorldDependents, libraryApi, type CampaignRef } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { ImportDialog } from "./ImportDialog";
import { PromptDialog } from "../../components/PromptDialog";

const ENTITY_TABS = [
  { to: ".", label: "Overview", end: true },
  { to: "characters", label: "Characters" },
  { to: "monsters", label: "Monsters" },
  { to: "items", label: "Items" },
  { to: "locations", label: "Locations" },
  { to: "lore", label: "Lore" },
  { to: "factions", label: "Factions" },
  { to: "greetings", label: "Greetings" },
  { to: "meta", label: "Meta" },
  { to: "dependents", label: "Dependent campaigns" },
];

const WORLD_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export function WorldDetailView() {
  const { worldId = "" } = useParams();
  const navigate = useNavigate();
  const [forkErr, setForkErr] = useState<string | null>(null);
  const [forking, setForking] = useState(false);
  const [forkOpen, setForkOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshErr, setRefreshErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<{
    dependents: CampaignRef[] | undefined;
    busy: boolean;
    err: string | null;
  } | null>(null);
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getWorld(worldId), [worldId]),
  );

  async function openDelete() {
    setDeleting({ dependents: undefined, busy: false, err: null });
    try {
      const deps = await fetchWorldDependents(worldId);
      setDeleting((d) => (d ? { ...d, dependents: deps } : d));
    } catch {
      setDeleting((d) => (d ? { ...d, dependents: [] } : d));
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleting({ ...deleting, busy: true, err: null });
    try {
      await libraryApi.deleteWorld(worldId);
      navigate("/library/worlds");
    } catch (err) {
      setDeleting({
        ...deleting,
        busy: false,
        err: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

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

  async function handleFork(targetId: string) {
    if (!targetId || !WORLD_ID_PATTERN.test(targetId)) {
      setForkErr(`Invalid id "${targetId}". Use [A-Za-z0-9][A-Za-z0-9._-]*.`);
      return;
    }
    setForking(true);
    setForkErr(null);
    try {
      // Fork copies the directory and preserves every entity's asset_id, so
      // the forked world's characters / items / locations / etc. appear as
      // cross-world variants of the source (see VariantsBreadcrumb).
      await libraryApi.forkWorld(worldId, targetId);
      setForkOpen(false);
      navigate(`/library/worlds/${encodeURIComponent(targetId)}`);
    } catch (err) {
      setForkErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setForking(false);
    }
  }

  return (
    <div className="library-section world-detail">
      <header className="world-detail-header">
        <p className="library-breadcrumb">
          <NavLink to="/library/worlds">Worlds</NavLink>
          {" / "}
          {data?.name || worldId}
        </p>
        <AsyncBoundary loading={loading} error={error} onRetry={reload}>
          <div className="world-detail-heading">
            <h3>{data?.name || worldId}</h3>
            <button
              type="button"
              className="world-refresh-button"
              onClick={() => void refresh()}
              disabled={refreshing}
              title="Re-scan the library folder for changes made outside the UI"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            <button
              type="button"
              className="world-fork-button"
              onClick={() => {
                setForkErr(null);
                setForkOpen(true);
              }}
              disabled={forking}
            >
              {forking ? "Forking…" : "Fork world"}
            </button>
            <button
              type="button"
              className="world-import-button"
              onClick={() => setImportOpen(true)}
            >
              Import character card
            </button>
            {/* eslint-disable-next-line local/no-bespoke-delete -- world detail delete action, not a card */}
            <button type="button" className="world-delete-button" onClick={() => void openDelete()}>
              Delete world
            </button>
          </div>
          {refreshErr && (
            <p className="library-error" role="alert">
              {refreshErr}
            </p>
          )}
          {data?.description && <p className="world-description">{data.description}</p>}
          <p className="world-meta-line">
            id: <code>{data?.id}</code> · version {data?.version}
            {data?.genre ? ` · ${data.genre}` : ""}
          </p>
        </AsyncBoundary>

        <nav className="world-tabs" aria-label="World sections">
          {ENTITY_TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={(tab as { end?: boolean }).end}
              className={({ isActive }) => (isActive ? "world-tab active" : "world-tab")}
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
      {forkOpen && (
        <PromptDialog
          open
          title={`Fork "${data?.name || worldId}"`}
          label="New world id"
          hint="Lowercase letters, digits, ._- — the fork keeps every entity's asset id, so entities appear as cross-world variants."
          confirmLabel="Fork"
          busy={forking}
          error={forkErr}
          onSubmit={(v) => void handleFork(v)}
          onCancel={() => setForkOpen(false)}
        />
      )}
      {importOpen && (
        <ImportDialog
          worldId={worldId}
          onClose={(committed) => {
            setImportOpen(false);
            if (committed) reload();
          }}
        />
      )}
      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete world "${data?.name || worldId}"?`}
          body={
            <p>
              This permanently removes the world directory and all its entities. Cannot be undone.
            </p>
          }
          dependents={deleting.dependents}
          typedConfirmation={{
            expected: worldId,
            label: `Type id "${worldId}" to confirm`,
          }}
          busy={deleting.busy}
          error={deleting.err}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
      <Outlet />
    </div>
  );
}
