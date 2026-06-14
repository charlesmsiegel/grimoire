/**
 * Multi-step character-creation wizard (spec 06 §Character creation).
 *
 * Mechanics modules declare an ordered list of `CreationStep`s, each with a
 * JSON Schema describing the inputs. The wizard walks them sequentially:
 * per-step output is tracked in local state keyed by `step.id`, the user can
 * skip optional steps, and the final submit POSTs the accumulated outputs to
 * the campaign-bound `/characters/{id}/creation/submit` endpoint. The library
 * preview path uses the same component with a no-op submit so authors can
 * eyeball the resulting sheet without persisting anything.
 */

import { useCallback, useState } from "react";

import { campaignApi } from "../../api/campaign";
import { errorMessage } from "../../api/client";
import { mechanicsApi, type CreationStep } from "../../api/library";
import { useResource } from "../../api/useResource";
import { renderField } from "../../sheets/renderField";
import { SheetRenderer } from "../../sheets/SheetRenderer";
import type { SchemaProperty, SheetSchema } from "../../sheets/types";

export interface CharacterCreationProps {
  moduleId: string;
  /** Pre-loaded step list. If omitted, `loadSteps` is called on mount. */
  steps?: CreationStep[];
  loadSteps?: () => Promise<CreationStep[]>;
  /**
   * Called with the accumulated step outputs once the user reaches "Finish".
   * Returns the finalized sheet (or null for previews that don't persist).
   */
  onSubmit: (
    stepOutputs: Record<string, Record<string, unknown>>,
  ) => Promise<Record<string, unknown> | null>;
  /** Optional inline theme CSS to scope into the preview SheetRenderer. */
  themeCss?: string | null;
  onCancel?: () => void;
  onComplete?: (sheet: Record<string, unknown> | null) => void;
  /** Heading shown above the stepper (e.g. character name or "Preview"). */
  heading?: string;
}

