import { fetchModels, tokensPerDollar, priceLabel } from "./models";

function mockFetch(data: unknown, ok = true) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok,
    json: async () => data,
  }) as unknown as typeof fetch;
}

test("fetchModels maps fields and sorts by id", async () => {
  mockFetch({
    data: [
      { id: "z/model", name: "Zed", pricing: { prompt: "0.00001", completion: "0.00002" } },
      { id: "a/model", name: "Aaa", pricing: { prompt: "0", completion: "0" } },
    ],
  });
  const models = await fetchModels();
  expect(models.map((m) => m.id)).toEqual(["a/model", "z/model"]);
  expect(models[1]).toEqual({
    id: "z/model",
    name: "Zed",
    prompt: "0.00001",
    completion: "0.00002",
  });
});

test("fetchModels throws on a non-OK response", async () => {
  mockFetch({}, false);
  await expect(fetchModels()).rejects.toThrow();
});

test("tokensPerDollar formats compactly", () => {
  expect(tokensPerDollar("0.00001")).toBe("100K"); // 1/0.00001 = 100000
  expect(tokensPerDollar("0.00005")).toBe("20K"); // 1/0.00005 = 20000
  expect(tokensPerDollar("0.0000005")).toBe("2M"); // 1/5e-7 = 2,000,000
  expect(tokensPerDollar("0.01")).toBe("100"); // 1/0.01 = 100
});

test("tokensPerDollar renders a free side as Free", () => {
  expect(tokensPerDollar("0")).toBe("Free");
  expect(tokensPerDollar("not-a-number")).toBe("Free");
});

test("priceLabel renders Free only when both sides are zero", () => {
  expect(priceLabel({ id: "x", name: "X", prompt: "0", completion: "0" })).toBe("Free");
});

test("priceLabel combines both sides", () => {
  expect(priceLabel({ id: "x", name: "X", prompt: "0.00001", completion: "0.00005" })).toBe(
    "100K / 20K tok/$",
  );
});

test("priceLabel shows Free for a single free side", () => {
  expect(priceLabel({ id: "x", name: "X", prompt: "0", completion: "0.00002" })).toBe(
    "Free / 50K tok/$",
  );
});
