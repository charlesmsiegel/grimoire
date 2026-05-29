import type { WidgetName } from "../../../sheets/types";

export type ConfigInput = "text" | "number" | "boolean" | "string-list" | "json";

export interface ConfigFieldDef {
  /** Key written into the SchemaProperty. */
  key: string;
  label: string;
  input: ConfigInput;
  help?: string;
}

export interface WidgetConfigDef {
  /** JSON Schema `type` implied by this widget (used as the property default). */
  schemaType: "string" | "number" | "integer" | "boolean" | "array" | "object";
  /** Extra SchemaProperty keys this widget understands, beyond title/description. */
  fields: ConfigFieldDef[];
}

export const WIDGET_NAMES: WidgetName[] = [
  "text",
  "textarea",
  "number",
  "select",
  "multi-select",
  "boolean",
  "dot-rating",
  "dice-pool",
  "health-track",
  "power-list",
  "grid-rating",
  "slot-list",
  "keyword-list",
  "nested-section",
];

export const WIDGET_CONFIG: Record<WidgetName, WidgetConfigDef> = {
  text: { schemaType: "string", fields: [] },
  textarea: { schemaType: "string", fields: [] },
  number: {
    schemaType: "number",
    fields: [
      { key: "min", label: "Minimum", input: "number" },
      { key: "max", label: "Maximum", input: "number" },
    ],
  },
  select: {
    schemaType: "string",
    fields: [{ key: "enum", label: "Options", input: "string-list" }],
  },
  "multi-select": {
    schemaType: "array",
    fields: [{ key: "enum", label: "Options", input: "string-list" }],
  },
  boolean: { schemaType: "boolean", fields: [] },
  "dot-rating": {
    schemaType: "integer",
    fields: [
      { key: "min", label: "Min dots", input: "number" },
      { key: "max", label: "Max dots", input: "number" },
      { key: "halves", label: "Allow half dots", input: "boolean" },
    ],
  },
  "dice-pool": {
    schemaType: "object",
    fields: [
      { key: "currentField", label: "Current field", input: "text" },
      { key: "maxField", label: "Max field", input: "text" },
    ],
  },
  "health-track": {
    schemaType: "object",
    fields: [
      { key: "rows", label: "Rows (number or JSON)", input: "json" },
      { key: "severity_levels", label: "Severity levels (JSON)", input: "json" },
    ],
  },
  "power-list": {
    schemaType: "array",
    fields: [{ key: "items", label: "Item schema (JSON)", input: "json" }],
  },
  "grid-rating": {
    schemaType: "object",
    fields: [
      { key: "cols", label: "Columns", input: "string-list" },
      { key: "rowLabels", label: "Row labels", input: "string-list" },
    ],
  },
  "slot-list": {
    schemaType: "array",
    fields: [{ key: "size", label: "Slots", input: "number" }],
  },
  "keyword-list": { schemaType: "array", fields: [] },
  "nested-section": {
    schemaType: "object",
    fields: [{ key: "properties", label: "Nested properties (JSON)", input: "json" }],
  },
};
