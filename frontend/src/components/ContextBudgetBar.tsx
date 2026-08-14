import { type SceneContext } from "../api/client";

/** The four groups the stacked bar shows, in the order the packer protects
 *  them — most protected first, so the bar reads left to right as "what would
 *  survive" and the tail is what goes first.
 *
 *  These are the packer's tiers folded into the four names the Context copy
 *  already uses. `characters` is the lock-in tier: the system prompts, the
 *  character descriptions and the reply format, which is exactly the set that
 *  paragraph calls "never dropped". */
const BUCKETS: { id: string; label: string }[] = [
  { id: "characters", label: "CHARACTERS" },
  { id: "frame", label: "STANDING FRAME" },
  { id: "conversation", label: "CONVERSATION" },
  { id: "recalled", label: "RECALLED" },
];

/** Tier → bucket. Keyed by plain string rather than switched on
 *  `ContextSection["tier"]` on purpose: the backend grew a fifth tier
 *  (`recalled`, below `archive`, so semantic recall cannot evict the earlier
 *  scenes it was meant to add to) and the client's union has not caught up, so
 *  a switch would be exhaustive over a set that is missing a member. Anything
 *  unrecognised lands in the standing frame rather than vanishing from a bar
 *  whose whole claim is that it accounts for the prompt. */
const BUCKET_OF: Record<string, string> = {
  "lock-in": "characters",
  spotlight: "frame",
  background: "frame",
  history: "conversation",
  archive: "recalled",
  recalled: "recalled",
};

/** One prompt, drawn against the ceiling it was packed to.
 *
 *  Config has no campaign, so the caller has to say which prompt this is and
 *  where it came from — the numbers are only meaningful next to the campaign
 *  that produced them, and a bar labelled "the last turn" on a global settings
 *  page would be claiming an authority no stored record has.
 *
 *  Every figure here is read off a frozen snapshot the context builder already
 *  wrote; nothing is estimated. Sections the packer DROPPED are left out of
 *  the stack — they were rendered but not sent, and a bar that counted them
 *  would be drawing a prompt that never existed — and reported instead by the
 *  verdict, which is the one number a user is actually looking for. */
export function ContextBudgetBar({ ctx, label }: { ctx: SceneContext; label: string }) {
  const kept = ctx.sections.filter((s) => !s.dropped);
  const tokens = (bucket: string) => kept
    .filter((s) => (BUCKET_OF[s.tier] ?? "frame") === bucket)
    .reduce((sum, s) => sum + s.tokens, 0);
  const shown = BUCKETS.map((b) => ({ ...b, tokens: tokens(b.id) })).filter((b) => b.tokens > 0);

  // What the full width of the track means. With a budget, the ceiling — the
  // empty tail is the headroom, which is the point of showing it. Without one
  // there is no ceiling to be a fraction of, so the bar falls back to the
  // prompt's own size and reads as a composition rather than a fill.
  const ceiling = ctx.budget_tokens > 0 ? ctx.budget_tokens : 0;
  const scale = ceiling || shown.reduce((sum, b) => sum + b.tokens, 0);
  const width = (n: number) => (scale > 0 ? `${Math.min(100, (n / scale) * 100)}%` : "0%");
  const pct = ceiling > 0 ? Math.round((ctx.total_tokens / ceiling) * 100) : 0;

  const verdict = ctx.dropped_tokens > 0
    ? `${ctx.dropped_tokens.toLocaleString()} TOKENS DROPPED`
    : "NOTHING DROPPED";
  const total = ceiling > 0
    ? `${ctx.total_tokens.toLocaleString()} / ${ceiling.toLocaleString()} · ${pct}%`
    : `${ctx.total_tokens.toLocaleString()} TOKENS · NO CEILING`;

  return (
    <section className="ctx-budget">
      <header className="ctx-budget-head">
        <span className="data-label">{label}</span>
        <span className="ctx-budget-total">{total}</span>
      </header>
      {/* The bar is a picture of a sentence a screen reader still has to be
          told: the legend below carries the same figures as text, so the track
          itself is decorative and says so. */}
      <div className="ctx-budget-track" aria-hidden>
        {shown.map((b) => (
          <div key={b.id} className={`ctx-budget-slice ${b.id}`} style={{ width: width(b.tokens) }} />
        ))}
      </div>
      <ul className="ctx-budget-legend">
        {shown.map((b) => (
          <li key={b.id}>
            <span className={`ctx-budget-key ${b.id}`} aria-hidden />
            {b.label} {b.tokens.toLocaleString()}
          </li>
        ))}
        <li className={"ctx-budget-verdict" + (ctx.dropped_tokens > 0 ? " dropped" : "")}>
          {verdict}
        </li>
      </ul>
    </section>
  );
}
