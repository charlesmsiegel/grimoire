import { NavLink } from "react-router-dom";

import { useAppState } from "../state/useStore";

const navSections: { label: string; to: string; description: string }[] = [
  { label: "Library", to: "/library", description: "Settings, style guides, plugins" },
  { label: "Campaigns", to: "/campaigns", description: "Active plays and creation" },
];

export function NavSidebar() {
  const { campaigns, activeCampaignId } = useAppState();

  return (
    <nav className="nav-sidebar" aria-label="Primary">
      <h1 className="nav-brand">Grimoire</h1>
      <ul className="nav-sections">
        {navSections.map((s) => (
          <li key={s.to}>
            <NavLink
              to={s.to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              <span>{s.label}</span>
              <small>{s.description}</small>
            </NavLink>
          </li>
        ))}
      </ul>

      {campaigns.length > 0 && (
        <section className="nav-campaigns" aria-label="Recent campaigns">
          <h2>Recent</h2>
          <ul>
            {campaigns.map((c) => (
              <li key={c.id}>
                <NavLink
                  to={`/campaigns/${c.id}`}
                  className={({ isActive }) =>
                    isActive || activeCampaignId === c.id ? "nav-campaign active" : "nav-campaign"
                  }
                >
                  {c.name}
                </NavLink>
              </li>
            ))}
          </ul>
        </section>
      )}
    </nav>
  );
}
