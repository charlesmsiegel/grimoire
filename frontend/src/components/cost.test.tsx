import { render, screen } from "@testing-library/react";
import { Footnotes, PostCost, about, bucketPrice, money, turnPrice } from "./cost";

/** The rule these three surfaces share: a price nobody reported is never
 *  rendered as zero, and a figure grimoire computed is never rendered as one it
 *  was charged. */

const ZERO = {
  calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
  cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
  modelled_usd: 0, priced_calls: 0, unpriced_calls: 0,
  subscription_calls: 0, modelled_calls: 0, duration_ms: 0,
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
  expect(bucketPrice({ ...ZERO, calls: 3, unpriced_calls: 3 })).toBe("unpriced");
});

test("one billed call among unpriced ones keeps the figure", () => {
  expect(bucketPrice({ ...ZERO, calls: 3, priced_calls: 1, unpriced_calls: 2,
                       cost_usd: 0.02 })).toBe("$0.02");
});

test("a bucket that was only ever modelled reads as an estimate", () => {
  expect(bucketPrice({ ...ZERO, calls: 2, modelled_calls: 2, modelled_usd: 0.25 }))
    .toBe("≈ $0.25");
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
  expect(turnPrice({ cost_usd: null, modelled_usd: 0.01 })).toBe("≈ $0.01");
  expect(turnPrice({ cost_usd: null, modelled_usd: null })).toBe("unpriced");
  expect(turnPrice({ cost_usd: 0.02, modelled_usd: null })).toBe("$0.02");
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

test("a post nothing could price shows no chip rather than an empty one", () => {
  // What is worth interrupting a transcript for is a cost. The absence of one
  // is the inspector's business, not a badge on every post in the scene.
  const { container } = render(
    <PostCost bucket={{ ...ZERO, post: 0, rerolls: 1, calls: 2,
                        unpriced_calls: 2 }} />);

  expect(container.textContent).toBe("");
});
