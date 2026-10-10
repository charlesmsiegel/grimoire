import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import type {
  InferenceSettings, SamplerImportReport, SamplerParamSpec, SamplerPreset,
} from "../../api/client";
import { ControlsReadout } from "../inference/ControlsReadout";
import { ProviderModelPicker, type ProviderModel } from "../inference/ProviderModelPicker";
import { ImportReport } from "./ImportReport";
import { shown } from "./presetDraft";

/** A preset, read-only: its parameters, notes, a server-computed preview on
 *  one model, and a sidebar with its source, where it is used, Edit and Delete. */
export function PresetView(
  { preset: current, table, report, settings, previewOn, onPreviewOn, newer, busy,
    onEdit, onDelete, usedBy, usedByReading }: {
    preset: SamplerPreset;
    table: SamplerParamSpec[];
    report: SamplerImportReport | null;
    settings: InferenceSettings | null;
    previewOn: ProviderModel;
    onPreviewOn: (v: ProviderModel) => void;
    newer: boolean;
    busy: boolean;
    onEdit: () => void;
    onDelete: () => void;
    /** Null while the settings are not in hand: no claim is made either way. */
    usedBy: { label: string; to: string }[] | null;
    /** The settings read is still in flight (false once it failed). */
    usedByReading: boolean;
  },
) {
  return (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{current.name}</h3>
              {report && <ImportReport report={report} />}
              <div className="detail-rendered">
                {Object.keys(current.params).length === 0 ? (
                  <p>Sets nothing: every parameter is left at the provider's default.</p>
                ) : (
                  <table className="sampler-params">
                    <tbody>
                      {table.filter((t) => t.name in current.params).map((t) => (
                        <tr key={t.name}>
                          <th scope="row">{t.label}</th>
                          <td>{shown(current.params[t.name])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {current.notes && (
                  <Markdown remarkPlugins={[remarkGfm]}>{current.notes}</Markdown>
                )}
              </div>
              <section className="preset-preview" aria-label="Preview on…">
                <h4>Preview on…</h4>
                <p className="field-hint">
                  What this preset sends on one provider's model, as the server
                  will send it: a control the model cannot take is kept in the
                  preset and left off the wire.
                </p>
                <ProviderModelPicker needs={["generate"]} providers={settings?.providers ?? []}
                                     value={previewOn} onChange={onPreviewOn} />
                <ControlsReadout presetId={current.id} provider={previewOn.provider}
                                 model={previewOn.model} />
              </section>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={onEdit} disabled={newer}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Source</h4>
                <span className="chip on">
                  {current.source === "sillytavern" ? "Imported from SillyTavern" : "Made here"}
                </span>
              </div>
              <div className="side-section">
                <h4>Used by</h4>
                {usedBy === null ? (
                  usedByReading && <span className="field-hint">Reading…</span>
                ) : usedBy.length === 0 ? (
                  <span className="field-hint">Nothing uses this preset yet.</span>
                ) : (
                  <ul className="chips" aria-label="Used by">
                    {usedBy.map((u) => (
                      <li key={`${u.label}\u0000${u.to}`}>
                        <Link className="chip" to={u.to}>{u.label}</Link>
                      </li>
                    ))}
                  </ul>
                )}
                <span className="field-hint">
                  A campaign can override any of these in the scene inspector.
                </span>
              </div>
              <div className="side-section">
                <button className="subtle danger" onClick={onDelete}
                        disabled={busy || newer}>Delete</button>
              </div>
            </aside>
          </div>
  );
}
