import type { Dispatch, SetStateAction } from "react";
import type { CampaignBudget, Message, SceneMeta, SceneUsage, UsagePostBucket } from "../../api/client";
import { CastPanel } from "../../components/CastPanel";
import { SceneInspector, type RewrittenPost } from "../../components/SceneInspector";
import { CampaignSceneHeader } from "./CampaignSceneHeader";

export function CampaignSceneSetup({
  showInspector, activeId, cid, ctxKey, onInspectorSceneChanged, sceneRenamedStable, onSetupSceneRenamed, activePcless, sceneLocked, markRenaming, messages, transcriptIsActive, sceneCosts, budget, onBudgetSaved, rewrittenPosts, onInspectorTranscriptEdited, stripTasks, onInspectorTaskBusy, focus, scenes, sceneSpend, sceneSpendIsSpend, renamingScene, setRenamingScene, commitSceneRename, sceneTitle, activeBranched, activeClosed, forkAtScene, fail, deleteScene, openerOffered, openerRequest, ready, refreshAndAsk, seedPrompt, openedFrom
}: {
  showInspector: boolean;
  activeId: string | null;
  cid: string;
  ctxKey: number;
  onInspectorSceneChanged: () => void;
  sceneRenamedStable: (id: string) => void;
  onSetupSceneRenamed: (id: string) => void;
  activePcless: boolean;
  sceneLocked: boolean;
  markRenaming: (active: boolean) => void;
  messages: Message[];
  transcriptIsActive: boolean;
  sceneCosts: { cid: string; sid: string; byPost: Record<number, UsagePostBucket>; usage: SceneUsage; } | null;
  budget: CampaignBudget | null;
  onBudgetSaved: (forCid: string, b: CampaignBudget) => void;
  rewrittenPosts: RewrittenPost[];
  onInspectorTranscriptEdited: () => void;
  stripTasks: { rolling_summary: boolean; scene_break: boolean; };
  onInspectorTaskBusy: (task: "rolling_summary" | "scene_break", on: boolean) => void;
  focus: boolean;
  scenes: SceneMeta[];
  sceneSpend: string | null;
  sceneSpendIsSpend: boolean;
  renamingScene: { id: string; title: string; } | null;
  setRenamingScene: Dispatch<SetStateAction<{ id: string; title: string; } | null>>;
  commitSceneRename: () => Promise<void>;
  sceneTitle: string;
  activeBranched: boolean;
  activeClosed: { sid: string; title: string; } | null;
  forkAtScene: (sid: string, fromIndex?: number) => Promise<void>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  deleteScene: (s: SceneMeta) => Promise<void>;
  openerOffered: boolean;
  openerRequest: number;
  ready: boolean;
  refreshAndAsk: (id: string) => Promise<void>;
  seedPrompt: { cid: string; sid: string; prompt: string; } | null;
  openedFrom: string;
}) {
  return (
    <>          {showInspector && activeId && (
      <div className="panel-slot">
        {/* Memoized, with stable handlers: it re-renders for the scene,
                  the refresh beat and the cost reads, never for the composer.
                  The usage and budget are the reads this view already holds,
                  handed down so its Cost section does not repeat them. The
                  usage only while the transcript on screen is the ACTIVE
                  scene's: it is read for the transcript, which stays the scene
                  being left until the new one lands (for good, if it never
                  does), and this section is headed by the active scene. Until
                  then it reads as a read in flight, not as the wrong scene's
                  spend. */}
        <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
          onSceneChanged={onInspectorSceneChanged}
          onSceneRenamed={sceneRenamedStable} pcless={activePcless}
          sceneLocked={sceneLocked}
          onRenaming={markRenaming}
          posts={messages.length}
          usage={transcriptIsActive ? sceneCosts?.usage ?? null : null}
          budget={budget}
          onBudgetSaved={onBudgetSaved}
          rewritten={rewrittenPosts}
          onTranscriptEdited={onInspectorTranscriptEdited}
          stripTasks={stripTasks}
          onTaskBusy={onInspectorTaskBusy} />
      </div>
    )}
      {activeId && !focus && (
        <CampaignSceneHeader
          activeId={activeId}
          scenes={scenes}
          messages={messages}
          sceneSpend={sceneSpend}
          sceneCosts={sceneCosts}
          sceneSpendIsSpend={sceneSpendIsSpend}
          renamingScene={renamingScene}
          setRenamingScene={setRenamingScene}
          commitSceneRename={commitSceneRename}
          sceneTitle={sceneTitle}
          activePcless={activePcless}
          activeBranched={activeBranched}
          activeClosed={activeClosed}
          sceneLocked={sceneLocked}
          forkAtScene={forkAtScene}
          fail={fail}
          deleteScene={deleteScene} />
      )}
      {/* Under the scene's title and above its composer, which it gives way
              to: it shrinks to the room left and scrolls inside it (the
              stylesheet), so the place to write is on screen when a new scene
              opens. Above the title at its full height it pushed the composer
              below the fold.
              Only once THIS scene's transcript has landed and turned out empty.
              `messages` is also empty in the gap between opening a scene and
              its transcript arriving, and mounting here then cost a
              campaign-wide character list and a suggestion scan for a panel
              that unmounted as soon as the posts came in. `landedScene`
              rather than `loaded`, so a rename from the panel's own date field
              does not remount it -- see there. */}
      {activeId && openerOffered && (
        <CastPanel
          cid={cid}
          sid={activeId}
          openerRequest={openerRequest}
          ready={ready}
          onSeeded={() => { void refreshAndAsk(activeId); }}
          onSceneRenamed={onSetupSceneRenamed}
          initialPrompt={seedPrompt?.cid === cid && seedPrompt.sid === activeId
            ? seedPrompt.prompt : undefined}
          greeting={openedFrom || undefined}
          pcless={activePcless}
          sceneLocked={sceneLocked}
          onRenaming={markRenaming}
        />
      )}
    </>
  );
}
