import { ErrorNote } from "../ErrorNote";
import { LOCKED_WHILE_GENERATING } from "../sceneLock";
import { SideSection, type SectionControls } from "./SideSection";
import type { InspectorInputs } from "./types";
import type { SceneProgress } from "./useSceneProgress";

export function SceneProgressSections({ cid, sid, sceneLocked, stripTasks, progress,
  collapsed, toggleSection }: SectionControls & InspectorInputs<"cid" | "sid" | "sceneLocked" | "stripTasks"> & { progress: SceneProgress }) {
  const { rolling, breakState, rollingUnread, breakUnread, rollingBusy, breakBusy,
    rollingError, breakError, refreshRolling, askBreakNow, dismissBreak } = progress;
  return <>
      {/* The scene being PLAYED, where "Story so far" above is the scenes that
          ended. Display-only — this text is never added to the prompt (#85). */}
      <SideSection id="scenesofar" title="Scene so far" collapsed={!!collapsed.scenesofar}
                   onToggle={toggleSection}>
        {(() => {
          // Only this campaign-and-scene's answer is shown: another record's is
          // not an answer about this one.
          const r = rolling?.key === `${cid}/${sid}` ? rolling.data : undefined;
          // The prose and the status line are decided separately, and review
          // caught why they have to be. A failed read used to be suppressed
          // whenever ANY cached value matched the key — which is every reread of
          // the same scene — so the panel kept presenting the coverage it read
          // before the turn as current. The prose is still the best thing anyone
          // has and stays; what it may no longer claim is to be up to date.
          const status = rollingUnread
            ? "The latest read failed, so this may be behind."
            : r?.stale
              ? "Posts it covered have changed since — it may be out of date."
              : r && `Covers ${r.at} of ${r.total} posts.`;
          if (!r?.summary) {
            return (
              <div className="field-hint">
                {rollingUnread
                  ? "The summary could not be read."
                  : `No summary yet${r && r.every > 0
                      ? ` — one is written every ${r.every} posts.` : "."}`}
              </div>
            );
          }
          return (
            <>
              <div className="field-hint">{r.summary}</div>
              {status && <div className="field-hint">{status}</div>}
            </>
          );
        })()}
        {rollingError?.key === `${cid}/${sid}` && (
          <div className="banner"><ErrorNote err={rollingError.err} /></div>
        )}
        <div className="form-actions">
          {/* Held while a turn is streaming into this scene, like the two date
              actions above. A chat appends the player's post before streaming
              and the reply only when it lands, so a refold in between covers an
              unanswered post — and the reply is an APPEND, which leaves the
              digest valid, so it would stay out of the "current" summary until
              the next threshold came round. */}
          <button className="primary" onClick={() => { void refreshRolling(); }}
                  disabled={rollingBusy === `${cid}/${sid}` || sceneLocked || !!stripTasks?.rolling_summary}
                  title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
            {rollingBusy === `${cid}/${sid}` ? "Summarizing…" : "Refresh now"}
          </button>
        </div>
      </SideSection>

      {/* The scene-break detector (#84). Directly under "Scene so far", which
          is the other thing this panel says about the scene being PLAYED — and
          the two answer adjacent questions: what has happened, and whether
          enough has. Nothing here ends or splits anything; the only actions are
          asking and declining. */}
      <SideSection id="scenebreak" title="Break here?" collapsed={!!collapsed.scenebreak}
                   onToggle={toggleSection}>
        {(() => {
          // Only this campaign-and-scene's answer, `rolling`'s rule: another
          // record's proposal is not an answer about this one.
          const b = breakState?.key === `${cid}/${sid}` ? breakState.data : undefined;
          if (breakUnread && !b) {
            return <div className="field-hint">The detector could not be read.</div>;
          }
          // A standing answer outranks "the feature is off", and review caught
          // why the other order was a bug rather than a preference: `Ask now`
          // works when the cadence is 0 — that is the whole point of a button
          // that says now — so putting the off-notice first meant the player
          // pressed it, paid for a call, and watched the panel go on saying
          // "Turned off".
          // Said beside either answer, never instead of it: the prose is still
          // the best thing anyone has, and what it may no longer do is claim to
          // be about the scene on screen. `rollingUnread`'s wording for the
          // failed-read case, `stale`'s for the moved-transcript one — they are
          // different facts and the panel says both.
          const behind = breakUnread
            ? "The latest read failed, so this may be behind."
            : b?.stale
              ? "Posts it was about have changed since — it may no longer apply."
              : "";
          if (b?.verdict === "yes") {
            return (
              <>
                <div className="field-hint">{b.reason}</div>
                {b.title && (
                  <div className="field-hint">Next scene, perhaps: “{b.title}”</div>
                )}
                {behind && <div className="field-hint">{behind}</div>}
              </>
            );
          }
          if (b?.verdict === "no") {
            return (
              <>
                <div className="field-hint">Not yet — the scene is still mid-beat.</div>
                {b.reason && <div className="field-hint">{b.reason}</div>}
                {behind && <div className="field-hint">{behind}</div>}
              </>
            );
          }
          if (b && b.every === 0) {
            return (
              <div className="field-hint">
                Turned off — set “Scene-break check” in Settings to switch it on.
                <br />Ask now still works.
              </div>
            );
          }
          // Nothing asked yet. The signals are shown even below the bar,
          // because "what the detector can see" is the honest answer to a
          // reader wondering why it has said nothing — and it is also what
          // makes the Ask now button legible.
          return (
            <>
              <div className="field-hint">
                {breakUnread ? "The latest read failed, so this may be behind."
                  : b ? `Nothing to suggest yet — checked every ${b.every} posts.`
                      : "Nothing to suggest yet."}
              </div>
              {b?.signals.map((sig) => (
                <div className="field-hint" key={sig.kind}>{sig.detail}</div>
              ))}
            </>
          );
        })()}
        {breakError?.key === `${cid}/${sid}` && (
          <div className="banner"><ErrorNote err={breakError.err} /></div>
        )}
        <div className="form-actions">
          {/* Held while a turn is streaming into this scene, like the summary's
              own button and the two date actions: a question asked over a
              half-written turn is asking about a beat whose reply has not
              arrived. */}
          <button className="primary" onClick={() => { void askBreakNow(); }}
                  disabled={breakBusy === `${cid}/${sid}` || sceneLocked || !!stripTasks?.scene_break}
                  title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
            {breakBusy === `${cid}/${sid}` ? "Asking…" : "Ask now"}
          </button>
          {breakState?.key === `${cid}/${sid}` && breakState.data.verdict === "yes" && (
            <button onClick={() => { void dismissBreak(); }}
                    disabled={breakBusy === `${cid}/${sid}` || !!stripTasks?.scene_break}>
              Not here
            </button>
          )}
        </div>
      </SideSection>

  </>;
}
