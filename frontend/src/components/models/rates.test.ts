import { addedModel, rateWords, setRateHref } from "./rates";

const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };

test("a stated rate names its source and quotes per million", () => {
  expect(rateWords({ source: "provider", entry: BOTH }))
    .toBe("would be priced at $3/M in · $15/M out (provider)");
  expect(rateWords({ source: "table", entry: BOTH }))
    .toBe("would be priced at $3/M in · $15/M out (your rates)");
});

test("an embedding shows only what it is charged for", () => {
  expect(rateWords({ source: "table", entry: BOTH }, { promptOnly: true }))
    .toBe("would be priced at $3/M (your rates)");
});

test("no rate is words, never a zero; a real zero is a zero", () => {
  expect(rateWords({ source: "none" })).toBe("no rate: calls unpriced");
  expect(rateWords({ source: "table", entry: { prompt_usd_per_1k: 0, completion_usd_per_1k: 0 } }))
    .toBe("would be priced at $0/M in · $0/M out (your rates)");
});

test("a native decision says why it has no figure", () => {
  expect(rateWords({ source: "native" }))
    .toBe("native decisions: priced only if the provider reports a cost");
});

test("an entry missing a side reads as no rate, not as a zero", () => {
  expect(rateWords({ source: "table", entry: { prompt_usd_per_1k: 0.003 } }))
    .toBe("no rate: calls unpriced");
});

test("nothing resolved, nothing said", () => {
  expect(rateWords(null)).toBeNull();
});

test("Set rate carries any model id through the address intact", () => {
  for (const id of ["vendor/model", "odd#id?x=1", "with space", "vendor/m:free"]) {
    const href = setRateHref(id);
    expect(href.endsWith("#rates")).toBe(true);
    const search = href.slice(href.indexOf("?"), href.indexOf("#rates"));
    expect(addedModel(search)).toBe(id);
  }
});
