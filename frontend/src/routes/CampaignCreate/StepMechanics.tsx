import type { MechanicsModuleSummary } from "../../api/wizard";
import type { WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  modules: MechanicsModuleSummary[];
  loading: boolean;
  error: string | null;
}

export function StepMechanics({ draft, update, modules, loading, error }: Props) {
  return (
    <div className="wizard-step">
      <h3>Step 3 — Mechanics</h3>
      <p className="wizard-step-help">
        Pick a mechanics module or choose "No mechanics" for a narrative-only campaign.
      </p>

      {loading && <p className="wizard-meta">Loading installed mechanics…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <div className="wizard-mechanics-options" role="radiogroup" aria-label="Mechanics module">
        <label className="wizard-option">
          <input
            type="radio"
            name="mechanics"
            checked={draft.mechanicsId === null}
            onChange={() => update({ mechanicsId: null })}
          />
          <div>
            <strong>No mechanics (narrative only)</strong>
            <small>Skip rules. The system runs as freeform fiction.</small>
          </div>
        </label>

        {modules.map((m) => {
          const disabled = Boolean(m.load_error);
          return (
            <label key={m.id} className={`wizard-option${disabled ? " disabled" : ""}`}>
              <input
                type="radio"
                name="mechanics"
                disabled={disabled}
                checked={draft.mechanicsId === m.id}
                onChange={() => update({ mechanicsId: m.id })}
              />
              <div>
                <strong>{m.name ?? m.id}</strong>
                <small>
                  {m.version ? `v${m.version}` : null}
                  {m.api_version ? ` · api ${m.api_version}` : null}
                </small>
                {disabled && <p className="wizard-error">Load error: {m.load_error}</p>}
              </div>
            </label>
          );
        })}
      </div>

      {draft.mechanicsId !== null && (
        <label className="wizard-toggle">
          <input
            type="checkbox"
            checked={draft.bulkCreateSheets}
            onChange={(e) => update({ bulkCreateSheets: e.target.checked })}
          />
          <span>Bulk-create starter sheets for cast in the composed settings</span>
        </label>
      )}
    </div>
  );
}
