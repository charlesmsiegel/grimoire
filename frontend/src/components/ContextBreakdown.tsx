import { type SceneContext, type TokenCounting } from "../api/client";
import { type Model } from "../api/models";
import { EpistemicAccept } from "./EpistemicAccept";
import { SamplingSummary } from "./SamplingSummary";
import { type ContextSection, type EpistemicDetail, type EpistemicItem, type EpistemicSlice,
  type HistoryPlanTrace } from "../api/types";
import { describeHeld, describeReason } from "./loreReasons";
import { promptFieldWarning } from "./promptFieldWarning";

/** The section a decision's prompt capture files its outcome under
 *  (`routes/decision_capture.OUTCOME_SECTION_ID`, which a backend test pins to
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
export function ContextBreakdown({ ctx, models, acceptCid }: {
  ctx: SceneContext; models: Model[];
  /** A captured turn's campaign: offers the history accept (11-S3). The live
   *  panel passes none, so it offers nothing. */
  acceptCid?: string | undefined;
}) {
  const ctxLen = contextLimit(ctx, models);
  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const pctNumber = (t: number) => (ctxLen > 0 ? Math.round((t / ctxLen) * 100) : 0);
  const approx = approxMark(ctx);
  const fieldWarning = promptFieldWarning(ctx.prompt_field_statuses);
  const carried = carriedPosts(ctx.sections);

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
      {fieldWarning && <div className="field-hint" role="status">{fieldWarning}</div>}
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
          <SourceEntries section={s} />
          <LoreEntries section={s} />
          <HistoryEntries section={s} acceptCid={acceptCid} carried={carried} />
        </details>
      ))}
    </>
  );
}

/** #128: why a cast source is in the prompt, in the reader's words. Snapshots are
 *  hand-editable, so a reason this build does not know says nothing rather than
 *  something invented. */
function describeSourceReason(reason: unknown): string | null {
  if (!reason || typeof reason !== "object") return null;
  const r = reason as { type?: unknown; tier?: unknown };
  if (r.type === "cast") return "in this scene's cast";
  if (r.type === "off_scene" && r.tier === "active") return "active in this campaign, not in this scene";
  if (r.type === "off_scene" && r.tier === "known") return "known to the world, not in this scene";
  return null;
}

/** #131: snapshots are hand-editable JSON. Ignore damaged optional source
 *  metadata while keeping the section itself readable. Source ids distinguish
 *  repeated names and survive a refresh or a reordered template. */
