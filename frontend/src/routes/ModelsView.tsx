import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type InferenceSettings } from "../api/client";
import { ErrorNote } from "../components/ErrorNote";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { InferenceNav } from "../components/inference/InferenceNav";
import { migrationBanner } from "../components/inference/migration";
import { RetiredNotes } from "../components/inference/RetiredNotes";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import { useProviderHealth } from "../components/models/health";
import { ModelsEditForm } from "../components/models/ModelsEditForm";
import { addedModel } from "../components/models/rates";
import { EmbeddingRow, RoleRow } from "../components/models/RoleRow";
import { GENERATIVE } from "../components/models/selections";
import { ADVANCED_HASH } from "../components/models/taskHash";
import { TokenRates } from "../components/models/TokenRates";
import { PageShell } from "../components/PageShell";

/** Routes this scope overrides: its own `use` or its own `preset`. */
export function overrideCount(settings: InferenceSettings): number {
  return settings.routes.filter((r) => r.use !== "" || r.preset !== "").length;
}

/** `/models` (spec 3): which provider, model and preset each role runs on,
 *  read-only, and what would price it; `/models/edit` (`edit`) is the one
 *  form for every role and, under Advanced, every task.
 *
 *  Settings → Inference's middle page. Library-wide only: a campaign's
 *  overrides are its Inspector's. Every "resolves", problem and rate is the
 *  server's; health is the connections list's. */
export default function ModelsView({ edit = false }: { edit?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { settings, error, install } = useInferenceSettings();
  const health = useProviderHealth();
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  // Gated on the layout, not on the migration finishing (unchanged rule).
  const blocked = !!settings && (settings.newer || settings.format !== "2");
  const banner = migrationBanner(settings);
  const add = addedModel(location.search);

  // `#rates` scrolls the rate table in once the page is drawn.
  useEffect(() => {
    if (location.hash !== "#rates" || !settings) return;
    document.getElementById("rates")?.scrollIntoView?.({ block: "start" });
  }, [location.hash, settings]);

  function ratesSaved() {
    api.getInferenceSettings().then(install).catch(() => {});
    if (add) navigate({ pathname: "/models", hash: "#rates" }, { replace: true });
  }

  let body;
  if (!settings) {
    body = error != null ? null : <p className="field-hint">Reading the model settings…</p>;
  } else {
    const n = overrideCount(settings);
    body = edit ? (
      <ModelsEditForm settings={settings} health={health} blocked={blocked}
                      onSaved={(next) => { install(next); navigate("/models"); }}
                      onCancel={() => navigate("/models")} />
    ) : (
      <>
        <section className="models-roles" aria-label="Roles">
          {GENERATIVE.map((r) => <RoleRow key={r} role={r} settings={settings} health={health} />)}
          {settings.roles.embedding && (
            <EmbeddingRow card={settings.roles.embedding} settings={settings} health={health} />
          )}
        </section>
        {n > 0 && (
          <p className="models-overrides"><Link to={`/models/edit${ADVANCED_HASH}`}>
            {n} task override{n === 1 ? "" : "s"} active
          </Link></p>
        )}
        <div className="form-actions models-actions">
          <button className="primary" disabled={blocked} onClick={() => navigate("/models/edit")}>
            Edit models
          </button>
          <Link className="button subtle" to="/providers/new" state={{ returnTo: "/models" }}>
            + Add provider
          </Link>
          <Link to="/providers">Provider status →</Link>
        </div>
        <TokenRates addModel={add || undefined} onSaved={ratesSaved} />
      </>
    );
  }

  return (
    <PageShell column={<InferenceNav current="models" />} columnLabel="Models">
      <div className="page view-anim">
        <div className="page-head"><h1 className="page-h1">Models</h1></div>
        <InferenceBanner status={banner} />
        {settings && (
          <RetiredNotes
            notes={(settings.retirement_notes ?? []).filter((n) => !dismissed.has(n.id))}
            onDismissed={(id) => setDismissed((prev) => new Set(prev).add(id))} />
        )}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        {body}
      </div>
    </PageShell>
  );
}
