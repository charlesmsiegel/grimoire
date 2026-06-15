import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SchemaField } from "../SchemaField";
import type { JsonSchema } from "../schemaForm";

function renderField(schema: JsonSchema, value: unknown, onChange = vi.fn()) {
  render(
    <SchemaField
      pluginId="p"
      name="field"
      schema={schema}
      required={false}
      value={value}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("SchemaField object/array editors", () => {
  it("renders a string→string map (extra_headers) with MapEditor, not raw JSON", () => {
    const onChange = renderField(
      { type: "object", title: "Extra headers", additionalProperties: { type: "string" } },
      { "X-Title": "Grimoire" },
    );
    expect(screen.getByText("X-Title")).toBeInTheDocument();
    const input = screen.getByDisplayValue("Grimoire");
    fireEvent.change(input, { target: { value: "G2" } });
    expect(onChange).toHaveBeenCalledWith({ "X-Title": "G2" });
  });

  it("renders a typed object's declared properties as sub-fields", () => {
    const schema: JsonSchema = {
      type: "object",
      title: "Provider routing",
      additionalProperties: true,
      properties: {
        sort: { type: "string", title: "Sort", enum: ["price", "throughput", "latency"] },
        allow_fallbacks: { type: "boolean", title: "Allow fallbacks" },
      },
    };
    const onChange = renderField(schema, {});
    const sort = screen.getByRole("combobox");
    fireEvent.change(sort, { target: { value: "price" } });
    expect(onChange).toHaveBeenCalledWith({ sort: "price" });
    expect(screen.getByRole("checkbox")).toBeInTheDocument(); // allow_fallbacks
  });

  it("renders an object-valued map (provider_overrides) as per-key rows", () => {
    const schema: JsonSchema = {
      type: "object",
      title: "Per-model provider routing",
      additionalProperties: { type: "object", additionalProperties: true },
    };
    renderField(schema, { "deepseek/deepseek-v4-pro": { max_price: { prompt: 0.4 } } });
    // The model slug appears as a row key (and as the nested value's label).
    expect(screen.getAllByText("deepseek/deepseek-v4-pro").length).toBeGreaterThan(0);
  });

  it("renders a string array with StringListEditor", () => {
    const onChange = renderField({ type: "array", title: "Order", items: { type: "string" } }, [
      "anthropic",
      "openai",
    ]);
    expect(screen.getByDisplayValue("anthropic")).toBeInTheDocument();
    expect(screen.getByDisplayValue("openai")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
