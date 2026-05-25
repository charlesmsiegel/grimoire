import { useCallback, useEffect, useState } from "react";

import { type PluginManifest, pluginsApi } from "../../api/library";
import { configApi } from "../../api/config";
import { type PluginSummary, fetchInstalledPlugins } from "../../api/wizard";
import { errorMessage } from "./shared";
import { ProviderCard, type ModelSlot } from "./ProviderCard";

const LLM_SLOTS: ModelSlot[] = [
  { key: "heavy", label: "Heavy model", sublabel: "Generation — narration, summaries, rewrites" },
  {
    key: "light",
    label: "Light model",
    sublabel: "Classification — drift checks, validation, NPC ticks",
  },
];

const EMBED_SLOTS: ModelSlot[] = [
  { key: "route", label: "Embedding model", clearable: true },
];

const IMAGEGEN_SLOTS: ModelSlot[] = [];

export function ProvidersTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [manifests, setManifests] = useState<PluginManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [llmDefaults, setLlmDefaults] = useState<{ heavy: string; light: string }>({
    heavy: "",
    light: "",
  });
  const [embedDefaults, setEmbedDefaults] = useState<{ route: string | null }>({ route: null });
  const [imagegenDefaults, setImagegenDefaults] = useState<{ backend: string | null }>({
    backend: null,
  });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [data, full] = await Promise.all([
          fetchInstalledPlugins(),
          pluginsApi.listInstalled(),
        ]);
        if (!cancelled) {
          setPlugins(data);
          setManifests(full);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setLoading(false);
        }
      }
      try {
        const llm = await configApi.getLLMDefaults();
        if (!cancelled) setLlmDefaults(llm);
      } catch {
        /* best-effort */
      }
      try {
        const embed = await configApi.getEmbeddingDefaults();
        if (!cancelled) setEmbedDefaults(embed);
      } catch {
        /* best-effort */
      }
      try {
        const img = await configApi.getImagegenDefaults();
        if (!cancelled) setImagegenDefaults(img);
      } catch {
        /* best-effort */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const llmPlugins = plugins.filter((p) => p.kind === "llm_provider");
  const embedPlugins = plugins.filter((p) => p.kind === "embedding_provider");
  const imageBackends = plugins.filter((p) => p.kind === "imagegen_backend");

  const onLlmChange = useCallback(
    async (slot: string, modelId: string | null, providerId: string) => {
      const route = modelId ? `${providerId}.${modelId}` : "";
      const prev = { ...llmDefaults };
      const next = { ...llmDefaults, [slot]: route };
      setLlmDefaults(next);
      try {
        await configApi.setLLMDefaults(next);
      } catch (err) {
        setLlmDefaults(prev);
        setError(errorMessage(err));
      }
    },
    [llmDefaults],
  );

  const onEmbedChange = useCallback(
    async (_slot: string, modelId: string | null, providerId: string) => {
      const route = modelId ? `${providerId}.${modelId}` : null;
      const prev = { ...embedDefaults };
      setEmbedDefaults({ route });
      try {
        await configApi.patchEmbeddingDefaults({ route });
      } catch (err) {
        setEmbedDefaults(prev);
        setError(errorMessage(err));
      }
    },
    [embedDefaults],
  );

  const onImagegenProviderChange = useCallback(
    async (providerId: string | null) => {
      const prev = { ...imagegenDefaults };
      setImagegenDefaults({ backend: providerId });
      try {
        await configApi.patchImagegenDefaults({ backend: providerId });
      } catch (err) {
        setImagegenDefaults(prev);
        setError(errorMessage(err));
      }
    },
    [imagegenDefaults],
  );

  return (
    <div className="settings-form providers-form">
      <div className="providers-wizard-launch">
        <div>
          <strong>Setup wizard</strong>
          <p className="provider-card-sub">
            Re-run the first-run wizard to walk through language model, embeddings, image
            generation, and a starter campaign.
          </p>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => window.dispatchEvent(new Event("grimoire:open-startup-wizard"))}
        >
          Run setup wizard
        </button>
      </div>

      {loading && <p className="wizard-meta">Loading providers…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <ProviderCard
        title="Language Models"
        icon="✦"
        plugins={llmPlugins}
        manifests={manifests}
        modelSlots={LLM_SLOTS}
        defaults={{ heavy: llmDefaults.heavy, light: llmDefaults.light }}
        onDefaultChange={onLlmChange}
        loading={loading}
      />

      <ProviderCard
        title="Embeddings"
        icon="⊕"
        plugins={embedPlugins}
        manifests={manifests}
        modelSlots={EMBED_SLOTS}
        defaults={{ route: embedDefaults.route }}
        onDefaultChange={onEmbedChange}
        loading={loading}
      />

      <ProviderCard
        title="Image Generation"
        icon="◎"
        plugins={imageBackends}
        manifests={manifests}
        modelSlots={IMAGEGEN_SLOTS}
        defaults={{ backend: imagegenDefaults.backend }}
        onDefaultChange={() => {}}
        onProviderChange={onImagegenProviderChange}
        loading={loading}
      />
    </div>
  );
}
