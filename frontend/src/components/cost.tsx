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
  const off = n(bucket.modelled_usd) + n(bucket.estimated_usd);
  if (off > 0) return about(off);
  return n(bucket.unpriced_calls) > 0 ? UNPRICED : money(billed);
}

/** A turn's price, or what an absent one means. `null` is a provider that
 *  reported nothing, which is not the same as a call that cost nothing — and
 *  once a rate exists for its model, the estimate is shown in its place,
 *  marked. */
export function turnPrice(turn: Pick<UsageTurn, "cost_usd" | "modelled_usd">): string {
  // `== null` covers both null and the absent field an older response carries,
  // which is the difference between "unpriced" and a `≈ <$0.0001` built out of
  // `undefined`.
  if (turn.cost_usd != null) return money(turn.cost_usd);
  return turn.modelled_usd != null ? about(turn.modelled_usd) : UNPRICED;
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
          At least: {plural(bucket.unpriced_calls, "call")} came back with no
          price{showRatesHint
            ? ", and no rate here can price them. Set per-token rates in Configuration."
            : "."}
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
  const figure = bucketPrice(bucket);
  if (figure === UNPRICED) return null;
  // The server's count, not `calls - 1`: a turn continued past a dice roll is
  // two calls and one answer, and reporting that as a reroll would tell a
  // player they had redone a turn they never touched.
  const rerolls = n(bucket.rerolls);
  const title = [
    `${plural(n(bucket.calls), "generation")} answering this post`,
    `${n(bucket.total_tokens).toLocaleString()} tokens`,
    n(bucket.subscription_calls) > 0
      ? `${plural(n(bucket.subscription_calls), "call")} billed to a subscription`
      : "",
    n(bucket.modelled_calls) > 0
      ? `${plural(n(bucket.modelled_calls), "call")} estimated from your rates`
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
