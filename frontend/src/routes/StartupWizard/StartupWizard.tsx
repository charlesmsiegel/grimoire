/**
 * First-run setup wizard.
 *
 * Walks the user through wiring a language model, embedding model, and
 * image generator, and then hands off to the existing campaign-creation
 * wizard. Completion is persisted server-side via
 * ``POST /api/setup/status`` so a fresh browser on the same machine
 * doesn't re-run the flow.
 *
 * Each provider step is optional — the wizard explains what each kind
 * does, lets the user pick from installed plugins, configure secrets
 * inline (via the shared {@link SchemaField} renderer), and pick a
 * default model. "Skip" is always available.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  type PluginConfig,
  type PluginKind,
  type PluginManifest,
  pluginsApi,
} from "../../api/library";
import { setupApi } from "../../api/setup";
import type { GGUFInfo } from "../../components/FilePathPicker";
import { PluginModelPicker } from "../../components/PluginModelPicker";
import { SchemaField } from "../../components/SchemaField";
import { type JsonSchema, initialDraftFromSchema } from "../../components/schemaForm";

export interface StartupWizardProps {
  /** Called after completion or close; the parent persists the flag. */
  onClose: () => void;
  /** Title shown above the stepper. Override when launched from settings. */
  title?: string;
}

type StepId = "welcome" | "llm" | "embedding" | "imagegen" | "campaign";

type ProviderKind = Exclude<PluginKind, "export_adapter">;

interface StepDef {
  id: StepId;
  label: string;
  kind?: ProviderKind;
}

const STEPS: StepDef[] = [
  { id: "welcome", label: "Welcome" },
  { id: "llm", label: "Language model", kind: "llm_provider" },
  { id: "embedding", label: "Embeddings", kind: "embedding_provider" },
  { id: "imagegen", label: "Image generation", kind: "imagegen_backend" },
  { id: "campaign", label: "First campaign" },
];

const DEFAULT_KEYS: Record<ProviderKind, string> = {
  llm_provider: "grimoire.llm.default",
  embedding_provider: "grimoire.embedding.default",
  imagegen_backend: "grimoire.imagegen.default",
};

const KIND_COPY: Record<
  ProviderKind,
  { lead: string; detail: string; skipNote: string; icon: string }
