import { render, screen } from "@testing-library/react";
import { EQUIVALENT, Footnotes, PostCost, SUBSCRIPTION_NOT_BILLED, TOKENS_ESTIMATED, UNPRICED,
         about, bound, bucketPrice, headlineIsEstimate, money, perMillionRate, perThousand,
         estimatedTokensTitle, tokenTotal, turnPrice, turnTags, MoneyColumns } from "./cost";

/** The rule these three surfaces share: a price nobody reported is never
 *  rendered as zero, and a figure grimoire computed is never rendered as one it
 *  was charged. */

const ZERO = {
  calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
  cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
  modelled_usd: 0, priced_calls: 0, unpriced_calls: 0,
  subscription_calls: 0, modelled_calls: 0, unmetered_calls: 0, duration_ms: 0,
};

test("a cheap turn keeps the digits that make it non-zero", () => {
  // `toFixed(2)` renders a $0.0042 turn as $0.00, which is a whole scene of
  // "free" turns adding up to a bill.
  expect(money(0.0042)).toBe("$0.0042");
  expect(money(12.5)).toBe("$12.50");
  expect(money(0.00001)).toBe("<$0.0001");
  expect(money(0)).toBe("$0.00");
});

test("an estimate is marked as one", () => {
  expect(about(0.25)).toBe("≈ $0.25");
});

test("a bucket of calls nobody priced says so rather than $0.00", () => {
  expect(bucketPrice({ ...ZERO, calls: 3, unpriced_calls: 3 })).toBe("not reported");
});

test("one billed call among unpriced ones keeps the figure", () => {
  expect(bucketPrice({ ...ZERO, calls: 3, priced_calls: 1, unpriced_calls: 2,
                       cost_usd: 0.02 })).toBe("$0.02");
});

test("a bucket that was only ever modelled reads as an estimate", () => {
  expect(bucketPrice({ ...ZERO, calls: 2, modelled_calls: 2, modelled_usd: 0.25 }))
    .toBe("≈ $0.25");
});

test("a bucket holding both kinds of estimate refuses to merge them", () => {
  // Both are per-token equivalents, and totalling them would print a figure
  // that reconciles to neither column. The footnotes name each separately.
  expect(bucketPrice({ ...ZERO, calls: 4, priced_calls: 2, subscription_calls: 2,
                       estimated_usd: 0.5, modelled_calls: 2,
                       modelled_usd: 0.25 })).toBe("not reported");
});

test("a bucket that was only ever subscription-billed reads as an estimate too", () => {
  // `priced_calls` counts it, but nobody was charged — so it must not be
  // rendered as a bill of $0.00 either.
  expect(bucketPrice({ ...ZERO, calls: 2, priced_calls: 2, subscription_calls: 2,
                       estimated_usd: 0.5 })).toBe("≈ $0.50");
});

test("a response missing a money column renders nothing rather than NaN", () => {
  // An older build's cached answer. `undefined + 0.5` is NaN, and `money(NaN)`
  // is a price.
  const stale = { ...ZERO, calls: 1, priced_calls: 1, cost_usd: 0.02 } as never;
  delete (stale as Record<string, unknown>).modelled_usd;
  expect(bucketPrice(stale)).toBe("$0.02");
});

test("a turn's estimate is shown in place of an absent price, marked", () => {
  const turn = { cost_usd: null, modelled_usd: null, cost_basis: "" };
  expect(turnPrice({ ...turn, modelled_usd: 0.01 })).toBe("≈ $0.01");
  expect(turnPrice(turn)).toBe("not reported");
  expect(turnPrice({ ...turn, cost_usd: 0.02, cost_basis: "billed" })).toBe("$0.02");
});

test("a subscription turn is marked in the collapsed row, not just when opened", () => {
  // Its `cost_usd` is what it WOULD have cost. Rendered bare in the turn list
  // — the surface a reader scans — that is non-spend presented as spend.
  expect(turnPrice({ cost_usd: 0.5, modelled_usd: null, cost_basis: "equivalent" }))
    .toBe("≈ $0.50");
});

test("a date-only ledger bound is not shifted a day by a timezone", () => {
  // `new Date("2026-06-01")` is UTC midnight, which west of Greenwich renders
  // as May 31 — a scan window reported a day early at both ends.
  expect(bound("2026-06-01")).toBe(new Date(2026, 5, 1, 12).toLocaleDateString());
  expect(bound("")).toBe("");
  expect(bound("not a date")).toBe("not a date");
});

