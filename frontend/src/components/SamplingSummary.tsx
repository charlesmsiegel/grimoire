import type { SamplingReport } from "../api/client";

/** Where a resolved preset came from: the scope the server reports for it. */
function from(scope: SamplingReport["scope"]): string {
  switch (scope) {
    case "campaign": return "this campaign's Models";
    case "global": return "the library's Models";
    case "connection": return "the provider";
    case "override": return "this reroll's override";
    default: return "";
  }
}

function value(v: unknown): string {
  return Array.isArray(v) ? v.map((s) => JSON.stringify(s)).join(", ") : String(v);
}

/** What one provider is sent from its sampler preset, and what it is not.
 *
 *  The request this whole feature answers is that a parameter a backend cannot
 *  take is dropped *and said so* — so the dropped list is never collapsed or
 *  hidden behind a toggle, and each entry carries its reason. Shown by the scene
 *  inspector's context breakdown (`ContextBreakdown`), its one caller now that
 *  the routing picker and the connection editor are gone. */
export function SamplingSummary({ report }: { report: SamplingReport | null | undefined }) {
  if (!report) return null;
  const applied = Object.entries(report.applied);
  const source = from(report.scope);
  const title = report.preset_id
    ? `${report.preset_name || report.preset_id}${source ? ` (from ${source})` : ""}`
    : source ? `No preset (cleared by ${source})` : "No preset — provider defaults";
  return (
    <div className="sampling-summary">
      <div className="sampling-title">Sampler: {title}</div>
      {applied.length > 0 && (
        <div className="sampling-applied">
          {applied.map(([k, v]) => (
            <span key={k} className="chip on">{k} {value(v)}</span>
          ))}
        </div>
      )}
      {report.dropped.length > 0 && (
        <ul className="sampling-dropped" aria-label="Not sent">
          {report.dropped.map((d) => (
            <li key={d.param} className="field-hint">
              <strong>Not sent: {d.param}</strong> — {d.reason}
            </li>
          ))}
        </ul>
      )}
      {/* Said where the cap is actually being SENT, not only where it is
          edited: a cap that eats a reasoning model's reply looks, from the
          transcript, like a model that stopped mid-sentence for no reason. */}
      {report.applied.max_tokens !== undefined && (
        <div className="field-hint">
          A max-tokens cap is sent. On a reasoning model, many providers count
          the thinking against it as well, so a reply can be cut short — or
          never start — under a cap sized for the prose.
        </div>
      )}
      {!report.verified && (
        <div className="field-hint">
          Unverified: this provider's cached model list does not say which
          parameters the model takes, so these are sent unchecked — refresh its
          models on the Providers page to check.
        </div>
      )}
    </div>
  );
}
