import { useState } from "react";

import { ApiError } from "../../../api/library";
import {
  mechanicsApi,
  type ManifestSpec,
  type ModuleManifest,
  type RescanReport,
} from "../../../api/library/mechanics";
import type { SheetSchema } from "../../../sheets/types";
import { ManifestForm } from "./ManifestForm";
import { SchemaBuilder } from "./SchemaBuilder";

type Tab = "manifest" | "sheets" | "content" | "theme";

interface Props {
  manifest: ModuleManifest;
  themeCss: string | null;
  onSaved: (report: RescanReport) => void;
  /** Current on-disk sheet schemas, keyed by sheet kind. */
  sheetSchemas?: Record<string, Record<string, unknown>>;
  /** Current on-disk content schemas, keyed by content kind. */
  contentSchemas?: Record<string, Record<string, unknown>>;
}

function emptySchema(title: string): SheetSchema {
  return { type: "object", title, properties: {} };
}

function initialSchema(existing: Record<string, unknown> | undefined, title: string): SheetSchema {
  if (existing && Object.keys(existing).length > 0) {
    return existing as unknown as SheetSchema;
  }
  return emptySchema(title);
}

function errorMessages(err: unknown): string[] {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string" && err.detail) {
      try {
        const parsed = JSON.parse(err.detail) as { detail?: unknown };
        const d = parsed.detail;
        if (Array.isArray(d)) return d.map(String);
        if (d != null) return [String(d)];
      } catch {
        return [err.detail];
      }
    }
    return [err.message];
  }
  return [String(err)];
}

export function MechanicsEditor({
  manifest,
  themeCss,
  onSaved,
  sheetSchemas,
  contentSchemas,
}: Props) {
  const [tab, setTab] = useState<Tab>("manifest");
  const [spec, setSpec] = useState<ManifestSpec>(() => ({ ...manifest }));
  const [error, setError] = useState<string[] | null>(null);

  async function run(fn: () => Promise<RescanReport>) {
    setError(null);
    try {
      onSaved(await fn());
    } catch (err) {
      setError(errorMessages(err));
    }
  }

  return (
    <div className="mechanics-editor">
      <div role="tablist" className="mechanics-tabs">
        {(["manifest", "sheets", "content", "theme"] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <ul className="library-error" role="alert">
          {error.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}

      {tab === "manifest" && (
        <>
          <ManifestForm value={spec} onChange={setSpec} idEditable={false} />
          <button onClick={() => run(() => mechanicsApi.updateManifest(manifest.id, spec))}>
            Save manifest
          </button>
        </>
      )}

      {tab === "sheets" && (
        <SchemaTabs
          moduleId={manifest.id}
          themeCss={themeCss}
          kinds={manifest.sheet_kinds}
          existing={sheetSchemas}
          save={(k, s) => run(() => mechanicsApi.putSheetSchema(manifest.id, k, s))}
          emptyTitle="Sheet"
        />
      )}

      {tab === "content" && (
        <SchemaTabs
          moduleId={manifest.id}
          themeCss={themeCss}
          kinds={manifest.content_kinds}
          existing={contentSchemas}
          save={(k, s) => run(() => mechanicsApi.putContentSchema(manifest.id, k, s))}
          emptyTitle="Content"
        />
      )}

      {tab === "theme" && (
        <ThemeEditor
          initial={themeCss ?? ""}
          save={(css) => run(() => mechanicsApi.putThemeCss(manifest.id, css))}
        />
      )}
    </div>
  );
}

function SchemaTabs(props: {
  moduleId: string;
  themeCss: string | null;
  kinds: string[];
  existing?: Record<string, Record<string, unknown>>;
  save: (kind: string, schema: Record<string, unknown>) => void;
  emptyTitle: string;
}) {
  const { moduleId, themeCss, kinds, existing, save, emptyTitle } = props;
  const [drafts, setDrafts] = useState<Record<string, SheetSchema>>({});

  if (kinds.length === 0) {
    return <p className="library-status">Declare a kind in the manifest first.</p>;
  }

  return (
    <>
      {kinds.map((kind) => {
        const value = drafts[kind] ?? initialSchema(existing?.[kind], emptyTitle);
        return (
          <section key={kind} className="schema-tab">
            <h4>{kind}</h4>
            <SchemaBuilder
              title={emptyTitle}
              moduleId={moduleId}
              themeCss={themeCss ?? undefined}
              value={value}
              onChange={(next) => setDrafts((d) => ({ ...d, [kind]: next }))}
            />
            <button onClick={() => save(kind, value as unknown as Record<string, unknown>)}>
              Save {kind}
            </button>
          </section>
        );
      })}
    </>
  );
}

function ThemeEditor(props: { initial: string; save: (css: string) => void }) {
  const [css, setCss] = useState(props.initial);
  return (
    <div className="theme-editor">
      <textarea
        aria-label="theme css"
        rows={16}
        value={css}
        onChange={(e) => setCss(e.target.value)}
      />
      <button onClick={() => props.save(css)}>Save theme.css</button>
    </div>
  );
}
