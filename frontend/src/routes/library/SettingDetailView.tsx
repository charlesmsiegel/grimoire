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

export function SettingDetailView() {
  const { settingId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    () => libraryApi.getSetting(settingId),
    [settingId],
  );

  return (
    <div className="library-section setting-detail">
      <header className="setting-detail-header">
        <p className="library-breadcrumb">
          <NavLink to="/library/settings">Settings</NavLink>
          {" / "}
          {data?.name || settingId}
        </p>
        <AsyncBoundary loading={loading} error={error} onRetry={reload}>
          <h3>{data?.name || settingId}</h3>
          {data?.description && <p className="setting-description">{data.description}</p>}
          <p className="setting-meta-line">
            id: <code>{data?.id}</code> · version {data?.version}
            {data?.genre ? ` · ${data.genre}` : ""}
          </p>
        </AsyncBoundary>

        <nav className="setting-tabs" aria-label="Setting sections">
          {ENTITY_TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) => (isActive ? "setting-tab active" : "setting-tab")}
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
