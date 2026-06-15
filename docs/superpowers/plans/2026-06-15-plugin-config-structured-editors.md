# Structured Editors for Plugin Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bare-JSON `<textarea>` for plugin object/array config fields with schema-driven structured editors, enrich the OpenRouter manifest so provider routing renders as a typed form, and make the OpenRouter plugin defer fully to OpenRouter when nothing is configured.

**Architecture:** `components/SchemaField.tsx` dispatches on JSON-schema shape: typed objects recurse, string maps use `MapEditor`, free-form/per-model objects use a recursive key/value editor, string/object arrays use list editors, and anything unmatched uses a *fixed* JSON field (local state, parse-on-blur, inline error). `cleanDraftForSave` deep-compacts so untouched fields serialize to nothing. The OpenRouter plugin stops injecting opinionated defaults.

**Tech Stack:** TypeScript/React 18 + Vitest + React Testing Library (frontend); Python 3.12 + pytest + jsonschema Draft 2020-12 (backend).

**Spec:** `docs/superpowers/specs/2026-06-15-plugin-config-structured-editors-design.md`

**Branch:** `feat/plugin-config-structured-editors` (already created).

---

## File Structure

- `frontend/src/components/widgets/{MapEditor,StringListEditor,ObjectListEditor}.tsx` — moved from `routes/library/widgets/` (shared so `SchemaField` can use them).
- `frontend/src/components/widgets/__tests__/{objectWidgets,StringListEditor}.test.tsx` — moved alongside.
- `frontend/src/components/JsonField.tsx` — new fixed JSON textarea (leaf; no recursion).
- `frontend/src/components/SchemaField.tsx` — rewritten object/array branch + local `ObjectKeyValueField`.
- `frontend/src/components/schemaForm.ts` — `cleanDraftForSave` becomes deep-compacting.
- `frontend/src/routes/library/EntityForm.tsx` — update 3 import paths.
- `backend/bundled_plugins/llm-openrouter/plugin.py` — full-defer behavior.
- `backend/bundled_plugins/llm-openrouter/manifest.yaml` — enriched `provider` schema + updated descriptions.
- Tests: `frontend/src/components/__tests__/{schemaForm,SchemaField,JsonField}.test.tsx`, `backend/tests/bundled_plugins/test_llm_openrouter.py`.

---

## Task 1: Move reusable widgets into shared `components/widgets/`

**Files:**
- Move: `frontend/src/routes/library/widgets/MapEditor.tsx` → `frontend/src/components/widgets/MapEditor.tsx`
- Move: `frontend/src/routes/library/widgets/StringListEditor.tsx` → `frontend/src/components/widgets/StringListEditor.tsx`
- Move: `frontend/src/routes/library/widgets/ObjectListEditor.tsx` → `frontend/src/components/widgets/ObjectListEditor.tsx`
- Move: `frontend/src/routes/library/widgets/__tests__/objectWidgets.test.tsx` → `frontend/src/components/widgets/__tests__/objectWidgets.test.tsx`
- Move: `frontend/src/routes/library/widgets/__tests__/StringListEditor.test.tsx` → `frontend/src/components/widgets/__tests__/StringListEditor.test.tsx`
- Modify: `frontend/src/routes/library/EntityForm.tsx:16-19`

These three widgets are self-contained (only React imports) and styled by the global `styles/structured-editor.css`. Only `EntityForm.tsx` imports them; their tests use relative `../` imports and move with them. `EnumSelect`, `TagsInput`, `RefPicker` and `widgets.test.tsx` / `RefPicker.test.tsx` stay put.

- [ ] **Step 1: Create the target directory and move the files with git**

```bash
cd frontend
mkdir -p src/components/widgets/__tests__
git mv src/routes/library/widgets/MapEditor.tsx src/components/widgets/MapEditor.tsx
git mv src/routes/library/widgets/StringListEditor.tsx src/components/widgets/StringListEditor.tsx
git mv src/routes/library/widgets/ObjectListEditor.tsx src/components/widgets/ObjectListEditor.tsx
git mv src/routes/library/widgets/__tests__/objectWidgets.test.tsx src/components/widgets/__tests__/objectWidgets.test.tsx
git mv src/routes/library/widgets/__tests__/StringListEditor.test.tsx src/components/widgets/__tests__/StringListEditor.test.tsx
```

