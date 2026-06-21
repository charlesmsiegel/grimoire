import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [world, setWorld] = useState("");

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then((ws) => {
      setWorlds(ws);
      if (ws.length) setWorld(ws[0].id);
    });
  }, []);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed || !world) return;
    const { id } = await api.createCampaign(trimmed, world);
    navigate(`/campaigns/${id}`);
  }

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

      {worlds.length === 0 ? (
        <p className="muted">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      ) : (
        <div className="picker">
          <input
            placeholder="Campaign name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <select value={world} onChange={(e) => setWorld(e.target.value)} aria-label="World">
            {worlds.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <button className="primary" onClick={create} disabled={!name.trim()}>
            Create campaign
          </button>
        </div>
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
