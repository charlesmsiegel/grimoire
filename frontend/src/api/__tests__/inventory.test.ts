import { describe, expect, it } from "vitest";

import { InventoryHoldingSchema } from "../inventory";

describe("InventoryHoldingSchema", () => {
  it("parses a holding row", () => {
    const row = {
      item_ref: "ring",
      item_name: "Ring",
      quantity: 2,
      fungible: false,
      equipped: false,
      holder_kind: "character",
      holder_id: "flo",
    };
    expect(() => InventoryHoldingSchema.parse(row)).not.toThrow();
  });

  it("rejects a row missing item_ref", () => {
    expect(() => InventoryHoldingSchema.parse({ item_name: "x", quantity: 1 })).toThrow();
  });
});
