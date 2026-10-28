import type { Dispatch, SetStateAction } from "react";
import type { GroupSettings, ResponseBundle, ResponseFields } from "../../api/client";
import {
  type Actor,
  type Message,
  type QuickReply,
  type SceneCheckActor
} from "../../api/client";
import { ComposerMeta } from "../../components/play/ComposerMeta";
import GroupPanel from "../../components/play/GroupPanel";
import ReplyChips from "../../components/play/ReplyChips";
import type { QuickReplyContext } from "../../components/quickReplies";
import { QuickReplyStrip } from "../../components/QuickReplyStrip";
import { LOCKED_WHILE_GENERATING } from "../../components/sceneLock";
import { GROUP_ORDER_LABEL } from "./campaignShared";
import type { SceneKey } from "./campaignTypes";
import { DicePopover } from "./DicePopover";

export function CampaignComposer({
  directing, activePcless, setDirectMode, heldKind, pendingResponse, sceneResponse, chooseResponseOverride, clearResponseOverride, activeId, group, showGroup, setShowGroup, selectedActor, quickNotice, cid, showInspector, setShowInspector, cast, currentCampaign, selected, groupSaved, quickSet, quickCtx, runQuickReply, moduleBound, sceneLocked, busy, messages, toggleRollPop, rollForm, rolling, setRollForm, enterCheckMode, checkActors, doCheck, fail, rollFromForm, showRollSyntax, setShowRollSyntax, input, setInput, onKeyDown, renamesInFlight, canGenerate, send, respondAs, cancelTurn, notReadyId
}: {
  directing: boolean;
  activePcless: boolean;
  setDirectMode: Dispatch<SetStateAction<boolean>>;
  heldKind: "note" | "post" | null;
  pendingResponse: Partial<ResponseFields> | null;
  sceneResponse: ResponseBundle | null;
  chooseResponseOverride: (key: "response_continuation_words" | "response_continuation_paragraphs", value: string) => void;
  clearResponseOverride: () => void;
  activeId: string | null;
  group: GroupSettings | null;
  showGroup: boolean;
  setShowGroup: Dispatch<SetStateAction<boolean>>;
  selectedActor: { kind: string; id: string; } | null;
  quickNotice: { cid: string; sid: string; text: string; } | null;
  cid: string;
  showInspector: boolean;
  setShowInspector: Dispatch<SetStateAction<boolean>>;
  cast: Actor[];
  currentCampaign: () => string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  groupSaved: (scope: SceneKey, next: GroupSettings) => void;
  quickSet: { cid: string; replies: QuickReply[]; } | null;
  quickCtx: QuickReplyContext;
  runQuickReply: (r: QuickReply) => void;
  moduleBound: boolean | null;
  sceneLocked: boolean;
  busy: boolean;
  messages: Message[];
  toggleRollPop: () => void;
  rollForm: { mode: "dice" | "check"; notation: string; label: string; error: string | null; checkActor: string; checkId: string; difficulty: number | ""; modifier: number; } | null;
  rolling: boolean;
  setRollForm: Dispatch<SetStateAction<{ mode: "dice" | "check"; notation: string; label: string; error: string | null; checkActor: string; checkId: string; difficulty: number | ""; modifier: number; } | null>>;
  enterCheckMode: () => void;
  checkActors: SceneCheckActor[];
  doCheck: () => Promise<void>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  rollFromForm: () => Promise<void>;
  showRollSyntax: boolean;
  setShowRollSyntax: Dispatch<SetStateAction<boolean>>;
  input: string;
  setInput: Dispatch<SetStateAction<string>>;
  onKeyDown: (e: React.KeyboardEvent<Element>) => void;
  renamesInFlight: number;
  canGenerate: boolean;
  send: (speakerRef?: string) => Promise<void>;
  respondAs: (ref: string) => Promise<void>;
  cancelTurn: () => void;
  notReadyId: string;
}) {
  return (
    <div className={`composer${directing ? " directing" : ""}`}>
      <ComposerMeta
        directing={directing} pcless={activePcless} onDirect={setDirectMode} held={heldKind}
        targets={{
          words: pendingResponse?.response_continuation_words ?? "",
          paragraphs: pendingResponse?.response_continuation_paragraphs ?? "",
          wordsDefault: String(sceneResponse?.effective.continuation.words ?? 150),
          paragraphsDefault: String(sceneResponse?.effective.continuation.paragraphs ?? 2),
        }}
        onTarget={chooseResponseOverride} onClearTargets={clearResponseOverride}
        order={activeId && group ? GROUP_ORDER_LABEL[group.order] : null}
        orderOpen={showGroup} onOrder={() => setShowGroup((v) => !v)}
        dossierOpen={!!selectedActor}
        notice={quickNotice && quickNotice.cid === cid && quickNotice.sid === activeId
          ? quickNotice.text : null}
        inspectorOpen={showInspector} onInspector={() => setShowInspector((v) => !v)} />
      {showGroup && activeId && group && (
        <GroupPanel key={`${cid}/${activeId}`} cid={cid} sid={activeId} cast={cast}
          settings={group}
          onChange={(next) => {
            // A save that lands after the reader moved on belongs to the
            // scene it was made in, and the read it supersedes is stale.
            if (currentCampaign() !== cid || (selected()?.sid ?? null) !== activeId) return;
            groupSaved({ cid, sid: activeId }, next);
          }}
          onClose={() => setShowGroup(false)} />
      )}
      {activeId && quickSet?.cid === cid && (
        <QuickReplyStrip replies={quickSet.replies} ctx={quickCtx} onRun={runQuickReply} />
      )}
      <div className="inputbar">
        {/* Dice are a mechanics affordance: both the popover's tabs lead to
                routes that only mean something with a pack bound (Check needs one
                outright; freeform Dice is offered alongside it as part of the same
                tool). An unbound campaign is freeform play, so the button is not
                there at all -- and not merely while the read is still out, which
                would flash a control and then remove it. */}
        {moduleBound === true && (
          <button className="roll-btn"
            title={sceneLocked ? LOCKED_WHILE_GENERATING : "Roll dice"}
            aria-label="Roll dice"
            disabled={!activeId || busy || sceneLocked || messages.length === 0}
            onClick={toggleRollPop}>
            ⚄
          </button>
        )}
        {rollForm && (
          <DicePopover
            rollForm={rollForm}
            rolling={rolling}
            setRollForm={setRollForm}
            enterCheckMode={enterCheckMode}
            checkActors={checkActors}
            doCheck={doCheck}
            fail={fail}
            rollFromForm={rollFromForm}
            showRollSyntax={showRollSyntax}
            setShowRollSyntax={setShowRollSyntax} />
        )}
        <textarea
          rows={3}
          placeholder={directing ? "Direct the scene (optional)…" : "Speak your intent…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {activeId && <div className="form-actions">
          <ReplyChips cast={cast} sittingOut={group?.sitting_out ?? []}
            disabled={busy || rolling || sceneLocked || renamesInFlight > 0 || !canGenerate}
            onReply={(ref) => { (input.trim() ? send(ref) : respondAs(ref)).catch((err: unknown) => fail(err, false, "scene")); }} />
        </div>}
        {/* Selection and every handoff share the run's busy latch. Keep
                Continue visible but unavailable until the entire run settles;
                the separate Stop still cancels it before any prose arrives. */}
        <div className="composer-run-actions">
          {busy && <button className="send cancel-turn" onClick={cancelTurn}>Stop ■</button>}
          {/* Disabled where no model can answer, rather than sending into
                  the refusal: the banner above says why, and is named as this
                  button's description. */}
          <button className="send" onClick={() => { send().catch((err: unknown) => fail(err, false, "scene")); }}
            disabled={busy || rolling || renamesInFlight > 0 || !canGenerate}
            title={canGenerate ? undefined : "No model is ready to generate"}
            aria-describedby={canGenerate ? undefined : notReadyId}>
            {/* An empty box requests one additional response in either mode. */}
            {!input.trim() ? "Continue ▶" : directing ? "Direct ▸" : "Send ▸"}
          </button>
        </div>
      </div>
    </div>
  );
}
