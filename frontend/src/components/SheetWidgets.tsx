import { useEffect, useState } from "react";
import { api, type EntityScope, type ModuleContentEntry, type ModuleDetail, type ModuleField } from "../api/client";
import { Field } from "./Field";

export type WidgetMode = "view" | "edit";

type WidgetProps = {
  def: ModuleField; value: unknown; mode: WidgetMode;
  grid?: boolean; onChange?: (v: unknown) => void;
  // ref-only context (RefView/RefEdit read from these; other widgets ignore them).
  scope?: EntityScope; module?: ModuleDetail; onOpenRef?: (kind: string, id: string) => void;
  // advancement-only: presence signals "this field is advancement-eligible in the
  // current (campaign, view-mode) context" -- callers gate before passing it.
  onAdvance?: () => void;
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
function Pips({ def, value, mode, shape, onChange, onAdvance }: WidgetProps & { shape: "dot" | "box" }) {
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
      {onAdvance && (
        <button className="subtle" aria-label={`Advance ${name}`} onClick={onAdvance}>+</button>
      )}
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

function NumberW({ def, value, mode, grid, onChange, onAdvance }: WidgetProps) {
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
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span><span>{n}</span>
      {onAdvance && (
        <button className="subtle" aria-label={`Advance ${name}`} onClick={onAdvance}>+</button>
      )}
    </div>
  );
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

/** Splits a `ref` field's address into kind/id, distinguishing the two Task-1 address forms:
 *  "<ref_kind>:<id>" (2 segments, an instantiated entity) vs. "<ref_kind>:module:<id>" (3
 *  segments, the literal "module" marker, uninstantiated module content). Entity ids never
 *  contain colons, so segment count alone disambiguates the two forms. */
function refKindAndId(ref: string): { kind: string; id: string; isModule: boolean } {
  const parts = ref.split(":");
  return parts.length === 3
    ? { kind: parts[0], id: parts[2], isModule: true }
    : { kind: parts[0], id: parts[1], isModule: false };
}

function RefView({ def, value, scope, module, onOpenRef }: WidgetProps) {
  const [preview, setPreview] = useState<{ kind: string; id: string; entry: ModuleContentEntry } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refs = Array.isArray(value) ? (value as string[]) : [];
  const name = label(def);

  async function instantiate() {
    if (!preview || !scope || !module) return;
    setError(null);
    try {
      await api.instantiateContent(scope, preview.kind, module.id, preview.id);
      setPreview(null);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <div className="chips">
        {refs.map((ref) => {
          const { kind, id, isModule } = refKindAndId(ref);
          return (
            <button key={ref} className="chip owner-chip"
                    onClick={() => isModule
                      ? module && api.readModuleContent(module.id, kind, id).then((entry) => {
                          setError(null);
                          setPreview({ kind, id, entry });
                        })
                      : onOpenRef?.(kind, id)}>
              {id}
            </button>
          );
        })}
        {refs.length === 0 && <span className="field-hint">none</span>}
      </div>
      {preview && (
        <div className="side-section">
          <h4>{preview.entry.name}</h4>
          <p>{preview.entry.body}</p>
          {error && <div className="banner">{error}</div>}
          <div className="form-actions">
            <button className="primary" onClick={instantiate}>Instantiate</button>
            <button className="subtle" onClick={() => setPreview(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

function RefEdit({ def, value, scope, module, onChange }: WidgetProps) {
  const [entities, setEntities] = useState<{ id: string; name: string }[]>([]);
  const refKind = def.ref_kind!;
  useEffect(() => {
    if (!scope) return;
    api.listEntities(scope, refKind as any).then(setEntities).catch(() => setEntities([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope?.kind, scope?.id, refKind]);
  const current = new Set(Array.isArray(value) ? (value as string[]) : []);
  const content = (module?.content ?? []).filter((c) => c.kind === refKind);

  function toggle(ref: string, checked: boolean) {
    const next = new Set(current);
    if (checked) next.add(ref); else next.delete(ref);
    onChange?.([...next]);
  }

  return (
    <Field label={label(def)}>
      <div className="side-section">
        <h4>In your world/campaign</h4>
        <div className="chips owner-picker">
          {entities.map((e) => {
            const ref = `${refKind}:${e.id}`;
            return (
              <label key={ref} className="owner-option">
                <input type="checkbox" aria-label={e.name} checked={current.has(ref)}
                       onChange={(ev) => toggle(ref, ev.target.checked)} />
                {e.name}
              </label>
            );
          })}
          {entities.length === 0 && <span className="field-hint">None yet.</span>}
        </div>
      </div>
      <div className="side-section">
        <h4>From {module?.manifest.name}</h4>
        <div className="chips owner-picker">
          {content.map((c) => {
            const ref = `${refKind}:module:${c.id}`;
            return (
              <label key={ref} className="owner-option">
                <input type="checkbox" aria-label={c.name} checked={current.has(ref)}
                       onChange={(ev) => toggle(ref, ev.target.checked)} />
                {c.name}
              </label>
            );
          })}
          {content.length === 0 && <span className="field-hint">None.</span>}
        </div>
      </div>
    </Field>
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
    case "ref":
      return props.mode === "edit" ? <RefEdit {...props} /> : <RefView {...props} />;
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
