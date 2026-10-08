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
export const UNPRICED = "not reported";

/** `cost_basis` for a call billed against a subscription rather than per token.
 *  Its `cost_usd` is the provider's own estimate of what it would have cost,
 *  which is real usage and not money anybody paid. */
export const EQUIVALENT = "equivalent";

/** A turn a subscription served that no provider billed. A label, never a
 *  figure: `billing` says what served the call, and `cost_basis` alone says
 *  which column its money is in (ruling 4). */
export const SUBSCRIPTION_NOT_BILLED = "subscription — not billed";

/** A turn whose token counts were counted here, because the provider reported
 *  none -- so any modelled figure on it rests on a local count too. */
export const TOKENS_ESTIMATED = "tokens estimated";

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

/** A per-token rate as it was stated, in the unit the rate files keep (dollars
 *  per 1,000 tokens). Not `money`: a rate is not a bill, and four places would
 *  round a cheap model's $0.00015 to $0.0002 and anything below that to
 *  "<$0.0001". Significant digits rather than fraction digits, so no stated
 *  rate however small rounds to `$0` (ten of them also absorb the float noise
 *  a typed 0.15 can carry). A stated zero is a price and reads as `$0` — this
 *  is only ever called with a rate somebody stated; an absent one is not
 *  rendered at all. */
export function perThousand(usd: number): string {
  return `$${usd.toLocaleString(undefined, { maximumSignificantDigits: 10 })} / 1K`;
}

/** The same rate as price sheets quote it, per million tokens — shown beside
 *  the per-1K box so a rate is not typed a thousandfold off. Cents where there
 *  are any; below a cent, two significant digits, so a tiny non-zero rate
 *  never reads as `$0/M`. */
export function perMillionRate(usdPer1k: number): string {
  const perM = usdPer1k * 1000;
  const shown = perM === 0 || perM >= 0.01
    ? perM.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : perM.toLocaleString(undefined, { maximumSignificantDigits: 2 });
  return `$${shown}/M`;
}

/** An estimate, marked as one. The `≈` is not decoration — it is the whole
 *  difference between this number and the one beside it. */
export function about(usd: number): string {
  return `≈ ${money(usd)}`;
}

/** The fields the estimate-kind rule reads. */
type EstimateFields = Pick<UsageBucket,
  "estimated_usd" | "modelled_usd" | "subscription_calls" | "modelled_calls">;

/** Whether a bucket billed anything: something was charged, or there are
 *  billed calls behind a zero. Otherwise its headline falls back to an
 *  estimate, and `$0.00` would be a bill nobody reported. */
function billedAny(bucket: Pick<UsageBucket,
  "cost_usd" | "priced_calls" | "subscription_calls">): boolean {
  return n(bucket.cost_usd) > 0 || n(bucket.priced_calls) > n(bucket.subscription_calls);
}

/** The two estimate kinds a bucket holds, by the one rule every surface here
 *  uses (I4). A kind is present when its calls are -- or, for a response that
 *  names a figure without its count, when its figure is. */
function presentKinds(bucket: EstimateFields): { estimated: boolean; modelled: boolean } {
  return {
    estimated: n(bucket.subscription_calls) > 0 || n(bucket.estimated_usd) > 0,
    modelled: n(bucket.modelled_calls) > 0 || n(bucket.modelled_usd) > 0,
  };
}

/** What the headline estimate of an unbilled bucket is.
 *
 *  - `null` -- no estimate kind is present;
 *  - `"conflict"` -- two kinds whose figures are both non-zero, so any one
 *    figure reconciles to neither column;
 *  - a number -- the one non-zero figure, or 0 when every present kind is a
 *    stated zero (a local model the user rated at nothing).
 *
 *  A present-but-zero kind conflicts with nothing: adding its `$0` to the
 *  other figure reconciles to both columns. That is what lets a zero-rated
 *  local Fast sit beside a subscription Primary without the headline turning
 *  into "not reported" -- a price somebody did report is never rendered as
 *  absent. */
