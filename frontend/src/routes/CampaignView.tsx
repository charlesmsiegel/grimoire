import { useEffect, useRef } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";

import { markEnd, markStart } from "../state/perf";
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

  // Spec 14 §Performance budgets: campaign switch < 300ms with library cached.
  // We mark on every campaignId change; the first frame after the new value
  // shows up in the DOM ends the measurement. This is a coarse approximation
  // — it does not wait for nested async loads — but matches the budget which
  // targets perceived layout, not data hydration.
  const prevIdRef = useRef<string | undefined>(undefined);
  if (campaignId && prevIdRef.current !== campaignId) {
    markStart("campaign:switch");
    prevIdRef.current = campaignId;
  }
  useEffect(() => {
    if (campaignId) {
      markEnd("campaign:switch");
    }
  }, [campaignId]);

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
