# Unified Providers Tab

**Date:** 2026-05-25
**Status:** Draft

## Summary

Restructure the Settings → Providers tab so that **Language Models**, **Embeddings**, and **Image Generation** each get an equal, rich provider card — replacing the current layout where LLM gets a full card and the other two are link lists. The LLM card gains stacked Heavy/Light model pickers, absorbing the LLM Defaults tab entirely. Campaigns inherit app-wide defaults through the existing routing/tier system and can override per-campaign.

## Current State

- **ProvidersTab** shows one primary LLM card (status badge, provider dropdown, model picker, config summary) and a secondary grid of links for embedding/imagegen/export plugins.
- **LLMDefaultsTab** is a separate settings tab with two text fields for Heavy and Light routes (`provider.model` format). These seed new campaigns' `model_tiers` block.
- **Embedding provider** is configured per-campaign in the Routing tab as a text field, or globally via `state_store.yaml` → `library.embedding_provider`. No app-level UI for choosing the default.
- **ImageGen provider** is configured per-campaign in the ImageGen tab as a dropdown. No app-level default UI.

## Design

### Providers Tab Layout

Three cards stacked vertically, each following the same structure:

1. **Header row** — Icon + title + status badge (Connected / Not configured / No provider)
2. **Provider dropdown** — Select from installed plugins of that type
3. **Model picker(s)** — `PluginModelPicker` bound to the selected provider's model catalog
4. **Local model indicator** — When the selected provider's config schema includes a `model_path` field (e.g. `embed-llamacpp`), show the current path value read-only with a link to Configure
5. **Configure button** — Navigates to the plugin detail page for API keys and advanced settings

The existing "re-run setup wizard" button stays, moved below the three cards.

### Language Models Card

- Provider dropdown listing all installed `llm_provider` plugins
- Two stacked `PluginModelPicker` instances:
  - **Heavy model** (generation) — used for main narrative, scene summaries, extractor
  - **Light model** (classification) — used for drift checks, validation, NPC ticks
- Writes to `PUT /api/config/llm-defaults` with `{ heavy: "provider.model", light: "provider.model" }`
- Status badge reflects the selected provider's health

### Embeddings Card

- Provider dropdown listing all installed `embedding_provider` plugins
- One `PluginModelPicker` for the active embedding model
- For local providers (e.g. `embed-llamacpp`) that have a `model_path` config field: display the current path read-only with a Configure link
- Writes to `PATCH /api/config/embedding-defaults` with `{ route: "provider.model" }`
- Status badge reflects the selected provider's health

### Image Generation Card

- Provider dropdown listing all installed `imagegen_backend` plugins (nullable — "No provider" is valid)
- One `PluginModelPicker` if the selected backend exposes a model catalog
- Writes to `PATCH /api/config/imagegen-defaults` with `{ backend: "plugin_id" }`
- Status badge reflects the selected backend's health, or "No provider" if none selected

### LLM Defaults Tab Removal

The LLM Defaults tab (`LLMDefaultsTab.tsx`) is removed. Its functionality is fully absorbed by the Heavy/Light pickers in the Language Models card. The backend endpoint `GET/PUT /api/config/llm-defaults` is unchanged — it is now called from the Providers tab instead of a dedicated tab.

## Backend Changes

### Existing endpoints (no changes)

- `GET /api/plugins/installed` — lists all plugins with their `implements` array
- `GET /api/plugins/{id}/config` — returns config with `configured` boolean
- `GET /api/plugins/{id}/models` — returns model catalog
- `GET/PUT /api/config/llm-defaults` — reads/writes Heavy/Light routes in `app.yaml`

### New endpoint: Embedding defaults

```
GET  /api/config/embedding-defaults → { route: string | null }
PATCH /api/config/embedding-defaults { route: string | null }
```

Reads/writes the app-level embedding route. On write:
- Stores in `state_store.yaml` → `library.embedding_provider`
- Sets the default embedding tier route via `gateway.set_default_route("library.embed", route)`

### New endpoint: ImageGen defaults

```
GET  /api/config/imagegen-defaults → { backend: string | null }
PATCH /api/config/imagegen-defaults { backend: string | null }
```

Reads/writes the app-level default imagegen backend. On write:
- Stores in `app.yaml` → `imagegen_defaults.backend`
- The ImageGen service already falls back to `default_backend_id` when no campaign override is set; this endpoint sets that value persistently.

## Campaign Inheritance

No changes to the routing architecture. The existing three-tier resolution chain applies:

1. Per-campaign per-task override
2. Per-campaign tier route
3. App-level default (what the Providers tab writes)
4. Fallback route

**Campaign Routing tab update**: When a tier has no campaign-level override, show the inherited app default in muted text (e.g. "App default: deepseek/deepseek-v4-pro") instead of an empty field. The override mechanism is unchanged — users can still set per-campaign values.

## Frontend Components

### New: `ProviderCard`

Shared component used by all three cards. Props:

- `type: "llm" | "embedding" | "imagegen"` — determines which plugin kind to filter
- `title: string` — card header text
- `plugins: PluginManifest[]` — installed plugins of this type
- `modelSlots: { key: string, label: string, sublabel?: string }[]` — one entry per model picker. LLM has two (heavy/light); embedding and imagegen have one each.
- `defaults: Record<string, string | null>` — current app-level defaults keyed by slot key
- `onDefaultsChange: (slot: string, route: string) => void` — callback when user picks a model

### Modified: `ProvidersTab.tsx`

Rewritten to render three `ProviderCard` instances. Fetches installed plugins once via `GET /api/plugins/installed`, filters by `implements` array, and distributes to cards.

### Modified: Campaign `RoutingTab.tsx`

When a tier field is empty (no campaign override), fetch and display the app default in muted text. No functional change to override behavior.

### Removed: `LLMDefaultsTab.tsx`

Deleted. Its tab entry removed from the settings page tab registry.

## Scope Boundaries

**In scope:**
- Providers tab restructure (three equal cards)
- Heavy/Light pickers in LLM card
- Embedding defaults endpoint + card
- ImageGen defaults endpoint + card
- LLM Defaults tab removal
- Campaign routing tab "inherited default" display

**Out of scope:**
- Plugin installation/uninstallation UI
- Per-task routing overrides (stay in campaign Routing tab advanced section)
- Export adapter configuration
- Plugin health check endpoint changes
- File picker widget for local model paths (show path read-only with Configure link for now)
