/**
 * Sheet schema types — a JSON Schema subset extended with a `widget` annotation
 * that mechanics modules use to drive the Frontend widget library
 * (spec 14 §Sheet widget library).
 */

export type WidgetName =
  | "text"
  | "textarea"
  | "number"
  | "select"
  | "multi-select"
  | "boolean"
  | "dot-rating"
  | "dice-pool"
  | "health-track"
  | "power-list"
  | "grid-rating"
  | "slot-list"
  | "keyword-list"
  | "nested-section";

export interface SchemaProperty {
  widget?: WidgetName | string;
  title?: string;
  description?: string;
  type?: "string" | "number" | "integer" | "boolean" | "array" | "object";
  enum?: ReadonlyArray<string | number>;
  default?: unknown;
  // dot-rating
  min?: number;
  max?: number;
  halves?: boolean;
  // dice-pool
  currentField?: string;
  maxField?: string;
  // health-track
  rows?: number | ReadonlyArray<HealthRow>;
  severity_levels?: ReadonlyArray<SeverityLevel>;
  // power-list
  items?: SchemaProperty | { properties?: Record<string, SchemaProperty> };
  // grid-rating
  cols?: ReadonlyArray<string>;
  rowLabels?: ReadonlyArray<string>;
  // slot-list
  size?: number;
  // nested-section
  properties?: Record<string, SchemaProperty>;
}

export interface SheetSchema {
  type?: "object";
  title?: string;
  properties: Record<string, SchemaProperty>;
  required?: ReadonlyArray<string>;
}

export interface SeverityLevel {
  name: string;
  penalty?: number;
}

export interface HealthRow {
  label?: string;
  severity?: string;
}

export type SheetValue = Record<string, unknown>;

export interface WidgetProps<TValue = unknown> {
  property: SchemaProperty;
  name: string;
  value: TValue;
  onChange: (next: TValue) => void;
  readOnly?: boolean;
}

export interface PowerItem {
  name: string;
  rating?: number;
  description?: string;
  source?: string;
}

export interface DicePoolValue {
  [field: string]: number | undefined;
}
