# Structured editors for plugin config object/array fields

**Date:** 2026-06-15
**Status:** Design — approved, pending spec review

## Problem

Configuring an OpenRouter provider fails or is unusable, for two linked reasons:

1. **The editor is broken for object/array fields.** `components/SchemaField.tsx`
   renders any `object`/`array` schema property as a single bare JSON
   `<textarea>`. It is a *controlled* field whose value is re-derived by
   `JSON.parse` on every keystroke, and parse errors are silently swallowed
   (`catch { /* keep last good value */ }`). Any momentarily-invalid keystroke
   (i.e. almost every intermediate edit) is reverted, so the field is nearly
   impossible to edit. When malformed input does slip through, the backend
   rejects it with an opaque schema error (`"123 is not of type 'string'"`,
   `additionalProperties` violations) surfaced as a generic save failure. The
   backend validation is correct; the input path is broken.

   Affected OpenRouter fields: `extra_headers`, `provider` (routing),
   `provider_overrides` (per-model routing).

2. **Opinionated defaults leak into requests.** When `provider` is unset the
   plugin injects Grimoire's cost-safe default (`{sort: price,
   allow_fallbacks: false}`) and a built-in per-model price guard
   (`max_price` cap on `deepseek/deepseek-v4-pro`). These are OpenRouter-only
   request fields a strict/proxied endpoint can reject ("openrouter is failing
   due to settings"), and they mean "doing nothing" does **not** defer to the
   user's OpenRouter account settings.

## Goals

- Replace the bare JSON textarea with structured, schema-driven editors so the
  three fields (and every other plugin's object/array fields) are editable and
  cannot produce schema-invalid input.
- When the user configures nothing specific, send nothing — defer entirely to
  OpenRouter's own routing and pricing.

## Non-goals

- A full typed form for *per-model* routing (`provider_overrides`). Per-model
  routing is advanced and rarely more than `max_price`; a guided key/value
  editor is enough. No manifest duplication / `$ref` resolution.
- Changing `usage_accounting` behavior (separate, documented toggle).
- Any non-OpenRouter plugin's schema. They benefit automatically from the
  generic renderer but are not otherwise touched.

## Design

### 1. Relocate the reusable widgets (layering fix)

`MapEditor`, `StringListEditor`, `ObjectListEditor` currently live under
`frontend/src/routes/library/widgets/` but will be consumed by the shared
`components/SchemaField.tsx`. A shared component importing route-level code is a
layering inversion. Move those three to `frontend/src/components/widgets/` and
update the existing importers (`EntityForm`, `FrontmatterEditor`,
`StructuredValueEditor`, `WorldAtmosphereForm`, `WorldCalendarForm`,
`WorldDefaultsForm`, and their tests). `EnumSelect`, `RefPicker`, `TagsInput`
stay where they are (route-specific). CSS (`styles/structured-editor.css`) is a
global import, so styling is unaffected.

### 2. Schema-shape dispatch in `SchemaField`

Replace the object/array branch. Dispatch on the property schema's shape:

| Schema shape | Editor |
|---|---|
| `object` with `properties` | Nested typed group: each declared property recurses through `SchemaField`. If `additionalProperties: true`, append a collapsible **"Custom keys"** `MapEditor` for undeclared string keys. |
| `object`, `additionalProperties` is a *string* schema | `MapEditor` (string→string) — e.g. `extra_headers`. |
| `object`, `additionalProperties` is an *object* schema | Model-key → object map: each value recurses as a guided key/value object editor — e.g. `provider_overrides`. |
| `array`, `items.type === "string"` | `StringListEditor`. |
| `array`, `items.type === "object"` | `ObjectListEditor`. |
| anything unmatched / `additionalProperties: true` free-form | **Fixed** JSON textarea (see §4). |

The typed-object and map editors emit **sparse** objects: empty string, `null`,
`undefined`, empty array, and empty nested object sub-values are dropped when
assembling the object (the nested analogue of `cleanDraftForSave`). This is what
makes "the user set nothing" serialize to an absent/empty object rather than
`{sort: ""}`.

### 3. Enrich the OpenRouter manifest `config_schema`

Declare `provider`'s real keys so it renders as a typed form, while keeping
`additionalProperties: true` for the power-user escape hatch:

- `sort`: enum `[price, throughput, latency]`
- `allow_fallbacks`: boolean
- `order`, `only`, `ignore`: array of strings
- `require_parameters`: boolean
- `data_collection`: enum `[allow, deny]`
- `max_price`: object `{ prompt: number, completion: number }`

`provider` and `provider_overrides` carry **no** `default`, so
`initialDraftFromSchema` does not seed them and an untouched form omits them.
`extra_headers` already declares `additionalProperties: {type: string}` → map
editor for free. Update the `provider` / `provider_overrides` descriptions to
state the new "unset = defer to OpenRouter" semantics.

### 4. Fix the fallback textarea

For schemas that don't match a structured editor, keep a JSON textarea but make
it usable: hold the raw text in local component state (so keystrokes are never
reverted), parse on blur, propagate the parsed value on success, and show an
inline error message on failure instead of silently discarding input.

### 5. Backend: full defer when unset (OpenRouter plugin)

In `backend/bundled_plugins/llm-openrouter/plugin.py`:

- `_provider_default` = the user's `provider` dict when present, else `{}`
  (was `DEFAULT_PROVIDER_CONFIG`).
- `_provider_overrides` = the user's `provider_overrides` when present, else
  `{}` (was `BUILTIN_PROVIDER_OVERRIDES`).
- Remove the `DEFAULT_PROVIDER_CONFIG` and `BUILTIN_PROVIDER_OVERRIDES`
  constants and the now-redundant `routing_opted_out` distinction — unset and
  explicit `{}` now behave identically (both defer).
- `_resolve_provider(model)` is unchanged: it merges default + per-model
  overrides and returns `{}` when both are empty; `_build_payload` already omits
  the `provider` key for an empty result, so no `provider` field is sent →
  OpenRouter uses its own routing and pricing. Explicit user-set keys are still
  honored and merged field-by-field.

This changes documented default behavior, so existing plugin tests that assert
the injected cost-safe default or the deepseek price guard must be updated to
assert the defer behavior.

## Testing

- **`SchemaField` component tests** (`frontend`, Vitest + RTL):
  - string-`additionalProperties` object renders `MapEditor`;
  - object-with-`properties` renders sub-fields (enum→select, bool→checkbox,
    number→number, string array→`StringListEditor`);
  - object-`additionalProperties`-object renders the model→object map;
  - the typed editor emits a sparse object (untouched/cleared fields omitted);
  - the fallback textarea accepts intermediate invalid JSON without reverting,
    parses on blur, and surfaces an inline error.
- **OpenRouter manifest** (`backend`): `check_schema` passes on the enriched
  schema; representative configs validate (typed provider routing, extra
  headers, per-model overrides); invalid ones still fail.
- **OpenRouter plugin** (`backend`): unset `provider`/`provider_overrides` →
  no `provider` field in the payload; explicit keys → merged and sent;
  explicit `{}` → defer. Update the existing default/guard assertions.

## Risks

- Removing the deepseek price guard removes a cost safety net (accepted: full
  defer was the chosen behavior). The user can re-add `max_price` per model
  through the new per-model editor.
- Moving widgets touches several `routes/library` importers; mechanical, covered
  by their existing tests.
