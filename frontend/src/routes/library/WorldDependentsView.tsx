/**
 * Lists campaigns whose composition references this world. The backend
 * publishes per-entity dependents directly; for world-wide queries we
 * fan out through `/api/campaigns/<id>/composition`. The list is small in
 * practice (campaigns count, not entity count) so this stays fast.
 */

import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import { useResource } from "../../api/useResource";

interface CampaignSummary {
  id: string;
  name?: string;
}

interface WorldRef {
  world_id: string;
  priority: number;
  bound_at_version: number;
  track_latest: boolean;
  include?: string[];
}

interface CompositionPayload {
  worlds?: WorldRef[];
}

interface DependentRow {
  campaign: CampaignSummary;
  ref: WorldRef;
}

export function WorldDependentsView() {
  const { worldId = "" } = useParams();
  const {
    data: rows,
    loading,
    error,
  } = useResource(
    useCallback(async (): Promise<DependentRow[]> => {
      const campaigns = await api.get<CampaignSummary[]>(`/api/campaigns`);
      const compositions = await Promise.all(
        campaigns.map(async (c) => {
          try {
            const comp = await api.get<CompositionPayload>(
              `/api/campaigns/${encodeURIComponent(c.id)}/composition`,
            );
            return { campaign: c, comp };
          } catch {
            return { campaign: c, comp: null };
          }
        }),
      );
      const matched: DependentRow[] = [];
      for (const { campaign, comp } of compositions) {
        const ref = comp?.worlds?.find((r) => r.world_id === worldId);
        if (ref) matched.push({ campaign, ref });
      }
      return matched;
    }, [worldId]),
  );

  if (loading && rows === null)
    return <p className="library-status">Loading dependent campaigns…</p>;
  if (error) {
    return (
      <p className="library-error" role="alert">
        Failed to load: {error.message}
      </p>
    );
  }
  if (rows === null || rows.length === 0) {
    return <p className="library-status">No campaigns currently reference this world.</p>;
  }

  return (
    <section className="world-dependents">
      <p>
        Editing entities in this world affects pinned campaigns only after they upgrade their ref;{" "}
        <code>track_latest</code> campaigns see changes immediately.
      </p>
      <table className="library-table">
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Priority</th>
            <th>Bound version</th>
            <th>Tracking</th>
            <th>Include filter</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ campaign, ref }) => (
            <tr key={campaign.id}>
              <td>
                <Link to={`/campaigns/${encodeURIComponent(campaign.id)}`}>
                  {campaign.name || campaign.id}
                </Link>
              </td>
              <td>{ref.priority}</td>
              <td>{ref.bound_at_version}</td>
              <td>{ref.track_latest ? "track latest" : "pinned"}</td>
              <td>{(ref.include ?? []).join(", ") || "all"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
