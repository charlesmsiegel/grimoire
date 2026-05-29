import { describe, expect, it } from "vitest";
import { fieldsToSchema, schemaToFields, type FieldModel } from "../schemaModel";

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
});
