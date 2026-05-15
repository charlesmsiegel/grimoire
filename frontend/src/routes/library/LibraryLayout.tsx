import { NavLink, Outlet } from "react-router-dom";

const tabs = [
  { to: "/library/settings", label: "Settings" },
  { to: "/library/style-guides", label: "Style Guides" },
  { to: "/library/image-presets", label: "Image Presets" },
  { to: "/library/mechanics", label: "Installed Mechanics" },
  { to: "/library/plugins", label: "Installed Plugins" },
];

export function LibraryLayout() {
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
