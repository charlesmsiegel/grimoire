import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../../../api/client";
import { useResource } from "../../../api/useResource";
import { type CampaignRecord } from "./shared";
import { GeneralTab } from "./GeneralTab";
import { RoutingTab } from "./RoutingTab";
import { ImageGenTab } from "./ImageGenTab";
import { MechanicsTab } from "./MechanicsTab";
import { NarratorTab } from "./NarratorTab";
import { GenerationTab } from "./GenerationTab";
import { SummariesTab } from "./SummariesTab";
import { StorageTab } from "./StorageTab";
import { AdvancedTab } from "./AdvancedTab";
import { ExpressionsTab } from "./ExpressionsTab";
import { Tabs } from "../../../components/Tabs";

type Tab =
  | "general"
  | "expressions"
  | "routing"
  | "imagegen"
  | "mechanics"
  | "narrator"
  | "generation"
  | "summaries"
  | "storage"
  | "advanced";

const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "expressions", label: "Expressions" },
  { id: "routing", label: "Model routing" },
  { id: "imagegen", label: "ImageGen" },
  { id: "mechanics", label: "Mechanics" },
  { id: "narrator", label: "Narrator" },
  { id: "generation", label: "Generation" },
  { id: "summaries", label: "Summaries" },
  { id: "storage", label: "Storage" },
  { id: "advanced", label: "Advanced" },
];

export function CampaignSettings() {
  const { campaignId } = useParams();
  const [tab, setTab] = useState<Tab>("general");
  const {
    data: loaded,
    error,
    loading,
  } = useResource(
    useCallback(
      () =>
        campaignId
          ? api.get<CampaignRecord>(`/api/campaigns/${encodeURIComponent(campaignId)}`)
          : Promise.resolve<CampaignRecord | null>(null),
      [campaignId],
    ),
  );

  if (!campaignId) return null;

  return (
    <section className="route campaign-settings" aria-labelledby="campaign-settings-heading">
      <header>
        <h2 id="campaign-settings-heading">Campaign settings: {campaignId}</h2>
      </header>

      <Tabs
        tabs={TABS.map((t) => ({ key: t.id, label: t.label }))}
        active={tab}
        onSelect={setTab}
        ariaLabel="Campaign settings tabs"
        className="tab-bar"
      />

      {loading && <p className="wizard-meta">Loading…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error.message}
        </p>
      )}

      {loaded && <SettingsTabs campaignId={campaignId} tab={tab} initialCampaign={loaded} />}
    </section>
  );
}

function SettingsTabs({
  campaignId,
  tab,
  initialCampaign,
}: {
  campaignId: string;
  tab: Tab;
  initialCampaign: CampaignRecord;
}) {
  const [campaign, setCampaign] = useState<CampaignRecord>(initialCampaign);

  return (
    <div className="tab-panel">
      {tab === "general" && (
        <GeneralTab key={campaign.id} campaign={campaign} onUpdate={setCampaign} />
      )}
      {tab === "expressions" && <ExpressionsTab key={campaignId} campaignId={campaignId} />}
      {tab === "routing" && <RoutingTab key={campaignId} campaignId={campaignId} />}
      {tab === "imagegen" && <ImageGenTab key={campaignId} campaignId={campaignId} />}
      {tab === "mechanics" && (
        <MechanicsTab key={campaign.id} campaign={campaign} onUpdate={setCampaign} />
      )}
      {tab === "narrator" && <NarratorTab key={campaignId} campaignId={campaignId} />}
      {tab === "generation" && <GenerationTab key={campaignId} campaignId={campaignId} />}
      {tab === "summaries" && <SummariesTab key={campaignId} campaignId={campaignId} />}
      {tab === "storage" && <StorageTab key={campaignId} campaignId={campaignId} />}
      {tab === "advanced" && <AdvancedTab key={campaignId} campaignId={campaignId} />}
    </div>
  );
}
