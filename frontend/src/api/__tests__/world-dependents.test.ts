import { afterEach, describe, expect, it, vi } from "vitest";

import { clearLibraryCache, fetchWorldDependents } from "../library";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearLibraryCache();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("fetchWorldDependents", () => {
  it("returns campaigns whose composition references the given world", async () => {
    const responses: Record<string, unknown> = {
      "/api/campaigns": [
        { id: "c1", name: "First" },
        { id: "c2", name: "Second" },
        { id: "c3", name: "Third" },
      ],
      "/api/campaigns/c1/composition": { worlds: [{ world_id: "sakura-high", priority: 1 }] },
      "/api/campaigns/c2/composition": { worlds: [{ world_id: "other", priority: 1 }] },
      "/api/campaigns/c3/composition": { worlds: [{ world_id: "sakura-high", priority: 2 }] },
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      return jsonResponse(responses[url]);
    }) as unknown as typeof fetch;

    const result = await fetchWorldDependents("sakura-high");
    expect(result.map((c) => c.id).sort()).toEqual(["c1", "c3"]);
  });

  it("skips campaigns whose composition lookup fails", async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/campaigns") {
        return jsonResponse([{ id: "c1", name: "First" }]);
      }
      return new Response("nope", { status: 500 });
    }) as unknown as typeof fetch;
    const result = await fetchWorldDependents("sakura-high");
    expect(result).toEqual([]);
  });
});
