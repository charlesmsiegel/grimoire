import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { markStart } from "../../state/perf";

const tabs = [
  { to: "/library/worlds", label: "Worlds" },
  { to: "/library/style-guides", label: "Style Guides" },
  { to: "/library/image-presets", label: "Image Presets" },
  { to: "/library/mechanics", label: "Installed Mechanics" },
  { to: "/library/plugins", label: "Installed Plugins" },
];

export function LibraryLayout() {
  // Spec 14 §Performance budgets: library 100 assets < 500ms. The matching
  // `markEnd("library:render")` lives in `WorldsListView` (the default tab)
  // so the span covers fetch + render to first content.
  useEffect(() => {
    markStart("library:render");
  }, []);

  return (
    <section className="route library-view" aria-labelledby="library-heading">
      <header className="library-header">
        <h2 id="library-heading">Library</h2>
        <nav className="library-tabs" aria-label="Library sections">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) => (isActive ? "library-tab active" : "library-tab")}
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
