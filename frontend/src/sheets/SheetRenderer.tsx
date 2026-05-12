import { useEffect, useMemo, useRef } from "react";
import { renderField } from "./renderField";
import { scopeCss } from "./scopeCss";
import type { SheetSchema, SheetValue } from "./types";

interface SheetRendererProps {
  /**
   * Mechanics module identifier used to scope the rendered sheet. The wrapper
   * becomes `.mechanics-<moduleId>` and any `themeCss` provided is prefixed
   * to that class so styles cannot leak between systems.
   */
  moduleId: string;
  schema: SheetSchema;
  value: SheetValue;
  onChange: (next: SheetValue) => void;
  /** Optional per-mechanics theme stylesheet (raw CSS). */
  themeCss?: string;
  readOnly?: boolean;
}

export function SheetRenderer({
  moduleId,
  schema,
  value,
  onChange,
  themeCss,
  readOnly,
}: SheetRendererProps) {
  const scopeClass = `mechanics-${moduleId}`;
  const styleRef = useRef<HTMLStyleElement | null>(null);
  const scoped = useMemo(
    () => (themeCss ? scopeCss(themeCss, scopeClass) : null),
    [themeCss, scopeClass],
  );

  useEffect(() => {
    if (!scoped) return;
    const tag = document.createElement("style");
    tag.dataset.mechanicsScope = scopeClass;
    tag.textContent = scoped;
    document.head.appendChild(tag);
    styleRef.current = tag;
    return () => {
      tag.remove();
      styleRef.current = null;
    };
  }, [scoped, scopeClass]);

  const properties = schema.properties ?? {};
  const update = (key: string, next: unknown) => {
    if (readOnly) return;
    onChange({ ...value, [key]: next });
  };

  return (
    <div className={`sheet ${scopeClass}`} data-module={moduleId}>
      {schema.title && <h2 className="sheet-title">{schema.title}</h2>}
      <div className="sheet-fields">
        {Object.entries(properties).map(([key, property]) =>
          renderField({
            name: key,
            property,
            value: value[key],
            onChange: (next) => update(key, next),
            readOnly,
          }),
        )}
      </div>
    </div>
  );
}
