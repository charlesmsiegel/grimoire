import { useCallback, useEffect, useState } from "react";
import { Link, useMatch, useNavigate, useParams } from "react-router-dom";
import {
  api, type SamplerImportReport, type SamplerParams, type SamplerParamSpec, type SamplerPreset,
} from "../api/client";
import { errorText } from "../api/errors";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { InferenceNav } from "../components/inference/InferenceNav";
import type { ProviderModel } from "../components/inference/ProviderModelPicker";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import { ColumnSection, PageShell } from "../components/PageShell";
import { PresetForm } from "../components/presets/PresetForm";
import { BLANK, type Draft, toDraft, toParams } from "../components/presets/presetDraft";
import { PresetImport } from "../components/presets/PresetImport";
import { PresetView } from "../components/presets/PresetView";
import { presetUses } from "../components/presets/usedBy";

const presetPath = (id: string) => `/presets/${encodeURIComponent(id)}`;

/** `/presets` (spec 4): Settings → Inference's sampler presets, as a page that
 *  owns the screen -- the presets are the column's records, the open one is
 *  main's, read-only until Edit; `+ New preset` and `Import…` are addresses.
 *  Each preset says where it is used. Writes are refused only by a newer
 *  build's store, so only that gates them here: + New preset and Import… stay
 *  in view, disabled, while it does. */
export default function PresetsView() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isNew = useMatch("/presets/new") !== null;
  const isImport = useMatch("/presets/import") !== null;
  const { settings, error: settingsError } = useInferenceSettings();
  const [presets, setPresets] = useState<SamplerPreset[] | null>(null);
  const [table, setTable] = useState<SamplerParamSpec[]>([]);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** An import's report, held with the preset it made: shown only there. */
  const [report, setReport] = useState<{ id: string; report: SamplerImportReport } | null>(null);
  /** Kept across presets, so flicking through them compares each on one model. */
  const [previewOn, setPreviewOn] = useState<ProviderModel>({ provider: "", model: "" });

  const reload = useCallback(() => api.listSamplerPresets().then((r) => {
    setPresets(r.presets);
    setTable(r.params);
    return r.presets;
  }), []);
  useEffect(() => { reload().catch((err: unknown) => setError(errorText(err))); }, [reload]);
  useEffect(() => { setMode("view"); setError(null); }, [id]);
  useEffect(() => { if (isNew) { setDraft(BLANK); setError(null); } }, [isNew]);
  // `/presets` alone opens the first preset, as a list page opens on a record.
  useEffect(() => {
    if (!id && !isNew && !isImport && presets && presets.length > 0) {
      navigate(presetPath(presets[0].id), { replace: true });
    }
  }, [id, isNew, isImport, presets, navigate]);

  const newer = !!settings?.newer;
  const banner = settings?.newer ? { ...settings.migration, state: "newer" as const } : null;
  const current = presets?.find((p) => p.id === id) ?? null;

  async function save() {
    if (!draft.name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body = { name: draft.name, notes: draft.notes,
                     params: toParams(draft, table) as SamplerParams };
      const saved = current && !isNew ? await api.updateSamplerPreset(current.id, body)
                                      : await api.createSamplerPreset(body);
      await reload();
      setMode("view");
      navigate(presetPath(saved.id));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current || !window.confirm(`Delete the preset “${current.name}”?`)) return;
    setBusy(true);
    try {
      await api.deleteSamplerPreset(current.id);
      await reload();
      navigate("/presets");
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function runImport(name: string, data: unknown, withMax: boolean) {
    setBusy(true);
    setError(null);
    try {
      const got = await api.importSamplerPreset({ name, data, include_max_tokens: withMax });
      await reload();
      setReport({ id: got.preset.id, report: got.report });
      navigate(presetPath(got.preset.id));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const column = (
    <>
      <InferenceNav current="presets" />
      <ColumnSection label="Presets" count={presets?.length}>
        {/* Buttons rather than links so a newer build's store can show them
            disabled -- present, and plainly not available -- as before. */}
        <div className="column-actions">
          <button type="button" className="column-primary" disabled={newer}
                  onClick={() => navigate("/presets/new")}>+ New preset</button>
          <button type="button" className="subtle" disabled={newer}
                  onClick={() => navigate("/presets/import")}>Import…</button>
        </div>
        {presets?.length === 0 && <p className="column-empty">None yet.</p>}
        {presets?.map((p) => (
          <Link key={p.id} to={presetPath(p.id)}
                className={"column-row" + (p.id === id ? " active" : "")}>
            <span className="column-row-label">{p.name}</span>
          </Link>
        ))}
      </ColumnSection>
    </>
  );

  let body;
  if (isNew || (mode === "edit" && current)) {
    body = <PresetForm draft={draft} onDraft={setDraft} table={table} busy={busy} newer={newer}
                       onSave={() => void save()}
                       onCancel={() => (isNew ? navigate("/presets") : setMode("view"))} />;
  } else if (isImport) {
    body = <PresetImport busy={busy} newer={newer} onImport={(n, d, m) => void runImport(n, d, m)}
                         onCancel={() => navigate("/presets")} onError={setError} />;
  } else if (presets === null) {
    body = <p className="field-hint">Reading presets…</p>;
  } else if (presets.length === 0) {
    body = <p className="empty-state">No presets yet.</p>;
  } else if (!current) {
    body = id ? <p className="empty-state">No preset is called {id}.</p> : null;
  } else {
    body = <PresetView preset={current} table={table} settings={settings}
                       report={report?.id === current.id ? report.report : null}
                       previewOn={previewOn} onPreviewOn={setPreviewOn}
                       newer={newer} busy={busy}
                       onEdit={() => { setDraft(toDraft(current)); setMode("edit"); }}
                       onDelete={() => void remove()}
                       usedBy={settings ? presetUses(settings, current.id) : null}
                       usedByReading={!settings && settingsError == null} />;
  }

  return (
    <PageShell column={column} columnLabel="Presets">
      <div className="page view-anim">
        <div className="page-head"><h1 className="page-h1">Presets</h1></div>
        <p className="config-copy">
          A preset is a named set of temperature, top-p, top-k, min-p, the three
          penalties, a token cap, stop strings and a reasoning effort — the
          settings SillyTavern users share per model. A preset sets only what it
          names; everything it leaves blank stays at the provider's default.
        </p>
        <p className="config-copy">
          Attach one to a role or a task on the <Link to="/models">Models</Link>{" "}
          page; a campaign can override either from the scene inspector, the same
          way it overrides the model. Not every backend takes every parameter, so
          what cannot be sent is dropped — <em>Preview on…</em> below, the Models
          page and the scene inspector say which.
        </p>
        <InferenceBanner status={banner} />
        {error && <div className="banner">{error}</div>}
        {body}
      </div>
    </PageShell>
  );
}
