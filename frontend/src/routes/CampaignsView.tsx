import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  deleteCampaign,
  discoverCampaigns,
  fetchCampaigns,
  rescanCampaigns,
  type CampaignSummaryPayload,
} from "../api/wizard";
import { useStore } from "../state/useStore";
import type { CampaignSummary } from "../state/storeContext";
import { CardIconBar } from "../components/CardIconBar";
import { deleteAction } from "../components/cardActions";
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
  const sort = (a: CampaignNode, b: CampaignNode) => a.campaign.name.localeCompare(b.campaign.name);
  const visit = (n: CampaignNode) => {
    n.children.sort(sort);
    n.children.forEach(visit);
  };
  roots.sort(sort);
  roots.forEach(visit);
  return roots;
}

/** Flatten the forest into a list of (node, depth) for card layout. */
function flatten(forest: CampaignNode[]): { node: CampaignNode; depth: number }[] {
  const out: { node: CampaignNode; depth: number }[] = [];
  const visit = (n: CampaignNode, depth: number) => {
    out.push({ node: n, depth });
    n.children.forEach((c) => visit(c, depth + 1));
  };
  forest.forEach((n) => visit(n, 0));
  return out;
}

interface CampaignCardProps {
  node: CampaignNode;
  depth: number;
  onFork: (campaign: CampaignSummary) => void;
  onDelete: (campaign: CampaignSummary) => void;
  busyDeleting: boolean;
}

function CampaignCard({ node, depth, onFork, onDelete, busyDeleting }: CampaignCardProps) {
  const isChild = depth > 0;
  return (
    <li className={`campaign-card${isChild ? " is-child" : ""}`}>
      <Link to={`/campaigns/${node.campaign.id}`} className="campaign-card-title">
        {node.campaign.name}
      </Link>
      {(isChild || node.forkedAtPostId) && (
        <p className="campaign-card-meta">
          {isChild && <>Fork </>}
          {node.forkedAtPostId && <>at {node.forkedAtPostId}</>}
        </p>
      )}
      <div className="campaign-card-actions">
        <button type="button" onClick={() => onFork(node.campaign)}>
          Fork
        </button>
        <Link to={`/campaigns/${node.campaign.id}/settings`}>Settings</Link>
      </div>
      <CardIconBar
        actions={[
          deleteAction({
            onClick: () => onDelete(node.campaign),
            label: `Delete campaign ${node.campaign.name}`,
            busy: busyDeleting,
          }),
        ]}
      />
    </li>
  );
}

export function CampaignsView() {
  const { state, dispatch } = useStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forkSource, setForkSource] = useState<CampaignSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshErr, setRefreshErr] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  async function refresh() {
    setRefreshing(true);
    setRefreshErr(null);
    try {
      await discoverCampaigns();
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

  async function handleDelete(c: CampaignSummary) {
    if (
      !window.confirm(
        `Delete campaign "${c.name}"? This removes the campaign and all of its scenes from disk. This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingId(c.id);
    setDeleteErr(null);
    try {
      await deleteCampaign(c.id);
      await reload();
    } catch (err) {
      setDeleteErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDeletingId(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await discoverCampaigns().catch(() => {});
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
  const flat = useMemo(() => flatten(forest), [forest]);

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
      {deleteErr && (
        <p className="wizard-error" role="alert">
          {deleteErr}
        </p>
      )}
      {!loading && state.campaigns.length === 0 && !error && (
        <p>No campaigns yet. Click "New campaign" to start the creation wizard.</p>
      )}
      {flat.length > 0 && (
        <ul className="campaign-list">
          {flat.map(({ node, depth }) => (
            <CampaignCard
              key={node.campaign.id}
              node={node}
              depth={depth}
              onFork={(c) => setForkSource(c)}
              onDelete={(c) => void handleDelete(c)}
              busyDeleting={deletingId === node.campaign.id}
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
