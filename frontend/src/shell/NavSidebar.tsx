import { NavLink } from "react-router-dom";

import { useAppState } from "../state/useStore";

interface NavSection {
  label: string;
  to: string;
  description: string;
  icon: string;
}

const navSections: NavSection[] = [
  { label: "Library", to: "/library", description: "Worlds, style guides, plugins", icon: "L" },
  { label: "Campaigns", to: "/campaigns", description: "Active plays and creation", icon: "C" },
  { label: "Observability", to: "/observability", description: "Metrics, health, errors", icon: "O" },
  { label: "Settings", to: "/settings", description: "App-level configuration", icon: "S" },
];

interface Props {
  collapsed: boolean;
  onToggle: () => void;
}

export function NavSidebar({ collapsed, onToggle }: Props) {
  const { campaigns, activeCampaignId } = useAppState();

  return (
    <nav className="nav-sidebar" aria-label="Primary" data-collapsed={collapsed || undefined}>
      <div className="nav-header">
        <h1 className="nav-brand">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              isActive ? "nav-brand-link active" : "nav-brand-link"
            }
            title={collapsed ? "Grimoire — home" : "Home"}
            aria-label="Grimoire — home"
          >
            <span className="nav-brand-mark" aria-hidden="true" />
            <span className="nav-brand-text">Grimoire</span>
          </NavLink>
        </h1>
        <button
          type="button"
          className="nav-toggle"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-pressed={collapsed}
          title={collapsed ? "Expand sidebar (Ctrl+B)" : "Collapse sidebar (Ctrl+B)"}
          onClick={onToggle}
        >
          <span aria-hidden="true" className="nav-toggle-icon">
            {collapsed ? "›" : "‹"}
          </span>
        </button>
      </div>

      <ul className="nav-sections">
        {navSections.map((s) => (
          <li key={s.to}>
            <NavLink
              to={s.to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              title={collapsed ? s.label : undefined}
            >
              <span className="nav-link-icon" aria-hidden="true">
                {s.icon}
              </span>
              <span className="nav-link-body">
                <span>{s.label}</span>
                <small>{s.description}</small>
              </span>
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
                  title={collapsed ? c.name : undefined}
                >
                  <span className="nav-campaign-mark" aria-hidden="true">
                    {(c.name ?? "?").slice(0, 1).toUpperCase()}
                  </span>
                  <span className="nav-campaign-text">{c.name}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </section>
      )}
    </nav>
  );
}