function soleEstimate(bucket: EstimateFields): number | "conflict" | null {
  const kinds = presentKinds(bucket);
  const present = [
    ...(kinds.estimated ? [n(bucket.estimated_usd)] : []),
    ...(kinds.modelled ? [n(bucket.modelled_usd)] : []),
  ];
  if (present.length === 0) return null;
  const nonZero = present.filter((v) => v > 0);
  if (nonZero.length > 1) return "conflict";
  return nonZero[0] ?? 0;
}

/** What a bucket of calls cost, or that nobody priced them.
 *
 *  A bucket whose calls were ALL unpriced sums to 0.0, and rendering that as
 *  `$0.00` is the one claim these views exist not to make. A bucket with even
 *  one billed call keeps its figure; `Footnotes` below is what says the figure
 *  is a floor. A bucket with no billed calls but a modelled or subscription
 *  figure shows that instead, parenthesised — an estimate is better than
 *  "not reported", and worse than a price, and reads as exactly that. */
export function bucketPrice(bucket: UsageBucket): string {
  const billed = n(bucket.cost_usd);
  if (billedAny(bucket)) return money(billed);
  // Nothing was charged, so the headline falls back to an estimate — but only
  // when ONE figure stands for it. `estimated_usd` and `modelled_usd` are both
  // per-token equivalents, and it is tempting to total them; a bucket holding
  // both (a connection changed between rerolls) would then print a figure that
  // reconciles to neither column, which is the failure the three-column split
  // exists to prevent. With two non-zero kinds the headline says
  // `not reported` and `Footnotes` prints each one separately, named, which is
  // the only rendering that can be checked against the ledger. A stated zero
  // reads `≈ $0.00`: an estimate of nothing, never a bare `$0.00` that looks
  // like spend (see `soleEstimate`).
  const sole = soleEstimate(bucket);
  if (sole === "conflict" || zeroBesideUnpriced(bucket, sole)) return UNPRICED;
  if (sole !== null) return about(sole);
  return n(bucket.unpriced_calls) > 0 ? UNPRICED : money(billed);
}

/** A stated zero is an estimate of nothing only when nothing else is missing.
 *  Beside calls nobody priced, `≈ $0.00` would read as a complete total of
 *  zero -- so the headline says `not reported`, as it did before the zero was
 *  stated. A non-zero estimate beside them keeps its figure, a floor that
 *  `Footnotes` names as one. */
function zeroBesideUnpriced(bucket: UsageBucket, sole: ReturnType<typeof soleEstimate>): boolean {
  return sole === 0 && n(bucket.unpriced_calls) > 0;
}

/** Whether `bucketPrice` is already showing an estimate as the headline.
 *
 *  It does that when nothing was billed and exactly one kind of estimate sits
 *  under the bucket. A caller that also prints a "+ ≈ $X" line then shows the
 *  same amount twice, reading as two amounts where the ledger holds one — so
 *  the supplemental lines ask this first. */
export function headlineIsEstimate(bucket: UsageBucket): boolean {
  if (billedAny(bucket)) return false;
  const sole = soleEstimate(bucket);
  return typeof sole === "number" && !zeroBesideUnpriced(bucket, sole);
}

/** A turn's price, or what an absent one means. `null` is a provider that
 *  reported nothing, which is not the same as a call that cost nothing — and
 *  once a rate exists for its model, the estimate is shown in its place,
 *  marked. */
