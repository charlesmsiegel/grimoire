import type { ImagePresetSummary, StyleGuideSummary } from "../../api/wizard";
import type { WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  styleGuides: StyleGuideSummary[];
  imagePresets: ImagePresetSummary[];
  loading: boolean;
  error: string | null;
}

export function StepStyle({ draft, update, styleGuides, imagePresets, loading, error }: Props) {
  return (
    <div className="wizard-step">
      <h3>Step 5 — Style & content</h3>
      <p className="wizard-step-help">
        Pick a library style guide, write one inline, or skip. Optionally pick an image preset and
        set content boundaries.
      </p>

      {loading && <p className="wizard-meta">Loading style guides…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <fieldset className="wizard-style-mode" aria-label="Style guide source">
        <legend>Style guide</legend>
        <label>
          <input
            type="radio"
            name="style-mode"
            checked={draft.styleGuideMode === "none"}
            onChange={() => update({ styleGuideMode: "none", styleGuideId: null })}
          />
          <span>None</span>
        </label>
        <label>
          <input
            type="radio"
            name="style-mode"
            checked={draft.styleGuideMode === "library"}
            onChange={() => update({ styleGuideMode: "library" })}
          />
          <span>From library</span>
        </label>
        <label>
          <input
            type="radio"
            name="style-mode"
            checked={draft.styleGuideMode === "inline"}
            onChange={() => update({ styleGuideMode: "inline", styleGuideId: null })}
          />
          <span>Inline</span>
        </label>
      </fieldset>

      {draft.styleGuideMode === "library" && (
        <label className="form-field wizard-field">
          <span>Library style guide</span>
          <select
            value={draft.styleGuideId ?? ""}
            onChange={(e) => update({ styleGuideId: e.target.value || null })}
          >
            <option value="">— pick —</option>
            {styleGuides.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name ?? g.id}
              </option>
            ))}
          </select>
        </label>
      )}

      {draft.styleGuideMode === "inline" && (
        <label className="form-field wizard-field">
          <span>Inline style guide</span>
          <textarea
            rows={5}
            value={draft.inlineStyleGuide}
            onChange={(e) => update({ inlineStyleGuide: e.target.value })}
            placeholder="Prose style notes for the LLM."
          />
        </label>
      )}

      <label className="form-field wizard-field">
        <span>Image preset</span>
        <select
          value={draft.imagePresetId ?? ""}
          onChange={(e) => update({ imagePresetId: e.target.value || null })}
        >
          <option value="">— none —</option>
          {imagePresets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name ?? p.id}
            </option>
          ))}
        </select>
      </label>

      <label className="form-field wizard-field">
        <span>Content boundaries</span>
        <textarea
          rows={4}
          value={draft.contentBoundaries}
          onChange={(e) => update({ contentBoundaries: e.target.value })}
          placeholder="Tones to avoid, hard limits, content warnings."
        />
      </label>
    </div>
  );
}