- [ ] **Step 2: Update the import paths in `EntityForm.tsx`**

Change lines 16-19 from `./widgets/...` to the shared location:

```tsx
import { MapEditor } from "../../components/widgets/MapEditor";
import { ObjectListEditor } from "../../components/widgets/ObjectListEditor";
import { StringListEditor } from "../../components/widgets/StringListEditor";
```

(Keep the other `./widgets/...` imports — `EnumSelect`, etc. — untouched.)

- [ ] **Step 3: Run the moved tests and the importer's typecheck**

Run: `cd frontend && pnpm test -- src/components/widgets && pnpm typecheck`
Expected: moved widget tests PASS; `tsc` reports no errors (EntityForm resolves the new paths).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(frontend): move shared form widgets into components/widgets"
```

---

## Task 2: Make `cleanDraftForSave` deep-compact (so "do nothing" sends nothing)

**Files:**
- Modify: `frontend/src/components/schemaForm.ts:44-51`
- Test: `frontend/src/components/__tests__/schemaForm.test.ts`

Nested editors hold raw values during editing; compaction at save time drops empty strings/null/undefined and empty arrays/objects recursively, preserving `0`/`false`. An untouched typed object (e.g. `provider`) thus serializes away entirely → the backend receives no `provider` key → OpenRouter defers.

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/components/__tests__/schemaForm.test.ts`:

```ts
import { cleanDraftForSave } from "../schemaForm";

describe("cleanDraftForSave deep compaction", () => {
  it("drops empty nested objects and arrays but keeps 0/false", () => {
    expect(
      cleanDraftForSave({
        api_key: "k",
        provider: { sort: "", order: [], allow_fallbacks: false },
        extra_headers: {},
        timeout_seconds: 0,
      }),
    ).toEqual({ api_key: "k", provider: { allow_fallbacks: false }, timeout_seconds: 0 });
  });

  it("removes a nested object that compacts to empty", () => {
    expect(cleanDraftForSave({ provider: { sort: "", order: [] } })).toEqual({});
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test -- src/components/__tests__/schemaForm.test.ts`
Expected: FAIL — current `cleanDraftForSave` keeps `provider: { sort: "", order: [], allow_fallbacks: false }` and `extra_headers: {}`.

- [ ] **Step 3: Implement deep compaction**

Replace the body of `cleanDraftForSave` (lines 44-51) with:

```ts
export function cleanDraftForSave(draft: Record<string, unknown>): Record<string, unknown> {
  return (compact(draft) as Record<string, unknown>) ?? {};
}

/**
 * Recursively drop "unset" values: empty string, null, undefined, and empty
 * arrays/objects (after compacting their contents). `0` and `false` are real
 * values and are preserved. Returns `undefined` when nothing survives, so a
 * parent can in turn drop the now-empty container.
 */
function compact(value: unknown): unknown {
  if (Array.isArray(value)) {
    const arr = value.map(compact).filter((v) => v !== undefined);
    return arr.length ? arr : undefined;
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const cv = compact(v);
      if (cv !== undefined) out[k] = cv;
    }
    return Object.keys(out).length ? out : undefined;
  }
  if (value === "" || value === null) return undefined;
  return value;
}
```

Keep the existing JSDoc block above the function (update its first line to mention nested compaction).

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && pnpm test -- src/components/__tests__/schemaForm.test.ts`
Expected: PASS (new tests and any pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/schemaForm.ts frontend/src/components/__tests__/schemaForm.test.ts
git commit -m "feat(frontend): deep-compact plugin config drafts on save"
```

---

## Task 3: Add the fixed `JsonField` component

**Files:**
- Create: `frontend/src/components/JsonField.tsx`
- Test: `frontend/src/components/__tests__/JsonField.test.tsx`

Replaces the broken controlled JSON textarea: local text state (keystrokes never revert), parse on blur, inline error on invalid JSON, empty text → `undefined`.

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/__tests__/JsonField.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { JsonField } from "../JsonField";