test("the footnotes name each kind of uncounted call separately", () => {
  render(<Footnotes bucket={{ ...ZERO, calls: 6, priced_calls: 3,
                              subscription_calls: 2, estimated_usd: 0.5,
                              modelled_calls: 2, modelled_usd: 0.25,
                              unpriced_calls: 1 }} />);

  expect(screen.getByText(/2 calls billed to a subscription/)).toBeInTheDocument();
  expect(screen.getByText(/2 calls the provider did not price/)).toBeInTheDocument();
  expect(screen.getByText(/1 call came back with no price/)).toBeInTheDocument();
});

test("the rates hint is offered only where a rate could actually help", () => {
  // A call whose provider reported no token counts cannot be priced by any
  // rate, so sending the reader to Configuration is sending them nowhere.
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, unpriced_calls: 2, unmetered_calls: 2 }} />);

  expect(container.textContent).toMatch(/No rate can price these/);
  expect(container.textContent).not.toMatch(/Set per-token rates/);
});

test("unpriced embed calls that reported a prompt count are offered rates", () => {
  // An embedding has no output, so its row carries a prompt count and no
  // completion count. The server reads that as zero output, not as a call
  // nobody counted (`usage._completion_count`), so such a bucket arrives with
  // `unmetered_calls: 0` -- and the card must send the reader to the rate that
  // would price them rather than say no rate can.
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, prompt_tokens: 40, total_tokens: 40,
                         unpriced_calls: 2, unmetered_calls: 0 }} />);

  expect(container.textContent).toMatch(/Set per-token rates in Settings/);
  expect(container.textContent).not.toMatch(/No rate can price these/);
  expect(container.textContent).not.toMatch(/reported no token counts/);
});

test("a mix says how many of them no rate can reach", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 3, unpriced_calls: 3, unmetered_calls: 1 }} />);

  expect(container.textContent).toMatch(/Set per-token rates in Settings/);
  expect(container.textContent).toMatch(/1 of them reported no token counts/);
});

test("native decisions nobody priced are never offered a rate", () => {
  // A native decision is never modelled (`usage._modellable`), whatever its
  // counts, so a rate would price nothing: no hint, and no $0 either.
  const native = { ...ZERO, calls: 2, prompt_tokens: 40, total_tokens: 40,
                   unpriced_calls: 2, unpriced_native_calls: 2 };
  const { container } = render(<Footnotes bucket={native} />);

  expect(container.textContent).not.toMatch(/Set per-token rates/);
  expect(container.textContent).toMatch(
    /No rate can price these — a native decision is priced by its provider or not at all/);
  expect(container.textContent).not.toMatch(/reported no token counts/);
  expect(bucketPrice(native)).toBe(UNPRICED);
});

test("beside calls a rate could price, native decisions are named apart", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 4, unpriced_calls: 4, unpriced_native_calls: 1,
                         unmetered_calls: 1 }} />);

  // Two of the four could be priced by a rate.
  expect(container.textContent).toMatch(/Set per-token rates in Settings/);
  expect(container.textContent).toMatch(/1 of them reported no token counts/);
  expect(container.textContent).toMatch(/1 of them was a native decision, which no rate prices/);
});

test("native and unmetered calls alone leave no call a rate could price", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 3, unpriced_calls: 3, unpriced_native_calls: 2,
                         unmetered_calls: 1 }} />);

  expect(container.textContent).not.toMatch(/Set per-token rates/);
  expect(container.textContent).toMatch(/2 of them were native decisions, which no rate prices/);
});

test("a complete bucket has no footnotes at all", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, priced_calls: 2, cost_usd: 0.02 }} />);

  expect(container.textContent).toBe("");
});

// ---- the per-post chip in the transcript ----
test("a post's chip totals every generation made for it", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 2, calls: 3,
                             priced_calls: 3, cost_usd: 0.06,
                             total_tokens: 900 }} />);

  expect(screen.getByText(/\$0\.06/)).toBeInTheDocument();
  // The half the transcript itself cannot show: two takes were paid for and
  // thrown away.
  expect(screen.getByText(/2 rerolls/)).toBeInTheDocument();
});

