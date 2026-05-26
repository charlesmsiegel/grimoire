import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import { pcProfileApi } from "../../api/campaign";
import {
  type CampaignCreateInput,
  type CharacterSummary,
  type GreetingSummary,
  type ImagePresetSummary,
  type MechanicsModuleSummary,
  type WorldSummary,
  type StyleGuideSummary,
  addCampaignPC,
  createCampaign,
  seedFirstScene,
  fetchGreetings,
  fetchImagePresets,
  fetchInstalledMechanics,
  fetchWorldCharacters,
  fetchWorlds,
  fetchStyleGuides,
} from "../../api/wizard";
import { useStore } from "../../state/useStore";
import { StepComposition } from "./StepComposition";
import { StepIdentity } from "./StepIdentity";
import { StepMechanics } from "./StepMechanics";
import { StepPCs } from "./StepPCs";
import { StepStartingScene } from "./StepStartingScene";
import { StepStyle } from "./StepStyle";
import { emptyDraft, type WizardDraft } from "./types";

const STEP_LABELS = [
  "Identity",
  "Composition",
  "Mechanics",
  "PCs",
  "Style & content",
  "Starting scene",
];

interface LoadState<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function CampaignCreate() {
  const navigate = useNavigate();
  const { state, dispatch } = useStore();

  const [draft, setDraft] = useState<WizardDraft>(() => emptyDraft());
  const [idEdited, setIdEdited] = useState(false);
  const [step, setStep] = useState(0);

  const [worlds, setWorlds] = useState<LoadState<WorldSummary[]>>({
    data: [],
    loading: true,
    error: null,
  });
  const [mechanics, setMechanics] = useState<LoadState<MechanicsModuleSummary[]>>({
    data: [],
    loading: true,
    error: null,
  });
  const [styleGuides, setStyleGuides] = useState<LoadState<StyleGuideSummary[]>>({
    data: [],
    loading: false,
    error: null,
  });
  const [imagePresets, setImagePresets] = useState<LoadState<ImagePresetSummary[]>>({
    data: [],
    loading: false,
    error: null,
  });
  const [castByWorld, setCastByWorld] = useState<Map<string, CharacterSummary[]>>(new Map());
  const [castLoading, setCastLoading] = useState(false);
  const [castError, setCastError] = useState<string | null>(null);
  const [greetings, setGreetings] = useState<LoadState<GreetingSummary[]>>({
    data: [],
    loading: false,
    error: null,
  });

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Each lazy fetch fires at most once per wizard session. An empty array or
  // a persistent error must not trigger an infinite refetch loop.
  const styleAssetsAttempted = useRef(false);

