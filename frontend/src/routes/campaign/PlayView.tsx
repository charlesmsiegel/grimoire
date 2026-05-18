import { useCallback, useEffect, useState } from "react";

import { campaignApi, type CampaignSummary, type OpenCommitment } from "../../api/campaign";
import { useCampaignEvent } from "../../state/useCampaignEvent";
import { DriftBanner } from "./DriftBanner";
import { InputArea } from "./InputArea";
import { PCSwitcher } from "./PCSwitcher";
import { SceneHeader } from "./SceneHeader";
import { ScenePane } from "./ScenePane";
import { SidePanel } from "./SidePanel";
import { usePlayState } from "./usePlayState";

interface Props {
  campaignId: string;
}

export function PlayView({ campaignId }: Props) {
  const play = usePlayState(campaignId);
  const [commitments, setCommitments] = useState<OpenCommitment[]>([]);
  const [campaign, setCampaign] = useState<CampaignSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    campaignApi
      .get(campaignId)
      .then((c) => {
        if (!cancelled) setCampaign(c);
      })
      .catch(() => {
        // Best-effort: a missing summary just falls back to the campaign id.
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  const refreshCommitments = useCallback(async () => {
    try {
      const list = await campaignApi.listCommitments(campaignId);
      setCommitments(list);
    } catch {
      // Silent: commitments are best-effort context.
    }
  }, [campaignId]);

  useEffect(() => {
    void refreshCommitments();
  }, [refreshCommitments]);

  useCampaignEvent(
    ["commitment_created", "commitment_paid_off", "turn_complete"],
    useCallback(() => {
      void refreshCommitments();
    }, [refreshCommitments]),
  );

  const runAction = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleSkipTime = useCallback(() => {
    const raw = window.prompt("Skip how many minutes?", "60");
    if (!raw) return;
    const minutes = Number.parseInt(raw, 10);
    if (!Number.isFinite(minutes) || minutes <= 0) return;
    void runAction(() => campaignApi.timeAdvance(campaignId, { duration: { minutes } }));
  }, [campaignId, runAction]);

  const handleManualFact = useCallback(() => {
    const statement = window.prompt("Record a fact (free text):");
    if (!statement) return;
    void runAction(() => campaignApi.createFact(campaignId, { predicate: "user_note", statement }));
  }, [campaignId, runAction]);

  if (play.state.loading) {
    return (
      <section className="play-view play-view-loading" aria-busy="true">
        <p>Loading campaign…</p>
      </section>
    );
  }
  if (play.state.error) {
    return (
      <section className="play-view play-view-error" role="alert">
        <p>Failed to load campaign: {play.state.error}</p>
        <button type="button" onClick={() => void play.refresh()}>
          Retry
        </button>
      </section>
    );
  }

  const driftWarnings = Object.entries(play.state.driftWarnings)
    .filter(([, w]) => !w.suppressed)
    .map(([ref, w]) => ({ ref, score: w.score }));

  return (
    <section className="play-view" aria-label="Campaign play view">
      <div className="play-top-bar">
        <h2 className="play-campaign">{campaign?.name ?? campaignId}</h2>
        <PCSwitcher
          pcs={play.state.pcs}
          activePcRef={play.state.activePcRef}
          onChange={(ref) => void play.setActivePC(ref)}
        />
      </div>

      <DriftBanner warnings={driftWarnings} onSuppress={play.suppressDrift} />

      {actionError && (
        <div className="play-error" role="alert">
          {actionError}
          <button type="button" onClick={() => setActionError(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <div className="play-layout">
        <div className="play-main">
          <SceneHeader scene={play.state.scene} />
          <ScenePane
            posts={play.state.posts}
            pcs={play.state.pcs}
            streaming={play.state.streaming}
            images={play.state.images}
          />
          <InputArea
            scene={play.state.scene}
            pcs={play.state.pcs}
            activePcRef={play.state.activePcRef}
            onChangePC={(ref) => void play.setActivePC(ref)}
            onSubmit={(text) => runAction(() => play.submit(text))}
            onAdvance={() => runAction(() => play.advance())}
            advanceEnabled={play.state.advanceEnabled}
            advanceReason={play.state.advanceReason}
            busy={busy}
          />
        </div>
        <SidePanel
          scene={play.state.scene}
          pcs={play.state.pcs}
          commitments={commitments}
          actions={{
            onRegenerate: () => void runAction(() => play.regenerate()),
            onUndo: () => void runAction(() => play.undo()),
            onEndScene: () => void runAction(() => play.endScene()),
            onSkipTime: handleSkipTime,
            onManualFact: handleManualFact,
            busy,
          }}
        />
      </div>
    </section>
  );
}