test("a turn continued past a dice roll is not a reroll", () => {
  // Two calls, one answer. `calls - 1` would tell a player they had redone a
  // turn they never touched.
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 0, calls: 2,
                             priced_calls: 2, cost_usd: 0.04 }} />);

  expect(screen.getByText(/\$0\.04/)).toBeInTheDocument();
  expect(screen.queryByText(/reroll/)).not.toBeInTheDocument();
});

test("a post answered once says nothing about rerolls", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 0, calls: 1,
                             priced_calls: 1, cost_usd: 0.02 }} />);

  expect(screen.queryByText(/reroll/)).not.toBeInTheDocument();
});

test("a post whose rerolls mixed both estimate kinds still gets a chip", () => {
  // `bucketPrice` answers UNPRICED there so no merged headline is printed, but
  // the ledger priced every one of these generations — hiding the chip would
  // drop the post entirely, reroll count and all.
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 1, calls: 2,
                             priced_calls: 1, subscription_calls: 1,
                             estimated_usd: 0.5, modelled_calls: 1,
                             modelled_usd: 0.25 }} />);

  expect(screen.getByText(/≈ \$0\.50 \+ ≈ \$0\.25/)).toBeInTheDocument();
  expect(screen.getByText(/1 reroll/)).toBeInTheDocument();
  // Side by side, never summed: $0.75 reconciles to neither column.
  expect(screen.queryByText(/\$0\.75/)).toBeNull();
});

test("a post nothing could price shows no chip rather than an empty one", () => {
  // What is worth interrupting a transcript for is a cost. The absence of one
  // is the inspector's business, not a badge on every post in the scene.
  const { container } = render(
    <PostCost bucket={{ ...ZERO, post: 0, rerolls: 1, calls: 2,
                        unpriced_calls: 2 }} />);

  expect(container.textContent).toBe("");
});

test("the three columns are separate, and none of them sums the others", () => {
  // Adding any two produces a number that is wrong in a direction nobody can
  // recover. There is no total on screen because a UI that offers one gets
  // quoted.
  const { container } = render(
    <MoneyColumns bucket={{
      calls: 4, priced_calls: 2, subscription_calls: 1, unpriced_calls: 1,
      cost_usd: 4.82, estimated_usd: 1.1, modelled_usd: 0.36,
      total_tokens: 0, prompt_tokens: 0, completion_tokens: 0,
    } as any} />);
  const figures = [...container.querySelectorAll(".money-figure")]
    .map((n) => n.textContent);
  expect(figures).toEqual(["$4.82", "≈ $1.10", "≈ $0.36"]);
  // ...and the incomplete total says so rather than reading as complete.
  expect(container.textContent).toMatch(/1 call reported no price/);
});

test("a bucket nobody priced does not render spend as $0.00", () => {
  // The one claim this module exists to prevent, in the column most likely to
  // make it: `money(0)` is a perfectly good "$0.00" and a lie here.
  const { container } = render(
    <MoneyColumns bucket={{
      calls: 2, priced_calls: 0, subscription_calls: 0, unpriced_calls: 2,
      cost_usd: 0, estimated_usd: 0, modelled_usd: 0,
      total_tokens: 0, prompt_tokens: 0, completion_tokens: 0,
    } as any} />);
  expect(container.querySelector(".money-figure")?.textContent).toBe("not reported");
  expect(container.textContent).not.toMatch(/\$0\.00/);
});

test("a post that sent images says how many in its title (#377)", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 0, calls: 1,
                             priced_calls: 1, cost_usd: 0.02, images: 2 }} />);
  expect(screen.getByText("$0.02").closest(".post-cost")?.getAttribute("title"))
    .toContain("2 images sent");
});

test("a post that sent none does not mention images", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 0, calls: 1,
                             priced_calls: 1, cost_usd: 0.02 }} />);
  expect(screen.getByText("$0.02").closest(".post-cost")?.getAttribute("title"))
    .not.toContain("image");
});

test("a per-token rate reads as stated, and a zero rate is a price", () => {
  // A rate is not a bill: `money`'s four places would round $0.00015 / 1K
  // away, and a typed 0.001 must read back as 0.001.
  expect(perThousand(0.001)).toBe("$0.001 / 1K");
  expect(perThousand(0.00015)).toBe("$0.00015 / 1K");
  expect(perThousand(0)).toBe("$0 / 1K");
  expect(perMillionRate(1.5)).toBe("$1,500/M");
  expect(perMillionRate(0)).toBe("$0/M");
  expect(perThousand(1234.5)).toBe("$1,234.5 / 1K");
});

