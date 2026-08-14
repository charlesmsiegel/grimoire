import { spanOf, boundRank, inSpan } from "./timelineSpan";

const at = (...dates: string[]) => dates.map((date) => ({ date }));

describe("spanOf", () => {
  test("the moments are the distinct dates, in play order rather than sorted", () => {
    // Deliberately out of alphabetical AND chronological order: play order is
    // the authority (a flashback is out of date order on purpose), and native
    // dates carry a provider-supplied month *string* that sorts alphabetically.
    const { moments } = spanOf(at("3 Reaping", "28 Sowing", "3 Reaping", "4 Reaping"));
    expect(moments).toEqual(["3 Reaping", "28 Sowing", "4 Reaping"]);
  });

  test("a dated scene ranks at its moment", () => {
    expect(spanOf(at("28 Sowing", "3 Reaping", "4 Reaping")).ranks).toEqual([0, 1, 2]);
  });

  test("an undated scene carries the rank of the dated scene before it", () => {
    // The rule the whole filter turns on: an undated scene happened after that
    // date and before the next, so it belongs to that stretch.
    expect(spanOf(at("28 Sowing", "", "", "3 Reaping", "")).ranks).toEqual([0, 0, 0, 1, 1]);
  });

  test("a scene before every dated one ranks -1", () => {
    expect(spanOf(at("", "", "28 Sowing")).ranks).toEqual([-1, -1, 0]);
  });

  test("a campaign with no dates at all has no moments and ranks nothing", () => {
    const { moments, ranks } = spanOf(at("", "", ""));
    expect(moments).toEqual([]);
    expect(ranks).toEqual([-1, -1, -1]);
  });

  test("a repeated date does not open a second moment", () => {
    // Several scenes on one in-fiction day is ordinary, and each extra copy in
    // the FROM/TO lists would be a duplicate option that filters identically.
    const { moments, ranks } = spanOf(at("28 Sowing", "28 Sowing", "3 Reaping", "28 Sowing"));
    expect(moments).toEqual(["28 Sowing", "3 Reaping"]);
    // ...and a scene that revisits an earlier date ranks back at it, which is
    // what a flashback is.
    expect(ranks).toEqual([0, 0, 1, 0]);
  });

  test("nothing in, nothing out", () => {
    expect(spanOf([])).toEqual({ moments: [], ranks: [] });
  });
});

describe("boundRank", () => {
  const moments = ["28 Sowing", "3 Reaping", "4 Reaping"];

  test("no bound is no bound", () => {
    expect(boundRank(moments, null)).toBeNull();
    expect(boundRank(moments, "")).toBeNull();
  });

  test("a held date resolves to its position", () => {
    expect(boundRank(moments, "3 Reaping")).toBe(1);
  });

  test("a date the campaign no longer has drops the bound rather than moving it", () => {
    // Why the bounds are held as dates and not as indices. An index is a
    // position in a list derived from the data, so re-dating a scene underneath
    // it silently re-points the filter at a different moment; a date that has
    // gone can only honestly mean "no bound".
    expect(boundRank(moments, "12 Harvestmoon")).toBeNull();
  });
});

describe("inSpan", () => {
  test("unbounded on both sides admits everything, the -1 rank included", () => {
    expect(inSpan(-1, null, null)).toBe(true);
    expect(inSpan(7, null, null)).toBe(true);
  });

  test("the bounds are inclusive", () => {
    expect(inSpan(1, 1, 1)).toBe(true);
    expect(inSpan(0, 1, 2)).toBe(false);
    expect(inSpan(3, 1, 2)).toBe(false);
  });

  test("a scene before every dated one is in a span that ends at one, not one that starts there", () => {
    expect(inSpan(-1, null, 0)).toBe(true);
    expect(inSpan(-1, 0, null)).toBe(false);
  });

  test("a reversed span admits nothing rather than silently swapping the bounds", () => {
    // The reader asked for something empty; answering with the inverse would be
    // the screen deciding it knew better.
    expect(inSpan(1, 2, 0)).toBe(false);
  });
});
