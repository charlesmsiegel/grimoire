import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then(setWorlds);
  }, []);

  async function rename(id: string, next: string) {
    await api.renameCampaign(id, next);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  return (
    <div className="view">
      <h2>Campaigns</h2>

      <div className="picker">
        <button className="primary" onClick={() => navigate("/campaigns/new")} disabled={worlds.length === 0}>
          + New campaign
        </button>
      </div>
      {worlds.length === 0 && (
        <p className="muted">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      )}

      <div className="list">
        {campaigns.map((c) => (
          <EditableRow
            key={c.id}
            label={c.name}
            subtitle={c.world}
            onSelect={() => navigate(`/campaigns/${c.id}`)}
            onRename={(next) => rename(c.id, next)}
            onDelete={() => remove(c)}
          />
        ))}
      </div>
    </div>
  );
}
