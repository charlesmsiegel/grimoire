import { useParams } from "react-router-dom";

export function CampaignView() {
  const { campaignId } = useParams();
  return (
    <section className="route campaign-view" aria-labelledby="campaign-heading">
      <header>
        <h2 id="campaign-heading">Campaign: {campaignId}</h2>
      </header>
      <p>
        The Play / Cast / World / Timeline / Mechanics / Composition / Images views attach to this
        route in subsequent tasks. The active-campaign WebSocket subscribes automatically while this
        view is mounted.
      </p>
    </section>
  );
}
