import type { SchemaProperty, SheetSchema, WidgetName } from "../../../sheets/types";
import { WIDGET_CONFIG, type WidgetConfigDef } from "./widgetConfig";

export interface FieldModel {
  key: string;
  widget: WidgetName | string;
  required: boolean;
  /**
   * The JSON Schema `type` of the original property, preserved so a round-trip
   * through the visual editor never rewrites e.g. `integer` to `string`.
   */
  type?: string;
  /** Extra SchemaProperty keys (title, description, and widget-specific). */
  config: Record<string, unknown>;
}

const RESERVED = new Set(["type", "widget"]);
const FALLBACK_DEF: WidgetConfigDef = { schemaType: "string", fields: [] };

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Pick a sensible widget for a property that carries no `widget` annotation. */
function inferWidget(type: string | undefined): WidgetName {
  switch (type) {
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "array":
      return "keyword-list";
    case "object":
      return "nested-section";
    default:
      return "text";
  }
}

export function fieldsToSchema(fields: FieldModel[], title: string): SheetSchema {
  const properties: Record<string, SchemaProperty> = {};
  const required: string[] = [];
  for (const field of fields) {
    const def = WIDGET_CONFIG[field.widget as WidgetName] ?? FALLBACK_DEF;
    const prop: Record<string, unknown> = {
      type: field.type ?? def.schemaType,
      widget: field.widget,
    };
    for (const [k, v] of Object.entries(field.config)) {
      if (RESERVED.has(k)) continue;
      if (v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
      prop[k] = v;
    }
    // multi-select: a top-level `enum` constrains the whole array, not each
    // item. Nest under `items.enum` so values like ["fire"] validate.
    if (field.widget === "multi-select" && Array.isArray(prop.enum)) {
      prop.items = { type: "string", enum: prop.enum };
      delete prop.enum;
    }
    properties[field.key] = prop as SchemaProperty;
    if (field.required) required.push(field.key);
  }
  const schema: SheetSchema = { type: "object", title, properties };
  if (required.length > 0) schema.required = required;
  return schema;
}

export function schemaToFields(schema: SheetSchema): FieldModel[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([key, prop]) => {
    const { type, widget, ...rest } = prop as Record<string, unknown>;
    const resolvedWidget = (widget as WidgetName | undefined) ?? inferWidget(type as string);
    const config: Record<string, unknown> = { ...rest };
    // Surface multi-select options (stored under items.enum) as `enum` for the
    // string-list editor; drop the nested object from the config view.
    if (
      resolvedWidget === "multi-select" &&
      isRecord(config.items) &&
      Array.isArray(config.items.enum)
    ) {
      config.enum = config.items.enum;
      delete config.items;
    }
    return {
      key,
      widget: resolvedWidget,
      required: required.has(key),
      type: type as string | undefined,
      config,
    };
  });
}