describe("JsonField", () => {
  it("does not revert intermediate invalid JSON and parses on blur", () => {
    const onChange = vi.fn();
    render(<JsonField value={{}} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: '{"a":' } }); // intermediate, invalid
    expect((ta as HTMLTextAreaElement).value).toBe('{"a":'); // not reverted
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(ta, { target: { value: '{"a":1}' } });
    fireEvent.blur(ta);
    expect(onChange).toHaveBeenCalledWith({ a: 1 });
  });

  it("shows an inline error for invalid JSON on blur and does not call onChange", () => {
    const onChange = vi.fn();
    render(<JsonField value={{}} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "{nope" } });
    fireEvent.blur(ta);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
  });

  it("treats empty text as undefined", () => {
    const onChange = vi.fn();
    render(<JsonField value={{ a: 1 }} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "  " } });
    fireEvent.blur(ta);
    expect(onChange).toHaveBeenCalledWith(undefined);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test -- src/components/__tests__/JsonField.test.tsx`
Expected: FAIL — `Cannot find module '../JsonField'`.

- [ ] **Step 3: Implement `JsonField`**

Create `frontend/src/components/JsonField.tsx`:

```tsx
import { useState } from "react";

function serialize(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

/**
 * A JSON textarea that is actually editable: the text lives in local state so
 * keystrokes are never reverted mid-edit, parsing happens on blur, and invalid
 * JSON surfaces an inline error instead of being silently discarded. Empty text
 * means "unset" and emits `undefined`.
 */
export function JsonField({
  value,
  onChange,
  rows = 4,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  rows?: number;
}) {
  const [text, setText] = useState(() => serialize(value));
  const [error, setError] = useState<string | null>(null);

  function commit() {
    const trimmed = text.trim();
    if (trimmed === "") {
      setError(null);
      onChange(undefined);
      return;
    }
    try {
      onChange(JSON.parse(trimmed));
      setError(null);
    } catch (e) {
      setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="json-field">
      <textarea
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (error) setError(null);
        }}
        onBlur={commit}
      />
      {error && <small className="json-field-error">{error}</small>}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && pnpm test -- src/components/__tests__/JsonField.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/JsonField.tsx frontend/src/components/__tests__/JsonField.test.tsx
git commit -m "feat(frontend): add fixed JsonField (parse-on-blur, inline error)"
```

---

## Task 4: Rewrite the object/array branch of `SchemaField`

**Files:**
- Modify: `frontend/src/components/SchemaField.tsx` (replace the `object`/`array` branch at lines 120-140; add imports + a local `ObjectKeyValueField`)
- Test: `frontend/src/components/__tests__/SchemaField.test.tsx`

Dispatch rules (object): declared `properties` → typed group (recurse), plus a key/value editor for custom keys when `additionalProperties: true`; string `additionalProperties` → `MapEditor`; object `additionalProperties` → key/value map of recursive value editors; `additionalProperties: true` w/o properties → key/value map with `JsonField` values; otherwise → `JsonField`. Array: string items → `StringListEditor`; object items → `ObjectListEditor` (recurse per item property); else → `JsonField`.

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/__tests__/SchemaField.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SchemaField } from "../SchemaField";
import type { JsonSchema } from "../schemaForm";

function renderField(schema: JsonSchema, value: unknown, onChange = vi.fn()) {
  render(
    <SchemaField pluginId="p" name="field" schema={schema} required={false} value={value} onChange={onChange} />,
  );
  return onChange;
}

describe("SchemaField object/array editors", () => {
  it("renders a string→string map (extra_headers) with MapEditor, not raw JSON", () => {
    const onChange = renderField(
      { type: "object", title: "Extra headers", additionalProperties: { type: "string" } },
      { "X-Title": "Grimoire" },
    );
    // MapEditor shows the key label and an editable value input.
    expect(screen.getByText("X-Title")).toBeInTheDocument();
    const input = screen.getByDisplayValue("Grimoire");
    fireEvent.change(input, { target: { value: "G2" } });
    expect(onChange).toHaveBeenCalledWith({ "X-Title": "G2" });
    // No raw-JSON textarea for this field.
    expect(screen.queryByRole("textbox")).not.toBeNull(); // MapEditor inputs are textboxes
  });

  it("renders a typed object's declared properties as sub-fields", () => {
    const schema: JsonSchema = {
      type: "object",
      title: "Provider routing",
      additionalProperties: true,
      properties: {
        sort: { type: "string", title: "Sort", enum: ["price", "throughput", "latency"] },
        allow_fallbacks: { type: "boolean", title: "Allow fallbacks" },
      },
    };
    const onChange = renderField(schema, {});
    const sort = screen.getByRole("combobox");
    fireEvent.change(sort, { target: { value: "price" } });
    expect(onChange).toHaveBeenCalledWith({ sort: "price" });
    expect(screen.getByRole("checkbox")).toBeInTheDocument(); // allow_fallbacks
  });

  it("renders an object-valued map (provider_overrides) as per-key rows", () => {
    const schema: JsonSchema = {
      type: "object",
      title: "Per-model provider routing",
      additionalProperties: { type: "object", additionalProperties: true },
    };
    renderField(schema, { "deepseek/deepseek-v4-pro": { max_price: { prompt: 0.4 } } });
    // The model slug appears as a row key.
    expect(screen.getByText("deepseek/deepseek-v4-pro")).toBeInTheDocument();
  });

  it("renders a string array with StringListEditor", () => {
    const onChange = renderField(
      { type: "array", title: "Order", items: { type: "string" } },
      ["anthropic", "openai"],
    );
    expect(screen.getByDisplayValue("anthropic")).toBeInTheDocument();
    expect(screen.getByDisplayValue("openai")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test -- src/components/__tests__/SchemaField.test.tsx`
Expected: FAIL — the current object branch renders one JSON `<textarea>`; no `combobox`/`checkbox`/`X-Title` label.

- [ ] **Step 3: Add imports to `SchemaField.tsx`**

After the existing imports (top of file), add:

```tsx
import { JsonField } from "./JsonField";
import { MapEditor } from "./widgets/MapEditor";
import { ObjectListEditor } from "./widgets/ObjectListEditor";
import { StringListEditor } from "./widgets/StringListEditor";
```

- [ ] **Step 4: Replace the object/array branch (lines 120-140)**

Replace the entire `if (type === "object" || type === "array") { ... }` block with:

```tsx
  if (type === "object") {
    const obj =
      value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
    const props = (schema.properties ?? undefined) as Record<string, JsonSchema> | undefined;
    const ap = schema.additionalProperties;
    const reqd = Array.isArray(schema.required) ? new Set(schema.required as string[]) : new Set<string>();

    // 1. Typed object: render declared properties; offer a custom-keys editor
    //    when the schema also allows arbitrary keys.
    if (props && Object.keys(props).length > 0) {
      const declared = new Set(Object.keys(props));
      const custom: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj)) if (!declared.has(k)) custom[k] = v;
      return (
        <fieldset className="schema-object">
          <legend>
            {label} {required && <em>*</em>}
          </legend>
          {schema.description && <small>{schema.description}</small>}
          {Object.entries(props).map(([k, sub]) => (
            <SchemaField
              key={k}
              pluginId={pluginId}
              name={k}
              schema={sub}
              required={reqd.has(k)}
              value={obj[k]}
              onChange={(v) => onChange({ ...obj, [k]: v })}
            />
          ))}
          {ap === true && (
            <ObjectKeyValueField
              pluginId={pluginId}
              label="Custom keys"
              valueSchema={true}
              value={custom}
              onChange={(next) => {
                const merged: Record<string, unknown> = {};
                for (const k of declared) if (k in obj) merged[k] = obj[k];
                onChange({ ...merged, ...next });
              }}
            />
          )}
        </fieldset>
      );
    }

    // 2. String map (e.g. extra_headers).
    if (ap && typeof ap === "object" && (ap as JsonSchema).type === "string") {
      const strMap: Record<string, string> = {};
      for (const [k, v] of Object.entries(obj)) strMap[k] = typeof v === "string" ? v : String(v ?? "");
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <MapEditor value={strMap} onChange={(next) => onChange(next)} />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }

    // 3. Object-valued map (e.g. provider_overrides) or free-form object.
    if ((ap && typeof ap === "object") || ap === true) {
      const valueSchema: JsonSchema | true = ap === true ? true : (ap as JsonSchema);
      return (
        <ObjectKeyValueField
          pluginId={pluginId}
          label={`${label}${required ? " *" : ""}`}
          description={schema.description}
          valueSchema={valueSchema}
          value={obj}
          onChange={onChange}
        />
      );
    }

    // 4. Opaque object: fixed JSON editor.
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <JsonField value={value} onChange={onChange} />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  if (type === "array") {
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    const items = (schema.items ?? {}) as JsonSchema;
    if (items.type === "string") {
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <StringListEditor label="" value={arr.map((x) => String(x ?? ""))} onChange={(next) => onChange(next)} />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }
    if (items.type === "object" && items.properties) {
      const itemProps = items.properties as Record<string, JsonSchema>;
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <ObjectListEditor
            value={arr as Record<string, unknown>[]}
            fieldKeys={Object.keys(itemProps)}
            onChange={(next) => onChange(next)}
            renderRow={(row, patch) => (
              <>
                {Object.entries(itemProps).map(([k, sub]) => (
                  <SchemaField
                    key={k}
                    pluginId={pluginId}
                    name={k}
                    schema={sub}
                    required={false}
                    value={row[k]}
                    onChange={(v) => patch({ ...row, [k]: v })}
                  />
                ))}
              </>
            )}
          />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <JsonField value={value} onChange={onChange} />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }
```

- [ ] **Step 5: Add the local `ObjectKeyValueField` component**

At the bottom of `SchemaField.tsx` (same module — avoids an import cycle with `SchemaField`), add:

```tsx
/**
 * Edits an object whose keys are user-chosen (a map). Each value is rendered by
 * recursing into {@link SchemaField} with `valueSchema`, except when the values
 * are fully free-form (`valueSchema === true`), in which case a {@link JsonField}
 * is used. Powers free-form objects, per-model routing maps, and the custom-keys
 * escape hatch on typed objects.
 */
function ObjectKeyValueField({
  pluginId,
  label,
  description,
  valueSchema,
  value,
  onChange,
}: {
  pluginId: string;
  label: string;
  description?: string;
  valueSchema: JsonSchema | true;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const [newKey, setNewKey] = useState("");
  function setKey(k: string, v: unknown) {
    onChange({ ...value, [k]: v });
  }
  function removeKey(k: string) {
    const next = { ...value };
    delete next[k];
    onChange(next);
  }
  return (
    <fieldset className="schema-object schema-kv">
      <legend>{label}</legend>
      {description && <small>{description}</small>}
      <ul>
        {Object.entries(value).map(([k, v]) => (
          <li key={k} className="schema-kv-row">
            <span className="schema-kv-key">{k}</span>
            {valueSchema === true ? (
              <JsonField value={v} onChange={(nv) => setKey(k, nv)} />
            ) : (
              <SchemaField
                pluginId={pluginId}
                name={k}
                schema={valueSchema}
                required={false}
                value={v}
                onChange={(nv) => setKey(k, nv)}
              />
            )}
            <button type="button" aria-label={`Remove ${k}`} onClick={() => removeKey(k)}>
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="schema-kv-add">
        <input placeholder="key" value={newKey} onChange={(e) => setNewKey(e.target.value)} />
        <button
          type="button"
          disabled={!newKey.trim() || newKey in value}
          onClick={() => {
            setKey(newKey.trim(), valueSchema === true ? {} : {});
            setNewKey("");
          }}
        >
          + add
        </button>
      </div>
    </fieldset>
  );
}
```

Add `useState` to the React import at the top of the file: `import { useState } from "react";` (if the file has no existing React import, add this line; SchemaField is a function component module).

- [ ] **Step 6: Run the SchemaField tests**

Run: `cd frontend && pnpm test -- src/components/__tests__/SchemaField.test.tsx`
Expected: PASS.

- [ ] **Step 7: Add minimal CSS for the new structural classes**

Append to `frontend/src/styles/structured-editor.css`:

```css
.schema-object {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.75rem;
  margin: 0.25rem 0;
}
.schema-object > legend {
  font-weight: 600;
  padding: 0 0.25rem;
}
.schema-kv-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}
.schema-kv-key {
  font-family: var(--font-mono);
  min-width: 12ch;
}
.schema-kv-add {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
}
.json-field-error {
  color: var(--danger);
  display: block;
}
```

(If any of `--border`, `--radius-sm`, `--font-mono`, `--danger` is absent in `styles/tokens.css`, substitute the nearest existing token — every token must be defined in both themes; do not add literal fallbacks.)

- [ ] **Step 8: Typecheck, lint, and run the component test suite**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm test -- src/components`
Expected: no type errors, no lint errors, all component tests PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/SchemaField.tsx frontend/src/components/__tests__/SchemaField.test.tsx frontend/src/styles/structured-editor.css
git commit -m "feat(frontend): schema-driven structured editors for object/array config fields"
```

---

## Task 5: OpenRouter plugin — defer fully when nothing is configured

**Files:**
- Modify: `backend/bundled_plugins/llm-openrouter/plugin.py` (constants at 44-57, ctor at ~139-152)
- Test: `backend/tests/bundled_plugins/test_llm_openrouter.py`

Unset `provider` and unset `provider_overrides` now both send nothing; explicit user keys are still honored; explicit `{}` still defers (now identical to unset).

- [ ] **Step 1: Update the tests to express the new behavior (TDD)**

In `backend/tests/bundled_plugins/test_llm_openrouter.py`:

Replace `test_default_provider_routing_is_cost_safe` (lines 250-257) with:

```python
def test_default_provider_routing_defers_to_openrouter(openrouter_module) -> None:
    """With no provider config, Grimoire injects nothing — the payload carries
    no `provider` key, so OpenRouter uses its own/account routing and pricing."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    request = CompletionRequest(
        model="openai/gpt-4o", messages=[Message(role=MessageRole.USER, content="hi")]
    )
    payload = provider._build_payload(request, stream=False)
    assert "provider" not in payload
```

Replace `test_builtin_max_price_guard_for_deepseek_v4_pro` (lines 270-276) with:

```python
def test_no_builtin_price_guard_when_unconfigured(openrouter_module) -> None:
    """The former built-in deepseek price guard is gone: unset config defers
    entirely, so even known cost-variance models carry no `provider` field."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    assert _provider_of(provider, "deepseek/deepseek-v4-pro") == {}
```

Replace `test_user_provider_overrides_apply_per_model` (lines 288-307) with:

```python
def test_user_provider_overrides_apply_per_model(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "provider_overrides": {
                "anthropic/claude-opus-4-7": {"max_price": {"prompt": 15.0, "completion": 75.0}}
            },
        }
    )
    # The targeted model sends exactly its override (no injected default base).
    assert _provider_of(provider, "anthropic/claude-opus-4-7") == {
        "max_price": {"prompt": 15.0, "completion": 75.0}
    }
    # Other models still defer entirely.
    assert _provider_of(provider, "deepseek/deepseek-v4-pro") == {}
```

Leave `test_user_provider_config_overrides_default`, `test_empty_provider_config_omits_routing`, and `test_explicit_overrides_still_apply_under_opt_out` as-is — they remain correct under the new behavior.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/bundled_plugins/test_llm_openrouter.py -q`
Expected: FAIL — current code injects `{sort: price, allow_fallbacks: false}` and the deepseek guard, so the new "defers"/"== {}" assertions fail.

- [ ] **Step 3: Remove the opinionated constants**

Delete the `DEFAULT_PROVIDER_CONFIG` and `BUILTIN_PROVIDER_OVERRIDES` definitions (and their explanatory comments) at lines 44-57.

- [ ] **Step 4: Simplify the ctor provider setup**

Replace the provider block in `__init__` (the `user_provider`/`routing_opted_out`/`overrides` lines, ~139-152) with:

```python
        # Provider routing: send only what the user explicitly configures. With
        # nothing set (or an explicit `provider: {}`), no `provider` field is
        # sent and OpenRouter uses its own/account routing and pricing. Per-model
        # overrides merge on top of the default for that model.
        user_provider = cfg.get("provider")
        self._provider_default: dict[str, Any] = (
            dict(user_provider) if isinstance(user_provider, dict) else {}
        )
        overrides = cfg.get("provider_overrides")
        self._provider_overrides: dict[str, dict[str, Any]] = (
            {str(k): dict(v) for k, v in overrides.items() if isinstance(v, dict)}
            if isinstance(overrides, dict)
            else {}
        )
```

(`_resolve_provider` and `_build_payload` are unchanged — they already omit an empty `provider`.)

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest tests/bundled_plugins/test_llm_openrouter.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/bundled_plugins/llm-openrouter/plugin.py backend/tests/bundled_plugins/test_llm_openrouter.py
git commit -m "feat(openrouter): defer to OpenRouter routing/pricing when unconfigured"
```

---

## Task 6: Enrich the OpenRouter manifest `provider` schema

**Files:**
- Modify: `backend/bundled_plugins/llm-openrouter/manifest.yaml` (the `provider` and `provider_overrides` property blocks, 65-87)
- Test: `backend/tests/bundled_plugins/test_llm_openrouter.py`

Declare `provider`'s real keys so the frontend renders the typed form; keep `additionalProperties: true` for advanced keys; update descriptions to the defer semantics. No `default` on `provider`/`provider_overrides` (so the form stays empty until touched).

- [ ] **Step 1: Write a failing schema test**

Append to `backend/tests/bundled_plugins/test_llm_openrouter.py`:

```python
def test_manifest_provider_schema_is_typed_and_valid(openrouter_module) -> None:
    import yaml
    from pathlib import Path

    from grimoire.validation.validator import check_schema, validate_config

    manifest_path = Path(openrouter_module.__file__).with_name("manifest.yaml")
    schema = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["config_schema"]
    assert check_schema(schema).ok

    provider_props = schema["properties"]["provider"]["properties"]
    assert provider_props["sort"]["enum"] == ["price", "throughput", "latency"]
    assert provider_props["allow_fallbacks"]["type"] == "boolean"
    assert provider_props["order"]["type"] == "array"
    assert provider_props["max_price"]["properties"]["prompt"]["type"] == "number"
    # Advanced/unknown routing keys still allowed.
    assert schema["properties"]["provider"]["additionalProperties"] is True
    # No defaults: an untouched form must not seed these.
    assert "default" not in schema["properties"]["provider"]
    assert "default" not in schema["properties"]["provider_overrides"]

    cfg = {
        "api_key": "k",
        "provider": {"sort": "price", "allow_fallbacks": False, "order": ["anthropic"],
                     "max_price": {"prompt": 0.4, "completion": 0.8}},
        "extra_headers": {"X-Title": "Grimoire"},
        "provider_overrides": {"deepseek/deepseek-v4-pro": {"max_price": {"prompt": 1.0}}},
    }
    assert validate_config(cfg, schema).ok
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/bundled_plugins/test_llm_openrouter.py::test_manifest_provider_schema_is_typed_and_valid -q`
Expected: FAIL — `provider` currently has no `properties` key (`KeyError`).

- [ ] **Step 3: Replace the `provider` block in `manifest.yaml`**

Replace the `provider:` property (lines 65-74) with:

```yaml
    provider:
      type: object
      title: Provider routing (default)
      description: |
        OpenRouter `provider` routing applied to every request. Leave every
        field blank to defer entirely to OpenRouter's own/account routing and
        pricing (nothing is sent). Set fields to constrain which upstream
        provider serves a model and to cap per-token price.
      additionalProperties: true
      properties:
        sort:
          type: string
          title: Sort
          description: Order candidate providers by price, throughput, or latency.
          enum: [price, throughput, latency]
        allow_fallbacks:
          type: boolean
          title: Allow fallbacks
          description: Fall back to a pricier/slower provider when the preferred one is unavailable.
        order:
          type: array
          title: Provider order
          description: Preferred provider slugs, tried in order.
          items:
            type: string
        only:
          type: array
          title: Only these providers
          items:
            type: string
        ignore:
          type: array
          title: Ignore these providers
          items:
            type: string
        require_parameters:
          type: boolean
          title: Require parameters
          description: Only route to providers that support all request parameters.
        data_collection:
          type: string
          title: Data collection
          description: Allow or deny providers that may train on prompts.
          enum: [allow, deny]
        max_price:
          type: object
          title: Max price (USD per million tokens)
          additionalProperties: false
          properties:
            prompt:
              type: number
              title: Prompt
            completion:
              type: number
              title: Completion
```

- [ ] **Step 4: Update the `provider_overrides` description**

Replace the `provider_overrides` description text (lines 79-84) to drop the "replaces the builtin price guards" sentence (there are no built-in guards now):

```yaml
      description: |
        Map of model slug -> provider routing object, merged over the default
        for that model (nested objects such as `max_price` merge field-by-field).
        Use it to constrain or price-cap specific models; models you don't list
        defer to the default routing above.
```

(Keep its `type`, `additionalProperties` structure unchanged.)

- [ ] **Step 5: Run the manifest test and the full plugin suite**

Run: `cd backend && uv run pytest tests/bundled_plugins/test_llm_openrouter.py -q`
Expected: PASS (new schema test + all behavior tests).

- [ ] **Step 6: Commit**

```bash
git add backend/bundled_plugins/llm-openrouter/manifest.yaml backend/tests/bundled_plugins/test_llm_openrouter.py
git commit -m "feat(openrouter): declare provider-routing keys so the UI renders a typed form"
```

---

## Task 7: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Frontend — full test + typecheck + lint**

Run: `cd frontend && pnpm test && pnpm typecheck && pnpm lint`
Expected: all PASS, no errors. Pay attention to the moved-widget tests and `routes/library` (EntityForm) still passing.

- [ ] **Step 2: Backend — affected suites + lint**

Run: `cd backend && uv run pytest tests/bundled_plugins tests/llm_gateway -q && uv run ruff check && uv run ruff format --check`
Expected: all PASS, lint/format clean.

- [ ] **Step 3: Manual smoke (real app)**

Start the app (`scripts/run.sh`), open Settings → Providers → OpenRouter (or Library → Plugins → OpenRouter). Confirm:
- `extra_headers` shows add/remove key→value rows (no JSON box).
- `provider` shows Sort dropdown, Allow fallbacks checkbox, Order/Only/Ignore list editors, Max price prompt/completion number inputs, and a "Custom keys" section.
- `provider_overrides` shows per-model rows.
- Saving with only an API key succeeds, and a request payload (Observability wire log) carries no `provider` key.
- Setting Sort=price + Max price, saving, then reloading round-trips the values.

- [ ] **Step 4: Finalize the branch**

Use the `superpowers:finishing-a-development-branch` skill to choose merge/PR.

---

## Self-Review Notes

- **Spec coverage:** §1 widget move → Task 1; §2 dispatch rules → Task 4; §2 sparse emission → Task 2 (deep-compact at save) + Task 4; §3 manifest enrichment → Task 6; §4 fixed fallback → Task 3 + Task 4; §5 backend full-defer → Task 5; testing section → Tasks 2–6 + Task 7.
- **Refinement vs. spec wording:** the spec described `provider_overrides` values as a "guided key/value object editor"; concretely that is `ObjectKeyValueField` with per-model rows whose leaf values use the fixed `JsonField` (free-form `additionalProperties: true` has no typed shape). Still structured at the map level, no manifest duplication, no `$ref` — consistent with the non-goals.
- **Type consistency:** `ObjectKeyValueField` prop names (`pluginId`, `label`, `description`, `valueSchema`, `value`, `onChange`) are used identically at all three call sites; `JsonField`/`MapEditor`/`StringListEditor`/`ObjectListEditor` signatures match Tasks 1 and 3.