function SourceEntries({ section }: { section: ContextSection }) {
  const sources = Array.isArray(section.sources) ? section.sources.filter((source) =>
    source && typeof source.id === "string"
      && typeof source.label === "string" && typeof source.text === "string") : [];
  if (!sources.length) return null;
  return <div className="ctx-sources">
    <div className="ctx-caption">Sources · expand to inspect</div>
    {sources.map((source) => {
      const why = describeSourceReason(source.reason);
      return <details className={"ctx-source" + (source.shed === true ? " shed" : "")} key={source.id}>
        <summary>
          {source.label}
          {why && <span className="ctx-reason">{why}</span>}
          {source.shed === true && <span className="ctx-shed"> shed: budget</span>}
        </summary>
        <pre className="ctx-text">{source.text}</pre>
      </details>;
    })}
  </div>;
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
 *  packed to rather than today's -- and, since 01i, the window too: the
 *  breakdown's own `model_window`, which the backend resolved from the target
 *  it named (a stated window included, and a model no catalog lists). Only a
 *  snapshot frozen before that field existed looks the model up in the
 *  catalog; a window the backend says is unknown stays unknown.
 */
function contextLimit(ctx: SceneContext, models: Model[]): number {
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


/** Who has which part of each item (roadmap 11): for the narrator, who on
 *  stage knows or suspects it; for an NPC, what it has and why, its withheld
 *  parts included. The basis codes are shown here and sent nowhere. */
function EpistemicRows({ detail, acceptCid, carried }: {
  detail: EpistemicDetail; acceptCid?: string | undefined; carried: Set<string>;
}) {
  const actor = detail.perspective === "narrator" ? null : detail.perspective.replace(/^actor:/, "");
  const names = detail.names ?? {};
  const named = (ref: string) => names[ref] ?? ref;
  const reader = actor === null ? "the narrator" : named(actor);
  // Who a part can be marked for: the NPC the turn was written for, or for
  // the narrator's turn, any character on stage.
  const readers = (actor === null ? Object.keys(names) : [actor])
    .filter((r) => r.startsWith("characters:"));
  // Each classified ref's own class of a part: per post, the class it has
  // for each of the slice's posts; the summary, its whole-scene class.
  const classesOf = (item: EpistemicItem, slice: EpistemicSlice) =>
    Object.entries(item.by_ref ?? {}).map(([ref, parts]) => {
      const found = slice.part === "summary" ? [parts.summary]
        : slice.posts.map((i) => parts[String(i)]);
      const access = [...new Set(found.filter(Boolean).map(([a]) => a))];
      return access.length ? `${named(ref)}: ${access.join("/")}` : "";
    }).filter(Boolean);
  return <>
    {detail.round && <div className="field-hint">
      This round: {detail.round.retrievals} history {detail.round.retrievals === 1 ? "retrieval" : "retrievals"},{" "}
      {detail.round.embeds} query {detail.round.embeds === 1 ? "embedding" : "embeddings"}
    </div>}
    <div className="field-hint">Access, for {reader}:</div>
    <ul className="ctx-entries">
      {detail.items.map((item) => <li key={`${item.key}:${item.scene}:${item.slices.map((x) => x.posts.join(".")).join("/")}`}>
        {item.header.title ? <span className="chip on">{item.header.title}</span> : <span className="chip on">{item.scene || item.key}</span>}{" "}
        {item.state}{!item.admitted && "; dropped"}
        {item.known_to.length > 0 && <div className="field-hint">Known to: {item.known_to.map(named).join(", ")}</div>}
        {item.suspected_by.length > 0 && <div className="field-hint">Suspected by: {item.suspected_by.map(named).join(", ")}</div>}
        {item.slices.map((slice) => <div key={`${slice.part}:${slice.posts.join(",")}`}>
          <div className="field-hint">
            {slice.part === "summary" ? "Summary" : `Posts ${slice.posts.join(", ")}`}: {slice.access} ({slice.basis.join(", ")})
          </div>
          {actor === null && classesOf(item, slice).length > 0 && (
            <div className="field-hint">{classesOf(item, slice).join("; ")}</div>)}
          {acceptCid && <EpistemicAccept cid={acceptCid} item={item} slice={slice}
                                         readers={readers} names={names}
                                         shown={VISIBLE.has(slice.access) && (slice.keys ?? [])
                                           .every((k) => carried.has(k.key))} />}
        </div>)}
      </li>)}
    </ul>
  </>;
}

/** The classes whose text a prompt carries (spec 11 §3.3). */
const VISIBLE = new Set(["witnessed", "known", "suspected"]);

/** Historical reasons are kept beside the sent text, including scenes shed by packing. */
function HistoryPlan({ plan }: { plan: HistoryPlanTrace }) {
  return <>
    <div className="field-hint">Planning: {plan.terminal}{plan.trigger && `; trigger ${plan.trigger}`};
      {" "}added {plan.gain.length} scene{plan.gain.length === 1 ? "" : "s"}
      {plan.sufficiency !== "not_asked" && `; check ${plan.sufficiency}`}</div>
    {plan.rounds.map((round, n) => <div className="field-hint" key={[round.questions, round.terms, round.subjects].map((part) => part.join("|")).join("\u0000")}>
      Planned round {n + 1}: {round.questions.join(" / ") || "no questions"}
      {round.terms.length > 0 && `; terms ${round.terms.join(", ")}`}
    </div>)}
  </>;
}

/** The post keys this prompt actually carried in a history section: a
 *  section the packer dropped carries none, and an item it shed carries none
 *  of its posts. What the capture's accept may grant (11-S3). */
function carriedPosts(sections: ContextSection[]): Set<string> {
  const out = new Set<string>();
  for (const s of sections) {
    if (s.dropped || !s.history) continue;
    for (const item of s.history.items) {
      if (item.shed) continue;
      for (const post of item.posts ?? []) if (post.key) out.add(post.key);
    }
  }
  return out;
}

function HistoryEntries({ section, acceptCid, carried }: {
  section: ContextSection; acceptCid?: string | undefined; carried: Set<string>;
}) {
  const history = section.history;
  if (!history) return null;
  const coverage = history.coverage;
  return <>
    <div className="ctx-caption">Ceiling: {history.ceiling.toLocaleString()} tokens ({history.budget_from.join(", ")})</div>
    <div className="field-hint">Coverage: {coverage.verdict}; tiers {coverage.tiers.join(", ")};
      admitted {coverage.admitted}; agreement {coverage.agreement}</div>
    <div className="field-hint">Semantic: {coverage.semantic}</div>
    {coverage.rerank && coverage.rerank !== "off" && (
      <div className="field-hint">Rerank: {coverage.rerank}</div>)}
    {history.plan && <HistoryPlan plan={history.plan} />}
    <ul className="ctx-entries">
      {history.items.map((item) => <li key={item.key} className={item.shed ? "shed" : undefined}>
        <span className="chip on">{item.title}</span> {item.scene}; tier {item.tier};
        admitted by {item.admitted_by.join(", ") || "none"}; {item.tokens} tokens
        {item.shed && <span className="ctx-shed"> shed: budget</span>}
        <div className="field-hint">Summary: {item.summary}; excerpts: {item.excerpts.map((indices) => indices.join(", ")).join("; ") || "none"}</div>
        {item.ranks.map((rank) => <div className="field-hint" key={`${rank.round}:${rank.signal}`}>
          Round {rank.round + 1}: {rank.signal} rank {rank.rank}
        </div>)}
        {Object.entries(item.rounds).map(([roundId, round]) => <div key={roundId}>
          {round.structural?.map((reason) => <div className="field-hint" key={`${reason.relation}:${reason.refs.join(",")}`}>
            Round {Number(roundId) + 1}: {reason.relation}: {reason.refs.join(", ")}
          </div>)}
          {round.lexical && <div className="field-hint">Round {Number(roundId) + 1}: lexical score {round.lexical.score}; {round.lexical.matched.join(", ")}</div>}
          {round.semantic && <div className="field-hint">Round {Number(roundId) + 1}: cosine {round.semantic.cosine}; query {round.semantic.query + 1}</div>}
          {round.rerank && <div className="field-hint">Round {Number(roundId) + 1}: rerank {round.rerank.level ?? "ungraded"}; position {round.rerank.position ?? "none"}; {round.rerank.backend}</div>}
        </div>)}
      </li>)}
    </ul>
    {history.epistemic && <EpistemicRows detail={history.epistemic} acceptCid={acceptCid}
                                         carried={carried} />}
    {!!history.unselected && <>
      <div className="field-hint">Found, not selected: {history.unselected}</div>
      <ul className="ctx-entries">
        {(history.candidates ?? []).map((candidate) => <li key={candidate.key}>
          {candidate.scene}; tier {candidate.tier}; admitted by {candidate.admitted_by.join(", ") || "none"};{" "}
          {candidate.ranks.map((rank) => `${rank.signal} rank ${rank.rank}`).join(", ") || "unranked"}
        </li>)}
      </ul>
    </>}
  </>;
}
