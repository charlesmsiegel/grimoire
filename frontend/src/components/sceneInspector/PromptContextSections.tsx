import type { ReactNode } from "react";
import { approxMark, ContextBreakdown, contextPercent } from "../ContextBreakdown";
import { ContextDiff } from "../ContextDiff";
import { isDecision, taskLabel, whenLabel } from "../turnLabels";
import { SideSection, type SectionControls } from "./SideSection";
import { LIVE_SIDE, type PromptHistory } from "./usePromptHistory";

type Props = SectionControls & { cid: string; history: PromptHistory };
export function ContextSection({ cid, history, collapsed, toggleSection }: Props) {
  const { seen, shown, models, shownTurns, turnRows, decisionRows, shownDiff, recomputing,
    compare, frozen, liveShown, ctxHeld, setComparison, backToLive } = history;
  return <>
      <SideSection id="context"
                   title={shownDiff ? "Context (compared)"
                          : frozen ? "Context (past turn)" : "Context"}
                   collapsed={!!collapsed.context} onToggle={toggleSection}
                   // Shut, the badge is only as good as the read behind it, and
                   // a shut section is not re-read per turn -- so a figure a turn
                   // has since moved is not shown as though it were current.
                   extra={!shownDiff && shown && (seen || liveShown || ctxHeld)
                          && contextPercent(shown, models) > 0
                     ? <span className="ctx-pct">{approxMark(shown).trim()}{contextPercent(shown, models)}%</span>
                     : undefined}>
        {seen && (
          <div className="ctx-frozen">
            <div className="field-hint">
              What the model saw · {taskLabel(seen.task)} · {whenLabel(seen.ts)}
            </div>
            {/* The comparison is offered only from a past turn, which is the
                only place it means anything: the live composition has nothing
                to be the "before" of. */}
            <label className="ctx-compare">
              <span>Compare with</span>
              {/* `setError(null)` here rather than in the effect below, the way
                  `showTurn` clears it: a failed comparison leaves a banner and
                  resets the picker, and without this the reader's retry renders
                  its diff underneath "those turns could not be compared". The
                  effect is the wrong place because it also runs on `refreshKey`,
                  where clearing a banner nobody dismissed would hide someone
                  else's error. */}
              <select value={compare}
                      onChange={(e) => { setComparison(e.target.value); }}>
                <option value="">Nothing — show this turn</option>
                {/* A decision's prompt against the chat composition is noise
                    (the server refuses it, 409 `not_comparable`), and against
                    another kind of entry too: a decision compares only with
                    another of its own task, and a turn only with turns. */}
                {!isDecision(seen) && <option value={LIVE_SIDE}>The live preview</option>}
                {(isDecision(seen) ? decisionRows.filter((t) => t.task === seen.task)
                                   : turnRows)
                  .filter((t) => t.id !== seen.id).map((t) => (
                  <option key={t.id} value={t.id}>
                    {taskLabel(t.task) + " · " + whenLabel(t.ts)}
                  </option>
                ))}
                {/* The comparison on screen outliving its row in the rail: a
                    turn-against-turn diff is frozen at both ends and so is not
                    re-read, but the retention window can still evict the entry
                    it names. Without an option for it the browser falls back to
                    the first one and the picker starts contradicting the panel
                    below it. Clearing instead would throw away a comparison the
                    reader is in the middle of. */}
                {compare && compare !== LIVE_SIDE
                  && !shownTurns.some((t) => t.id === compare) && (
                  <option value={compare}>Turn {compare} · aged out of the log</option>
                )}
              </select>
            </label>
            <button className="ctx-frozen-back"
                    onClick={() => {
                      backToLive();
                    }}>
              ← Back to live context
            </button>
          </div>
        )}
        {/* A comparison replaces the breakdown rather than sitting under it.
            Both are long, the rail is one column, and a reader who asked what
            MOVED is not helped by having to scroll past what did not.

            The middle branch is what a comparison looks like before its answer
            arrives, and it exists because the fall-through was wrong: with a
            comparison selected but no diff stamped for THESE two ends -- the
            first pick, or the moment after clicking another turn while the live
            comparison stays selected -- the panel dropped to the ordinary
            breakdown while the picker still read "The live preview". Two
            controls contradicting each other, and indefinitely so if the
            request never answers. The stale-diff case above keeps its answer
            and marks it; this one has no answer to keep, so it says that
            instead of showing something else. */}
        {shownDiff ? <ContextDiff diff={shownDiff} recomputing={recomputing} />
         : compare && seen ? <p className="field-hint">Comparing…</p>
         : shown && <ContextBreakdown ctx={shown} models={models}
                                     acceptCid={seen ? cid : undefined} />}
      </SideSection>

  </>;
}
export function TurnHistorySection({ history, collapsed, toggleSection, children }:
  Omit<Props, "cid"> & { children: ReactNode }) {
  const { seen, turnRows, showTurn } = history;
  return <>
      <SideSection id="turns" title="Turn history" collapsed={!!collapsed.turns} onToggle={toggleSection}>
        {/* The live Context section above always shows the composition as it
            stands NOW, which is not what any past turn was sent: chronicle,
            state, cast and world-info activation have all moved since. These
            are the frozen ones (#157). */}
        {turnRows.length === 0 && (
          <div className="field-hint">No captured turns yet.</div>
        )}
        {turnRows.map((t) => (
          <button key={t.id}
                  className={"inspector-row" + (seen?.id === t.id ? " on" : "")}
                  onClick={() => { void showTurn(t.id); }}>
            <span className="inspector-name">{taskLabel(t.task)}</span>
            <span className="ctx-meta">{whenLabel(t.ts)}</span>
          </button>
        ))}
        {children}
      </SideSection>
  </>;
}
export function DecisionsSection({ history, collapsed, toggleSection }: Omit<Props, "cid">) {
  const { seen, decisionRows, showTurn } = history;
  return <>
      {/* What each decision was asked and answered (roadmap 01b): one entry
          per speaker pick, scene-break check, voice check or duplicate check,
          opened read-only in the Context section like a past turn. Collapsed
          by default: a reader comes here to ask why something was decided. */}
      <SideSection id="decisions" title="Decisions" collapsed={collapsed.decisions ?? true}
                   onToggle={toggleSection}>
        {decisionRows.length === 0 && (
          <div className="field-hint">No captured decisions yet.</div>
        )}
        {decisionRows.map((t) => (
          <button key={t.id}
                  className={"inspector-row" + (seen?.id === t.id ? " on" : "")}
                  onClick={() => { void showTurn(t.id); }}>
            <span className="inspector-name">{taskLabel(t.task)}</span>
            <span className="ctx-meta">{whenLabel(t.ts)}</span>
          </button>
        ))}
      </SideSection>

  </>;
}
