import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../../../api/client";
import { type CampaignRecord, errorMessage } from "./shared";
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
  const [campaign, setCampaign] = useState<CampaignRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const data = await api.get<CampaignRecord>(
          `/api/campaigns/${encodeURIComponent(campaignId)}`,
        );
        if (!cancelled) {
          setCampaign(data);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  if (!campaignId) return null;

  return (
    <section className="route campaign-settings" aria-labelledby="campaign-settings-heading">
      <header>
        <h2 id="campaign-settings-heading">Campaign settings: {campaignId}</h2>
      </header>

      <nav className="tab-bar" aria-label="Campaign settings tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {loading && <p className="wizard-meta">Loading…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}

      {campaign && (
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
      )}
    </section>
  );
}
