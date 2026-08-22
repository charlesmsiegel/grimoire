import { useEffect, useRef, useState } from "react";
import { api, type CampaignBudget, type SceneUsage, type UsageTurn } from "../api/client";
import { Footnotes, about, bound, bucketPrice, money, turnPrice } from "./cost";

/** What this scene's turns cost, and where the campaign stands against its
 *  budget (#153).
 *
 *  The inspector's Cost section, sibling to Context. The two answer the same
 *  question from opposite ends: Context is what grimoire *composed*, counted by
 *  a local tokenizer and knowing nothing about the reply or the bill; this is
 *  what the provider said it actually charged, per turn, after the fact.
 *
 *  Deliberately not a panel of its own. Cost is read while playing — "that
 *  reroll was expensive" is a thought you have mid-scene — and a surface you
 *  have to leave the scene to open is one nobody opens.
 *
 *  Campaign-scoped budget in a scene-scoped rail, the same shape (and the same
 *  justification) as the Campaign clock section below it: a budget is only
 *  useful where the spending happens.
 *
 *  **A price nobody reported is never rendered as zero.** Every OpenAI-compatible
 *  endpoint reports no cost at all today, and a `$0.00` on those turns would say
 *  the calls were free rather than uncounted — so an unpriced turn says
 *  "unpriced", and a total with unpriced calls under it says it is a floor.
 */
