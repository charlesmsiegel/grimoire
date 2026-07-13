import type { ModuleField } from "../api/client";
import { Field } from "./Field";

export type WidgetMode = "view" | "edit";

type WidgetProps = {
  def: ModuleField; value: unknown; mode: WidgetMode;
  grid?: boolean; onChange?: (v: unknown) => void;
};

export function isResource(v: unknown): v is { current: number; max: number } {
  return !!v && typeof v === "object" && "current" in (v as object) && "max" in (v as object);
}

const label = (f: ModuleField) => f.label ?? f.key;

// Pips are for small rated tracks (dots/boxes rendered one-per-point); beyond
// this cap a numeric control is the only usable rendering anyway.
const PIP_CAP = 40;

/** dots + track share click-to-set: pip n sets value n; clicking the pip at
 *  the current value decrements to n-1 so 0 stays reachable. */
function Pips({ def, value, mode, shape, onChange }: WidgetProps & { shape: "dot" | "box" }) {
  const max = typeof def.max === "number" ? def.max : 5;
  const n = typeof value === "number" ? value : 0;
  const name = label(def);
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span className="pips">
        {Array.from({ length: max }, (_, i) =>
          mode === "edit" ? (
            <button key={i} type="button" className={`pip ${shape}${i < n ? " on" : ""}`}
                    aria-label={`${name} ${i + 1}`} aria-pressed={i < n}
                    onClick={() => onChange?.(i + 1 === n ? i : i + 1)} />
          ) : (
            <span key={i} className={`pip ${shape}${i < n ? " on" : ""}`} />
          ))}
      </span>
    </div>
  );
}

function Resource({ def, value, mode, onChange }: WidgetProps) {
  const rv = isResource(value) ? value : { current: 0, max: def.max ?? 0 };
  const pct = rv.max > 0 ? Math.max(0, Math.min(1, rv.current / rv.max)) * 100 : 0;
  const name = label(def);
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span className="resource">
        <span className="resource-bar"><span className="resource-fill" style={{ width: `${pct}%` }} /></span>
        {mode === "edit" ? (
          <span className="resource-inputs">
            <input type="number" aria-label={`${name} current`} min={0} value={rv.current}
                   onChange={(e) => onChange?.({ ...rv, current: Number(e.target.value) })} />
            <span>/</span>
            <input type="number" aria-label={`${name} max`} min={0} value={rv.max}
                   onChange={(e) => onChange?.({ ...rv, max: Number(e.target.value) })} />
          </span>
        ) : (
          <span className="resource-text">{rv.current} / {rv.max}</span>
        )}
      </span>
    </div>
  );
}

function NumberW({ def, value, mode, grid, onChange }: WidgetProps) {
  const n = typeof value === "number" ? value : 0;
  const name = label(def);
  if (grid) {
    return (
      <div className="stat-cell">
        {mode === "edit" ? (
          <input type="number" aria-label={name} min={def.min ?? 0} max={def.max} value={n}
                 onChange={(e) => onChange?.(Number(e.target.value))} />
        ) : (
          <span className="stat-value">{n}</span>
        )}
        <span className="stat-label">{name}</span>
      </div>
    );
  }
  if (mode === "edit") {
    return (
      <Field label={name}>
        <input type="number" min={def.min ?? 0} max={def.max} value={n}
               onChange={(e) => onChange?.(Number(e.target.value))} />
      </Field>
    );
  }
  return <div className="widget-row"><span className="widget-label">{name}</span><span>{n}</span></div>;
}

function TextW({ def, value, mode, onChange }: WidgetProps) {
  const name = label(def);
  if (mode === "edit") {
    return (
      <Field label={name}>
        <input type="text" value={typeof value === "string" ? value : ""}
               onChange={(e) => onChange?.(e.target.value)} />
      </Field>
    );
  }
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span>{typeof value === "string" && value ? value : "—"}</span>
    </div>
  );
}

/** Edit mode renders and emits the raw draft string; joining stored arrays /
 *  splitting back happens at SheetEditor's commit points, exactly as before. */
function ListW({ def, value, mode, onChange }: WidgetProps) {
  const name = label(def);
  if (mode === "edit") {
    const s = typeof value === "string" ? value
      : Array.isArray(value) ? (value as string[]).join("\n") : "";
    return (
      <Field label={name} hint="one per line">
        <textarea rows={3} value={s} onChange={(e) => onChange?.(e.target.value)} />
      </Field>
    );
  }
  const items = Array.isArray(value) ? (value as string[]) : [];
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      {items.length > 0
        ? <ul className="widget-list">{items.map((v, i) => <li key={i}>{v}</li>)}</ul>
        : <span>—</span>}
    </div>
  );
}

export function DerivedBadge({ name, value }: { name: string; value: unknown }) {
  return (
    <span className="derived-badge">
      <span className="derived-name">{name}</span>
      <strong className="derived-value">{value === undefined ? "—" : String(value)}</strong>
    </span>
  );
}

export function FieldWidget(props: WidgetProps) {
  switch (props.def.type) {
    case "dots":
    case "track": {
      const oversized = typeof props.def.max === "number" && props.def.max > PIP_CAP;
      if (oversized) return <NumberW {...props} />;
      return <Pips {...props} shape={props.def.type === "dots" ? "dot" : "box"} />;
    }
    case "resource": return <Resource {...props} />;
    case "text": return <TextW {...props} />;
    case "list": return <ListW {...props} />;
    default: return <NumberW {...props} />;
  }
}
