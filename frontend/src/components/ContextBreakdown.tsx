import { type SceneContext, type TokenCounting } from "../api/client";
import { type Model } from "../api/models";
import { SamplingSummary } from "./SamplingSummary";
import { type ContextSection } from "../api/types";
import { describeHeld, describeReason } from "./loreReasons";

/** The section a decision's prompt capture files its outcome under
 *  (`routes/character_turns.OUTCOME_SECTION_ID`, which a backend test pins to
 *  this string): the answer, never part of what was sent. */
const OUTCOME_ID = "decision";

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
  const approx = approxMark(ctx);

  return (
    <>
      <div className="ctx-bar">
        <div className="ctx-bar-fill" style={{ width: `${Math.min(100, pctNumber(ctx.total_tokens))}%` }} />
      </div>
      <div className="ctx-tokens">
        {approx}{ctx.total_tokens.toLocaleString()}{ctxLen > 0 ? ` / ${ctxLen.toLocaleString()}` : ""} tok
      </div>
      {approx && <div className="field-hint ctx-estimate">{estimateNote(ctx)}</div>}
      {ctx.dropped_tokens > 0 && (
        <div className="ctx-tokens">
          {ctx.dropped_tokens.toLocaleString()} tok dropped to fit the budget
        </div>
      )}
      {/* What this turn is (or was) sent with, and what its backend could not
          take — live for the next turn, frozen for a past one. */}
      <SamplingSummary report={ctx.sampling} />
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
            {/* A decision's outcome is what the call answered, filed beside the
                prompt it was asked with: it was never sent, so it has no count
                and no share of the bar. */}
            <span className="ctx-meta">
              {s.id === OUTCOME_ID ? "outcome · not sent" : `${s.tokens.toLocaleString()}${pct(s.tokens)}`}
            </span>
          </summary>
          {s.id !== OUTCOME_ID && (
            <div className="ctx-mini">
              <div style={{ width: `${Math.min(100, pctNumber(s.tokens))}%` }} />
            </div>
          )}
          {/* A World info row with nothing sent still lists what it held back. */}
          {s.text && <pre className="ctx-text">{s.text}</pre>}
          <LoreEntries section={s} />
        </details>
      ))}
    </>
  );
}

/** "≈ " when these counts are not the model's own tokenizer's, "" when they
 *  are. A snapshot frozen before the server said which tokenizer counted is an
 *  estimate too: that is the answer nobody can rule out. */
export function approxMark(ctx: { token_count?: TokenCounting | null }): string {
  return ctx.token_count?.native ? "" : "≈ ";
}

/** Why the counts are estimates, in one sentence. Only meaningful when
 *  `approxMark` is non-empty. */
export function estimateNote(ctx: SceneContext): string {
  const model = ctx.model ? ctx.model : "this model";
  const tokenizer = ctx.token_count?.tokenizer;
  if (tokenizer === undefined) {
    return `Estimated: counted with Grimoire's tokenizer, which may not be ${model}'s own.`;
  }
  if (tokenizer === "heuristic") {
    return "Estimated: no tokenizer is available here, so tokens are counted as "
      + "about four characters each. The provider's own count will differ.";
  }
  if (tokenizer === "mixed") {
    return "Estimated: partly counted with cl100k_base and partly as about four "
      + `characters per token, where the tokenizer refused some text. ${model}'s own count will differ.`;
  }
  const whose = tokenizer === "cl100k_base" ? " (OpenAI's GPT-4 tokenizer)" : "";
  return `Estimated: counted with ${tokenizer}${whose}; `
    + `${model} uses its own, so the provider's count will differ.`;
}

/** The entries of a World info or Recalled lore row, one line each: the name
 *  and why it is in (spec §10). Nothing to render for any other row, nor for a
 *  capture recorded before rows carried entries. The name is a plain chip and
 *  a post number plain text: the play view can open neither a lore entry nor a
 *  transcript index, and a link that went nowhere would only look like one. */
function LoreEntries({ section }: { section: ContextSection }) {
  const entries = section.entries ?? [];
  const held = section.held_back ?? [];
  const names = section.names ?? {};
  if (!entries.length && !held.length) return null;
  return (
    <>
      {entries.length > 0 && (
        <ul className="ctx-entries">
          {entries.map((e) => (
            <li key={e.ref} className={e.shed ? "shed" : undefined}>
              <span className="chip on">{e.name}</span> {describeReason(e.reason, names)}
              {e.shed && <span className="ctx-shed"> shed: budget</span>}
            </li>
          ))}
        </ul>
      )}
      {held.length > 0 && (
        <>
          <div className="ctx-caption">Held back</div>
          <ul className="ctx-entries">
            {held.map((h) => (
              <li key={h.ref}>
                <span className="chip on">{h.name}</span> {describeHeld(h.reason)}
              </li>
            ))}
          </ul>
        </>
      )}
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
 *  packed to rather than today's -- and, since 01i, the window too
 *  (`model_window`).
 */
function contextLimit(ctx: SceneContext, models: Model[]): number {
  // The server's resolved window (spec 01i), stated or listed, is preferred;
  // the catalog lookup answers only for a snapshot frozen before the field
  // existed, so such a turn is still measured against a window.
  const modelLen = ctx.model_window
    ? ctx.model_window.value ?? 0
    : models.find((m) => m.id === ctx.model)?.context ?? 0;
  const limits = [ctx.budget_tokens ?? 0, modelLen].filter((n) => n > 0);
  return limits.length ? Math.min(...limits) : 0;
}

/** The percentage chip in the section header, off the same limit as the bar. */
export function contextPercent(ctx: SceneContext, models: Model[]): number {
  const ctxLen = contextLimit(ctx, models);
  return ctxLen > 0 ? Math.round((ctx.total_tokens / ctxLen) * 100) : 0;
}
