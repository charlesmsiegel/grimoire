import type { GreetingSummary } from "../../api/wizard";
import type { WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  greetings: GreetingSummary[];
  loading: boolean;
  error: string | null;
}

export function StepStartingScene({ draft, update, greetings, loading, error }: Props) {
  const selectedGreeting = greetings.find((g) => g.id === draft.greetingId) ?? null;

  return (
    <div className="wizard-step">
      <h3>Step 6 — Starting scene</h3>
      <p className="wizard-step-help">
        Pick a greeting from the composed settings or skip to start with a blank scene. Confirm the
        opening location, time, and cast.
      </p>

      {loading && <p className="wizard-meta">Loading greetings…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <label className="wizard-field">
        <span>Greeting</span>
        <select
          value={draft.greetingId ?? ""}
          onChange={(e) => {
            const id = e.target.value || null;
            const greeting = greetings.find((g) => g.id === id);
            update({
              greetingId: id,
              startingLocation: greeting?.starting_location ?? draft.startingLocation,
              startingTime: greeting?.starting_time ?? draft.startingTime,
            });
          }}
        >
          <option value="">— blank start —</option>
          {greetings.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name ?? g.id}
            </option>
          ))}
        </select>
        {selectedGreeting?.description && (
          <small className="wizard-meta">{selectedGreeting.description}</small>
        )}
      </label>

      <label className="wizard-field">
        <span>Starting location</span>
        <input
          type="text"
          value={draft.startingLocation}
          onChange={(e) => update({ startingLocation: e.target.value })}
          placeholder="Whitechapel chantry"
        />
      </label>

      <label className="wizard-field">
        <span>In-game time</span>
        <input
          type="text"
          value={draft.startingTime}
          onChange={(e) => update({ startingTime: e.target.value })}
          placeholder="1888-01-15 22:00"
        />
      </label>

      <label className="wizard-field">
        <span>Present cast</span>
        <input
          type="text"
          value={draft.startingCast.join(", ")}
          onChange={(e) =>
            update({
              startingCast: e.target.value
                .split(",")
                .map((c) => c.trim())
                .filter(Boolean),
            })
          }
          placeholder="aleksandr, beatrice"
        />
        <small>Comma-separated character refs.</small>
      </label>

      <h4>Review</h4>
      <dl className="wizard-summary">
        <dt>Id</dt>
        <dd>{draft.id || <em>missing</em>}</dd>
        <dt>Name</dt>
        <dd>{draft.name || <em>missing</em>}</dd>
        <dt>Settings</dt>
        <dd>
          {draft.settingRefs.length === 0 ? (
            <em>none — pick at least one</em>
          ) : (
            draft.settingRefs.map((r) => r.setting_id).join(", ")
          )}
        </dd>
        <dt>Mechanics</dt>
        <dd>{draft.mechanicsId ?? <em>none</em>}</dd>
        <dt>PCs</dt>
        <dd>
          {draft.pcs.length === 0 ? (
            <em>none — add at least one</em>
          ) : (
            draft.pcs.map((p) => p.name).join(", ")
          )}
        </dd>
      </dl>
    </div>
  );
}
