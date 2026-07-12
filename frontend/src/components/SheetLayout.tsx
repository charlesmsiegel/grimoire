import type { CSSProperties, ReactNode } from "react";
import type { LayoutNode, ModuleDetail, ModuleField, ModuleTheme } from "../api/client";
import { DerivedBadge, FieldWidget, type WidgetMode } from "./SheetWidgets";

/** Full field-def set (group fields + own fields) for a sheet type — the one
 *  flattening helper; SheetEditor and SheetPanel import it from here. */
export function assembledDefs(module: ModuleDetail, t: string | null): ModuleField[] {
  if (!t) return [];
  const st = module.sheets.sheet_types[t];
  if (!st) return [];
  return st.groups.flatMap((g) => module.sheets.groups[g]?.fields ?? []).concat(st.fields);
}

/** The Phase-3 arrangement (groups in order → own fields) as a layout tree —
 *  one rendering path whether or not the module ships a layout. Places no
 *  derived; the trailing sections pick those up. */
export function defaultLayout(module: ModuleDetail, tid: string): LayoutNode {
  const st = module.sheets.sheet_types[tid];
  const children: LayoutNode[] = (st?.groups ?? [])
    .filter((g) => module.sheets.groups[g])
    .map((g) => ({ group: g, title: module.sheets.groups[g].label ?? g }));
  const own = (st?.fields ?? []).map((f) => f.key);
  if (own.length > 0) children.push({ fields: own, title: "Details" });
  return { column: children };
}

const FONT_VALUES: Record<string, string> = {
  display: "var(--fd)", body: "var(--fb)", mono: "var(--fm)",
  serif: "Georgia, 'Times New Roman', serif", sans: "system-ui, sans-serif",
};

/** Validated theme.json tokens → scoped --sheet-* inline vars. */
export function themeStyle(theme: ModuleTheme | undefined): CSSProperties {
  const s: Record<string, string> = {};
  const c = theme?.colors ?? {};
  if (c.bg) s["--sheet-bg"] = c.bg;
  if (c.ink) s["--sheet-ink"] = c.ink;
  if (c.muted) s["--sheet-muted"] = c.muted;
  if (c.accent) s["--sheet-accent"] = c.accent;
  if (c.rule) s["--sheet-rule"] = c.rule;
  const f = theme?.fonts ?? {};
  if (f.display && FONT_VALUES[f.display]) s["--sheet-fd"] = FONT_VALUES[f.display];
  if (f.body && FONT_VALUES[f.body]) s["--sheet-fb"] = FONT_VALUES[f.body];
  return s as CSSProperties;
}

type Ctx = {
  defs: Map<string, ModuleField>;
  groupFields: (gid: string) => string[];
  values: Record<string, unknown>;
  derived: Record<string, unknown>;
  mode: WidgetMode;
  onChange?: (key: string, v: unknown) => void;
  placedFields: Set<string>;
  placedDerived: Set<string>;
};

function fieldSet(keys: string[], grid: boolean | undefined, ctx: Ctx): ReactNode {
  const widgets = keys.map((k) => {
    ctx.placedFields.add(k);
    const def = ctx.defs.get(k);
    if (!def) return null; // backend-validated; defensive only
    return (
      <FieldWidget key={k} def={def} value={ctx.values[k]} mode={ctx.mode} grid={grid}
                   onChange={ctx.onChange ? (v) => ctx.onChange!(k, v) : undefined} />
    );
  });
  return grid ? <div className="stat-grid">{widgets}</div> : <>{widgets}</>;
}

function badges(names: string[], ctx: Ctx): ReactNode {
  return (
    <div className="derived-badges">
      {names.map((n) => {
        ctx.placedDerived.add(n);
        return <DerivedBadge key={n} name={n} value={ctx.derived[n]} />;
      })}
    </div>
  );
}

function renderNode(node: LayoutNode, ctx: Ctx, key: number): ReactNode {
  let inner: ReactNode = null;
  if (node.row) inner = <div className="sheet-cols">{node.row.map((c, i) => renderNode(c, ctx, i))}</div>;
  else if (node.column) inner = <div className="sheet-stack">{node.column.map((c, i) => renderNode(c, ctx, i))}</div>;
  else if (node.group) inner = fieldSet(ctx.groupFields(node.group), node.grid, ctx);
  else if (node.fields) inner = fieldSet(node.fields, node.grid, ctx);
  else if (node.derived) inner = badges(node.derived, ctx);
  return node.title ? (
    <section className="sheet-panel" key={key}><h4>{node.title}</h4>{inner}</section>
  ) : (
    <div className="sheet-slot" key={key}>{inner}</div>
  );
}

export default function SheetLayout({ module, sheetType, mode, values, derived, onChange }: {
  module: ModuleDetail; sheetType: string; mode: WidgetMode;
  values: Record<string, unknown>; derived: Record<string, unknown>;
  onChange?: (key: string, v: unknown) => void;
}) {
  const tree = module.layout?.sheet_types?.[sheetType] ?? defaultLayout(module, sheetType);
  const ctx: Ctx = {
    defs: new Map(assembledDefs(module, sheetType).map((f) => [f.key, f])),
    groupFields: (gid) => (module.sheets.groups[gid]?.fields ?? []).map((f) => f.key),
    values, derived, mode, onChange,
    placedFields: new Set(), placedDerived: new Set(),
  };
  const body = renderNode(tree, ctx, 0); // eager: populates placed* sets
  const restFields = [...ctx.defs.keys()].filter((k) => !ctx.placedFields.has(k));
  const restDerived = Object.keys(derived).filter((n) => !ctx.placedDerived.has(n));
  return (
    <div className="sheet-arranged">
      {body}
      {restFields.length > 0 && (
        <section className="sheet-panel"><h4>Other</h4>{fieldSet(restFields, undefined, ctx)}</section>
      )}
      {restDerived.length > 0 && (
        <section className="sheet-panel"><h4>Derived</h4>{badges(restDerived, ctx)}</section>
      )}
    </div>
  );
}
