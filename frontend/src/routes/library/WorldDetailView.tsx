import { useCallback, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";

import { ApiError, libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
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
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getWorld(worldId), [worldId]),
  );

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
      <Outlet />
    </div>
  );
}
