import type { UsageBucket, UsagePostBucket, UsageTurn } from "../api/client";

/** How money is written everywhere costs are shown (#153, #158).
 *
 *  One module rather than helpers per view, because the rule it encodes is not
 *  a formatting preference — it is the promise the ledger makes. **A price
 *  nobody reported is never rendered as zero.** Every OpenAI-compatible
 *  endpoint reports no cost at all, and a `$0.00` on those calls says they were
 *  free rather than uncounted. Three surfaces show costs now (the scene
 *  inspector, the campaign cost report, the transcript's per-post chip), and
 *  three copies of that judgement is how one of them comes to break it.
 *
 *  The three money columns and what each is worth reading as:
 *
 *  - `cost_usd` — charged. The only figure that is spend.
 *  - `estimated_usd` — a call billed against a subscription rather than per
 *    token, priced by the provider at what it *would* have cost. Not spend, so
 *    it is shown separately with its per-token equivalent in a parenthetical.
 *  - `modelled_usd` — a call nobody priced, costed here against the user's own
 *    rate table (#158). Weaker still, and shown the same way: separately, with
 *    the estimate parenthesised so it can never be mistaken for a bill.
 */

/** A bucket column as a number, whatever came back.
 *
 *  The ledger rounds every money column before it leaves the server, so this
 *  cannot be `undefined` from a current build. It can be from an older one — a
 *  response already in the client's cache when the app updated, or an Android
 *  shell whose packaged backend predates a column — and `undefined + 0.5` is
 *  `NaN`, which `money` renders as a price. A missing column has to read as
 *  "nothing in it", never as an unreadable figure. */
function n(value: number | undefined | null): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** What a bucket or a turn reads as when nothing can price it. A constant
 *  because two callers branch on it, and a second spelling of the word would
 *  silently stop one of them from recognising the case. */
export const UNPRICED = "unpriced";

/** `cost_basis` for a call billed against a subscription rather than per token.
 *  Its `cost_usd` is the provider's own estimate of what it would have cost,
 *  which is real usage and not money anybody paid. */
export const EQUIVALENT = "equivalent";

/** A dollar figure at the precision it is actually worth reading at. A cheap
 *  model's turn costs $0.0042, and `toFixed(2)` renders every one of them as
 *  $0.00 — a whole scene of "free" turns adding up to a bill. */
export function money(usd: number): string {
  // Grouped, like the token counts beside it: an ungrouped $1000.00 next to a
  // 1,880 tok is two number systems in one line.
  if (usd >= 0.01 || usd === 0) {
    return `$${usd.toLocaleString(undefined, { minimumFractionDigits: 2,
                                               maximumFractionDigits: 2 })}`;
  }
  return usd >= 0.0001 ? `$${usd.toFixed(4)}` : "<$0.0001";
}

/** An estimate, marked as one. The `≈` is not decoration — it is the whole
 *  difference between this number and the one beside it. */
export function about(usd: number): string {
  return `≈ ${money(usd)}`;
}

/** What a bucket of calls cost, or that nobody priced them.
 *
 *  A bucket whose calls were ALL unpriced sums to 0.0, and rendering that as
 *  `$0.00` is the one claim these views exist not to make. A bucket with even
 *  one billed call keeps its figure; `Footnotes` below is what says the figure
 *  is a floor. A bucket with no billed calls but a modelled or subscription
 *  figure shows that instead, parenthesised — an estimate is better than
 *  "unpriced", and worse than a price, and reads as exactly that. */
export function bucketPrice(bucket: UsageBucket): string {
  const billed = n(bucket.cost_usd);
  if (billed > 0 || n(bucket.priced_calls) > n(bucket.subscription_calls)) {
    return money(billed);
  }
  // Nothing was charged, so the headline falls back to an estimate — but only
  // when there is ONE kind of estimate under it. `estimated_usd` and
  // `modelled_usd` are both per-token equivalents, and it is tempting to total
  // them; a bucket holding both (a connection changed between rerolls) would
  // then print a figure that reconciles to neither column, which is the
  // failure the three-column split exists to prevent. With both present the
  // headline says `unpriced` and `Footnotes` prints each one separately,
  // named, which is the only rendering that can be checked against the ledger.
  const estimates = [n(bucket.estimated_usd), n(bucket.modelled_usd)].filter((v) => v > 0);
  if (estimates.length === 1) return about(estimates[0]);
  if (estimates.length > 1) return UNPRICED;
  return n(bucket.unpriced_calls) > 0 ? UNPRICED : money(billed);
}

/** A turn's price, or what an absent one means. `null` is a provider that
 *  reported nothing, which is not the same as a call that cost nothing — and
 *  once a rate exists for its model, the estimate is shown in its place,
 *  marked. */
export function turnPrice(
  turn: Pick<UsageTurn, "cost_usd" | "modelled_usd" | "cost_basis">,
): string {
  // `== null` covers both null and the absent field an older response carries,
  // which is the difference between "unpriced" and a `≈ <$0.0001` built out of
  // `undefined`.
  if (turn.cost_usd != null) {
    // A subscription turn's `cost_usd` is what it WOULD have cost, not what
    // anyone paid — so it is marked here, in the collapsed row. The row's body
    // says so too, but that is only visible once expanded, and the turn list
    // is the surface a reader scans. An unmarked `$…` there is non-spend
    // presented as spend, which is the one thing this module exists to prevent.
    return turn.cost_basis === EQUIVALENT ? about(turn.cost_usd) : money(turn.cost_usd);
  }
  return turn.modelled_usd != null ? about(turn.modelled_usd) : UNPRICED;
}

