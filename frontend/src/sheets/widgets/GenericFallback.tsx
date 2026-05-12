import type { WidgetProps } from "../types";

/**
 * Catch-all renderer used when a schema property names a widget the Frontend
 * doesn't recognize (forward-compat) or doesn't name one at all. Renders the
 * value as JSON in a textarea so the data is at least editable and visible.
 */
export function GenericFallbackWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<unknown>) {
  const unknownWidget = property.widget && !isBuiltin(property.widget);
  const serialized = (() => {
    if (value === undefined) return "";
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  })();

  return (
    <div className="sheet-widget sheet-generic-fallback">
      {unknownWidget && (
        <p className="sheet-fallback-warning" role="alert">
          Unknown widget <code>{property.widget}</code> — using generic editor.
        </p>
      )}
      <textarea
        id={`sheet-${name}`}
        rows={4}
        value={serialized}
        readOnly={readOnly}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            onChange(null);
            return;
          }
          try {
            onChange(JSON.parse(raw));
          } catch {
            onChange(raw);
          }
        }}
      />
    </div>
  );
}

const BUILTINS = new Set([
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
]);

function isBuiltin(widget: string): boolean {
  return BUILTINS.has(widget);
}
