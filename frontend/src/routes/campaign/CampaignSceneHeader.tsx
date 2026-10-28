import type { Dispatch, SetStateAction } from "react";
import type { SceneUsage, UsagePostBucket } from "../../api/client";
import {
  type Message,
  type SceneMeta
} from "../../api/client";
import {
  estimatedTokensTitle,
  UNPRICED
} from "../../components/cost";
import { LOCKED_WHILE_GENERATING } from "../../components/sceneLock";
import { sceneNumber } from "./campaignShared";
import { SceneRenameInput } from "./SceneRenameInput";

export function CampaignSceneHeader({
  activeId, scenes, messages, sceneSpend, sceneCosts, sceneSpendIsSpend, renamingScene, setRenamingScene, commitSceneRename, sceneTitle, activePcless, activeBranched, activeClosed, sceneLocked, forkAtScene, fail, deleteScene
}: {
  activeId: string;
  scenes: SceneMeta[];
  messages: Message[];
  sceneSpend: string | null;
  sceneCosts: { cid: string; sid: string; byPost: Record<number, UsagePostBucket>; usage: SceneUsage; } | null;
  sceneSpendIsSpend: boolean;
  renamingScene: { id: string; title: string; } | null;
  setRenamingScene: Dispatch<SetStateAction<{ id: string; title: string; } | null>>;
  commitSceneRename: () => Promise<void>;
  sceneTitle: string;
  activePcless: boolean;
  activeBranched: boolean;
  activeClosed: { sid: string; title: string; } | null;
  sceneLocked: boolean;
  forkAtScene: (sid: string, fromIndex?: number) => Promise<void>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  deleteScene: (s: SceneMeta) => Promise<void>;
}) {
  return (
    <div className="scene-head">
      <div className="eyebrow">
        SCENE {sceneNumber(activeId, scenes.length)} · {messages.length}{" "}
        {messages.length === 1 ? "TURN" : "TURNS"}
        {/* What the scene has cost, which used to be reachable only by
                    opening the inspector. It is the ledger's own total for the
                    scene, not a sum of the gutter chips: those are capped and
                    can be clamped, and a headline built by adding a capped
                    list would understate without saying so. */}
        {sceneSpend && (
          <span className={sceneSpend === UNPRICED ? "money-unpriced" : undefined}
            title={sceneCosts ? estimatedTokensTitle(sceneCosts.usage.totals)
              : undefined}>
            {" · "}{sceneSpend}{sceneSpendIsSpend ? " SPEND" : ""}
          </span>
        )}
      </div>
      {renamingScene ? (
        <SceneRenameInput value={renamingScene.title}
          onChange={(title) => setRenamingScene({ id: renamingScene.id, title })}
          onCommit={() => { commitSceneRename().catch((err: unknown) => fail(err, false)); }}
          onCancel={() => setRenamingScene(null)} />
      ) : (
        <h2 className="scene-title">
          {sceneTitle}
          {activePcless && <span className="chip on offscreen-badge">Offscreen</span>}
          {activeBranched && <span className="chip">branch</span>}
          {activeClosed && (
            <span className="chip" title="A sibling branch was absorbed">closed</span>
          )}
        </h2>
      )}
      {/* Rename and delete belong to the scene you are reading, and to
                  no other. They used to sit on every row of the rail, where a
                  ✕ was one mis-click from deleting a transcript you were not
                  even looking at. Locked while this scene is the one being
                  streamed into: renaming re-slugs the file, which moves it out
                  from under the write in flight (#95). */}
      <div className="row-actions">
        <button aria-label="Rename scene" disabled={sceneLocked}
          title={sceneLocked ? LOCKED_WHILE_GENERATING : "Rename scene"}
          onClick={() => setRenamingScene({ id: activeId, title: sceneTitle })}>✎</button>
        {/* Forking is not one of the two: it writes nothing here at
                    all, and is offered from this row because the scene you are
                    reading is the turn you would branch at. Locked with the
                    others while a turn is streaming — a copy taken mid-write
                    would carry half a reply. */}
        <button aria-label="Fork campaign at this scene" disabled={sceneLocked}
          title={sceneLocked ? LOCKED_WHILE_GENERATING
            : "Fork the campaign at this scene"}
          onClick={() => { forkAtScene(activeId).catch((err: unknown) => fail(err, false)); }}>⑂</button>
        <button aria-label="Delete scene" disabled={sceneLocked}
          title={sceneLocked ? LOCKED_WHILE_GENERATING : "Delete scene"}
          onClick={() => {
            const meta = scenes.find((x) => x.id === activeId);
            if (meta) void deleteScene(meta);
          }}>✕</button>
      </div>
    </div>
  );
}
