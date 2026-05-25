# Unified Providers Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Providers tab (one LLM card + link lists) with three equal provider cards (LLM, Embedding, ImageGen), add Heavy/Light model pickers to LLM, and remove the LLM Defaults tab.

**Architecture:** The existing ProvidersTab is rewritten with a shared `ProviderCard` component rendered three times. Backend adds two small endpoints for embedding and imagegen defaults. Campaign RoutingTab shows inherited app defaults when no override is set.

**Tech Stack:** React/TypeScript frontend, FastAPI/Python backend, YAML config files

---

### Task 1: Backend — Embedding defaults endpoint

**Files:**
- Modify: `backend/src/grimoire/api/config.py`
- Test: `backend/tests/api/test_config_embedding_defaults.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_config_embedding_defaults.py`:

```python
"""Tests for GET/PATCH /api/config/embedding-defaults."""

import pytest


@pytest.mark.asyncio
async def test_get_embedding_defaults_empty(client):
    resp = await client.get("/api/config/embedding-defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] is None


@pytest.mark.asyncio
async def test_patch_embedding_defaults(client):
    resp = await client.patch(
        "/api/config/embedding-defaults",
        json={"route": "embed-openrouter.openai/text-embedding-3-small"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "embed-openrouter.openai/text-embedding-3-small"

    # Verify it persists on re-read
    resp2 = await client.get("/api/config/embedding-defaults")
    assert resp2.json()["route"] == "embed-openrouter.openai/text-embedding-3-small"


@pytest.mark.asyncio
async def test_patch_embedding_defaults_clear(client):
    await client.patch(
        "/api/config/embedding-defaults",
        json={"route": "embed-openrouter.openai/text-embedding-3-small"},
    )
    resp = await client.patch(
        "/api/config/embedding-defaults",
        json={"route": None},
    )
    assert resp.status_code == 200
    assert resp.json()["route"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_config_embedding_defaults.py -v`
