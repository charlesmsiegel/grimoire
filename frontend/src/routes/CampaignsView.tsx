import { Link } from "react-router-dom";

import { useAppState } from "../state/useStore";

export function CampaignsView() {
  const { campaigns } = useAppState();

  return (
    <section className="route campaigns-view" aria-labelledby="campaigns-heading">
      <header>
        <h2 id="campaigns-heading">Campaigns</h2>
      </header>
      {campaigns.length === 0 ? (
        <p>No campaigns yet. The campaign creation flow ships in a later task.</p>
      ) : (
        <ul className="campaign-list">
          {campaigns.map((c) => (
            <li key={c.id}>
              <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