> = {
  llm_provider: {
    icon: "✦",
    lead: "Pick a language model. Grimoire uses it to narrate scenes, voice NPCs, and summarize long histories.",
    detail:
      "Anything you've installed under ~/.grimoire/plugins shows up here. You can pick a hosted provider (Anthropic, OpenAI, OpenRouter, …) or a local backend (Ollama, llama.cpp). You'll be asked for an API key for hosted providers — the key is stored in the OS keyring when possible.",
    skipNote:
      "Without an LLM, Grimoire can still browse your library and edit settings, but narration and chat are disabled.",
  },
  embedding_provider: {
    icon: "❄",
    lead: "Pick an embedding model. Grimoire uses it to look up relevant lore, characters, and prior scenes when building prompts.",
    detail:
      "Embedding models turn text into vectors so the retrieval layer can find related material. A small local model (e.g., nomic-embed-text via Ollama) is usually plenty; hosted ones work too.",
    skipNote:
      "Without embeddings, context-building falls back to keyword matching. You can configure this later.",
  },
  imagegen_backend: {
    icon: "✺",
    lead: "Pick an image generator. Optional — used to illustrate scenes, locations, and characters.",
    detail:
      "Backends register through plugins (e.g., a local Stable Diffusion / ComfyUI bridge, or a hosted backend). Each one declares its own settings and supported models.",
    skipNote: "Skipping is fine — you can add an image backend later from Settings → Providers.",
  },
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function StartupWizard({ onClose, title = "Set up Grimoire" }: StartupWizardProps) {
  const navigate = useNavigate();
  const [stepIdx, setStepIdx] = useState(0);
  const [manifests, setManifests] = useState<PluginManifest[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await pluginsApi.listInstalled();
        if (!cancelled) setManifests(data);
      } catch (err) {
        if (!cancelled) setLoadError(errorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const step = STEPS[stepIdx]!;
  const isLast = stepIdx === STEPS.length - 1;
  const isFirst = stepIdx === 0;

  async function markComplete(): Promise<boolean> {
    setFinishing(true);
    setFinishError(null);
    try {
      await setupApi.setCompleted(true);
      return true;
    } catch (err) {
      setFinishError(errorMessage(err));
      return false;
    } finally {
      setFinishing(false);
    }
  }

  async function finishAndCreateCampaign() {
    if (await markComplete()) {
      onClose();
      navigate("/campaigns/new");
    }
  }

  async function finishAndExit() {
    if (await markComplete()) onClose();
  }

  return (
    <div
      className="modal-backdrop startup-wizard-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="startup-wizard-heading"
    >
      <div className="modal startup-wizard">
        <header className="startup-wizard-head">
          <h2 id="startup-wizard-heading">{title}</h2>
          <button
            type="button"
            className="startup-wizard-close"
            onClick={onClose}
            aria-label="Close setup"
            title="Close (resume later)"
          >
            ×
          </button>
        </header>

        <ol className="wizard-stepper startup-wizard-stepper" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li
              key={s.id}
              className={i === stepIdx ? "current" : i < stepIdx ? "done" : "todo"}
              aria-current={i === stepIdx ? "step" : undefined}
            >
              <span className="wizard-step-index">{i + 1}</span>
              <span>{s.label}</span>
            </li>
          ))}
        </ol>

        <div className="startup-wizard-body">
          {loadError && (
            <p className="wizard-error" role="alert">
              Couldn't load installed plugins: {loadError}
            </p>
          )}

          {step.id === "welcome" && <WelcomeStep />}

          {step.id !== "welcome" && step.id !== "campaign" && step.kind && (
            <ProviderStep
              key={step.kind}
              kind={step.kind}
              manifests={manifests}
              storageKey={DEFAULT_KEYS[step.kind]}
            />
          )}

          {step.id === "campaign" && <CampaignHandoffStep />}
        </div>

        {finishError && (
          <p className="wizard-error" role="alert">
            Couldn't save setup state: {finishError}
          </p>
        )}

        <footer className="wizard-nav startup-wizard-nav">
          <button
            type="button"
            onClick={onClose}
            disabled={finishing}
            className="startup-wizard-cancel"
          >
            Resume later
          </button>
          <div className="wizard-nav-right">
            <button
              type="button"
              disabled={isFirst || finishing}
              onClick={() => setStepIdx((s) => Math.max(0, s - 1))}
            >
              Back
            </button>
            {step.id !== "welcome" && step.id !== "campaign" && (
              <button
                type="button"
                onClick={() => setStepIdx((s) => s + 1)}
                disabled={finishing}
                className="startup-wizard-skip"
              >
                Skip this step
              </button>
            )}
            {!isLast ? (
              <button
                type="button"
                className="primary"
                onClick={() => setStepIdx((s) => s + 1)}
                disabled={finishing}
              >
                {step.id === "welcome" ? "Get started" : "Next"}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void finishAndExit()}
                  disabled={finishing}
                >
                  {finishing ? "Saving…" : "Finish later"}
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => void finishAndCreateCampaign()}
                  disabled={finishing}
                >
                  {finishing ? "Saving…" : "Create my first campaign"}
                </button>
              </>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function WelcomeStep() {
  return (
    <div className="wizard-step startup-welcome">
      <h3>Welcome to Grimoire.</h3>
      <p>
        Grimoire is a local-first RPG companion: it keeps your settings, characters, lore, and
        play history on your machine, then uses a language model to help you run sessions. This
        short setup wires the pieces it needs:
      </p>
      <ol className="startup-welcome-list">
        <li>
          <strong>A language model</strong> — narration, NPCs, and summaries. Hosted or local.
        </li>
        <li>
          <strong>An embedding model</strong> — used so prompts pull in the right lore.
          Optional but recommended.
        </li>
        <li>
          <strong>An image generator</strong> — optional; renders scene and character art.
        </li>
        <li>
          <strong>A first campaign</strong> — we'll hand off to the campaign creator.
        </li>
      </ol>
      <p className="wizard-meta">
        You can resume this wizard any time from <em>Settings → Providers → Run setup</em>.
        Nothing leaves your machine except calls you explicitly point at hosted providers.
      </p>
    </div>
  );
}

function CampaignHandoffStep() {
  return (
    <div className="wizard-step startup-handoff">
      <h3>Ready to play.</h3>
      <p>
        That's the substrate set up. Campaigns are where the work happens: you pick a setting
        from your library, compose mechanics, add your party of player characters, and choose
        an opening scene.
      </p>
      <p>
        The campaign creator is a separate, more detailed wizard. Choosing{" "}
        <strong>Create my first campaign</strong> will mark setup complete and take you there
        now. Choosing <strong>Finish later</strong> just marks setup complete — you can start
        a campaign from <em>Campaigns → New campaign</em> whenever you're ready.
      </p>
      <p className="wizard-meta">
        Tip: the library ships with example settings (try{" "}
        <code>~/.grimoire/library/settings</code>) so you have something to compose from on
        day one.
      </p>
    </div>
  );
}

interface ProviderStepProps {
  kind: ProviderKind;
  manifests: PluginManifest[] | null;
  storageKey: string;
}

function readDefault(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeDefault(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode / quota */
  }
}

function ProviderStep({ kind, manifests, storageKey }: ProviderStepProps) {
  const copy = KIND_COPY[kind];
  const candidates = useMemo(
    () => (manifests ?? []).filter((m) => m.implements.includes(kind)),
    [manifests, kind],
  );
  const [selectedId, setSelectedId] = useState<string>(() => {
    const stored = readDefault(storageKey);
    return stored;
  });

  // Clear a stored ID that no longer matches an installed plugin (uninstalled
  // or renamed since the last run). Without this the <select> sits blank and
  // the config card never renders.
  useEffect(() => {
    if (!manifests) return;
    if (selectedId && !candidates.some((m) => m.id === selectedId)) {
      setSelectedId("");
    }
  }, [manifests, candidates, selectedId]);

  // If nothing is stored and only one candidate exists, preselect it so the
  // user lands on the config form immediately.
  useEffect(() => {
    if (!selectedId && candidates.length === 1) {
      setSelectedId(candidates[0]!.id);
    }
  }, [candidates, selectedId]);

  useEffect(() => {
    if (selectedId) writeDefault(storageKey, selectedId);
  }, [selectedId, storageKey]);

  const manifest = candidates.find((m) => m.id === selectedId) ?? null;

  return (
    <div className="wizard-step startup-provider-step">
      <header className="startup-provider-head">
        <span className="provider-card-icon" aria-hidden="true">
          {copy.icon}
        </span>
        <div>
          <h3>{copy.lead}</h3>
          <p className="wizard-step-help">{copy.detail}</p>
        </div>
      </header>

      {manifests === null ? (
        <p className="wizard-meta">Loading installed plugins…</p>
      ) : candidates.length === 0 ? (
        <div className="startup-provider-empty">
          <p>No plugins of this kind are installed yet.</p>
          <p className="wizard-meta">
            Drop a plugin into <code>~/.grimoire/plugins</code> and rescan, or skip this step.{" "}
            {copy.skipNote}
          </p>
        </div>
      ) : (
        <>
          <label className="provider-combobox">
            <span className="provider-combobox-label">Provider</span>
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              aria-label={`Select ${kind} provider`}
            >
              <option value="">— Select a provider —</option>
              {candidates.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} {m.version ? `· v${m.version}` : ""}
                </option>
              ))}
            </select>
            <small>{copy.skipNote}</small>
          </label>

          {manifest && <ProviderConfigCard manifest={manifest} />}
        </>
      )}
    </div>
  );
}