/** A date-only ledger bound (`since`/`until`) as a reader's own calendar day.
 *
 *  NOT `new Date(s).toLocaleDateString()`: a bare `YYYY-MM-DD` is parsed as UTC
 *  midnight, so west of Greenwich that renders the day before — a scan window
 *  reported a day early at both ends, describing a file the report never read.
 *  Built from the parts at noon local, where no offset can move the day. */
export function bound(date: string): string {
  const [y, m, d] = (date ?? "").split("-").map(Number);
  if (!y || !m || !d) return date;
  return new Date(y, m - 1, d, 12).toLocaleDateString();
}

function plural(n: number, one: string): string {
  return `${n} ${n === 1 ? one : one + "s"}`;
}

/** Everything a headline figure is NOT covering, as separate lines.
 *
 *  Three warnings rather than one, because the reasons differ and so does what
 *  a reader can do about each: a subscription call is real usage that cost no
 *  money, a modelled call is a guess the reader themself configured, and an
 *  unpriced call is a hole they can close by typing a rate. Collapsed into one
 *  "totals may be incomplete" they would say nothing actionable at all. */
export function Footnotes({ bucket, showRatesHint = true }: {
  bucket: UsageBucket;
  /** Suppressed where the hint has nowhere to point — a chip in the
   *  transcript is not a place to send someone to Configuration. */
  showRatesHint?: boolean;
}) {
  return (
    <>
      {n(bucket.subscription_calls) > 0 && (
        <div className="field-hint">
          Plus {plural(bucket.subscription_calls, "call")} billed to a
          subscription, not charged ({about(n(bucket.estimated_usd))} at the
          provider's per-token rates).
        </div>
      )}
      {n(bucket.modelled_calls) > 0 && (
        <div className="field-hint">
          Plus {plural(bucket.modelled_calls, "call")} the provider did not
          price ({about(n(bucket.modelled_usd))} at your per-token rates).
        </div>
      )}
      {n(bucket.unpriced_calls) > 0 && (
        <div className="field-hint">
          At least: {plural(n(bucket.unpriced_calls), "call")} came back with no
          price.{" "}
          {/* Only offered where it would actually help. A call whose provider
              reported no token counts cannot be priced by any rate, and telling
              a reader to go and set one sends them to an action that cannot
              resolve the warning they are reading. */}
          {showRatesHint && n(bucket.unpriced_calls) > n(bucket.unmetered_calls)
            && "Set per-token rates in Configuration to estimate them. "}
          {n(bucket.unmetered_calls) > 0 && (
            n(bucket.unmetered_calls) === n(bucket.unpriced_calls)
              ? "No rate can price these — their provider reported no token counts."
              : `${n(bucket.unmetered_calls)} of them reported no token counts, `
                + "which no rate can price."
          )}
        </div>
      )}
    </>
  );
}

/** What one player post cost to answer, in the transcript beside it (#153).
 *
 *  The figure covers every generation made for this post — the reply on screen
 *  and each reroll that was thrown away — which is precisely the number a
 *  reader cannot get any other way: the transcript shows one reply, and the
 *  four that preceded it are gone but were paid for.
 *
 *  A chip rather than a line, and only where there is something to say: an
 *  unmetered post (an endpoint that reports nothing, with no rate set) shows
 *  no chip at all rather than a `$0.00` or an "unpriced" badge on every post in
 *  the scene. What is worth interrupting a transcript for is a cost; the
 *  absence of one is the inspector's business.
 */
export function PostCost({ bucket }: { bucket: UsagePostBucket }) {
  // `bucketPrice` answers UNPRICED for a bucket holding BOTH kinds of estimate,
  // because a merged headline reconciles to neither column. On the scene and
  // campaign surfaces `Footnotes` prints them separately underneath; a chip has
  // no underneath, so it prints them side by side instead — still two figures,
  // still never summed. Hiding the chip there would drop a post whose every
  // generation the ledger actually priced, reroll count and all.
  const estimates = [n(bucket.estimated_usd), n(bucket.modelled_usd)].filter((v) => v > 0);
  const figure = bucketPrice(bucket) === UNPRICED && estimates.length > 1
    ? estimates.map(about).join(" + ")
    : bucketPrice(bucket);
  if (figure === UNPRICED) return null;
  // The server's count, not `calls - 1`: a turn continued past a dice roll is
  // two calls and one answer, and reporting that as a reroll would tell a
  // player they had redone a turn they never touched.
  const rerolls = n(bucket.rerolls);
  const title = [
    `${plural(n(bucket.calls), "generation")} answering this post`,
    `${n(bucket.total_tokens).toLocaleString()} tokens`,
    n(bucket.subscription_calls) > 0
      ? `${plural(n(bucket.subscription_calls), "call")} billed to a subscription `
        + `(${about(n(bucket.estimated_usd))})`
      : "",
    n(bucket.modelled_calls) > 0
      ? `${plural(n(bucket.modelled_calls), "call")} estimated from your rates `
        + `(${about(n(bucket.modelled_usd))})`
      : "",
    n(bucket.unpriced_calls) > 0
      ? `${plural(n(bucket.unpriced_calls), "call")} came back with no price`
      : "",
  ].filter(Boolean).join(" · ");
  return (
    <span className="post-cost" title={title}>
      {figure}
      {/* The reroll count is the half of this that the transcript itself
          cannot show — the takes that were discarded are not on screen. */}
      {rerolls > 0 && <span className="post-cost-rerolls">
        {" "}· {rerolls === 1 ? "1 reroll" : `${rerolls} rerolls`}
      </span>}
    </span>
  );
}
