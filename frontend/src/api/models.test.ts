vi.mock("./client", () => ({ api: { getConfig: vi.fn(), readConnection: vi.fn() } }));

import { api } from "./client";
import { configChanged } from "../appEvents";
import { getModels, invalidateModelsCache, tokensPerDollar, priceLabel, contextLabel } from "./models";

const ACTIVE = { id: "local", kind: "openai_compatible", name: "Local", model: "m" };

function serving(models: unknown[], active: unknown = ACTIVE) {
  (api.getConfig as any).mockResolvedValue({ active_connection: active });
  (api.readConnection as any).mockResolvedValue({ models });
}

beforeEach(() => {
  vi.clearAllMocks();
  invalidateModelsCache();
});

test("the catalog comes from the connection that is actually configured", async () => {
  // #149: this used to fetch OpenRouter's catalog from the browser whichever
  // provider was configured, so a reader on a local endpoint was picking from
  // a list of models their connection could not run.
  serving([{ id: "local-model", name: "Local", context: 8192, prompt: null, completion: null }]);

  const models = await getModels();

  expect(api.readConnection).toHaveBeenCalledWith("local");
  expect(models.map((m) => m.id)).toEqual(["local-model"]);
});

test("no active connection means no catalog, not a failed request", async () => {
  serving([], null);

  await expect(getModels()).resolves.toEqual([]);
  expect(api.readConnection).not.toHaveBeenCalled();
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
  expect(priceLabel({ id: "x", name: "X", context: 0, prompt: "0", completion: "0" })).toBe("Free");
});

test("priceLabel combines both sides", () => {
  expect(
    priceLabel({ id: "x", name: "X", context: 0, prompt: "0.00001", completion: "0.00005" }),
  ).toBe("100K / 20K tok/$");
});

test("priceLabel shows Free for a single free side", () => {
  expect(
    priceLabel({ id: "x", name: "X", context: 0, prompt: "0", completion: "0.00002" }),
  ).toBe("Free / 50K tok/$");
});

test("contextLabel formats compactly and omits when unknown", () => {
  expect(contextLabel(131072)).toBe("131K ctx");
  expect(contextLabel(1048576)).toBe("1M ctx");
  expect(contextLabel(8192)).toBe("8K ctx");
  expect(contextLabel(0)).toBe("");
});

test("getModels fetches once and serves later mounts from cache", async () => {
  serving([]);
  await getModels();
  await getModels();
  expect(api.readConnection).toHaveBeenCalledTimes(1);
});

test("getModels does not cache a failure", async () => {
  (api.getConfig as any).mockResolvedValue({ active_connection: ACTIVE });
  (api.readConnection as any)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ models: [] });

  await expect(getModels()).rejects.toThrow();
  await expect(getModels()).resolves.toEqual([]);
  expect(api.readConnection).toHaveBeenCalledTimes(2);
});

test("a connection change drops the cached catalog", async () => {
  // The cache is keyed on nothing, so switching the active connection would
  // otherwise leave one provider's models describing another's (#149). Every
  // mutator that could move it emits this signal already.
  serving([{ id: "first", name: "First", context: null, prompt: null, completion: null }]);
  expect((await getModels()).map((m) => m.id)).toEqual(["first"]);

  configChanged();
  serving([{ id: "second", name: "Second", context: null, prompt: null, completion: null }]);

  expect((await getModels()).map((m) => m.id)).toEqual(["second"]);
});
