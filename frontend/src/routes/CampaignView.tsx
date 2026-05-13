import { NavLink, Outlet, useParams } from "react-router-dom";

import { PlayView } from "./campaign/PlayView";

const subSections: { to: string; label: string; end?: boolean }[] = [
  { to: "", label: "Play", end: true },
  { to: "cast", label: "Cast" },
  { to: "world", label: "World" },
  { to: "timeline", label: "Timeline" },
  { to: "mechanics", label: "Mechanics" },
  { to: "composition", label: "Composition" },
  { to: "images", label: "Images" },
];

export function CampaignView() {
  const { campaignId } = useParams();
  if (!campaignId) {
    return (
      <section className="route campaign-view">
        <p>Missing campaign id.</p>
      </section>
    );
  }
  return (
    <section className="campaign-view" aria-labelledby="campaign-heading">
      <header className="campaign-header">
        <h2 id="campaign-heading">Campaign: {campaignId}</h2>
        <nav className="campaign-subnav" aria-label="Campaign sections">
          {subSections.map((s) => (
            <NavLink
              key={s.to || "play"}
              to={s.to}
              end={s.end}
              className={({ isActive }) =>
                isActive ? "campaign-subnav-link active" : "campaign-subnav-link"
              }
            >
              {s.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <div className="campaign-body">
        <Outlet />
      </div>
    </section>
  );
}

export function CampaignPlayRoute() {
  const { campaignId } = useParams();
  if (!campaignId) return null;
  return <PlayView campaignId={campaignId} />;
}
