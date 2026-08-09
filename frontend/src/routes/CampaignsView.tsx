import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  // Keyed by id AND version, not id alone: a cover that failed to load must
  // not keep its replacement hidden after the next listCampaigns() refresh.
  const [broken, setBroken] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then(setWorlds);
  }, []);

  const worldName = (id: string) => worlds.find((w) => w.id === id)?.name ?? id;

  async function rename() {
    if (!renaming) return;
    await api.renameCampaign(renaming.id, renaming.name);
    setRenaming(null);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  return (
    <div className="page view-anim">
      <div className="page-head">
        <h1 className="page-h1">Campaigns</h1>
        <button className="btn-accent" onClick={() => navigate("/campaigns/new")} disabled={worlds.length === 0}>
          + New Campaign
        </button>
      </div>
      <div className="count-label">
        {campaigns.length} {campaigns.length === 1 ? "campaign" : "campaigns"}
      </div>
      {worlds.length === 0 && (
        <p className="page-note">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      )}
      <div className="list-block">
        {campaigns.map((c) => (
          <div className="list-row" key={c.id}>
            <div className="list-row-cover">
              {c.cover && !broken[`${c.id}:${c.cover}`] && (
                // w=96 for a box `index.css` sizes at 64x64: 1.5x of headroom,
                // so the thumbnail is still sharp on a 1.5x/2x display rather
                // than upscaled. More than that only costs bytes.
                <img className="list-row-cover-img"
                     src={api.campaignCoverUrl(c.id, { w: 96, v: c.cover })}
                     alt={`${c.name} cover`}
                     onError={() => setBroken((b) => ({ ...b, [`${c.id}:${c.cover}`]: true }))} />
              )}
            </div>
            {renaming?.id === c.id ? (
              <input
                className="row-rename" aria-label="Rename campaign" autoFocus
                value={renaming.name}
                onChange={(e) => setRenaming({ id: c.id, name: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }}
              />
            ) : (
              <button className="list-row-main" onClick={() => navigate(`/campaigns/${c.id}`)}>
                <span className="list-row-name">{c.name}</span>
                <span className="list-row-meta">
                  WORLD ▸ {worldName(c.world)} · {c.scenes} SCENES{c.last_scene ? ` · LAST: ${c.last_scene}` : ""}
                </span>
              </button>
            )}
            <div className="row-actions">
              <button aria-label={`Rename ${c.name}`} onClick={() => setRenaming({ id: c.id, name: c.name })}>✎</button>
              <button aria-label={`Delete ${c.name}`} onClick={() => remove(c)}>✕</button>
            </div>
            <span className="list-row-arrow" aria-hidden>→</span>
          </div>
        ))}
      </div>
    </div>
  );
}
