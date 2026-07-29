import { useEffect, useMemo, useState } from "react";
import { api, type ModuleDetail, type ModuleTheme } from "../api/client";
import SheetLayout, { themeStyle } from "./SheetLayout";
import { ErrorList, useModuleDryRun, type SaveFn } from "./moduleEditShared";

const PREVIEW_SCOPE = { kind: "world", id: "preview" } as const;

const FONT_OPTIONS = ["display", "body", "mono", "serif", "sans"] as const;
const DOTS_OPTIONS = ["circle", "square", "diamond"] as const;
const CORNERS_OPTIONS = ["sharp", "rounded"] as const;

/** Client-side counterpart to the server's fragment splice (Phase 6): resolves
 *  `{ use: "fragmentId" }` nodes against `fragments` so a live-edited layout
 *  draft can be previewed without a server round trip. Cycle-guarded (a
 *  fragment can't reference itself, directly or transitively) and depth-capped
 *  so a malformed draft degrades to an empty node instead of hanging the tab. */
function splice(node: any, fragments: Record<string, any>, seen: string[] = []): any {
  if (!node || typeof node !== "object") return node;
  if (typeof node.use === "string") {
    if (seen.includes(node.use) || seen.length > 32) return {};
    return splice(fragments[node.use] ?? {}, fragments, [...seen, node.use]);
  }
  const out: any = { ...node };
  for (const arr of ["row", "column"] as const) {
    if (Array.isArray(out[arr])) out[arr] = out[arr].map((k: any) => splice(k, fragments, seen));
  }
  return out;
}

/** A representative sheet for the given type, built from schema defaults --
 *  the preview has no real sheet to render, so it fabricates one the same way
 *  `sheets.default_fields` would server-side. */
function sampleSheet(pack: ModuleDetail, tid: string) {
  const st = pack.sheets.sheet_types[tid];
  const fields: Record<string, unknown> = {};
  const defs = [...(st?.groups ?? []).flatMap((g) => pack.sheets.groups[g]?.fields ?? []),
                ...(st?.fields ?? [])];
  for (const f of defs) {
    if (["number", "dots", "track"].includes(f.type)) fields[f.key] = f.default ?? 0;
    else if (f.type === "resource") fields[f.key] = { current: f.max ?? 0, max: f.max ?? 0 };
    else if (f.type === "list" || f.type === "ref") fields[f.key] = [];
    else fields[f.key] = "";
  }
  return { sheet_type: tid, fields, derived: {} as Record<string, unknown> };
}

function SheetTypeSelect({ pack, value, onChange }: {
  pack: ModuleDetail; value: string; onChange: (t: string) => void;
}) {
  return (
    <label>Sheet type
      <select aria-label="Sheet type" value={value} onChange={(e) => onChange(e.target.value)}>
        {Object.entries(pack.sheets.sheet_types).map(([tid, st]) => (
          <option key={tid} value={tid}>{st.label ?? tid}</option>
        ))}
      </select>
    </label>
  );
}

function Preview({ pack, sheetType, tree, theme }: {
  pack: ModuleDetail; sheetType: string; tree?: unknown; theme?: ModuleTheme;
}) {
  const sample = sampleSheet(pack, sheetType);
  const previewModule: ModuleDetail = tree === undefined
    ? pack
    : { ...pack, layout: { sheet_types: { [sheetType]: tree as any } } };
  return (
    <div className="sheet-arranged-preview" style={themeStyle(theme)}
         data-dots={theme?.dots} data-corners={theme?.corners}>
      {sheetType ? (
        <SheetLayout module={previewModule} sheetType={sheetType} mode="view"
                     values={sample.fields} derived={sample.derived} scope={PREVIEW_SCOPE} />
      ) : (
        <div className="field-hint">No sheet types defined yet.</div>
      )}
    </div>
  );
}

export function LayoutSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const [text, setText] = useState(() => JSON.stringify(pack.layout_source ?? {}, null, 2));
  const [sheetType, setSheetType] = useState(() => Object.keys(pack.sheets.sheet_types)[0] ?? "");
  const [lastGood, setLastGood] = useState<any>(pack.layout_source ?? {});

  const parsed = useMemo<any>(() => {
    try {
      const v = JSON.parse(text);
      return v && typeof v === "object" && !Array.isArray(v) ? v : undefined;
    } catch {
      return undefined;
    }
  }, [text]);

  useEffect(() => {
    if (parsed !== undefined) setLastGood(parsed);
  }, [parsed]);

  const save: SaveFn = (dryRun) => {
    if (parsed === undefined) return Promise.resolve({ ok: false, errors: [], display_errors: [] });
    return api.putModuleLayout(pack.id, parsed, dryRun);
  };
  const dr = useModuleDryRun(save, [text]);

  const active = parsed !== undefined ? parsed : lastGood;
  const fragments = (active && typeof active === "object" ? active.fragments : undefined) ?? {};
  const tree = splice((active?.sheet_types ?? {})[sheetType] ?? {}, fragments);

  return (
    <div className="detail-main">
      <ErrorList result={dr.result} />
      {parsed === undefined && <div className="field-hint">invalid JSON</div>}
      <label>Layout JSON
        <textarea aria-label="Layout JSON" rows={16} value={text}
                  onChange={(e) => setText(e.target.value)} />
      </label>
      <SheetTypeSelect pack={pack} value={sheetType} onChange={setSheetType} />
      <Preview pack={pack} sheetType={sheetType} tree={tree} theme={pack.theme} />
      <div className="form-actions">
        <button className="primary" disabled={parsed === undefined || dr.saving}
                onClick={() => void dr.requestSave(() => void reload())}>Save</button>
      </div>
    </div>
  );
}

