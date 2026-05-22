import { useCallback, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";

import { ApiError, fetchWorldDependents, libraryApi, type CampaignRef } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { ImportDialog } from "./ImportDialog";

const ENTITY_TABS = [
  { to: "characters", label: "Characters" },
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
  const [importOpen, setImportOpen] = useState(false);
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

  async function handleFork() {
    setForkErr(null);
    const targetId = window.prompt(
      `Fork "${worldId}" to a new world id (lowercase letters, digits, ._-):`,
      "",
    );
    if (!targetId) return;
    if (!WORLD_ID_PATTERN.test(targetId)) {
      setForkErr(`Invalid id "${targetId}". Use [A-Za-z0-9][A-Za-z0-9._-]*.`);
      return;
    }
    setForking(true);
    try {
      // Fork copies the directory and preserves every entity's asset_id, so
      // the forked world's characters / items / locations / etc. appear as
      // cross-world variants of the source (see VariantsBreadcrumb).
      await libraryApi.forkWorld(worldId, targetId);
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
              className="world-fork-button"
              onClick={() => void handleFork()}
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
            <button type="button" className="world-delete-button" onClick={() => void openDelete()}>
              Delete world
            </button>
          </div>
          {forkErr && (
            <p className="library-error" role="alert">
              {forkErr}
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
              className={({ isActive }) => (isActive ? "world-tab active" : "world-tab")}
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
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
