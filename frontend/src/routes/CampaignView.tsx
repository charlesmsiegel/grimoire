import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";

import { campaignApi } from "../api/campaign";
import { useResource } from "../api/useResource";
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

export function CampaignView() {
  const { campaignId } = useParams();
  const [navCollapsed, setNavCollapsed] = useState(false);

  // The load is best-effort: on error we fall back to showing the id and skip
  // the preserved-sheets check (mechanicsModule stays undefined = "not loaded").
  const { data: campaign } = useResource(
    useCallback(
      () => (campaignId ? campaignApi.get(campaignId) : Promise.resolve(null)),
      [campaignId],
    ),
  );
  const campaignName = campaign?.name ?? null;
  const mechanicsModule = campaign ? (campaign.mechanics_module ?? null) : undefined;

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
        <button
          type="button"
          className="campaign-header-toggle"
          onClick={() => setNavCollapsed((c) => !c)}
          aria-expanded={!navCollapsed}
          aria-controls="campaign-subnav"
        >
          <h2 id="campaign-heading">{campaignName ?? campaignId}</h2>
          <span className="campaign-header-chevron" aria-hidden>
            {navCollapsed ? "▸" : "▾"}
          </span>
        </button>
        {!navCollapsed && (
          <nav id="campaign-subnav" className="campaign-subnav" aria-label="Campaign sections">
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
        )}
      </header>
      <PreservedSheetsBanner campaignId={campaignId} mechanicsModule={mechanicsModule} />
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
 *
 * When `mechanicsModule` is `undefined` the parent campaign load hasn't
 * finished yet; when it's `null` the campaign has no mechanics bound and
 * there are no preserved sheets to check by definition, so skip the fetch.
 */
export function PreservedSheetsBanner({
  campaignId,
  mechanicsModule,
}: {
  campaignId: string;
  mechanicsModule: string | null | undefined;
}) {
  const [dismissed, setDismissed] = useState(false);

  // Best-effort: a 404 (endpoint not implemented) or any other error leaves
  // data null and the banner hidden. When no mechanics is bound, skip the
  // fetch entirely (resolve to null).
  const { data: summary } = useResource(
    useCallback(
      () => (mechanicsModule ? campaignApi.preservedSheets(campaignId) : Promise.resolve(null)),
      [campaignId, mechanicsModule],
    ),
  );

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
