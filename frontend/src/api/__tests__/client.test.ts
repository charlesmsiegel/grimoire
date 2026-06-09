import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { _resetSchemaWarningsForTests, api } from "../client";
import { clearLibraryCache } from "../library";
import { mechanicsApi } from "../library/mechanics";
import { viewsApi } from "../views";

beforeEach(() => {
  _resetSchemaWarningsForTests();
  clearLibraryCache();
});
afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown, status = 200) {
  // A fresh Response per call: a body can only be consumed once.
  return vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

function spyWarn() {
  return vi.spyOn(console, "warn").mockImplementation(() => {});
}

const Shape = z.object({ id: z.string(), count: z.number() });

describe("checkSchema (observational response validation, issue #599)", () => {
  it("returns the payload silently when the shape matches", async () => {
    const warn = spyWarn();
    mockFetch({ id: "a", count: 1, extra_field: "tolerated" });

    const data = await api.get<unknown>("/api/thing", { checkSchema: Shape });

    // Raw payload, not a parsed copy: unknown extras survive.
    expect(data).toEqual({ id: "a", count: 1, extra_field: "tolerated" });
    expect(warn).not.toHaveBeenCalled();
  });

  it("warns on drift but still resolves with the raw payload", async () => {
    const warn = spyWarn();
    mockFetch({ id: 42 });

    const data = await api.get<unknown>("/api/thing", { checkSchema: Shape });

    expect(data).toEqual({ id: 42 });
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("GET /api/thing");
  });

  it("warns once per endpoint, so poll loops don't spam the console", async () => {
    const warn = spyWarn();
    mockFetch({ id: 42 });

    await api.get<unknown>("/api/thing", { checkSchema: Shape });
    await api.get<unknown>("/api/thing", { checkSchema: Shape });
    expect(warn).toHaveBeenCalledTimes(1);

    await api.get<unknown>("/api/other-thing", { checkSchema: Shape });
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it("strict `schema` still throws and returns the transformed output", async () => {
    mockFetch({ id: "a", count: 1, extra_field: "stripped" });
    const parsed = await api.get<unknown>("/api/thing", { schema: Shape });
    expect(parsed).toEqual({ id: "a", count: 1 });

    mockFetch({ id: 42 });
    await expect(api.get<unknown>("/api/thing", { schema: Shape })).rejects.toThrow();
  });
});

describe("checkSchema wiring", () => {
  it("viewsApi.listWorlds flags a drifting world payload without failing the call", async () => {
    const warn = spyWarn();
    mockFetch([{ id: "wod-london" }]);

    const worlds = await viewsApi.listWorlds();

    expect(worlds).toEqual([{ id: "wod-london" }]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("/api/library/worlds");
  });

  it("library request layer forwards checkSchema (mechanicsApi.listInstalled)", async () => {
    const warn = spyWarn();
    mockFetch([{ manifest: { id: "vamp" } }]);

    const installed = await mechanicsApi.listInstalled();

    expect(installed).toEqual([{ manifest: { id: "vamp" } }]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("/api/mechanics/installed");
  });
});
