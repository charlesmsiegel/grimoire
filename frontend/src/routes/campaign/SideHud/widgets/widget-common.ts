/**
 * Shared utilities for HUD widget components.
 *
 * The HUD owns no state — each widget's ``data`` payload is whatever its
 * canonical owner endpoint returned. We don't have a schema per widget,
 * so the components pull the fields they know about defensively and fall
 * back to a generic JSON view for unknown shapes.
 */

import type { WidgetSnapshot } from "../../../../api/hud";

export type RecordShape = Record<string, unknown>;

export function asRecord(value: unknown): RecordShape | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as RecordShape;
  }
  return null;
}

export function asArray(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Pull a likely "primary value" out of a small object payload. */
export function primaryScalar(data: unknown): string | null {
  if (typeof data === "string" || typeof data === "number" || typeof data === "boolean") {
    return String(data);
  }
  const r = asRecord(data);
  if (!r) return null;
  const candidates = [
    "value",
    "label",
    "name",
    "title",
    "date",
    "time",
    "time_of_day",
    "conditions",
    "weather",
    "temperature",
    "location",
    "summary",
    "count",
    "text",
  ];
  for (const k of candidates) {
    const v = r[k];
    if (typeof v === "string" && v.length > 0) return v;
    if (typeof v === "number") return String(v);
    if (typeof v === "boolean") return String(v);
  }
  return null;
}

/** Human-readable label for the snapshot's status badge. */
export function statusLabel(snapshot: WidgetSnapshot): string {
  switch (snapshot.status) {
    case "ok":
      return snapshot.stale ? "Stale" : "";
    case "error":
      return "Error";
    case "timeout":
      return "Timeout";
    case "hidden":
      return "Hidden";
  }
}

export function errorMessage(snapshot: WidgetSnapshot): string {
  return snapshot.error ?? "Widget failed to load";
}