  // Initial fetches — worlds and mechanics are needed across multiple steps.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchWorlds();
        if (!cancelled) setWorlds({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) setWorlds({ data: [], loading: false, error: errorMessage(err) });
      }
    })();
    void (async () => {
      try {
        const data = await fetchInstalledMechanics();
        if (!cancelled) setMechanics({ data, loading: false, error: null });
      } catch (err) {
        if (!cancelled) setMechanics({ data: [], loading: false, error: errorMessage(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Style guides and image presets — lazy-load when arriving at step 5.
  useEffect(() => {
    if (step !== 4) return;
    if (styleAssetsAttempted.current) return;
    styleAssetsAttempted.current = true;
    setStyleGuides({ data: [], loading: true, error: null });
    setImagePresets({ data: [], loading: true, error: null });
    void (async () => {
      try {
        const [guides, presets] = await Promise.all([fetchStyleGuides(), fetchImagePresets()]);
        setStyleGuides({ data: guides, loading: false, error: null });
        setImagePresets({ data: presets, loading: false, error: null });
      } catch (err) {
        const msg = errorMessage(err);
        setStyleGuides((s) => ({ ...s, loading: false, error: msg }));
        setImagePresets((s) => ({ ...s, loading: false, error: msg }));
      }
    })();
  }, [step]);

  // Cast — refetch whenever the composition changes. Step 3 (PCs) and step 5
  // (Starting scene) both consume the list, so we hydrate as soon as either
  // is active. Once loaded for a given composition it persists across steps.
  useEffect(() => {
    if (step !== 3 && step !== 5) return;
    const worldIds = draft.worldRefs.map((r) => r.world_id);
    if (worldIds.length === 0) {
      setCastByWorld(new Map());
      return;
    }
    let cancelled = false;
    setCastLoading(true);
    setCastError(null);
    void (async () => {
      try {
        const entries = await Promise.all(
          worldIds.map(async (id) => [id, await fetchWorldCharacters(id)] as const),
        );
        if (!cancelled) {
          setCastByWorld(new Map(entries));
          setCastLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setCastError(errorMessage(err));
          setCastLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [step, draft.worldRefs]);

  // Greetings — lazy-load when arriving at the starting-scene step
  // (zero-indexed step 5; "Step 6" in the UI heading numbering).
  useEffect(() => {
    if (step !== 5) return;
    const worldIds = draft.worldRefs.map((r) => r.world_id);
    if (worldIds.length === 0) {
      setGreetings({ data: [], loading: false, error: null });
      return;
    }
    let cancelled = false;
    setGreetings({ data: [], loading: true, error: null });
    void (async () => {
      try {
        const lists = await Promise.all(worldIds.map((id) => fetchGreetings(id)));
        if (!cancelled) {
          setGreetings({ data: lists.flat(), loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setGreetings({ data: [], loading: false, error: errorMessage(err) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [step, draft.worldRefs]);

  const update = useCallback((patch: Partial<WizardDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
  }, []);

  const stepValid = useMemo(() => {
    switch (step) {
      case 0:
        return draft.id.length > 0 && draft.name.trim().length > 0;
      case 1:
        return draft.worldRefs.length > 0;
      case 2:
        return true;
      case 3:
        return draft.pcs.length > 0;
      case 4:
        if (draft.styleGuideMode === "library") return draft.styleGuideId !== null;
        return true;
      case 5:
        return true;
      default:
        return false;
    }
  }, [step, draft]);

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const composition = {
        worlds: draft.worldRefs.map((r) => ({
          world_id: r.world_id,
          priority: r.priority,
          include: r.include,
          track_latest: r.track_latest,
        })),
        mechanics: draft.mechanicsId,
        style_guide_id: draft.styleGuideMode === "library" ? draft.styleGuideId : null,
        image_preset_id: draft.imagePresetId,
        inline_style_guide:
          draft.styleGuideMode === "inline" && draft.inlineStyleGuide.trim().length > 0
            ? draft.inlineStyleGuide
            : null,
        content_boundaries:
          draft.contentBoundaries.trim().length > 0 ? draft.contentBoundaries : null,
      };
      const input: CampaignCreateInput = {
        id: draft.id,
        name: draft.name,
        description: draft.description.trim() || null,
        composition,
        greeting_id: draft.greetingId,
        tags: draft.tags.length > 0 ? draft.tags : null,
      };
      const created = await createCampaign(input);
      for (const pc of draft.pcs) {
        try {
          await addCampaignPC(draft.id, {
            character_ref: pc.character_ref,
            name: pc.name,
            owner: pc.owner,
          });
        } catch (err) {
          // Surface PC add errors but don't unwind the campaign — the user can
          // fix in the per-campaign view.
          console.warn(`Failed to add PC ${pc.character_ref}: ${errorMessage(err)}`);
        }
      }
      for (const pc of draft.pcs) {
        const hasProfile =
          pc.profileDescription.trim() ||
          pc.profileGoals.some((g) => g.trim()) ||
          pc.profilePlayerNotes.trim();
        if (hasProfile) {
          try {
            await pcProfileApi.save(draft.id, pc.character_ref, {
              description: pc.profileDescription,
              goals: pc.profileGoals.filter((g) => g.trim()),
              player_notes: pc.profilePlayerNotes,
            });
          } catch (err) {
            console.warn(`Failed to save profile for ${pc.character_ref}: ${errorMessage(err)}`);
          }
        }
      }
      if (draft.greetingId) {
        try {
          await seedFirstScene(draft.id);
        } catch (err) {
          // Same posture as PC add: don't block navigation. The campaign view
          // can re-seed if the first scene is still missing.
          console.warn(`Failed to seed opening scene: ${errorMessage(err)}`);
        }
      }
      // Append to the in-memory campaigns list rather than clearing it; the
      // StatusBar and any other consumer read from this list and the wizard
      // doesn't trigger a remount of CampaignsView on navigate.
      dispatch({
        type: "set-campaigns",
        campaigns: [...state.campaigns, { id: created.id, name: created.name }],
      });
      navigate(`/campaigns/${encodeURIComponent(draft.id)}`);
    } catch (err) {
      setSubmitError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const canGoBack = step > 0;
  const isLast = step === STEP_LABELS.length - 1;

  return (
    <section className="route wizard-route" aria-labelledby="wizard-heading" aria-busy={submitting}>
      <header>
        <h2 id="wizard-heading">New campaign</h2>
        <ol className="wizard-stepper" aria-label="Steps">
          {STEP_LABELS.map((label, i) => (
            <li
              key={label}
              className={i === step ? "current" : i < step ? "done" : "todo"}
              aria-current={i === step ? "step" : undefined}
            >
              <span className="wizard-step-index">{i + 1}</span>
              <span>{label}</span>
            </li>
          ))}
        </ol>
      </header>

      {step === 0 && (
        <StepIdentity draft={draft} update={update} idEdited={idEdited} setIdEdited={setIdEdited} />
      )}
      {step === 1 && (
        <StepComposition
          draft={draft}
          update={update}
          worlds={worlds.data}
          loading={worlds.loading}
          error={worlds.error}
        />
      )}
      {step === 2 && (
        <StepMechanics
          draft={draft}
          update={update}
          modules={mechanics.data}
          loading={mechanics.loading}
          error={mechanics.error}
        />
      )}
      {step === 3 && (
        <StepPCs
          draft={draft}
          update={update}
          candidates={castByWorld}
          loading={castLoading}
          error={castError}
        />
      )}
      {step === 4 && (
        <StepStyle
          draft={draft}
          update={update}
          styleGuides={styleGuides.data}
          imagePresets={imagePresets.data}
          loading={styleGuides.loading || imagePresets.loading}
          error={styleGuides.error ?? imagePresets.error}
        />
      )}
      {step === 5 && (
        <StepStartingScene
          draft={draft}
          update={update}
          greetings={greetings.data}
          loading={greetings.loading}
          error={greetings.error}
          castByWorld={castByWorld}
        />
      )}

      {submitError && (
        <p className="wizard-error" role="alert">
          {submitError}
        </p>
      )}

      <footer className="wizard-nav">
        <button type="button" onClick={() => navigate("/campaigns")} disabled={submitting}>
          Cancel
        </button>
        <div className="wizard-nav-right">
          <button
            type="button"
            disabled={!canGoBack || submitting}
            onClick={() => setStep((s) => Math.max(0, s - 1))}
          >
            Back
          </button>
          {!isLast ? (
            <button
              type="button"
              className="primary"
              disabled={!stepValid || submitting}
              onClick={() => setStep((s) => s + 1)}
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              className="primary"
              disabled={!stepValid || submitting || !draft.id || draft.pcs.length === 0}
              onClick={submit}
            >
              {submitting ? "Creating…" : "Create"}
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}
