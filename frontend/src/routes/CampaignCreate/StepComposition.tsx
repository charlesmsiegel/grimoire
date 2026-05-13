import type { SettingSummary } from "../../api/wizard";
import type { DraftSettingRef, EntityKind, WizardDraft } from "./types";
import { ENTITY_KINDS } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  settings: SettingSummary[];
  loading: boolean;
  error: string | null;
}

function moveRef(refs: DraftSettingRef[], index: number, delta: number): DraftSettingRef[] {
  const target = index + delta;
  if (target < 0 || target >= refs.length) return refs;
  const a = refs[index];
  const b = refs[target];
  if (!a || !b) return refs;
  const next = [...refs];
  next[index] = b;
  next[target] = a;
  return next.map((r, i) => ({ ...r, priority: i + 1 }));
}

export function StepComposition({ draft, update, settings, loading, error }: Props) {
  const refs = draft.settingRefs;
  const remaining = settings.filter((s) => !refs.find((r) => r.setting_id === s.id));

  const addRef = (settingId: string) => {
    const next: DraftSettingRef[] = [
      ...refs,
      {
        setting_id: settingId,
        priority: refs.length + 1,
        include: [...ENTITY_KINDS],
        track_latest: false,
      },
    ];
    update({ settingRefs: next });
  };

  const removeRef = (index: number) => {
    const next = refs.filter((_, i) => i !== index).map((r, i) => ({ ...r, priority: i + 1 }));
    update({ settingRefs: next });
  };

  const patchRef = (index: number, patch: Partial<DraftSettingRef>) => {
    const next = refs.map((r, i) => (i === index ? { ...r, ...patch } : r));
    update({ settingRefs: next });
  };

  const toggleInclude = (index: number, kind: EntityKind) => {
    const ref = refs[index];
    if (!ref) return;
    const next = ref.include.includes(kind)
      ? ref.include.filter((k) => k !== kind)
      : [...ref.include, kind];
    patchRef(index, { include: next });
  };

  return (
    <div className="wizard-step">
      <h3>Step 2 — Composition</h3>
      <p className="wizard-step-help">
        Pick one or more library settings. Higher priority refs override lower ones during
        resolution. Uncheck a kind to exclude it from this setting.
      </p>

      {loading && <p className="wizard-meta">Loading settings…</p>}
      {error && <p className="wizard-error">{error}</p>}

      {refs.length === 0 ? (
        <p className="wizard-empty">No settings yet — pick one below.</p>
      ) : (
        <ol className="wizard-ref-list" aria-label="Selected settings">
          {refs.map((ref, i) => (
            <li key={ref.setting_id} className="wizard-ref">
              <div className="wizard-ref-head">
                <span className="wizard-ref-priority">{i + 1}.</span>
                <strong>{ref.setting_id}</strong>
                <div className="wizard-ref-actions">
                  <button
                    type="button"
                    aria-label="Move up"
                    disabled={i === 0}
                    onClick={() => update({ settingRefs: moveRef(refs, i, -1) })}
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    aria-label="Move down"
                    disabled={i === refs.length - 1}
                    onClick={() => update({ settingRefs: moveRef(refs, i, 1) })}
                  >
                    ▼
                  </button>
                  <button type="button" aria-label="Remove" onClick={() => removeRef(i)}>
                    ⨯
                  </button>
                </div>
              </div>
              <label className="wizard-ref-toggle">
                <input
                  type="checkbox"
                  checked={ref.track_latest}
                  onChange={(e) => patchRef(i, { track_latest: e.target.checked })}
                />
                <span>Track latest library version (auto-upgrade)</span>
              </label>
              <fieldset className="wizard-ref-include">
                <legend>Include</legend>
                {ENTITY_KINDS.map((kind) => (
                  <label key={kind} className="wizard-include-option">
                    <input
                      type="checkbox"
                      checked={ref.include.includes(kind)}
                      onChange={() => toggleInclude(i, kind)}
                    />
                    <span>{kind}</span>
                  </label>
                ))}
              </fieldset>
            </li>
          ))}
        </ol>
      )}

      {remaining.length > 0 && (
        <div className="wizard-add-ref">
          <label htmlFor="wizard-add-setting">Add setting</label>
          <select
            id="wizard-add-setting"
            value=""
            onChange={(e) => {
              if (e.target.value) addRef(e.target.value);
            }}
          >
            <option value="">— pick —</option>
            {remaining.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name ?? s.id}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