test("a tiny stated rate never reads as $0", () => {
  // Fraction digits would round these to zero, which is a claim nobody made.
  expect(perMillionRate(0.000001)).toBe("$0.001/M");
  expect(perMillionRate(0.0000012345)).toBe("$0.0012/M");
  expect(perMillionRate(0.00015)).toBe("$0.15/M");
  expect(perThousand(1e-12)).toBe("$0.000000000001 / 1K");
  expect(perThousand(0.15000000000000002)).toBe("$0.15 / 1K");
});

// ---- slice E: subscription calls, estimated tokens, and zero rates (I4) ----

test("a stated zero rate reads as an estimate of zero, never a bare $0.00", () => {
  // A local model the user rated at zero. `$0.00` alone reads as spend, and
  // "not reported" denies a price somebody did state.
  const bucket = { ...ZERO, calls: 2, modelled_calls: 2, modelled_usd: 0 };
  expect(bucketPrice(bucket)).toBe("≈ $0.00");
  expect(headlineIsEstimate(bucket)).toBe(true);
});

test("a zero-rated kind beside a subscription estimate headlines the estimate", () => {
  // A zero-rated local Fast next to a subscription Primary. Adding $0 to the
  // subscription figure reconciles to both columns, so there is no conflict.
  const bucket = { ...ZERO, calls: 5, priced_calls: 3, subscription_calls: 3,
                   estimated_usd: 0.4, modelled_calls: 2, modelled_usd: 0 };
  expect(bucketPrice(bucket)).toBe("≈ $0.40");
  expect(headlineIsEstimate(bucket)).toBe(true);
});

test("a zero estimate beside unpriced calls reads not reported, never ≈ $0.00", () => {
  // An unpriced call makes the total incomplete, and a zero beside it would
  // read as a complete one: ten calls nobody priced headlining as free.
  const modelled = { ...ZERO, calls: 11, modelled_calls: 1, modelled_usd: 0,
                     unpriced_calls: 10 };
  expect(bucketPrice(modelled)).toBe(UNPRICED);
  expect(headlineIsEstimate(modelled)).toBe(false);
  const subscription = { ...ZERO, calls: 3, priced_calls: 1, subscription_calls: 1,
                         estimated_usd: 0, unpriced_calls: 2 };
  expect(bucketPrice(subscription)).toBe(UNPRICED);
  // A non-zero estimate beside unpriced calls keeps its figure, as before;
  // `Footnotes` says it is a floor.
  expect(bucketPrice({ ...modelled, modelled_usd: 0.2 })).toBe("≈ $0.20");
});

test("two non-zero estimate kinds still read not reported", () => {
  const bucket = { ...ZERO, calls: 5, priced_calls: 3, subscription_calls: 3,
                   estimated_usd: 0.4, modelled_calls: 2, modelled_usd: 0.1 };
  expect(bucketPrice(bucket)).toBe(UNPRICED);
  expect(headlineIsEstimate(bucket)).toBe(false);
});

test("a zero-rated post beside a subscription one gets the subscription figure", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 1, calls: 2,
                             priced_calls: 1, subscription_calls: 1,
                             estimated_usd: 0.5, modelled_calls: 1,
                             modelled_usd: 0 }} />);
  expect(screen.getByText(/≈ \$0\.50/).textContent).not.toMatch(/\+/);
});

test("Footnotes names subscription calls in the modelled figure", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 3, modelled_calls: 3, modelled_usd: 0.3,
                         modelled_subscription_calls: 2 }} />);
  expect(container.textContent)
    .toMatch(/Of these, 2 calls ran on a subscription — not billed\./);
});

test("Footnotes names subscription calls among the unpriced", () => {
  // M13: a subscription call nothing could price is still not a bill.
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, unpriced_calls: 2,
                         unpriced_subscription_calls: 1 }} />);
  expect(container.textContent)
    .toMatch(/Of these, 1 call ran on a subscription — not billed\./);
});

test("Footnotes says when token counts were estimated", () => {
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, modelled_calls: 2, modelled_usd: 0.1,
                         estimated_token_calls: 2 }} />);
  expect(container.textContent)
    .toMatch(/2 calls had token counts estimated here — the provider reported none\./);
});

