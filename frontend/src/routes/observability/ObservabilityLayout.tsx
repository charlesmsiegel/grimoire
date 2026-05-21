import { NavLink, Outlet } from "react-router-dom";

const tabs = [{ to: "performance", label: "Performance" }];

export function ObservabilityLayout() {
  return (
    <section className="route observability-view" aria-labelledby="observability-heading">
      <header className="observability-header">
        <h2 id="observability-heading">Observability</h2>
        <nav className="observability-tabs" aria-label="Observability sections">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                isActive ? "observability-tab active" : "observability-tab"
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <Outlet />
    </section>
  );
}
