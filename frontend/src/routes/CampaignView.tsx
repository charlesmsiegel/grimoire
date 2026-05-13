import { useParams } from "react-router-dom";

import { PlayView } from "./campaign/PlayView";

export function CampaignView() {
  const { campaignId } = useParams();
  if (!campaignId) {
    return (
      <section className="route campaign-view">
        <p>Missing campaign id.</p>
      </section>
    );
  }
  return <PlayView campaignId={campaignId} />;
}
