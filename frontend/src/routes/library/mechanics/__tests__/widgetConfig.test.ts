import { describe, expect, it } from "vitest";
import { WIDGET_CONFIG, WIDGET_NAMES } from "../widgetConfig";

describe("WIDGET_CONFIG", () => {
  it("covers all 14 widgets", () => {
    expect(WIDGET_NAMES).toHaveLength(14);
    for (const name of WIDGET_NAMES) {
      expect(WIDGET_CONFIG[name]).toBeDefined();
    }
  });

  it("each config field has a key, label, and input kind", () => {
    for (const name of WIDGET_NAMES) {
      for (const field of WIDGET_CONFIG[name].fields) {
        expect(field.key).toBeTruthy();
        expect(field.label).toBeTruthy();
        expect(["text", "number", "boolean", "string-list", "json"]).toContain(field.input);
      }
    }
  });
});
