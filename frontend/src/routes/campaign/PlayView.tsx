import { useCallback, useMemo, useState } from "react";

import {
  campaignApi,
  type TimeAdvanceResult,
} from "../../api/campaign";

import { SceneLedgerDialog } from "./SceneLedgerDialog";
import { ScenePreviewPanel } from "./ScenePreviewPanel";
import { SceneSuggestionView } from "./SceneSuggestionView";
import { DriftBanner } from "./DriftBanner";
import { InputArea } from "./InputArea";


import { PreRollConfirmation } from "./PreRollConfirmation";
import { SceneBreakPrompt } from "./SceneBreakPrompt";
import { SceneHeader } from "./SceneHeader";
import { ScenePane } from "./ScenePane";
import { SideHud } from "./SideHud/SideHud";
import { TimeAdvanceDigest } from "./TimeAdvanceDigest";
import { usePlayState } from "./usePlayState";

interface Props {
  campaignId: string;
}

export function PlayView({ campaignId }: Props) {
  const play = usePlayState(campaignId);

  const loadMorePosts = useCallback(async () => {
    const firstOrder = play.state.posts[0]?.order_in_scene;
    if (!play.state.scene || firstOrder === undefined) return;
    const result = await campaignApi.getPostsPaginated(campaignId, play.state.scene.id, {
      limit: 50,
      before: firstOrder,
    });
    play.dispatch({ type: "prepend-posts", posts: result.posts, hasMore: result.has_more });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, play.state.scene, play.state.posts, play.dispatch]);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [timeDigest, setTimeDigest] = useState<TimeAdvanceResult | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [hudCollapsed, setHudCollapsed] = useState(false);



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
    void runAction(async () => {
      const result = await campaignApi.timeAdvance(campaignId, { duration: { minutes } });
      setTimeDigest(result);
    });
  }, [campaignId, runAction]);

  const handleManualFact = useCallback(() => {
    const statement = window.prompt("Record a fact (free text):");
    if (!statement) return;
    void runAction(() => campaignApi.createFact(campaignId, { predicate: "user_note", statement }));
  }, [campaignId, runAction]);

  // "What changed?" reads the most recent narrator turn — the one whose
  // audit record carries the deltas that just landed. Player posts share
  // a turn_id with the narrator response that followed; the latest
  // narrator post is the clearest target. Hook lives above the early
  // returns so the call order stays stable across renders.
  const latestNarratorTurnId = useMemo(() => {
    for (let i = play.state.posts.length - 1; i >= 0; i -= 1) {
      const p = play.state.posts[i];
      if (p && p.author_kind === "narrator" && p.turn_id) return p.turn_id;
    }
    return null;
  }, [play.state.posts]);

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
      <DriftBanner warnings={driftWarnings} onSuppress={play.suppressDrift} />

      <TimeAdvanceDigest result={timeDigest} onDismiss={() => setTimeDigest(null)} />

      <PreRollConfirmation campaignId={campaignId} />
      <SceneBreakPrompt campaignId={campaignId} />

      {actionError && (
        <div className="play-error" role="alert">
          {actionError}
          <button type="button" onClick={() => setActionError(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <div className={`play-layout${hudCollapsed ? " play-layout-collapsed" : ""}`}>
        <div className="play-main">
          <SceneHeader scene={play.state.scene} />
          {play.state.mode === "picking" && play.state.suggestions ? (
            <SceneSuggestionView
              campaignId={campaignId}
              suggestions={play.state.suggestions}
              dispatch={play.dispatch}
            />
          ) : play.state.mode === "previewing" && play.state.preview ? (
            <ScenePreviewPanel
              campaignId={campaignId}
              preview={play.state.preview}
              suggestions={play.state.suggestions}
              dispatch={play.dispatch}
              onSceneCreated={play.refresh}
            />
          ) : play.state.mode === "suggesting" || play.state.mode === "creating" ? (
            <div className="play-loading-scene">
              <p>{play.state.mode === "creating" ? "Creating scene..." : "Loading suggestions..."}</p>
            </div>
          ) : (
            <ScenePane
              posts={play.state.posts}
              pcs={play.state.pcs}
              streaming={play.state.streaming}
              images={play.state.images}
              campaignId={campaignId}
              scene={play.state.scene}
              hasMorePosts={play.state.hasMorePosts}
              onLoadMore={loadMorePosts}
            />
          )}
          {play.state.mode !== "play" ? null : play.state.scene?.closed ? (
            <p className="input-area-closed">This scene is closed. No new posts can be added.</p>
          ) : (
            <InputArea
              campaignId={campaignId}
              scene={play.state.scene}
              pcs={play.state.pcs}
              activePcRef={play.state.activePcRef}
              text={draft}
              onTextChange={setDraft}
              onChangePC={(ref) => void play.setActivePC(ref)}
              onSubmit={(text, emotion) => runAction(() => play.submit(text, emotion))}
              onAdvance={() => runAction(() => play.advance())}
              advanceEnabled={play.state.advanceEnabled}
              advanceReason={play.state.advanceReason}
              busy={busy}
            />
          )}
        </div>
        <div className="play-hud-col">
          <button
            type="button"
            className="hud-collapse-toggle"
            onClick={() => setHudCollapsed((c) => !c)}
            aria-label={hudCollapsed ? "Expand HUD" : "Collapse HUD"}
            title={hudCollapsed ? "Expand HUD" : "Collapse HUD"}
          >
            {hudCollapsed ? "◂" : "▸"}
          </button>
          {!hudCollapsed && (
            <SideHud
              campaignId={campaignId}
              sceneId={play.state.scene?.id ?? null}
              scene={play.state.scene}
              pcs={play.state.pcs}
              actions={{
                onRegenerate: () => void runAction(() => play.regenerate()),
                onUndo: () => void runAction(() => play.undo()),
                onEndScene: () => void runAction(() => play.endScene()),
                onDeleteScene: () => void runAction(() => play.deleteScene()),
                onNewScene: () => void runAction(() => play.newScene()),
                onOpenLedger: () => setLedgerOpen(true),
                onSkipTime: handleSkipTime,
                onManualFact: handleManualFact,
                busy,
              }}
              playerInput={draft}
              pcRef={play.state.activePcRef}
              latestNarratorTurnId={latestNarratorTurnId}
            />
          )}
        </div>
      </div>
      <SceneLedgerDialog
        campaignId={campaignId}
        open={ledgerOpen}
        onClose={() => setLedgerOpen(false)}
      />
    </section>
  );
}
