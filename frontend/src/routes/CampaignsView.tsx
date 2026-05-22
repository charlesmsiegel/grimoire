import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { fetchCampaigns, rescanCampaigns, type CampaignSummaryPayload } from "../api/wizard";
import { useStore } from "../state/useStore";
import type { CampaignSummary } from "../state/storeContext";
import { ForkDialog } from "./campaign/ForkDialog";

interface CampaignNode {
  campaign: CampaignSummary;
  forkedAtPostId: string | null;
  children: CampaignNode[];
}

function buildForest(rows: CampaignSummary[]): CampaignNode[] {
  const byId = new Map<string, CampaignNode>();
  for (const c of rows) {
    byId.set(c.id, {
      campaign: c,
      forkedAtPostId: c.forked_at_post_id ?? null,
      children: [],
    });
  }
  const roots: CampaignNode[] = [];
  for (const node of byId.values()) {
    const parentId = node.campaign.forked_from_campaign_id ?? null;
    if (parentId && byId.has(parentId)) {
      byId.get(parentId)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sort = (a: CampaignNode, b: CampaignNode) =>
    a.campaign.name.localeCompare(b.campaign.name);
  const visit = (n: CampaignNode) => {
    n.children.sort(sort);
    n.children.forEach(visit);
  };
  roots.sort(sort);
  roots.forEach(visit);
  return roots;
}

interface CampaignNodeRowProps {
  node: CampaignNode;
  depth: number;
  onFork: (campaign: CampaignSummary) => void;
}

function CampaignNodeRow({ node, depth, onFork }: CampaignNodeRowProps) {
  return (
    <>
      <li
        className="campaign-list-row"
        style={{ paddingLeft: `${depth * 1.25}rem` }}
      >
        <Link to={`/campaigns/${node.campaign.id}`}>{node.campaign.name}</Link>
        {node.forkedAtPostId && (
          <span className="campaign-list-fork-meta">
            (forked at {node.forkedAtPostId})
          </span>
        )}
        <button
          type="button"
          className="campaign-list-fork-btn"
          onClick={() => onFork(node.campaign)}
        >
          Fork
        </button>
        <Link
          to={`/campaigns/${node.campaign.id}/settings`}
          className="campaign-list-settings"
        >
          Settings
        </Link>
      </li>
      {node.children.map((child) => (
        <CampaignNodeRow
          key={child.campaign.id}
          node={child}
          depth={depth + 1}
          onFork={onFork}
        />
      ))}
    </>
  );
}

export function CampaignsView() {
  const { state, dispatch } = useStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forkSource, setForkSource] = useState<CampaignSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshErr, setRefreshErr] = useState<string | null>(null);

  async function refresh() {
    setRefreshing(true);
    setRefreshErr(null);
    try {
      await rescanCampaigns();
      await reload();
    } catch (err) {
      setRefreshErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }

  async function reload() {
    try {
      const rows = await fetchCampaigns();
      dispatch({
        type: "set-campaigns",
        campaigns: rows.map((r: CampaignSummaryPayload) => ({
          id: r.id,
          name: r.name,
          forked_from_campaign_id: r.forked_from_campaign_id ?? null,
          forked_at_post_id: r.forked_at_post_id ?? null,
        })),
      });
      setLoading(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await fetchCampaigns();
        if (!cancelled) {
          dispatch({
            type: "set-campaigns",
            campaigns: rows.map((r: CampaignSummaryPayload) => ({
              id: r.id,
              name: r.name,
              forked_from_campaign_id: r.forked_from_campaign_id ?? null,
              forked_at_post_id: r.forked_at_post_id ?? null,
            })),
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

  const forest = useMemo(() => buildForest(state.campaigns), [state.campaigns]);

  return (
    <section className="route campaigns-view" aria-labelledby="campaigns-heading">
      <header className="route-header">
        <h2 id="campaigns-heading">Campaigns</h2>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
          title="Re-scan the campaigns folder for changes made outside the UI"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
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
      {refreshErr && (
        <p className="wizard-error" role="alert">
          {refreshErr}
        </p>
      )}
      {!loading && state.campaigns.length === 0 && !error && (
        <p>No campaigns yet. Click "New campaign" to start the creation wizard.</p>
      )}
      {forest.length > 0 && (
        <ul className="campaign-list">
          {forest.map((node) => (
            <CampaignNodeRow
              key={node.campaign.id}
              node={node}
              depth={0}
              onFork={(c) => setForkSource(c)}
            />
          ))}
        </ul>
      )}
      {forkSource && (
        <ForkDialog
          open
          sourceCampaignId={forkSource.id}
          sourceCampaignName={forkSource.name}
          onClose={() => setForkSource(null)}
          onForked={() => {
            setForkSource(null);
            void reload();
          }}
        />
      )}
    </section>
  );
}