test("Footnotes reads a bucket without the new counts as none of them", () => {
  // `/usage` omits a count that is zero; absent is 0, never NaN.
  const { container } = render(
    <Footnotes bucket={{ ...ZERO, calls: 2, modelled_calls: 2, modelled_usd: 0.1,
                         unpriced_calls: 0 }} />);
  expect(container.textContent).not.toMatch(/subscription|estimated here|NaN/);
});

test("a subscription turn is tagged subscription — not billed", () => {
  const turn = { billing: "subscription", tokens_estimated: false,
                 cost_usd: null, cost_basis: "", modelled_usd: 0.01 };
  expect(turnTags(turn)).toEqual([SUBSCRIPTION_NOT_BILLED]);
  expect(turnTags({ ...turn, cost_usd: 0.5, cost_basis: EQUIVALENT }))
    .toEqual([SUBSCRIPTION_NOT_BILLED]);
  expect(SUBSCRIPTION_NOT_BILLED).toBe("subscription — not billed");
});

test("a billed turn on a subscription provider is not tagged", () => {
  // Ruling 4: a provider that reported a BILLED price said it charged.
  expect(turnTags({ billing: "subscription", cost_usd: 0.02, cost_basis: "billed" }))
    .toEqual([]);
  expect(turnTags({ cost_usd: null, cost_basis: "" })).toEqual([]);
});

test("a turn whose counts were estimated here says so", () => {
  expect(turnTags({ cost_usd: null, cost_basis: "", tokens_estimated: true }))
    .toEqual([TOKENS_ESTIMATED]);
  expect(TOKENS_ESTIMATED).toBe("tokens estimated");
});

test("a token total is marked when any of its counts were estimated", () => {
  expect(tokenTotal({ total_tokens: 1234, estimated_token_calls: 1 })).toBe("≈ 1,234 tok");
  expect(tokenTotal({ total_tokens: 1234, estimated_token_calls: 0 })).toBe("1,234 tok");
  expect(tokenTotal({ total_tokens: 1234 })).toBe("1,234 tok");
});

test("a post's title says how many calls ran on a subscription and were estimated", () => {
  render(<PostCost bucket={{ ...ZERO, post: 0, rerolls: 0, calls: 3,
                             modelled_calls: 2, modelled_usd: 0.2,
                             modelled_subscription_calls: 1, unpriced_calls: 1,
                             unpriced_subscription_calls: 1,
                             estimated_token_calls: 2 }} />);
  const title = screen.getByText(/≈ \$0\.20/).closest(".post-cost")?.getAttribute("title");
  expect(title).toContain("2 on a subscription — not billed");
  expect(title).toContain("2 with tokens estimated");
});

test("MoneyColumns says how much of the modelled column ran on a subscription, and when tokens were estimated", () => {
  const { container } = render(
    <MoneyColumns bucket={{
      priced_calls: 0, subscription_calls: 0, unpriced_calls: 0,
      cost_usd: 0, estimated_usd: 0, modelled_usd: 0.3, modelled_calls: 3,
      modelled_subscription_calls: 2, estimated_token_calls: 1,
    }} />);
  expect(container.textContent).toMatch(/Priced against your rates\. Arithmetic, not a receipt\./);
  expect(container.textContent).toMatch(/2 on a subscription — not billed\./);
  expect(container.textContent).toMatch(/Some token counts were estimated here\./);
});

test("MoneyColumns shows a zero-rated modelled column as an estimate of zero", () => {
  const { container } = render(
    <MoneyColumns bucket={{
      priced_calls: 0, subscription_calls: 0, unpriced_calls: 0,
      cost_usd: 0, estimated_usd: 0, modelled_usd: 0, modelled_calls: 2,
    }} />);
  const figures = [...container.querySelectorAll(".money-figure")].map((x) => x.textContent);
  expect(figures).toEqual(["not reported", "—", "≈ $0.00"]);
  expect(container.textContent).not.toMatch(/subscription — not billed|estimated here/);
});

test("a figure resting on locally counted tokens carries a note that says so", () => {
  // Every surface without `Footnotes` beside its figure spreads this onto it.
  expect(estimatedTokensTitle({ estimated_token_calls: 2 })).toMatch(/^2 calls with tokens estimated/);
  expect(estimatedTokensTitle({ estimated_token_calls: 1 })).toMatch(/^1 call with tokens estimated/);
  expect(estimatedTokensTitle({ estimated_token_calls: 0 })).toBeUndefined();
  expect(estimatedTokensTitle({})).toBeUndefined();
});