Expected: FAIL (endpoint doesn't exist yet)

- [ ] **Step 3: Implement the endpoint**

Add to `backend/src/grimoire/api/config.py`, after the existing `patch_library_settings` endpoint:

```python
class EmbeddingDefaultsPatch(BaseModel):
    route: str | None = None


@router.get("/embedding-defaults")
async def get_embedding_defaults() -> Any:
    raw = _read_yaml_safe("state_store.yaml")
    lib = raw.get("library") if isinstance(raw.get("library"), dict) else {}
    route = lib.get("embedding_provider") or None
    return {"route": route}


@router.patch("/embedding-defaults")
async def patch_embedding_defaults(payload: EmbeddingDefaultsPatch) -> Any:
    raw = _read_yaml_safe("state_store.yaml")
    lib = raw.get("library") if isinstance(raw.get("library"), dict) else {}
    if payload.route is not None:
        lib["embedding_provider"] = payload.route
    else:
        lib.pop("embedding_provider", None)
    raw["library"] = lib
    try:
        _write_yaml_safe("state_store.yaml", raw)
    except Exception as exc:
        logger.exception("state_store.yaml write failed")
        raise HTTPException(
            status_code=500, detail=f"failed to persist: {exc}"
        ) from exc
    return await get_embedding_defaults()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_config_embedding_defaults.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/grimoire/api/config.py tests/api/test_config_embedding_defaults.py
git add backend/src/grimoire/api/config.py backend/tests/api/test_config_embedding_defaults.py
git commit -m "feat(api): add embedding-defaults GET/PATCH endpoints"
```

---

### Task 2: Backend — ImageGen defaults endpoint

**Files:**
- Modify: `backend/src/grimoire/api/config.py`
- Test: `backend/tests/api/test_config_imagegen_defaults.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_config_imagegen_defaults.py`:

```python
"""Tests for GET/PATCH /api/config/imagegen-defaults."""

import pytest


@pytest.mark.asyncio
async def test_get_imagegen_defaults_empty(client):
    resp = await client.get("/api/config/imagegen-defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] is None


@pytest.mark.asyncio
async def test_patch_imagegen_defaults(client):
    resp = await client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": "imagegen-a1111"},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] == "imagegen-a1111"

    resp2 = await client.get("/api/config/imagegen-defaults")
    assert resp2.json()["backend"] == "imagegen-a1111"


@pytest.mark.asyncio
async def test_patch_imagegen_defaults_clear(client):
    await client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": "imagegen-a1111"},
    )
    resp = await client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": None},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_config_imagegen_defaults.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the endpoint**

Add to `backend/src/grimoire/api/config.py`:

```python
class ImagegenDefaultsPatch(BaseModel):
    backend: str | None = None


@router.get("/imagegen-defaults")
async def get_imagegen_defaults() -> Any:
    raw = _read_app_yaml()
    block = raw.get("imagegen_defaults") if isinstance(raw.get("imagegen_defaults"), dict) else {}
    return {"backend": block.get("backend") or None}


@router.patch("/imagegen-defaults")
async def patch_imagegen_defaults(payload: ImagegenDefaultsPatch) -> Any:
    raw = _read_app_yaml()
    block = raw.get("imagegen_defaults") if isinstance(raw.get("imagegen_defaults"), dict) else {}
    if payload.backend is not None:
        block["backend"] = payload.backend
    else:
        block.pop("backend", None)
    raw["imagegen_defaults"] = block
    try:
        write_yaml(_app_yaml_path(), raw)
    except Exception as exc:
        logger.exception("app.yaml write failed")
        raise HTTPException(
            status_code=500, detail=f"failed to persist: {exc}"
        ) from exc
    return await get_imagegen_defaults()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_config_imagegen_defaults.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/grimoire/api/config.py tests/api/test_config_imagegen_defaults.py
git add backend/src/grimoire/api/config.py backend/tests/api/test_config_imagegen_defaults.py
git commit -m "feat(api): add imagegen-defaults GET/PATCH endpoints"
```

---

### Task 3: Frontend — Add config API functions for new endpoints

**Files:**
- Modify: `frontend/src/api/config.ts`

- [ ] **Step 1: Add the API functions**

Edit `frontend/src/api/config.ts` to add embedding and imagegen defaults alongside the existing LLM defaults:

```typescript
/**
 * Application configuration REST client.
 *
 * Wraps endpoints for managing app-level defaults and settings,
 * including LLM tier configuration, embedding, and imagegen defaults.
 */

import { api } from "./client";

export interface LLMDefaults {
  heavy: string;
  light: string;
}

export interface EmbeddingDefaults {
  route: string | null;
}

export interface ImagegenDefaults {
  backend: string | null;
}

export const configApi = {
  getLLMDefaults: () => api.get<LLMDefaults>("/api/config/llm-defaults"),

  setLLMDefaults: (body: LLMDefaults) =>
    api.put<LLMDefaults>("/api/config/llm-defaults", body),

  getEmbeddingDefaults: () =>
    api.get<EmbeddingDefaults>("/api/config/embedding-defaults"),

  patchEmbeddingDefaults: (body: Partial<EmbeddingDefaults>) =>
    api.patch<EmbeddingDefaults>("/api/config/embedding-defaults", body),

  getImagegenDefaults: () =>
    api.get<ImagegenDefaults>("/api/config/imagegen-defaults"),

  patchImagegenDefaults: (body: Partial<ImagegenDefaults>) =>
    api.patch<ImagegenDefaults>("/api/config/imagegen-defaults", body),
};
```

- [ ] **Step 2: Lint and commit**

```bash
npx eslint src/api/config.ts
git add frontend/src/api/config.ts
git commit -m "feat(api): add config API functions for embedding and imagegen defaults"
```

---

### Task 4: Frontend — Create ProviderCard component

**Files:**
- Create: `frontend/src/routes/appsettings/ProviderCard.tsx`

- [ ] **Step 1: Create the ProviderCard component**

Create `frontend/src/routes/appsettings/ProviderCard.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  type PluginConfig,
  type PluginManifest,
  pluginsApi,
} from "../../api/library";
import { type PluginSummary } from "../../api/wizard";
import { PluginModelPicker } from "../../components/PluginModelPicker";

export interface ModelSlot {
  key: string;
  label: string;
  sublabel?: string;
}

interface Props {
  title: string;
  icon: string;
  plugins: PluginSummary[];
  manifests: PluginManifest[];
  modelSlots: ModelSlot[];
  defaults: Record<string, string | null>;
  onDefaultChange: (slotKey: string, route: string | null) => void;
  loading?: boolean;
}

function StatusBadge({
  plugin,
  configured,
  loading,
}: {
  plugin: PluginSummary | undefined;
  configured: boolean;
  loading?: boolean;
}) {
  if (!plugin) {
    return <span className="provider-status provider-status-idle">No provider</span>;
  }
  if (plugin.load_error) {
    return <span className="provider-status provider-status-error">Error</span>;
  }
  if (loading) {
    return <span className="provider-status provider-status-idle">Checking…</span>;
  }
  if (configured) {
    return <span className="provider-status provider-status-ok">Connected</span>;
  }
  return <span className="provider-status provider-status-idle">Not configured</span>;
}

