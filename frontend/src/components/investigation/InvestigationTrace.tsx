/** What an investigation looked at before it said what it said (roadmap 12
 *  §8): its steps -- which tool, what it asked, how much came back -- the
 *  scenes it read, and what the run cost, in the three money columns kept
 *  apart. Never a result's text: the trace carries none.
 *
 *  Shared by the continuity review's finding detail (a proposal's
 *  `investigation`) and the ledger's "Ask the history" answer. */
import type { ReactNode } from "react";
import type { InvestigationOutcome, InvestigationTrace as Trace } from "../../api/types";
import { about, money, UNPRICED } from "../cost";

/** What each tool is called in a sentence. */
const TOOL_PHRASES: Record<string, string> = {
  search_history: "Searched the history",
  get_scene_summary: "Read a scene's summary",
  get_scene_excerpt: "Read part of a scene",
  read_scene_window: "Read a page of a scene",
  get_record: "Looked up a record",
  get_record_history: "Read a record's history",
  get_related: "Looked at what a record touches",
  get_timeline: "Read the timeline",
  get_pressure: "Read the deadlines",
  get_actor: "Looked up a character",
  get_group: "Looked up a group",
  decide: "Asked a closed question",
  // The call the run ended on: its last step.
  propose: "Proposed a decision",
  answer: "Answered",
  finish_evidence: "Chose its evidence",
  finish_no_change: "Found nothing to change",
  finish_insufficient_evidence: "Found too little to say",
};

/** The calls that end a run rather than read: not reading, for "read nothing". */
const FINISHES = new Set(["propose", "answer", "finish_evidence", "finish_no_change",
                          "finish_insufficient_evidence"]);

/** What a budget limit is called. */
const LIMIT_PHRASES: Record<string, string> = {
  turns: "its turns", tool_calls: "its tool calls", decisions: "its questions",
  wall: "its time", spend: "its spend ceiling", result_chars: "how much it may read",
  context: "the model's context window",
};

/** One sentence for how a run ended. */
export function outcomeSentence(outcome: Pick<InvestigationOutcome, "state" | "limit" | "reason" | "error">): string {
  switch (outcome.state) {
    case "completed": return "The investigation finished.";
    case "no_change": return "The investigation found nothing to change.";
    case "insufficient_evidence":
      return "The investigation could not find enough in the history to settle it.";
    case "budget_exhausted":
      return `The investigation stopped at ${LIMIT_PHRASES[outcome.limit] ?? "its budget"}`
        + " before it finished.";
    case "refused":
      return outcome.reason === "unpriceable"
        ? "The investigation was not run: its model has no price, and a spend ceiling is set."
        : "The investigation was not run.";
    case "failed": return `The investigation failed (${outcome.error || "error"}).`;
    case "cancelled": return "The investigation was stopped.";
  }
}

function asked(args: Record<string, unknown>): string {
  const words = ["query", "question", "scene", "ref"]
    .map((k) => args[k]).filter((v): v is string => typeof v === "string" && v !== "");
  return words.join(" · ");
}

export function InvestigationTrace(
  { trace, sceneLink }: { trace: Trace; sceneLink: (sid: string) => ReactNode },
) {
  const { usage } = trace;
  const cost = usage.cost;
  const read = trace.inspected.map((ref) => ref.replace(/^scene:/, ""));
  // A step has no id of its own and the same call can repeat, so each key is
  // its content plus how many identical steps came before it.
  const seen = new Map<string, number>();
  const keyed = trace.steps.map((s) => {
    const base = `${s.turn}:${s.tool}:${JSON.stringify(s.args)}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return { step: s, key: `${base}#${n}` };
  });
  return (
    <section className="investigation-trace" aria-label="What the investigation read">
      <h4>What the investigation read</h4>
      {trace.steps.some((s) => !FINISHES.has(s.tool)) ? (
        <ol className="investigation-steps">
          {keyed.map(({ step: s, key }) => (
            <li key={key}>
              <span>{TOOL_PHRASES[s.tool] ?? s.tool}</span>
              {asked(s.args) && <span className="field-hint"> — {asked(s.args)}</span>}
              {!s.result.ok && <span className="field-hint"> (nothing it could use)</span>}
              {s.result.truncated && <span className="field-hint"> (cut short)</span>}
            </li>
          ))}
        </ol>
      ) : <p className="field-hint">It read nothing before it finished.</p>}
      {read.length > 0 && (
        <p className="field-hint">
          Scenes read: {read.map((sid, i) => <span key={sid}>{i > 0 && ", "}{sceneLink(sid)}</span>)}
        </p>
      )}
      <p className="field-hint">
        {usage.model_turns} model {usage.model_turns === 1 ? "turn" : "turns"}, {usage.tool_calls}{" "}
        tool {usage.tool_calls === 1 ? "call" : "calls"}
        {usage.seconds !== null && `, ${usage.seconds.toFixed(1)}s`}.
        {" Spend "}{cost.cost_usd !== null ? money(cost.cost_usd) : UNPRICED}
        {cost.estimated_usd !== null && `; estimated ${about(cost.estimated_usd)}`}
        {cost.modelled_usd !== null && `; modelled ${about(cost.modelled_usd)}`}
        {usage.unpriced_calls > 0
          && ` (${usage.unpriced_calls} ${usage.unpriced_calls === 1 ? "call" : "calls"} unpriced)`}
        .
      </p>
    </section>
  );
}

export default InvestigationTrace;
