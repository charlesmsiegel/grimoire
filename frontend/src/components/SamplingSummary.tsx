import type { SamplingReport } from "../api/client";

/** Where a resolved preset came from, in the routing picker's vocabulary. */
function from(scope: SamplingReport["scope"]): string {
  switch (scope) {
    case "campaign": return "this campaign's routing";
    case "global": return "the global routing";
    case "connection": return "the connection";
    default: return "";
  }
}

function value(v: unknown): string {
  return Array.isArray(v) ? v.map((s) => JSON.stringify(s)).join(", ") : String(v);
}

/** What one connection is sent from its sampler preset, and what it is not.
 *
 *  The request this whole feature answers is that a parameter a backend cannot
 *  take is dropped *and said so* — so the dropped list is never collapsed or
 *  hidden behind a toggle, and each entry carries its reason. One component for
 *  the three places that show it (routing picker, connection editor, scene
 *  inspector), so the three cannot describe the same split differently. */
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
          Unverified: this connection's cached model list does not say which
          parameters the model takes, so these are sent unchecked — refresh its
          models on the Connections page to check.
        </div>
      )}
    </div>
  );
}
