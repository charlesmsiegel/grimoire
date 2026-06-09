import { afterEach, describe, expect, it, vi } from "vitest";

import { viewsApi } from "../views";

afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("viewsApi.getSheetSchema", () => {
  it("parses a sheet schema at the boundary and preserves widget annotations", async () => {
    mockFetch({
      type: "object",
      title: "Character",
      properties: {
        willpower: { widget: "dot-rating", type: "integer", min: 0, max: 10 },
        attributes: {
          widget: "nested-section",
          properties: { strength: { widget: "dot-rating", max: 5 } },
        },
      },
      required: ["willpower"],
    });

    const schema = await viewsApi.getSheetSchema("vamp", "character");

    expect(schema.title).toBe("Character");
    // Open-ended widget annotations survive the parse (looseObject passthrough).
    expect(schema.properties.willpower?.widget).toBe("dot-rating");
    expect(schema.properties.willpower?.max).toBe(10);
    // Nested sections are validated recursively.
    expect(schema.properties.attributes?.properties?.strength?.max).toBe(5);
    expect(schema.required).toEqual(["willpower"]);
  });

  it("accepts Draft 2020-12 boolean subschemas, coercing them to empty objects", async () => {
    // The backend metaschema check (Draft202012Validator) accepts `true`/`false`
    // anywhere a subschema is expected, so the boundary parser must too.
    mockFetch({
      type: "object",
      properties: {
        metadata: true,
        powers: { widget: "power-list", items: false },
        attributes: { widget: "nested-section", properties: { hidden: true } },
      },
    });

    const schema = await viewsApi.getSheetSchema("vamp", "character");

    expect(schema.properties.metadata).toEqual({});
    expect(schema.properties.powers?.items).toEqual({});
    expect(schema.properties.attributes?.properties?.hidden).toEqual({});
  });

  it("defaults missing properties to an empty record", async () => {
    mockFetch({ type: "object", title: "Empty" });

    const schema = await viewsApi.getSheetSchema("vamp", "character");

    expect(schema.properties).toEqual({});
  });

  it("rejects a malformed schema whose properties is not an object", async () => {
    mockFetch({ type: "object", properties: "nope" });

    await expect(viewsApi.getSheetSchema("vamp", "character")).rejects.toThrow();
  });
});
