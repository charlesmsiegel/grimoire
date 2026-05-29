import { afterEach, describe, expect, it, vi } from "vitest";
import { mechanicsApi } from "../mechanics";

afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("mechanicsApi writes", () => {
  it("createModule POSTs the manifest spec", async () => {
    const f = mockFetch({
      id: "x",
      report: { discovered: [], loaded: ["x"], failed: [], removed: [] },
    });
    const res = await mechanicsApi.createModule({
      id: "x",
      name: "X",
      version: "1.0.0",
      api_version: "1",
    });
    expect(res.report.loaded).toContain("x");
    expect(f).toHaveBeenCalledWith(
      "/api/library/mechanics",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("putSheetSchema PUTs to the sheet route", async () => {
    const f = mockFetch({ discovered: [], loaded: ["x"], failed: [], removed: [] });
    await mechanicsApi.putSheetSchema("x", "character", { type: "object", properties: {} });
    expect(f).toHaveBeenCalledWith(
      "/api/library/mechanics/x/sheets/character",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});