function findModelPathValue(config: PluginConfig | null, manifest: PluginManifest | undefined): string | null {
  if (!config || !manifest) return null;
  const props = (manifest.config_schema as { properties?: Record<string, Record<string, unknown>> } | undefined)?.properties;
  if (!props || !props["model_path"]) return null;
  const val = config.values["model_path"];
  return typeof val === "string" ? val : null;
}

export function ProviderCard({
  title,
  icon,
  plugins,
  manifests,
  modelSlots,
  defaults,
  onDefaultChange,
  loading: parentLoading,
}: Props) {
  const [selectedId, setSelectedId] = useState<string>("");
  const [config, setConfig] = useState<PluginConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(false);

  // Auto-select the first plugin if only one is installed
  useEffect(() => {
    if (!selectedId && plugins.length === 1) {
      setSelectedId(plugins[0]!.id);
    }
  }, [plugins, selectedId]);

  // Auto-select the plugin that matches the first default route's provider prefix
  useEffect(() => {
    if (selectedId || plugins.length === 0) return;
    const firstDefault = Object.values(defaults).find((v) => v != null);
    if (!firstDefault) return;
    const providerPart = firstDefault.split(".")[0];
    const match = plugins.find((p) => p.id === providerPart);
    if (match) setSelectedId(match.id);
  }, [plugins, defaults, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setConfig(null);
      return;
    }
    let cancelled = false;
    setConfigLoading(true);
    void (async () => {
      try {
        const cfg = await pluginsApi.getConfig(selectedId);
        if (!cancelled) setConfig(cfg);
      } catch {
        if (!cancelled) setConfig(null);
      } finally {
        if (!cancelled) setConfigLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedId]);

  const activePlugin = plugins.find((p) => p.id === selectedId);
  const activeManifest = manifests.find((m) => m.id === selectedId);
  const modelPath = findModelPathValue(config, activeManifest);

  return (
    <section className="provider-card provider-card-primary">
      <header className="provider-card-head">
        <div className="provider-card-title">
          <span className="provider-card-icon" aria-hidden="true">{icon}</span>
          <h3>{title}</h3>
        </div>
        <StatusBadge plugin={activePlugin} configured={Boolean(config?.configured)} loading={configLoading} />
      </header>

      <label className="provider-combobox">
        <span className="provider-combobox-label">Provider</span>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          disabled={parentLoading || plugins.length === 0}
        >
          <option value="">
            {plugins.length === 0 ? "None installed" : "— Select a provider —"}
          </option>
          {plugins.map((p) => (
            <option key={p.id} value={p.id} disabled={Boolean(p.load_error)}>
              {(p.name ?? p.id) + (p.version ? `  ·  v${p.version}` : "")}
              {p.load_error ? "  ·  load error" : ""}
            </option>
          ))}
        </select>
      </label>

      {activePlugin?.load_error && (
        <p className="wizard-error" role="alert">Load error: {activePlugin.load_error}</p>
      )}

      {activePlugin && config?.configured && modelSlots.map((slot) => (
        <section key={slot.key} className="provider-model-picker">
          <PluginModelPicker
            pluginId={activePlugin.id}
            label={slot.label}
            description={slot.sublabel}
            value={defaults[slot.key] ?? ""}
            onChange={(next) => onDefaultChange(slot.key, next || null)}
          />
        </section>
      ))}

      {activePlugin && config && !config.configured && (
        <p className="wizard-meta">
          Configure your API key first.{" "}
          <Link to={`/library/plugins/${encodeURIComponent(activePlugin.id)}`}>Open settings</Link>
        </p>
      )}

      {modelPath && (
        <div className="provider-model-path">
          <span className="provider-model-path-label">Model file</span>
          <code>{modelPath}</code>
        </div>
      )}

      <div className="provider-card-actions">
        {activePlugin ? (
          <Link to={`/library/plugins/${encodeURIComponent(activePlugin.id)}`} className="button-link primary">
            Configure {activePlugin.name ?? activePlugin.id}
          </Link>
        ) : (
          <span className="provider-card-hint">Select a provider to get started.</span>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Lint and commit**

```bash
npx eslint src/routes/appsettings/ProviderCard.tsx
git add frontend/src/routes/appsettings/ProviderCard.tsx
git commit -m "feat(ui): create shared ProviderCard component for all provider types"
```

---

### Task 5: Frontend — Rewrite ProvidersTab with three cards

**Files:**
- Modify: `frontend/src/routes/appsettings/ProvidersTab.tsx`

- [ ] **Step 1: Rewrite ProvidersTab**

Replace the entire contents of `frontend/src/routes/appsettings/ProvidersTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";

import { type PluginManifest, pluginsApi } from "../../api/library";
import { configApi } from "../../api/config";
import { type PluginSummary, fetchInstalledPlugins } from "../../api/wizard";
import { errorMessage } from "./shared";
import { ProviderCard, type ModelSlot } from "./ProviderCard";

const LLM_SLOTS: ModelSlot[] = [
  { key: "heavy", label: "Heavy model", sublabel: "Generation — narration, summaries, rewrites" },
  { key: "light", label: "Light model", sublabel: "Classification — drift checks, validation, NPC ticks" },
];

const EMBED_SLOTS: ModelSlot[] = [
  { key: "route", label: "Embedding model" },
];

const IMAGEGEN_SLOTS: ModelSlot[] = [
  { key: "backend", label: "Image model" },
];

export function ProvidersTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [manifests, setManifests] = useState<PluginManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [llmDefaults, setLlmDefaults] = useState<{ heavy: string; light: string }>({ heavy: "", light: "" });
  const [embedDefaults, setEmbedDefaults] = useState<{ route: string | null }>({ route: null });
  const [imagegenDefaults, setImagegenDefaults] = useState<{ backend: string | null }>({ backend: null });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [data, full, llm, embed, img] = await Promise.all([
          fetchInstalledPlugins(),
          pluginsApi.listInstalled(),
          configApi.getLLMDefaults(),
          configApi.getEmbeddingDefaults(),
          configApi.getImagegenDefaults(),
        ]);
        if (!cancelled) {
          setPlugins(data);
          setManifests(full);
          setLlmDefaults(llm);
          setEmbedDefaults(embed);
          setImagegenDefaults(img);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const llmPlugins = plugins.filter((p) => p.kind === "llm_provider");
  const embedPlugins = plugins.filter((p) => p.kind === "embedding_provider");
  const imageBackends = plugins.filter((p) => p.kind === "imagegen_backend");

  const onLlmChange = useCallback(async (slot: string, value: string | null) => {
    const next = { ...llmDefaults, [slot]: value ?? "" };
    setLlmDefaults(next);
    try {
      await configApi.setLLMDefaults(next);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [llmDefaults]);

  const onEmbedChange = useCallback(async (_slot: string, value: string | null) => {
    setEmbedDefaults({ route: value });
    try {
      await configApi.patchEmbeddingDefaults({ route: value });
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const onImagegenChange = useCallback(async (_slot: string, value: string | null) => {
    setImagegenDefaults({ backend: value });
    try {
      await configApi.patchImagegenDefaults({ backend: value });
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

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
        onDefaultChange={onImagegenChange}
        loading={loading}
      />
    </div>
  );
}
```

- [ ] **Step 2: Lint and commit**

```bash
npx eslint src/routes/appsettings/ProvidersTab.tsx
git add frontend/src/routes/appsettings/ProvidersTab.tsx
git commit -m "feat(ui): rewrite ProvidersTab with three equal ProviderCard instances"
```

---

### Task 6: Frontend — Remove LLMDefaultsTab

**Files:**
- Delete: `frontend/src/routes/appsettings/LLMDefaultsTab.tsx`
- Modify: `frontend/src/routes/appsettings/AppSettings.tsx`

- [ ] **Step 1: Update AppSettings.tsx to remove the tab**

Edit `frontend/src/routes/appsettings/AppSettings.tsx`:

1. Remove the import: `import { LLMDefaultsTab } from "./LLMDefaultsTab";`
2. Remove `"llm-defaults"` from the `Tab` union type
3. Remove `{ id: "llm-defaults", label: "LLM defaults" }` from the `TABS` array
4. Remove `{tab === "llm-defaults" && <LLMDefaultsTab />}` from the tab panel

The file should become:

```tsx
import { useState } from "react";

import { AppearanceTab } from "./AppearanceTab";
import { BackupTab } from "./BackupTab";
import { LibraryTab } from "./LibraryTab";
import { MechanicsTab } from "./MechanicsTab";
import { PluginsTab } from "./PluginsTab";
import { ProvidersTab } from "./ProvidersTab";
import { TemplatesTab } from "./TemplatesTab";

type Tab =
  | "library"
  | "providers"
  | "templates"
  | "mechanics"
  | "plugins"
  | "backup"
  | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "providers", label: "Providers" },
  { id: "templates", label: "Prompts" },
  { id: "mechanics", label: "Mechanics" },
  { id: "plugins", label: "Plugins" },
  { id: "backup", label: "Backup" },
  { id: "appearance", label: "Appearance" },
];

export function AppSettings() {
  const [tab, setTab] = useState<Tab>("library");
  return (
    <section className="route app-settings" aria-labelledby="app-settings-heading">
      <header>
        <h2 id="app-settings-heading">Settings</h2>
      </header>
      <nav className="tab-bar" aria-label="App settings tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="tab-panel">
        {tab === "library" && <LibraryTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "templates" && <TemplatesTab />}
        {tab === "mechanics" && <MechanicsTab />}
        {tab === "plugins" && <PluginsTab />}
        {tab === "backup" && <BackupTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Delete LLMDefaultsTab.tsx**

```bash
rm frontend/src/routes/appsettings/LLMDefaultsTab.tsx
```

- [ ] **Step 3: Verify no remaining imports**

```bash
grep -r "LLMDefaultsTab\|llm-defaults" frontend/src/ --include="*.ts" --include="*.tsx"
```

Expected: No matches (the config API endpoint name `llm-defaults` is fine — it's the tab import/reference we're clearing).

- [ ] **Step 4: Lint and commit**

```bash
npx eslint src/routes/appsettings/AppSettings.tsx
git add frontend/src/routes/appsettings/AppSettings.tsx
git rm frontend/src/routes/appsettings/LLMDefaultsTab.tsx
git commit -m "refactor(ui): remove LLMDefaultsTab, absorbed into Providers tab"
```

---

### Task 7: Frontend — Update campaign RoutingTab to show inherited defaults

**Files:**
- Modify: `frontend/src/routes/campaign/settings/RoutingTab.tsx`

- [ ] **Step 1: Add app-defaults fetch to RoutingTab**

Edit `frontend/src/routes/campaign/settings/RoutingTab.tsx`. In the `RoutingTab` component (around line 224), add a state variable and effect to fetch app defaults, then use them as placeholder text:

Add import at the top of the file:

```typescript
import { configApi } from "../../../api/config";
```

Inside the `RoutingTab` component, after the existing `useAutoSavedResource` call, add:

```typescript
const [appDefaults, setAppDefaults] = useState<{
  heavy: string;
  light: string;
  embedding: string | null;
}>({ heavy: "", light: "", embedding: null });

useEffect(() => {
  let cancelled = false;
  void (async () => {
    try {
      const [llm, embed] = await Promise.all([
        configApi.getLLMDefaults(),
        configApi.getEmbeddingDefaults(),
      ]);
      if (!cancelled) {
        setAppDefaults({
          heavy: llm.heavy,
          light: llm.light,
          embedding: embed.route,
        });
      }
    } catch {
      // Best-effort: placeholders degrade to generic text
    }
  })();
  return () => { cancelled = true; };
}, []);
```

Then update the three `<input>` placeholder attributes:
- Heavy: `placeholder={appDefaults.heavy ? \`App default: ${appDefaults.heavy}\` : "e.g. deepseek.deepseek-v4-pro"}`
- Light: `placeholder={appDefaults.light ? \`App default: ${appDefaults.light}\` : "e.g. deepseek.deepseek-v4-flash"}`
- Embedding: `placeholder={appDefaults.embedding ? \`App default: ${appDefaults.embedding}\` : "e.g. voyage.voyage-3"}`

- [ ] **Step 2: Lint and commit**

```bash
npx eslint src/routes/campaign/settings/RoutingTab.tsx
git add frontend/src/routes/campaign/settings/RoutingTab.tsx
git commit -m "feat(ui): show inherited app defaults in campaign routing placeholders"
```

---

### Task 8: Smoke test and final verification

**Files:** None (verification only)

- [ ] **Step 1: Run all backend tests**

```bash
uv run pytest tests/ -x -q
```

Expected: All pass

- [ ] **Step 2: Run frontend type check**

```bash
npx tsc --noEmit
```

Expected: No errors

- [ ] **Step 3: Run frontend lint**

```bash
npx eslint src/
```

Expected: Clean

- [ ] **Step 4: Manual browser verification**

Start the app and verify:
1. Settings → Providers shows three cards (Language Models, Embeddings, Image Generation)
2. LLM card has stacked Heavy/Light model pickers
3. Embedding card has provider dropdown + model picker
4. ImageGen card has provider dropdown (empty state if none configured)
5. The "LLM defaults" tab no longer appears in the settings tab bar
6. Campaign → Settings → Routing shows "App default: ..." in placeholders when tiers are empty

- [ ] **Step 5: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: address smoke test issues from unified providers tab"
```
