import type { SchemaProperty, SheetSchema, WidgetName } from "../../../sheets/types";
import { WIDGET_CONFIG } from "./widgetConfig";

export interface FieldModel {
  key: string;
  widget: WidgetName;
  required: boolean;
  /** Extra SchemaProperty keys (title, description, and widget-specific). */
  config: Record<string, unknown>;
}

const RESERVED = new Set(["type", "widget"]);

export function fieldsToSchema(fields: FieldModel[], title: string): SheetSchema {
  const properties: Record<string, SchemaProperty> = {};
  const required: string[] = [];
  for (const field of fields) {
    const def = WIDGET_CONFIG[field.widget];
    const prop: Record<string, unknown> = {
      type: def.schemaType,
      widget: field.widget,
    };
    for (const [k, v] of Object.entries(field.config)) {
      if (RESERVED.has(k)) continue;
      if (v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
      prop[k] = v;
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
    const { type: _type, widget, ...rest } = prop as Record<string, unknown>;
    void _type;
    return {
      key,
      widget: (widget as WidgetName) ?? "text",
      required: required.has(key),
      config: rest,
    };
  });
}
