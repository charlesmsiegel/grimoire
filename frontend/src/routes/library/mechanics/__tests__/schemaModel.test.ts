import { describe, expect, it } from "vitest";
import { fieldsToSchema, schemaToFields, type FieldModel } from "../schemaModel";
import type { SheetSchema } from "../../../../sheets/types";

describe("schemaModel", () => {
  it("serializes a field list to a SheetSchema", () => {
    const fields: FieldModel[] = [
      { key: "name", widget: "text", required: true, config: { title: "Name" } },
      { key: "str", widget: "dot-rating", required: false, config: { min: 1, max: 5 } },
    ];
    const schema = fieldsToSchema(fields, "Character");
    expect(schema).toEqual({
      type: "object",
      title: "Character",
      properties: {
        name: { type: "string", widget: "text", title: "Name" },
        str: { type: "integer", widget: "dot-rating", min: 1, max: 5 },
      },
      required: ["name"],
    });
  });

  it("round-trips schema -> fields -> schema", () => {
    const schema = fieldsToSchema(
      [{ key: "hp", widget: "number", required: false, config: { max: 10 } }],
      "S",
    );
    const back = fieldsToSchema(schemaToFields(schema), "S");
    expect(back).toEqual(schema);
  });

  it("preserves the original type of a widget-less property", () => {
    const original: SheetSchema = {
      type: "object",
      title: "S",
      properties: { age: { type: "integer" } },
    };
    const fields = schemaToFields(original);
    expect(fields[0]!.type).toBe("integer");
    // Re-serializing must NOT downgrade integer -> string.
    const back = fieldsToSchema(fields, "S");
    expect((back.properties.age as { type: string }).type).toBe("integer");
  });

  it("emits items.enum for multi-select and round-trips it", () => {
    const schema = fieldsToSchema(
      [
        {
          key: "elements",
          widget: "multi-select",
          required: false,
          config: { enum: ["fire", "ice"] },
        },
      ],
      "S",
    );
    expect(schema.properties.elements).toMatchObject({
      type: "array",
      widget: "multi-select",
      items: { type: "string", enum: ["fire", "ice"] },
    });
    expect((schema.properties.elements as { enum?: unknown }).enum).toBeUndefined();
    const back = fieldsToSchema(schemaToFields(schema), "S");
    expect(back).toEqual(schema);
  });
});
