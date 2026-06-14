import { useCallback, useEffect, useState } from "react";

import { type PluginManifest, pluginsApi } from "../../api/library";
import { configApi } from "../../api/config";
import { type PluginSummary, fetchInstalledPlugins } from "../../api/wizard";
import { useResource } from "../../api/useResource";
import { errorMessage } from "./shared";
import { ProviderCard, type ModelSlot } from "./ProviderCard";
import { EmbeddingsIcon, ImageIcon, SparkleIcon } from "../../components/icons";

const LLM_SLOTS: ModelSlot[] = [
  { key: "heavy", label: "Heavy model", sublabel: "Generation — narration, summaries, rewrites" },
  {
    key: "light",
    label: "Light model",
    sublabel: "Classification — drift checks, validation, NPC ticks",
  },
];

const EMBED_SLOTS: ModelSlot[] = [{ key: "route", label: "Embedding model", clearable: true }];

const IMAGEGEN_SLOTS: ModelSlot[] = [];

async function fetchPluginCatalog(): Promise<{
  plugins: PluginSummary[];
  manifests: PluginManifest[];
}> {
  const [plugins, manifests] = await Promise.all([
    fetchInstalledPlugins(),
    pluginsApi.listInstalled(),
  ]);
  return { plugins, manifests };
}

export function ProvidersTab() {
  // Primary catalog load (gates loading/error); model defaults are best-effort
  // and tracked separately because they feed optimistic-update state below.
  const {
    data: catalog,
    error: catalogError,
    loading,
  } = useResource(useCallback(() => fetchPluginCatalog(), []));
  const [defaultsError, setDefaultsError] = useState<string | null>(null);
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

  const plugins = catalog?.plugins ?? [];
  const manifests = catalog?.manifests ?? [];
  const error = defaultsError ?? (catalogError ? errorMessage(catalogError) : null);

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
        setDefaultsError(errorMessage(err));
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
        setDefaultsError(errorMessage(err));
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
        setDefaultsError(errorMessage(err));
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
        icon={<SparkleIcon />}
        plugins={llmPlugins}
        manifests={manifests}
        modelSlots={LLM_SLOTS}
        defaults={{ heavy: llmDefaults.heavy, light: llmDefaults.light }}
        onDefaultChange={onLlmChange}
        loading={loading}
      />

      <ProviderCard
        title="Embeddings"
        icon={<EmbeddingsIcon />}
        plugins={embedPlugins}
        manifests={manifests}
        modelSlots={EMBED_SLOTS}
        defaults={{ route: embedDefaults.route }}
        onDefaultChange={onEmbedChange}
        loading={loading}
      />

      <ProviderCard
        title="Image Generation"
        icon={<ImageIcon />}
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