export function CharacterCreation({
  moduleId,
  steps: initialSteps,
  loadSteps,
  onSubmit,
  themeCss,
  onCancel,
  onComplete,
  heading,
}: CharacterCreationProps) {
  const stepsResource = useResource(
    useCallback(
      () => (initialSteps || !loadSteps ? Promise.resolve(initialSteps ?? []) : loadSteps()),
      // Match the original effect: re-fetch only when the module changes, not
      // on every render-fresh `loadSteps`/`initialSteps` identity.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [moduleId],
    ),
  );
  const steps = stepsResource.data ?? [];
  // Only show the loading line when there's actually a fetch to wait on; an
  // inline `initialSteps` resolves immediately.
  const loading = !initialSteps && Boolean(loadSteps) && stepsResource.loading;
  const loadError = stepsResource.error ? errorMessage(stepsResource.error) : null;

  const [outputs, setOutputs] = useState<Record<string, Record<string, unknown>>>({});
  const [currentIdx, setCurrentIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [finalSheet, setFinalSheet] = useState<Record<string, unknown> | null>(null);

  if (loading) {
    return <p className="wizard-meta">Loading creation steps…</p>;
  }
  if (loadError) {
    return (
      <p className="wizard-error" role="alert">
        Failed to load creation steps: {loadError}
      </p>
    );
  }
  if (steps.length === 0) {
    return (
      <p className="wizard-meta">
        This mechanics module does not declare any character-creation steps.
      </p>
    );
  }

  if (finalSheet !== null) {
    // Preview-mode render of the final composed sheet for library preview /
    // post-submit visual confirmation.
    const previewSchema: SheetSchema = {
      type: "object",
      title: "Final sheet (preview)",
      properties: Object.fromEntries(
        Object.entries(finalSheet).map(([k, v]) => [k, inferProperty(k, v)]),
      ),
    };
    return (
      <div className="wizard-step">
        <h3>Sheet preview</h3>
        <p className="wizard-step-help">
          Composed result of the wizard. {onComplete ? "" : "No data was persisted."}
        </p>
        <SheetRenderer
          moduleId={moduleId}
          schema={previewSchema}
          value={finalSheet}
          onChange={() => undefined}
          themeCss={themeCss ?? undefined}
          readOnly
        />
        <div className="modal-actions">
          {onCancel && (
            <button type="button" onClick={onCancel}>
              Close
            </button>
          )}
        </div>
      </div>
    );
  }

  const step = steps[currentIdx];
  if (!step) {
    return <p className="wizard-meta">No current step.</p>;
  }
  const currentStep = step;
  const stepSchema = currentStep.step_schema as unknown as SheetSchema;
  const stepProperties = stepSchema?.properties ?? {};
  const stepValue = outputs[currentStep.id] ?? {};

  const updateField = (key: string, next: unknown) => {
    setOutputs((prev) => ({
      ...prev,
      [currentStep.id]: { ...(prev[currentStep.id] ?? {}), [key]: next },
    }));
  };

  const isFirst = currentIdx === 0;
  const isLast = currentIdx === steps.length - 1;

  const goBack = () => setCurrentIdx((i) => Math.max(0, i - 1));
  const goNext = () => setCurrentIdx((i) => Math.min(steps.length - 1, i + 1));
  const skip = () => {
    setOutputs((prev) => {
      const next = { ...prev };
      delete next[currentStep.id];
      return next;
    });
    goNext();
  };

  async function finish() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const sheet = await onSubmit(outputs);
      setFinalSheet(sheet ?? {});
      if (onComplete) onComplete(sheet);
    } catch (err) {
      setSubmitError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="wizard-route character-creation-wizard">
      {heading && <h3 className="wizard-step-heading">{heading}</h3>}
      <ol className="wizard-stepper" aria-label="Creation steps">
        {steps.map((s, i) => (
          <li key={s.id} className={i === currentIdx ? "current" : i < currentIdx ? "done" : ""}>
            <span className="wizard-step-index">{i + 1}</span>
            <span>{s.title}</span>
          </li>
        ))}
      </ol>
      <div className="wizard-step" key={currentStep.id}>
        <h3>{currentStep.title}</h3>
        {currentStep.description && <p className="wizard-step-help">{currentStep.description}</p>}
        <div className={`sheet mechanics-${moduleId}`} data-module={moduleId}>
          <div className="sheet-fields">
            {Object.entries(stepProperties).map(([key, property]) =>
              renderField({
                name: key,
                property: property as SchemaProperty,
                value: (stepValue as Record<string, unknown>)[key],
                onChange: (next) => updateField(key, next),
              }),
            )}
            {Object.keys(stepProperties).length === 0 && (
              <p className="wizard-meta">
                This step has no fields; press Next or Finish to continue.
              </p>
            )}
          </div>
        </div>
        {submitError && (
          <p className="wizard-error" role="alert">
            {submitError}
          </p>
        )}
        <div className="modal-actions">
          {onCancel && (
            <button type="button" onClick={onCancel} disabled={submitting}>
              Cancel
            </button>
          )}
          <button type="button" onClick={goBack} disabled={isFirst || submitting}>
            Back
          </button>
          {currentStep.optional && (
            <button type="button" onClick={skip} disabled={submitting}>
              Skip
            </button>
          )}
          {!isLast && (
            <button type="button" className="primary" onClick={goNext} disabled={submitting}>
              Next
            </button>
          )}
          {isLast && (
            <button
              type="button"
              className="primary"
              onClick={() => void finish()}
              disabled={submitting}
            >
              {submitting ? "Submitting…" : "Finish"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Best-effort widget inference for the post-submit preview. The wizard does
 * not have the mechanics module's full sheet schema in scope, so we shim a
 * `SchemaProperty` from each top-level value's runtime type. Modules that
 * want a richer preview should expose a sheet-schema endpoint and let the
 * caller render through the real schema.
 */
function inferProperty(name: string, value: unknown): SchemaProperty {
  if (typeof value === "boolean") return { widget: "boolean", title: name, type: "boolean" };
  if (typeof value === "number") return { widget: "number", title: name, type: "number" };
  if (typeof value === "string") {
    if (value.length > 80 || value.includes("\n")) {
      return { widget: "textarea", title: name, type: "string" };
    }
    return { widget: "text", title: name, type: "string" };
  }
  if (Array.isArray(value)) {
    return { widget: "keyword-list", title: name, type: "array" };
  }
  if (value && typeof value === "object") {
    const props: Record<string, SchemaProperty> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      props[k] = inferProperty(k, v);
    }
    return { widget: "nested-section", title: name, type: "object", properties: props };
  }
  return { widget: "text", title: name };
}

// ---------------------------------------------------------------------------
// Convenience wrappers wired to specific endpoints.
// ---------------------------------------------------------------------------

interface CampaignWizardProps {
  campaignId: string;
  characterId: string;
  moduleId: string;
  themeCss?: string | null;
  onCancel?: () => void;
  onComplete?: (sheet: Record<string, unknown> | null) => void;
  heading?: string;
}

/**
 * Wizard bound to a campaign character. Steps come from the campaign-bound
 * endpoint (so any per-campaign module activation is respected) and the
 * submission writes the finalized sheet through the backend.
 */
export function CampaignCharacterCreation({
  campaignId,
  characterId,
  moduleId,
  themeCss,
  onCancel,
  onComplete,
  heading,
}: CampaignWizardProps) {
  return (
    <CharacterCreation
      moduleId={moduleId}
      loadSteps={() => campaignApi.characterCreationSteps(campaignId, characterId)}
      onSubmit={(stepOutputs) =>
        campaignApi.submitCharacterCreation(campaignId, characterId, {
          step_outputs: stepOutputs,
          source: "user",
        })
      }
      themeCss={themeCss}
      onCancel={onCancel}
      onComplete={onComplete}
      heading={heading}
    />
  );
}

interface LibraryPreviewProps {
  moduleId: string;
  themeCss?: string | null;
  onCancel?: () => void;
}

/**
 * Library-baseline preview: walks the steps against the module endpoint and
 * builds the composed sheet client-side. No persistence — purely a visual
 * check for module authors.
 */
export function LibraryCharacterCreationPreview({
  moduleId,
  themeCss,
  onCancel,
}: LibraryPreviewProps) {
  return (
    <CharacterCreation
      moduleId={moduleId}
      loadSteps={() => mechanicsApi.characterCreation(moduleId)}
      onSubmit={async (stepOutputs) => {
        // Client-side merge: previewing the composed object so authors can
        // visually verify their step schemas without writing a sheet.
        const merged: Record<string, unknown> = {};
        for (const out of Object.values(stepOutputs)) {
          for (const [k, v] of Object.entries(out)) merged[k] = v;
        }
        return merged;
      }}
      themeCss={themeCss}
      onCancel={onCancel}
      heading="Preview character creation"
    />
  );
}