export function CostPanel({ cid, sid, refreshKey }: {
  cid: string; sid: string;
  /** Bumped by the inspector once per turn, which is exactly when this moves. */
  refreshKey?: number;
}) {
  const [usage, setUsage] = useState<SceneUsage | null>(null);
  const [budget, setBudget] = useState<CampaignBudget | null>(null);
  const [limit, setLimit] = useState("");
  const [period, setPeriod] = useState("monthly");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Which campaign's stored budget the form below has been filled in from.
   *  This reloads once a turn, and seeding the inputs on every reload would
   *  wipe a figure half-typed when a reply landed. So the server fills the form
   *  once per campaign; after that the form belongs to the reader, and a
   *  successful save is what puts the stored value back into it. */
  const seededFrom = useRef<string | null>(null);

  // Guarded on the reader still being here, the rule every read in this rail
  // follows (`SceneInspector`'s `mine()`): the inspector stays mounted across a
  // scene switch, so a read issued for the scene they just left settles after
  // the new one's and would put its cost under the new scene's name. A ledger
  // scan is slower than most reads here, which makes that ordering likely
  // rather than theoretical.
  useEffect(() => {
    let live = true;
    api.getSceneUsage(cid, sid)
      .then((u) => { if (live) setUsage(u); })
      .catch(() => { if (live) setUsage(null); });
    api.getCampaignBudget(cid).then((b) => {
      if (!live) return;
      setBudget(b);
      if (seededFrom.current === cid) return;
      seededFrom.current = cid;
      // Seeded from the server's answer rather than from what was typed: the
      // stored figure is rounded to the cent, and leaving "12.567" in the box
      // next to a saved 12.57 invites a reader to "fix" the display by saving
      // the number back.
      setLimit(b.level === "off" ? "" : String(b.limit_usd));
      setPeriod(b.period);
    }).catch(() => { if (live) setBudget(null); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);

  async function saveBudget(next: number | null) {
    setError(null);
    setBusy(true);
    try {
      const b = await api.setCampaignBudget(cid, { budget_usd: next, budget_period: period });
      setBudget(b);
      setLimit(b.level === "off" ? "" : String(b.limit_usd));
      setEditing(false);
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const typed = parseFloat(limit);
  const totals = usage?.totals;

  return (
    <div className="cost-panel">
      {/* `calls > 0`, not merely "the read landed": a scene that has generated
          nothing would otherwise head its own empty list with "$0.00 · 0 turns",
          which is both noise and the one figure this panel must not print
          casually. The line below says the same thing in words. */}
      {totals && totals.calls > 0 && (
        <>
          <div className="ctx-tokens">
            {bucketPrice(totals)} · {totals.calls} {totals.calls === 1 ? "turn" : "turns"}
            {" · "}{totals.total_tokens.toLocaleString()} tok
          </div>
          {/* Everything the figure above is not covering, one line per reason
              — see `cost.Footnotes` for why they are not collapsed into one. */}
          <Footnotes bucket={totals} />
          {/* The window, when it is not the scene's whole life. Said here
              rather than left to the absent chips in the transcript, where a
              post with no cost recorded looks exactly like a post that cost
              nothing. */}
          {usage?.clamped && (
            <div className="field-hint">
              Only turns since {bound(usage.since)} were scanned — this scene is older
              than the ledger's scan window, so these totals and the per-post
              costs in the transcript are a floor.
            </div>
          )}
        </>
      )}

      {usage && usage.by_task.length > 0 && (
        <div className="cost-tasks">
          {usage.by_task.map((b) => (
            <span className="chip on" key={b.key}>
              {b.key} {b.calls} · {bucketPrice(b)}
            </span>
          ))}
        </div>
      )}

      {usage && (
        <>
          <div className="ctx-caption">Per turn · newest first</div>
          {usage.turns.length === 0 && (
            <div className="field-hint">Nothing metered in this scene yet.</div>
          )}
          {/* Keyed by position, not by content. Absorb runs its phases at once
              now, so two rows genuinely can share a stamp, a task and a model —
              and a duplicate key silently mis-renders one of them. The list is
              deterministically sorted, so the index is stable for a given
              answer, and every answer replaces the whole list anyway. */}
          {usage.turns.map((t, i) => <TurnRow turn={t} key={`${t.ts}-${i}`} />)}
          {usage.truncated && (
            <div className="field-hint">
              Showing the most recent {usage.listed}. The totals above cover all of them.
            </div>
          )}
        </>
      )}

      {/* Gated on the read having landed. A caption with nothing under it reads
          as a budget of nothing rather than as a lookup that failed. */}
      {budget && <div className="ctx-caption">Campaign budget</div>}
      {budget && budget.level !== "off" && !editing && (
        <>
          <div className="ctx-bar">
            <div className={"ctx-bar-fill " + budget.level}
                 style={{ width: `${Math.min(100, Math.round((budget.fraction ?? 0) * 100))}%` }} />
          </div>
          <div className="ctx-tokens">
            {money(budget.spent_usd ?? 0)} of {money(budget.limit_usd)}
            {" · "}{budget.period === "total" ? "all time" : "this month"}
          </div>
          {budget.level === "over" && (
            <div className="field-hint error">Over budget.</div>
          )}
          {budget.level === "warn" && (
            <div className="field-hint error">
              {Math.round((budget.fraction ?? 0) * 100)}% of the budget spent.
            </div>
          )}
          {(budget.unpriced_calls ?? 0) > 0 && (
            <div className="field-hint">
              Not counted: {budget.unpriced_calls} unpriced{" "}
              {budget.unpriced_calls === 1 ? "call" : "calls"} in this period.
            </div>
          )}
          <div className="picker">
            <button onClick={() => setEditing(true)}>Edit budget</button>
            <button onClick={() => saveBudget(null)} disabled={busy}>Clear</button>
          </div>
        </>
      )}

      {budget && (budget.level === "off" || editing) && (
        <>
          {budget.level === "off" && !editing && (
            <div className="field-hint">No budget set for this campaign.</div>
          )}
          <div className="picker">
            <input type="number" step="0.01" min="0" aria-label="Budget in dollars"
                   placeholder="0.00" value={limit}
                   onChange={(e) => setLimit(e.target.value)} />
            <select aria-label="Budget period" value={period}
                    onChange={(e) => setPeriod(e.target.value)}>
              <option value="monthly">Per month</option>
              <option value="total">All time</option>
            </select>
          </div>
          <div className="picker">
            {/* Disabled rather than 400'd: the store reads anything it cannot
                make a positive number of dollars out of as "no budget", so a
                live button here would silently clear one instead of setting it. */}
            {/* A cent, not merely positive. The store keeps budgets to the
                cent and reads anything below one as "no budget", so a live
                button here would answer "Set budget" by silently clearing it. */}
            <button className="primary" onClick={() => saveBudget(typed)}
                    disabled={busy || !(typed >= 0.01)}
                    title={typed >= 0.01 ? undefined : "A budget is at least one cent"}>
              Set budget
            </button>
            {editing && <button onClick={() => setEditing(false)} disabled={busy}>Cancel</button>}
          </div>
        </>
      )}

      {error && <div className="field-hint error">{error}</div>}
    </div>
  );
}

/** One metered call. `<details>` because the interesting part — which model,
 *  how the prompt split, how long it took — is only interesting for the one
 *  turn a reader is asking about, and eighty of them expanded is a wall. */
function TurnRow({ turn }: { turn: UsageTurn }) {
  return (
    <details className={"ctx-section cost-turn" + (turn.status === "error" ? " failed" : "")}>
      <summary>
        <span className="ctx-label">{turn.task}</span>
        {turn.status === "error" && <span className="ctx-drop">{turn.error || "failed"}</span>}
        <span className="ctx-meta">{clock(turn.ts)}</span>
        <span className="ctx-meta">{turnPrice(turn)}</span>
      </summary>
      <div className="cost-turn-body">
        <div className="field-hint">{turn.model}</div>
        <div className="field-hint">
          {turn.prompt_tokens.toLocaleString()} in · {turn.completion_tokens.toLocaleString()} out
          {turn.cache_read_tokens > 0 &&
            ` · ${turn.cache_read_tokens.toLocaleString()} cached`}
        </div>
        <div className="field-hint">
          {(turn.duration_ms / 1000).toFixed(1)}s
          {turn.attempts > 1 && ` · ${turn.attempts} attempts`}
          {/* The parenthetical this whole column exists for: the figure beside
              a subscription turn is what it WOULD have cost per token, and the
              turn itself cost nothing extra. */}
          {turn.cost_basis === "equivalent" && turn.cost_usd !== null
            && ` · billed to a subscription (${about(turn.cost_usd)} per-token equivalent)`}
          {turn.cost_usd === null && turn.modelled_usd !== null
            && " · estimated from your rates"}
        </div>
      </div>
    </details>
  );
}

/** The stamp is UTC (`…Z`, written by the store); a turn list is read against
 *  the sitting it happened in, so it shows local wall-clock time. */
function clock(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}
