import { type SceneContext } from "../api/client";
import { type Model } from "../api/models";

/** The context panel's body: the fill bar, the totals, and one collapsible row
 *  per prompt section.
 *
 *  Extracted from SceneInspector so the LIVE composition and a FROZEN past turn
 *  render through the same code (#157) — the two agreeing is the whole claim a
 *  snapshot makes, and two renderers would eventually disagree about what
 *  "dropped" or "trimmed" looks like. The frozen payload is deliberately the
 *  same shape `GET .../context` returns, so this component cannot tell them
 *  apart, and nothing here needs to.
 */
export function ContextBreakdown({ ctx, models }: { ctx: SceneContext; models: Model[] }) {
  const ctxLen = contextLimit(ctx, models);
  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const pctNumber = (t: number) => (ctxLen > 0 ? Math.round((t / ctxLen) * 100) : 0);

  return (
    <>
      <div className="ctx-bar">
        <div className="ctx-bar-fill" style={{ width: `${Math.min(100, pctNumber(ctx.total_tokens))}%` }} />
      </div>
      <div className="ctx-tokens">
        {ctx.total_tokens.toLocaleString()}{ctxLen > 0 ? ` / ${ctxLen.toLocaleString()}` : ""} tok
      </div>
      {ctx.dropped_tokens > 0 && (
        <div className="ctx-tokens">
          {ctx.dropped_tokens.toLocaleString()} tok dropped to fit the budget
        </div>
      )}
      <div className="ctx-caption">Breakdown · click a row to inspect</div>
      {/* Keyed on `id`, because the label stopped being unique the moment #29
          let a reader rename two sections the same string. `label` is the
          fallback only for a prompt snapshot frozen before ids existed — those
          predate editable labels too, so theirs are still unique. */}
      {ctx.sections.map((s) => (
        <details className={"ctx-section" + (s.dropped ? " dropped" : "")} key={s.id || s.label}>
          <summary>
            <span className={"ctx-dot" + (s.label.toLowerCase().includes("transcript") ? " hot" : "")} />
            <span className="ctx-label">{s.label}</span>
            {s.dropped && <span className="ctx-drop">dropped</span>}
            {/* Why this one survived a squeeze its neighbours did not (#129).
                Shown whatever the budget: a reader who pinned something should
                see the pin took, not have to squeeze the prompt to find out. */}
            {s.pinned && <span className="ctx-pin">pinned</span>}
            {s.trimmed > 0 && <span className="ctx-drop">{s.trimmed} trimmed</span>}
            <span className="ctx-meta">{s.tokens.toLocaleString()}{pct(s.tokens)}</span>
          </summary>
          <div className="ctx-mini">
            <div style={{ width: `${Math.min(100, pctNumber(s.tokens))}%` }} />
          </div>
          <pre className="ctx-text">{s.text}</pre>
        </details>
      ))}
    </>
  );
}

/** What actually bounds this prompt, in tokens; 0 when nothing is known.
 *
 *  With both a packer budget and a model window known, that is the SMALLER of
 *  the two: a 32k budget left over from a 32k model would otherwise report a
 *  full 8k window as a quarter used, hiding the overflow this panel exists to
 *  show. Either may be absent (no budget configured; an unknown model), so it
 *  falls back to whichever is present.
 *
 *  A frozen snapshot carries the budget that was in force when it was
 *  captured, so a past turn is measured against the ceiling it was actually
 *  packed to rather than today's.
 */
function contextLimit(ctx: SceneContext, models: Model[]): number {
  const modelLen = models.find((m) => m.id === ctx.model)?.context ?? 0;
  const limits = [ctx.budget_tokens ?? 0, modelLen].filter((n) => n > 0);
  return limits.length ? Math.min(...limits) : 0;
}

/** The percentage chip in the section header, off the same limit as the bar. */
export function contextPercent(ctx: SceneContext, models: Model[]): number {
  const ctxLen = contextLimit(ctx, models);
  return ctxLen > 0 ? Math.round((ctx.total_tokens / ctxLen) * 100) : 0;
}
