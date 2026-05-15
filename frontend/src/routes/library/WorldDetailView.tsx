import { NavLink, Outlet, useParams } from "react-router-dom";

import { libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

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

export function WorldDetailView() {
  const { worldId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    () => libraryApi.getWorld(worldId),
    [worldId],
  );

  return (
    <div className="library-section world-detail">
      <header className="world-detail-header">
        <p className="library-breadcrumb">
          <NavLink to="/library/worlds">Worlds</NavLink>
          {" / "}
          {data?.name || worldId}
        </p>
        <AsyncBoundary loading={loading} error={error} onRetry={reload}>
          <h3>{data?.name || worldId}</h3>
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
      <Outlet />
    </div>
  );
}
