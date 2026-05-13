import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { fetchCampaigns, type CampaignSummaryPayload } from "../api/wizard";
import { useStore } from "../state/useStore";

export function CampaignsView() {
  const { state, dispatch } = useStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await fetchCampaigns();
        if (!cancelled) {
          dispatch({
            type: "set-campaigns",
            campaigns: rows.map((r: CampaignSummaryPayload) => ({ id: r.id, name: r.name })),
          });
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : String(err));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  return (
    <section className="route campaigns-view" aria-labelledby="campaigns-heading">
      <header className="route-header">
        <h2 id="campaigns-heading">Campaigns</h2>
        <Link to="/campaigns/new" className="button-link primary">
          + New campaign
        </Link>
      </header>
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}
      {!loading && state.campaigns.length === 0 && !error && (
        <p>No campaigns yet. Click "New campaign" to start the creation wizard.</p>
      )}
      {state.campaigns.length > 0 && (
        <ul className="campaign-list">
          {state.campaigns.map((c) => (
            <li key={c.id}>
              <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
              <Link to={`/campaigns/${c.id}/settings`} className="campaign-list-settings">
                Settings
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