type ThemeForm = {
  useCustomColors: boolean; bg: string; ink: string;
  accent: string; muted: string; rule: string;
  display: string; body: string; dots: string; corners: string;
};

function themeFormOf(theme: ModuleTheme | undefined): ThemeForm {
  const c = theme?.colors ?? {};
  return {
    useCustomColors: !!(c.bg || c.ink),
    bg: c.bg ?? "#ffffff", ink: c.ink ?? "#111111",
    accent: c.accent ?? "", muted: c.muted ?? "", rule: c.rule ?? "",
    display: theme?.fonts?.display ?? "display", body: theme?.fonts?.body ?? "body",
    dots: theme?.dots ?? "circle", corners: theme?.corners ?? "sharp",
  };
}

function themeTokenOf(form: ThemeForm): ModuleTheme {
  const colors: ModuleTheme["colors"] = {};
  if (form.useCustomColors) { colors!.bg = form.bg; colors!.ink = form.ink; }
  if (form.accent) colors!.accent = form.accent;
  if (form.muted) colors!.muted = form.muted;
  if (form.rule) colors!.rule = form.rule;
  return {
    colors,
    fonts: { display: form.display as any, body: form.body as any },
    dots: form.dots, corners: form.corners,
  };
}

export function ThemeSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const [form, setForm] = useState<ThemeForm>(() => themeFormOf(pack.theme));
  const [sheetType, setSheetType] = useState(() => Object.keys(pack.sheets.sheet_types)[0] ?? "");

  const save: SaveFn = (dryRun) => api.putModuleTheme(pack.id, themeTokenOf(form), dryRun);
  const dr = useModuleDryRun(save, [form]);

  const toggleCustomColors = () => {
    setForm((f) => f.useCustomColors
      ? { ...f, useCustomColors: false, bg: "", ink: "" }
      : { ...f, useCustomColors: true, bg: f.bg || "#ffffff", ink: f.ink || "#111111" });
  };

  return (
    <div className="detail-main">
      <ErrorList result={dr.result} />
      <label>Use custom colors
        <input type="checkbox" checked={form.useCustomColors} onChange={toggleCustomColors} />
      </label>
      {form.useCustomColors && (
        <div className="chips">
          <label>Background
            <input type="color" aria-label="Background" value={form.bg || "#ffffff"}
                   onChange={(e) => setForm({ ...form, bg: e.target.value })} />
          </label>
          <label>Ink
            <input type="color" aria-label="Ink" value={form.ink || "#111111"}
                   onChange={(e) => setForm({ ...form, ink: e.target.value })} />
          </label>
        </div>
      )}
      <div className="chips">
        <label>Accent
          <input type="color" aria-label="Accent" value={form.accent || "#000000"}
                 onChange={(e) => setForm({ ...form, accent: e.target.value })} />
        </label>
        <label>Muted
          <input type="color" aria-label="Muted" value={form.muted || "#000000"}
                 onChange={(e) => setForm({ ...form, muted: e.target.value })} />
        </label>
        <label>Rule
          <input type="color" aria-label="Rule" value={form.rule || "#000000"}
                 onChange={(e) => setForm({ ...form, rule: e.target.value })} />
        </label>
      </div>
      <div className="chips">
        <label>Display font
          <select aria-label="Display font" value={form.display}
                  onChange={(e) => setForm({ ...form, display: e.target.value })}>
            {FONT_OPTIONS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <label>Body font
          <select aria-label="Body font" value={form.body}
                  onChange={(e) => setForm({ ...form, body: e.target.value })}>
            {FONT_OPTIONS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <label>Dots
          <select aria-label="Dots" value={form.dots}
                  onChange={(e) => setForm({ ...form, dots: e.target.value })}>
            {DOTS_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label>Corners
          <select aria-label="Corners" value={form.corners}
                  onChange={(e) => setForm({ ...form, corners: e.target.value })}>
            {CORNERS_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      </div>
      <SheetTypeSelect pack={pack} value={sheetType} onChange={setSheetType} />
      <Preview pack={pack} sheetType={sheetType} theme={themeTokenOf(form)} />
      <div className="form-actions">
        <button className="primary" disabled={dr.saving}
                onClick={() => void dr.requestSave(() => void reload())}>Save</button>
      </div>
    </div>
  );
}