export function turnPrice(
  turn: Pick<UsageTurn, "cost_usd" | "modelled_usd" | "cost_basis">,
): string {
  // `== null` covers both null and the absent field an older response carries,
  // which is the difference between "not reported" and a `≈ <$0.0001` built out of
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

/** The labels a turn row carries beside its price, in this order.
 *
 *  `SUBSCRIPTION_NOT_BILLED` only where no bill stands: a subscription turn
 *  the provider priced at its per-token equivalent, or one nobody priced. A
 *  provider tagged subscription that reported a BILLED price said it charged,
 *  so that turn is spend and is not labelled otherwise (ruling 4). */
export function turnTags(
  turn: Pick<UsageTurn, "cost_usd" | "cost_basis" | "billing" | "tokens_estimated">,
): string[] {
  const tags: string[] = [];
  if (turn.billing === "subscription"
      && (turn.cost_usd == null || turn.cost_basis === EQUIVALENT)) {
    tags.push(SUBSCRIPTION_NOT_BILLED);
  }
  if (turn.tokens_estimated === true) tags.push(TOKENS_ESTIMATED);
  return tags;
}

/** A bucket's token total, marked `≈` when any of its calls' counts were
 *  counted here rather than reported. One spelling, so every surface that
 *  prints a total marks it the same way. */
export function tokenTotal(
  bucket: Pick<UsageBucket, "total_tokens" | "estimated_token_calls">,
): string {
  const total = `${n(bucket.total_tokens).toLocaleString()} tok`;
  return n(bucket.estimated_token_calls) > 0 ? `≈ ${total}` : total;
}

/** What a figure says when some of its calls' token counts were counted here,
 *  because the provider reported none -- so a modelled figure over them rests
 *  on a local count too (spec 9.1.3: labelled "wherever its figure appears").
 *  `undefined` when none were. Spread onto a figure as its `title` on every
 *  surface that prints a bucket's price or projection without `Footnotes`
 *  beside it (the Scenes list, the scene head, the task chips, the monthly
 *  rows and the trend), so each one says so in the same words. A token total
 *  says so itself, through `tokenTotal`'s `≈`. */
export function estimatedTokensTitle(
  bucket: Partial<Pick<UsageBucket, "estimated_token_calls">>,
): string | undefined {
  const line = estimatedTokensLine(bucket);
  return line === undefined
    ? undefined
    : `${line}: the provider reported no counts, so they were counted here`;
}

/** The same note as visible text, short enough for a row of its own -- for a
 *  surface whose readers may be on a touch screen, where a `title` is never
 *  seen. `undefined` when no counts were estimated. */
export function estimatedTokensLine(
  bucket: Partial<Pick<UsageBucket, "estimated_token_calls">>,
): string | undefined {
  const k = n(bucket.estimated_token_calls);
  return k > 0 ? `${plural(k, "call")} with ${TOKENS_ESTIMATED}` : undefined;
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
 *  The two subscription lines and the estimated-tokens line are breakdowns of
 *  the counts above them, never figures of their own.
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
      {/* A slice of the line above, never a figure of its own: a fourth dollar
          figure is one more thing to add to the other three. */}
      {n(bucket.modelled_subscription_calls) > 0 && (
        <div className="field-hint">
          Of these, {plural(n(bucket.modelled_subscription_calls), "call")} ran
          on a {SUBSCRIPTION_NOT_BILLED}.
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
            && "Set per-token rates in Settings to estimate them. "}
          {n(bucket.unmetered_calls) > 0 && (
            n(bucket.unmetered_calls) === n(bucket.unpriced_calls)
              ? "No rate can price these — their provider reported no token counts."
              : `${n(bucket.unmetered_calls)} of them reported no token counts, `
                + "which no rate can price."
          )}
        </div>
      )}
      {n(bucket.unpriced_calls) > 0 && n(bucket.unpriced_subscription_calls) > 0 && (
        <div className="field-hint">
          Of these, {plural(n(bucket.unpriced_subscription_calls), "call")} ran
          on a {SUBSCRIPTION_NOT_BILLED}.
        </div>
      )}
      {n(bucket.estimated_token_calls) > 0 && (
        <div className="field-hint">
          {plural(n(bucket.estimated_token_calls), "call")} had token counts
          estimated here — the provider reported none.
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
 *  no chip at all rather than a `$0.00` or a "not reported" badge on every post in
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
  const figure = !billedAny(bucket) && soleEstimate(bucket) === "conflict"
    ? [n(bucket.estimated_usd), n(bucket.modelled_usd)].map(about).join(" + ")
    : bucketPrice(bucket);
  if (figure === UNPRICED) return null;
  // The server's count, not `calls - 1`: a turn continued past a dice roll is
  // two calls and one answer, and reporting that as a reroll would tell a
  // player they had redone a turn they never touched.
  const rerolls = n(bucket.rerolls);
  // Both slices of a subscription no provider billed: the modelled ones and the
  // ones nothing priced.
  const unbilledSubscription = n(bucket.modelled_subscription_calls)
    + n(bucket.unpriced_subscription_calls);
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
    unbilledSubscription > 0 ? `${unbilledSubscription} on a ${SUBSCRIPTION_NOT_BILLED}` : "",
    n(bucket.estimated_token_calls) > 0
      ? `${n(bucket.estimated_token_calls)} with ${TOKENS_ESTIMATED}`
      : "",
    // Pictures are paid for on every turn they ride along (#377), and an
    // estimate covers them only as far as the provider counted them as prompt
    // tokens -- so a post that sent some says so.
    n(bucket.images) > 0 ? `${plural(n(bucket.images), "image")} sent` : "",
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


/** The three money columns, side by side and never added together.
 *
 *  `bucketPrice` answers "one figure for this bucket", which is the right
 *  shape for a row in a table and the wrong one for a page whose whole subject
 *  is the money. Here each column is drawn in its own right, labelled with what
 *  it actually is, so the reader can see that a campaign whose spend is $0 has
 *  nonetheless used $4 of subscription and $1 of arithmetic.
 *
 *  There is deliberately no total. Adding any two of these produces a number
 *  that is wrong in a direction nobody can recover, and a UI that offers the
 *  sum is a UI that will be quoted.
 */
/** Exactly the fields this component reads, and no more.
 *
 *  Widened from `UsageBucket` when the campaign hub started drawing these
 *  columns from `GET /api/shell`, whose money block carries the three columns
 *  and their call counts but not the token or duration fields a ledger bucket
 *  has. Padding the shell payload with zeros to satisfy a type would have put
 *  six invented measurements on the wire; naming what is actually read costs
 *  one alias, and a `UsageBucket` still satisfies it structurally. */
export type MoneySpread = Pick<UsageBucket,
  "cost_usd" | "estimated_usd" | "modelled_usd"
  | "priced_calls" | "subscription_calls" | "unpriced_calls"
  | "modelled_calls" | "modelled_subscription_calls" | "estimated_token_calls">;
export function MoneyColumns({ bucket }: { bucket: MoneySpread }) {
  const billed = n(bucket.cost_usd);
  const estimated = n(bucket.estimated_usd);
  const modelled = n(bucket.modelled_usd);
  const unpriced = n(bucket.unpriced_calls);
  // The same presence rule `bucketPrice` uses: a column whose calls are there
  // shows its figure, a stated zero included (`≈ $0.00`); a column with none
  // draws a dash rather than a zero nobody reported.
  const kinds = presentKinds(bucket);
  const modelledSubscription = n(bucket.modelled_subscription_calls);
  return (
    <div className="money-columns">
      <div className="money-col spend">
        <div className="money-label">Spend</div>
        {/* `money(0)` is "$0.00", and a bucket nobody priced is exactly where
            that would be a lie -- the one claim this module exists to prevent.
            The same test `bucketPrice` uses: a figure only when something was
            actually charged, or when there are billed calls behind a zero. */}
        <div className="money-figure">
          {billedAny(bucket)
            ? money(billed)
            : <span className="money-unpriced">{UNPRICED}</span>}
        </div>
        <div className="money-hint">What a provider said it charged.</div>
      </div>
      <div className="money-col">
        <div className="money-label">Estimated</div>
        <div className="money-figure">{kinds.estimated ? about(estimated) : "—"}</div>
        <div className="money-hint">
          Billed to a subscription. Real usage; not money anybody paid.
        </div>
      </div>
      <div className="money-col">
        <div className="money-label">Modelled</div>
        <div className="money-figure">{kinds.modelled ? about(modelled) : "—"}</div>
        <div className="money-hint">
          Priced against your rates. Arithmetic, not a receipt.
          {modelledSubscription > 0
            && ` ${modelledSubscription} on a ${SUBSCRIPTION_NOT_BILLED}.`}
          {n(bucket.estimated_token_calls) > 0
            && " Some token counts were estimated here."}
        </div>
      </div>
      <p className="money-note">
        Never summed — three different claims about money.
        {unpriced > 0 && (
          <>
            {" "}
            <span className="money-unpriced">
              ⚠ {unpriced} call{unpriced === 1 ? "" : "s"} reported no price
              {" "}— not counted as zero.
            </span>
          </>
        )}
      </p>
    </div>
  );
}
