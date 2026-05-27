import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { campaignApi } from "../api/campaign";
import { markEnd, markStart } from "../state/perf";
import { PlayView } from "./campaign/PlayView";

const subSections: { to: string; label: string; end?: boolean }[] = [
  { to: "", label: "Play", end: true },
  { to: "cast", label: "Cast" },
  { to: "world", label: "World" },
  { to: "timeline", label: "Timeline" },
  { to: "ledger", label: "Ledger" },
  { to: "mechanics", label: "Mechanics" },
  { to: "composition", label: "Composition" },
  { to: "images", label: "Images" },
  { to: "debug/prompt", label: "Debug prompt" },
  { to: "observability/turns", label: "Observability" },
  { to: "budget", label: "Budget" },
  { to: "settings", label: "Settings" },
];

interface PreservedSummary {
  active: string | null;
  preserved: { mechanics_id: string; count: number }[];
}

export function CampaignView() {
  const { campaignId } = useParams();
  const [campaignName, setCampaignName] = useState<string | null>(null);

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

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    campaignApi.get(campaignId).then((c) => {
      if (!cancelled) setCampaignName(c.name ?? null);
    }).catch(() => {});
    return () => { cancelled = true; };
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
        <h2 id="campaign-heading">{campaignName ?? campaignId}</h2>
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
      <PreservedSheetsBanner campaignId={campaignId} />
      <div className="campaign-body">
        <Outlet />
      </div>
    </section>
  );
}

/**
 * Spec 06 §Switching modules mid-campaign: warn when sheets from a previously
 * bound mechanics module are still on disk. The endpoint is best-effort —
 * if the backend hasn't implemented it yet, the banner silently stays
 * hidden so the rest of the campaign view keeps working.
 */
function PreservedSheetsBanner({ campaignId }: { campaignId: string }) {
  const [summary, setSummary] = useState<PreservedSummary | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    campaignApi
      .preservedSheets(campaignId)
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch((err: unknown) => {
        // 404 → endpoint not implemented yet, suppress.
        if (err instanceof ApiError && err.status === 404) return;
        // Other errors: silently skip — the banner is best-effort context.
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  if (!summary || dismissed) return null;
  const orphans = summary.preserved.filter(
    (p) => p.mechanics_id !== (summary.active ?? "") && p.count > 0,
  );
  if (orphans.length === 0) return null;
  const totalCount = orphans.reduce((acc, p) => acc + p.count, 0);

  return (
    <div className="preserved-sheets-banner" role="status">
      <span>
        This campaign has {totalCount} sheet{totalCount === 1 ? "" : "s"} from{" "}
        {orphans.map((o) => o.mechanics_id).join(", ")} preserved from a previous mechanics binding.
      </span>{" "}
      <Link to={`/campaigns/${encodeURIComponent(campaignId)}/mechanics`}>
        Review preserved sheets
      </Link>
      <button type="button" aria-label="Dismiss" onClick={() => setDismissed(true)}>
        ×
      </button>
    </div>
  );
}

export function CampaignPlayRoute() {
  const { campaignId } = useParams();
  if (!campaignId) return null;
  return <PlayView campaignId={campaignId} />;
}
