import type { SamplerImportReport } from "../../api/client";
import { shown } from "./presetDraft";

/** What an import did with the file, one list per outcome. */
export function ImportReport({ report }: { report: SamplerImportReport }) {
  return (
    <div className="sampler-import-report" aria-label="Import report">
      {report.mapped.length > 0 && (
        <p className="field-hint">
          <strong>Imported:</strong>{" "}
          {report.mapped.map((m) => `${m.param} ← ${m.from}`).join(", ")}
        </p>
      )}
      {report.neutral.length > 0 && (
        <p className="field-hint">
          <strong>Left unset (the sampler's off position):</strong>{" "}
          {report.neutral.map((m) => m.from).join(", ")}
        </p>
      )}
      {report.skipped.map((m) => (
        <p className="field-hint" key={m.from}>
          <strong>Skipped {m.from}</strong> ({shown(m.value)}): {m.why}.
        </p>
      ))}
      {report.invalid.map((m) => (
        <p className="field-hint" key={`${m.key}:${m.why}`}>
          <strong>Not imported, {m.key}:</strong> {m.why}
        </p>
      ))}
      {report.unmapped.length > 0 && (
        <p className="field-hint">
          <strong>No grimoire equivalent:</strong> {report.unmapped.join(", ")}
        </p>
      )}
      {report.notes.map((n) => <p className="field-hint" key={n}>{n}</p>)}
    </div>
  );
}