function ProviderConfigCard({ manifest }: { manifest: PluginManifest }) {
  const properties = useMemo(() => {
    const schema = manifest.config_schema as JsonSchema | undefined;
    return (schema?.properties ?? {}) as Record<string, JsonSchema>;
  }, [manifest]);
  const required = useMemo(() => {
    const schema = manifest.config_schema as JsonSchema | undefined;
    return new Set((schema?.required ?? []) as string[]);
  }, [manifest]);
  const propertyKeys = useMemo(() => Object.keys(properties), [properties]);

  // Track which schema field is annotated as the "active model" picker; we
  // hide it from the bulk form until secrets are saved so users don't try to
  // pick a model before the catalog can load.
  const modelFieldName = useMemo(() => {
    for (const [k, v] of Object.entries(properties)) {
      if (v && v["x-source"] === "models") return k;
    }
    return null;
  }, [properties]);

  const [draft, setDraft] = useState<Record<string, unknown>>(() =>
    initialDraftFromSchema(properties),
  );
  const [config, setConfig] = useState<PluginConfig | null>(null);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingConfig(true);
    setErr(null);
    void (async () => {
      try {
        const cfg = await pluginsApi.getConfig(manifest.id);
        if (cancelled) return;
        setConfig(cfg);
        // Hydrate the draft with existing non-secret values so the user sees
        // what's already saved.
        setDraft({ ...initialDraftFromSchema(properties), ...cfg.values });
      } catch (e) {
        if (!cancelled) setErr(errorMessage(e));
      } finally {
        if (!cancelled) setLoadingConfig(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [manifest.id, properties]);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      await pluginsApi.configure(manifest.id, draft);
      const cfg = await pluginsApi.getConfig(manifest.id);
      setConfig(cfg);
      setSavedAt(Date.now());
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function saveModel(next: string) {
    if (!modelFieldName) return;
    setSaving(true);
    setErr(null);
    try {
      await pluginsApi.patchConfig(manifest.id, { [modelFieldName]: next });
      const cfg = await pluginsApi.getConfig(manifest.id);
      setConfig(cfg);
      setDraft((d) => ({ ...d, [modelFieldName]: next }));
      setSavedAt(Date.now());
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  const handleGGUFIntrospect = useCallback(
    (info: GGUFInfo) => {
      setDraft((d) => {
        const next = { ...d };
        if (info.context_length != null && !d["n_ctx"]) next["n_ctx"] = info.context_length;
        if (info.name && !d["model_id"]) next["model_id"] = info.name;
        return next;
      });
    },
    [],
  );

  if (propertyKeys.length === 0) {
    return (
      <div className="startup-provider-config">
        <p className="wizard-meta">This plugin declares no configuration. You're set.</p>
      </div>
    );
  }

  const configured = config?.configured ?? false;
  const currentModel =
    modelFieldName && typeof draft[modelFieldName] === "string"
      ? (draft[modelFieldName] as string)
      : "";

  return (
    <div className="startup-provider-config">
      <header className="startup-provider-config-head">
        <h4>{manifest.name} settings</h4>
        <ProviderStatusBadge configured={configured} loading={loadingConfig} />
      </header>

      {manifest.description && <p className="provider-card-sub">{manifest.description}</p>}

      <form
        className="library-form startup-provider-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {propertyKeys
          .filter((key) => key !== modelFieldName)
          .map((key) => (
            <SchemaField
              key={key}
              pluginId={manifest.id}
              name={key}
              schema={properties[key] ?? {}}
              required={required.has(key)}
              value={draft[key]}
              onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))}
              onFileIntrospect={handleGGUFIntrospect}
            />
          ))}
        <div className="startup-provider-form-actions">
          <button type="submit" className="primary" disabled={saving || loadingConfig}>
            {saving ? "Saving…" : configured ? "Update connection" : "Save connection"}
          </button>
          {savedAt !== null && !saving && (
            <small className="library-ok" role="status">
              Saved.
            </small>
          )}
        </div>
      </form>

      {modelFieldName && configured && (
        <section className="startup-provider-model" aria-label="Default model">
          <PluginModelPicker
            pluginId={manifest.id}
            label={properties[modelFieldName]?.title ?? "Default model"}
            description={
              properties[modelFieldName]?.description ??
              "Used by default when nothing else is specified."
            }
            value={currentModel}
            onChange={(next) => void saveModel(next)}
          />
        </section>
      )}

      {modelFieldName && !configured && (
        <p className="wizard-meta">Save the connection first to pick a default model.</p>
      )}

      {err && (
        <p className="wizard-error" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}

function ProviderStatusBadge({
  configured,
  loading,
}: {
  configured: boolean;
  loading: boolean;
}) {
  if (loading) return <span className="provider-status provider-status-idle">Checking…</span>;
  if (configured)
    return <span className="provider-status provider-status-ok">Connected</span>;
  return <span className="provider-status provider-status-idle">Not configured</span>;
}
