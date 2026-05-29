# Structured Entity Forms (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic untyped frontmatter editor for **characters** with a declarative, schema-mirroring structured form, keep unknown keys under an "Advanced" section, and show a live token estimate in the editor header.

**Architecture:** A front-end field-descriptor (`entitySchemas.ts`) declares each kind's sections/fields; a pure `<EntityForm>` renders it over the existing `frontmatter` dict; unknown keys fall through to the existing `FrontmatterEditor` under a collapsed `<details>`. A lazy-loaded `js-tiktoken` encoder powers a `<TokenBadge>`. A thin backend route exposes each model's JSON schema, and a committed property-name fixture links backend and front-end drift tests.

**Tech Stack:** React 18 + TypeScript, Vitest + @testing-library/react (front-end); FastAPI + pytest (backend); `js-tiktoken` (new front-end dep).

Spec: `docs/superpowers/specs/2026-05-28-world-creation-structured-forms-design.md`

---

## Task 1: Backend `entity-schemas` route

**Files:**
- Modify: `backend/src/grimoire/api/library.py` (add route near the other `/library/...` GETs, e.g. after `library.py:116` block)
- Test: `backend/tests/api/test_library_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_library_routes.py`:

```python
def test_entity_schema_returns_model_properties(client) -> None:
    response = client.get("/api/library/entity-schemas/character")
    assert response.status_code == 200
    props = response.json()["properties"]
    for key in ("name", "role", "voice", "structural_relationships", "image"):
        assert key in props


def test_entity_schema_unknown_kind_404(client) -> None:
    response = client.get("/api/library/entity-schemas/widget")
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py::test_entity_schema_returns_model_properties -v`
Expected: FAIL with 404 (route not found).

- [ ] **Step 3: Implement the route**

In `backend/src/grimoire/api/library.py`, add imports near the top with the other type imports:

```python
from grimoire.types.characters import Character
from grimoire.types.world import Faction, Item, Location, LoreEntry, Monster

_ENTITY_SCHEMA_MODELS = {
    "character": Character,
    "location": Location,
    "item": Item,
    "monster": Monster,
    "faction": Faction,
    "lore": LoreEntry,
}
```

Add the route (no service dependency — pure schema):

```python
@router.get("/library/entity-schemas/{kind}")
async def get_entity_schema(kind: str) -> dict[str, Any]:
    model = _ENTITY_SCHEMA_MODELS.get(kind)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown kind: {kind}")
    return model.model_json_schema()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py -k entity_schema -v`
Expected: PASS (both tests).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run ruff check src/grimoire/api/library.py && uv run ruff format src/grimoire/api/library.py
git add backend/src/grimoire/api/library.py backend/tests/api/test_library_routes.py
git commit -m "feat(api): add entity-schemas route for structured forms (#441)"
```

---

## Task 2: Add `js-tiktoken` and the token-estimate helper

**Files:**
- Modify: `frontend/package.json` (via pnpm)
- Create: `frontend/src/components/tokens.ts`
- Test: `frontend/src/components/__tests__/tokens.test.ts`

- [ ] **Step 1: Add the dependency**

Run: `cd frontend && pnpm add js-tiktoken`
Expected: `js-tiktoken` appears under `dependencies` in `package.json`.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/__tests__/tokens.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { estimateEntityTokens, estimateTokens } from "../tokens";

describe("estimateTokens", () => {
  it("returns 0 for empty text", () => {
    expect(estimateTokens("")).toBe(0);
  });

  it("falls back to len/4 before the encoder loads", () => {
    // "abcd".length / 4 === 1
    expect(estimateTokens("abcd")).toBe(1);
    expect(estimateTokens("a".repeat(40))).toBe(10);
  });
});

describe("estimateEntityTokens", () => {
  it("grows with body length", () => {
    const small = estimateEntityTokens({ name: "X" }, "short");
    const big = estimateEntityTokens({ name: "X" }, "a".repeat(400));
    expect(big).toBeGreaterThan(small);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/components/__tests__/tokens.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement `tokens.ts`**

Create `frontend/src/components/tokens.ts`:

```ts
/**
 * Token-count estimates for library entities. Uses a lazily-loaded
 * `js-tiktoken` cl100k encoder; before it resolves (or if loading fails) we
 * fall back to the backend's offline heuristic of len/4. The result is an
 * ESTIMATE — production models are Claude, whose tokenizer differs slightly.
 */

interface Encoder {
  encode(text: string): number[];
}

let encoder: Encoder | null = null;
let loading: Promise<void> | null = null;

/** Kick off (or await) lazy load of the cl100k encoder. */
export function ensureTokenizer(): Promise<void> {
  if (encoder) return Promise.resolve();
  if (!loading) {
    loading = import("js-tiktoken")
      .then(({ getEncoding }) => {
        encoder = getEncoding("cl100k_base") as Encoder;
      })
      .catch(() => {
        // Leave encoder null; estimateTokens keeps using the fallback.
      });
  }
  return loading;
}

/** Synchronous estimate: exact once the encoder is loaded, else len/4. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  if (encoder) return encoder.encode(text).length;
  return Math.ceil(text.length / 4);
}

/** Estimate the on-disk cost of an entity (frontmatter + markdown body). */
export function estimateEntityTokens(
  frontmatter: Record<string, unknown>,
  body: string,
): number {
  return estimateTokens(`${JSON.stringify(frontmatter)}\n${body}`);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/components/__tests__/tokens.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/components/tokens.ts frontend/src/components/__tests__/tokens.test.ts
git commit -m "feat(frontend): token estimate helper with lazy tiktoken (#441)"
```

---

## Task 3: `<TokenBadge>` component

**Files:**
- Create: `frontend/src/components/TokenBadge.tsx`
- Test: `frontend/src/components/__tests__/TokenBadge.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/__tests__/TokenBadge.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TokenBadge } from "../TokenBadge";

describe("TokenBadge", () => {
  it("renders an approximate token count with separators", () => {
    render(<TokenBadge text={"a".repeat(4000)} />);
    // 4000/4 = 1000 (fallback before encoder loads)
    expect(screen.getByText(/~1,000 tokens/)).toBeInTheDocument();
  });

  it("renders ~0 tokens for empty text", () => {
    render(<TokenBadge text="" />);
    expect(screen.getByText(/~0 tokens/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/components/__tests__/TokenBadge.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `TokenBadge.tsx`**

Create `frontend/src/components/TokenBadge.tsx`:

```tsx
import { useEffect, useState } from "react";

import { ensureTokenizer, estimateTokens } from "./tokens";

/** Badge showing an approximate token cost for a block of text. */
export function TokenBadge({ text, className }: { text: string; className?: string }) {
  // `ready` flips once the real encoder is loaded, forcing a recompute from
  // the len/4 fallback to the exact count.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let active = true;
    void ensureTokenizer().then(() => active && setReady(true));
    return () => {
      active = false;
    };
  }, []);

  // `ready` is read so the value recomputes when the encoder arrives.
  void ready;
  const count = estimateTokens(text);
  return (
    <span
      className={className ? `token-badge ${className}` : "token-badge"}
      title="Approximate token count (cl100k estimate; the live model's tokenizer differs)"
    >
      ~{count.toLocaleString("en-US")} tokens
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/components/__tests__/TokenBadge.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TokenBadge.tsx frontend/src/components/__tests__/TokenBadge.test.tsx
git commit -m "feat(frontend): TokenBadge component (#441)"
```

---

## Task 4: Extract `StringListEditor` to a shared module

`StringListEditor` currently lives inside `CharacterExtras.tsx` (`CharacterExtras.tsx:147-203`). The widgets and `EntityForm` need it; extract it verbatim so it can be imported, and re-point `CharacterExtras` at it (CharacterExtras is removed later in Task 10, but keep the app compiling between commits).

**Files:**
- Create: `frontend/src/routes/library/widgets/StringListEditor.tsx`
- Modify: `frontend/src/routes/library/CharacterExtras.tsx`
- Test: `frontend/src/routes/library/widgets/__tests__/StringListEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/widgets/__tests__/StringListEditor.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StringListEditor } from "../StringListEditor";

describe("StringListEditor", () => {
  it("adds an empty row on +Add", () => {
    const onChange = vi.fn();
    render(<StringListEditor label="Samples" value={["hi"]} onChange={onChange} />);
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith(["hi", ""]);
  });

  it("removes a row", () => {
    const onChange = vi.fn();
    render(<StringListEditor label="Samples" value={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Remove")[0]);
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/StringListEditor.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Create the module**

Create `frontend/src/routes/library/widgets/StringListEditor.tsx` with the component moved verbatim from `CharacterExtras.tsx:147-203`:

```tsx
export function StringListEditor({
  label,
  value,
  onChange,
  textarea,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  textarea?: boolean;
}) {
  return (
    <div className="string-list-editor">
      <span className="string-list-label">{label}</span>
      <ul>
        {value.map((s, idx) => (
          <li key={idx}>
            {textarea ? (
              <textarea
                rows={2}
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            ) : (
              <input
                type="text"
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            )}
            <button
              type="button"
              aria-label="Remove"
              onClick={() => onChange(value.filter((_, i) => i !== idx))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => onChange([...value, ""])}>
        + Add
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Re-point `CharacterExtras.tsx` at the shared module**

In `frontend/src/routes/library/CharacterExtras.tsx`: delete the local `StringListEditor` function (`:147-203`) and add at the top:

```tsx
import { StringListEditor } from "./widgets/StringListEditor";
```

- [ ] **Step 5: Run tests + typecheck to verify all pass**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/StringListEditor.test.tsx && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/widgets/StringListEditor.tsx frontend/src/routes/library/widgets/__tests__/StringListEditor.test.tsx frontend/src/routes/library/CharacterExtras.tsx
git commit -m "refactor(frontend): extract StringListEditor to widgets (#441)"
```

---

## Task 5: `EnumSelect` and `TagsInput` widgets

**Files:**
- Create: `frontend/src/routes/library/widgets/EnumSelect.tsx`
- Create: `frontend/src/routes/library/widgets/TagsInput.tsx`
- Test: `frontend/src/routes/library/widgets/__tests__/widgets.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/widgets/__tests__/widgets.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { EnumSelect } from "../EnumSelect";
import { TagsInput } from "../TagsInput";

describe("EnumSelect", () => {
  it("renders options and reports selection", () => {
    const onChange = vi.fn();
    render(
      <EnumSelect
        value="pc"
        options={[
          { value: "pc", label: "PC" },
          { value: "major_npc", label: "Major NPC" },
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "major_npc" } });
    expect(onChange).toHaveBeenCalledWith("major_npc");
  });
});

describe("TagsInput", () => {
  it("adds a tag on Enter", () => {
    const onChange = vi.fn();
    render(<TagsInput value={["a"]} onChange={onChange} />);
    const input = screen.getByPlaceholderText("add tag…");
    fireEvent.change(input, { target: { value: "b" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["a", "b"]);
  });

  it("removes a tag", () => {
    const onChange = vi.fn();
    render(<TagsInput value={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Remove a"));
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/widgets.test.tsx`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement `EnumSelect.tsx`**

```tsx
export function EnumSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (next: string) => void;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {!options.some((o) => o.value === value) && value !== "" && (
        <option value={value}>{value}</option>
      )}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
```

- [ ] **Step 4: Implement `TagsInput.tsx`**

```tsx
import { useState } from "react";

export function TagsInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const t = draft.trim();
    if (!t || value.includes(t)) {
      setDraft("");
      return;
    }
    onChange([...value, t]);
    setDraft("");
  }
  return (
    <div className="tags-input">
      <ul className="tags-input-chips">
        {value.map((t) => (
          <li key={t} className="tags-input-chip">
            {t}
            <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>
              ×
            </button>
          </li>
        ))}
      </ul>
      <input
        type="text"
        placeholder="add tag…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/widgets.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/widgets/EnumSelect.tsx frontend/src/routes/library/widgets/TagsInput.tsx frontend/src/routes/library/widgets/__tests__/widgets.test.tsx
git commit -m "feat(frontend): EnumSelect and TagsInput widgets (#441)"
```

---

## Task 6: `RefPicker` widget

A free-text input with a `<datalist>` of the world's entities of the target kind(s). Stores the chosen `asset_id` (free text allowed). Fetches via `libraryApi.listEntities`.

**Files:**
- Create: `frontend/src/routes/library/widgets/RefPicker.tsx`
- Test: `frontend/src/routes/library/widgets/__tests__/RefPicker.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/widgets/__tests__/RefPicker.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RefPicker } from "../RefPicker";
import * as libraryModule from "../../../../api/library";

vi.mock("../../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../../api/library");
  return { ...actual, libraryApi: { ...actual.libraryApi, listEntities: vi.fn() } };
});

describe("RefPicker", () => {
  it("loads suggestions and reports the chosen value", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
      { asset_id: "alistair", name: "Alistair" } as never,
    ]);
    const onChange = vi.fn();
    render(<RefPicker worldId="w1" refKinds={["character"]} value="" onChange={onChange} />);
    await waitFor(() => expect(screen.getByRole("option", { hidden: true })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "alistair" } });
    expect(onChange).toHaveBeenCalledWith("alistair");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/RefPicker.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `RefPicker.tsx`**

```tsx
import { useEffect, useId, useState } from "react";

import { ENTITY_KIND_PLURAL, type EntityKind, libraryApi } from "../../../api/library";

interface Suggestion {
  id: string;
  label: string;
}

export function RefPicker({
  worldId,
  refKinds,
  value,
  onChange,
}: {
  worldId: string;
  refKinds: EntityKind[];
  value: string;
  onChange: (next: string) => void;
}) {
  const listId = useId();
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);

  useEffect(() => {
    let active = true;
    async function load() {
      const results: Suggestion[] = [];
      for (const kind of refKinds) {
        try {
          const entities = await libraryApi.listEntities(worldId, ENTITY_KIND_PLURAL[kind]);
          for (const e of entities) {
            const id = "asset_id" in e ? (e.asset_id as string) : (e.id as string);
            results.push({ id, label: e.name || id });
          }
        } catch {
          // Ref pickers are advisory — failure leaves the input as free text.
        }
      }
      if (active) setSuggestions(results);
    }
    void load();
    return () => {
      active = false;
    };
  }, [worldId, refKinds]);

  return (
    <>
      <input
        type="text"
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </datalist>
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/RefPicker.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/widgets/RefPicker.tsx frontend/src/routes/library/widgets/__tests__/RefPicker.test.tsx
git commit -m "feat(frontend): RefPicker widget (#441)"
```

---

## Task 7: `ObjectListEditor` and `MapEditor` widgets

`ObjectListEditor` renders a list of rows, each a sub-form of nested fields (powers `structural_relationships`). `MapEditor` edits an object of string→string (powers `address_terms`). Both stay generic; the per-field rendering for `ObjectListEditor` is delegated by a `renderField` callback supplied by `EntityForm` in Task 9 (keeps this widget free of descriptor knowledge).

**Files:**
- Create: `frontend/src/routes/library/widgets/ObjectListEditor.tsx`
- Create: `frontend/src/routes/library/widgets/MapEditor.tsx`
- Test: `frontend/src/routes/library/widgets/__tests__/objectWidgets.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/widgets/__tests__/objectWidgets.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MapEditor } from "../MapEditor";
import { ObjectListEditor } from "../ObjectListEditor";

describe("MapEditor", () => {
  it("adds a key/value pair", () => {
    const onChange = vi.fn();
    render(<MapEditor value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText("key"), { target: { value: "boss" } });
    fireEvent.change(screen.getByPlaceholderText("value"), { target: { value: "sir" } });
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith({ boss: "sir" });
  });
});

describe("ObjectListEditor", () => {
  it("adds an empty row and renders fields via renderField", () => {
    const onChange = vi.fn();
    render(
      <ObjectListEditor
        value={[]}
        fieldKeys={["kind"]}
        onChange={onChange}
        renderRow={(row, patch) => (
          <input
            aria-label="kind"
            value={(row.kind as string) ?? ""}
            onChange={(e) => patch({ ...row, kind: e.target.value })}
          />
        )}
      />,
    );
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith([{}]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/objectWidgets.test.tsx`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement `MapEditor.tsx`**

```tsx
import { useState } from "react";

export function MapEditor({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const [k, setK] = useState("");
  const [v, setV] = useState("");
  return (
    <div className="map-editor">
      <ul>
        {Object.entries(value).map(([key, val]) => (
          <li key={key}>
            <span className="map-key">{key}</span>
            <input
              value={val}
              onChange={(e) => onChange({ ...value, [key]: e.target.value })}
            />
            <button
              type="button"
              aria-label={`Remove ${key}`}
              onClick={() => {
                const next = { ...value };
                delete next[key];
                onChange(next);
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="map-add">
        <input placeholder="key" value={k} onChange={(e) => setK(e.target.value)} />
        <input placeholder="value" value={v} onChange={(e) => setV(e.target.value)} />
        <button
          type="button"
          disabled={!k.trim() || k in value}
          onClick={() => {
            onChange({ ...value, [k.trim()]: v });
            setK("");
            setV("");
          }}
        >
          + Add
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement `ObjectListEditor.tsx`**

```tsx
type Row = Record<string, unknown>;

export function ObjectListEditor({
  value,
  fieldKeys,
  onChange,
  renderRow,
}: {
  value: Row[];
  /** Keys this row manages — used only to seed empty rows as `{}`. */
  fieldKeys: string[];
  onChange: (next: Row[]) => void;
  renderRow: (row: Row, patch: (next: Row) => void) => React.ReactNode;
}) {
  void fieldKeys;
  return (
    <div className="object-list-editor">
      <ul>
        {value.map((row, idx) => (
          <li key={idx} className="object-list-row">
            {renderRow(row, (next) => {
              const copy = [...value];
              copy[idx] = next;
              onChange(copy);
            })}
            <button
              type="button"
              aria-label="Remove row"
              onClick={() => onChange(value.filter((_, i) => i !== idx))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => onChange([...value, {}])}>
        + Add
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/widgets/__tests__/objectWidgets.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/widgets/ObjectListEditor.tsx frontend/src/routes/library/widgets/MapEditor.tsx frontend/src/routes/library/widgets/__tests__/objectWidgets.test.tsx
git commit -m "feat(frontend): ObjectListEditor and MapEditor widgets (#441)"
```

---

## Task 8: Descriptor types + character descriptor

**Files:**
- Create: `frontend/src/routes/library/entitySchemas.ts`
- Test: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/entitySchemas.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { getDescriptor, managedKeys } from "../entitySchemas";

describe("character descriptor", () => {
  it("is registered for the character kind", () => {
    expect(getDescriptor("character")?.kind).toBe("character");
  });

  it("manages the headline character keys", () => {
    const keys = managedKeys(getDescriptor("character")!);
    for (const k of ["name", "id", "role", "voice", "image", "structural_relationships"]) {
      expect(keys).toContain(k);
    }
  });

  it("has no descriptor for a kind not yet implemented", () => {
    expect(getDescriptor("item")).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `entitySchemas.ts`**

```ts
import type { EntityKind } from "../../api/library";

export type Widget =
  | "text"
  | "textarea"
  | "number"
  | "bool"
  | "enum"
  | "tags"
  | "stringList"
  | "ref"
  | "refList"
  | "object"
  | "objectList"
  | "map";

export interface FieldDescriptor {
  key: string;
  label: string;
  widget: Widget;
  help?: string;
  readOnly?: boolean;
  rows?: number;
  options?: { value: string; label: string }[];
  refKinds?: EntityKind[];
  fields?: FieldDescriptor[]; // object / objectList children
}

export interface EntitySectionDescriptor {
  title: string;
  collapsed?: boolean;
  fields: FieldDescriptor[];
}

export interface EntityDescriptor {
  kind: EntityKind;
  sections: EntitySectionDescriptor[];
}

const CHARACTER: EntityDescriptor = {
  kind: "character",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "role",
          label: "Role",
          widget: "enum",
          options: [
            { value: "pc", label: "PC" },
            { value: "major_npc", label: "Major NPC" },
            { value: "minor_npc", label: "Minor NPC" },
            { value: "ensemble", label: "Ensemble" },
            { value: "named_flavor", label: "Named flavor" },
          ],
        },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "age", label: "Age", widget: "text" },
        { key: "tags", label: "Tags", widget: "tags" },
        { key: "role_tags", label: "Role tags", widget: "tags" },
        { key: "household_id", label: "Household", widget: "text" },
      ],
    },
    {
      title: "Description",
      fields: [{ key: "description", label: "Description", widget: "textarea", rows: 4 }],
    },
    {
      title: "Voice",
      fields: [
        {
          key: "voice",
          label: "Voice",
          widget: "object",
          fields: [
            { key: "summary", label: "Summary", widget: "textarea", rows: 2 },
            { key: "voice_register", label: "Register", widget: "text" },
            { key: "samples", label: "Sample lines", widget: "stringList" },
            { key: "speech_patterns", label: "Speech patterns", widget: "stringList" },
            { key: "dos", label: "Dos", widget: "stringList" },
            { key: "donts", label: "Don'ts", widget: "stringList" },
            { key: "address_terms", label: "Address terms", widget: "map" },
          ],
        },
      ],
    },
    {
      title: "Image prompt",
      collapsed: true,
      fields: [
        {
          key: "image",
          label: "Image",
          widget: "object",
          fields: [
            { key: "base_prompt", label: "Base prompt", widget: "textarea", rows: 3 },
            { key: "negative_prompt", label: "Negative prompt", widget: "textarea", rows: 2 },
            { key: "canonical_seed", label: "Canonical seed", widget: "number" },
          ],
        },
      ],
    },
    {
      title: "Relationships",
      fields: [
        {
          key: "structural_relationships",
          label: "Relationships",
          widget: "objectList",
          fields: [
            { key: "to_ref", label: "To", widget: "ref", refKinds: ["character", "faction"] },
            { key: "kind", label: "Kind", widget: "text" },
            { key: "note", label: "Note", widget: "text" },
          ],
        },
      ],
    },
  ],
};

const REGISTRY: Partial<Record<EntityKind, EntityDescriptor>> = {
  character: CHARACTER,
};

export function getDescriptor(kind: EntityKind | string): EntityDescriptor | undefined {
  return REGISTRY[kind as EntityKind];
}

/** Every top-level frontmatter key a descriptor owns (for the Advanced fallback). */
export function managedKeys(descriptor: EntityDescriptor): string[] {
  return descriptor.sections.flatMap((s) => s.fields.map((f) => f.key));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/entitySchemas.ts frontend/src/routes/library/__tests__/entitySchemas.test.ts
git commit -m "feat(frontend): entity descriptor types + character descriptor (#441)"
```

---

## Task 9: `<EntityForm>` renderer

Renders a descriptor over a frontmatter dict, with the Advanced raw fallback. Reuses every widget from Tasks 4–7.

**Files:**
- Create: `frontend/src/routes/library/EntityForm.tsx`
- Test: `frontend/src/routes/library/__tests__/EntityForm.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/EntityForm.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { EntityForm } from "../EntityForm";
import { getDescriptor } from "../entitySchemas";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual("../../../api/library");
  return { ...(actual as object), libraryApi: { listEntities: vi.fn().mockResolvedValue([]) } };
});

const descriptor = getDescriptor("character")!;

describe("EntityForm", () => {
  it("renders known fields as widgets and edits them", () => {
    const onFm = vi.fn();
    render(
      <EntityForm
        descriptor={descriptor}
        worldId="w1"
        frontmatter={{ name: "Alistair", role: "major_npc" }}
        body=""
        onFrontmatterChange={onFm}
        onBodyChange={() => {}}
      />,
    );
    const name = screen.getByDisplayValue("Alistair");
    fireEvent.change(name, { target: { value: "Al" } });
    expect(onFm).toHaveBeenCalledWith(expect.objectContaining({ name: "Al" }));
  });

  it("routes unknown keys into the Advanced section", () => {
    render(
      <EntityForm
        descriptor={descriptor}
        worldId="w1"
        frontmatter={{ name: "X", custom_field: "keepme" }}
        body=""
        onFrontmatterChange={() => {}}
        onBodyChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByText(/Advanced/));
    expect(screen.getByDisplayValue("keepme")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityForm.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `EntityForm.tsx`**

```tsx
import type { EntityKind } from "../../api/library";
import type { Frontmatter, FrontmatterValue } from "./frontmatter";
import { FrontmatterEditor } from "./FrontmatterEditor";
import {
  type EntityDescriptor,
  type FieldDescriptor,
  managedKeys,
} from "./entitySchemas";
import { EnumSelect } from "./widgets/EnumSelect";
import { MapEditor } from "./widgets/MapEditor";
import { ObjectListEditor } from "./widgets/ObjectListEditor";
import { RefPicker } from "./widgets/RefPicker";
import { StringListEditor } from "./widgets/StringListEditor";
import { TagsInput } from "./widgets/TagsInput";

interface Props {
  descriptor: EntityDescriptor;
  worldId: string;
  frontmatter: Frontmatter;
  body: string;
  onFrontmatterChange: (next: Frontmatter) => void;
  onBodyChange: (next: string) => void;
}

function asString(v: FrontmatterValue | undefined): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}
function asStringArray(v: FrontmatterValue | undefined): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}
function asObject(v: FrontmatterValue | undefined): Record<string, FrontmatterValue> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, FrontmatterValue>) : {};
}
function asRows(v: FrontmatterValue | undefined): Record<string, unknown>[] {
  return Array.isArray(v) ? (v.filter((x) => x && typeof x === "object" && !Array.isArray(x)) as Record<string, unknown>[]) : [];
}

export function EntityForm({
  descriptor,
  worldId,
  frontmatter,
  body,
  onFrontmatterChange,
  onBodyChange,
}: Props) {
  function setKey(key: string, value: FrontmatterValue) {
    onFrontmatterChange({ ...frontmatter, [key]: value });
  }

  function renderField(
    field: FieldDescriptor,
    value: FrontmatterValue | undefined,
    onChange: (next: FrontmatterValue) => void,
  ) {
    switch (field.widget) {
      case "text":
        return (
          <input
            type="text"
            value={asString(value)}
            readOnly={field.readOnly}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "textarea":
        return (
          <textarea rows={field.rows ?? 3} value={asString(value)} onChange={(e) => onChange(e.target.value)} />
        );
      case "number":
        return (
          <input
            type="number"
            value={typeof value === "number" ? value : 0}
            onChange={(e) => onChange(Number(e.target.value))}
          />
        );
      case "bool":
        return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
      case "enum":
        return <EnumSelect value={asString(value)} options={field.options ?? []} onChange={onChange} />;
      case "tags":
        return <TagsInput value={asStringArray(value)} onChange={onChange} />;
      case "stringList":
        return <StringListEditor label={field.label} value={asStringArray(value)} onChange={onChange} />;
      case "map":
        return (
          <MapEditor
            value={asObject(value) as Record<string, string>}
            onChange={(next) => onChange(next as FrontmatterValue)}
          />
        );
      case "ref":
        return (
          <RefPicker worldId={worldId} refKinds={field.refKinds ?? []} value={asString(value)} onChange={onChange} />
        );
      case "refList":
        return (
          <StringListEditor label={field.label} value={asStringArray(value)} onChange={onChange} />
        );
      case "object": {
        const obj = asObject(value);
        return (
          <fieldset className="entity-form-object">
            {(field.fields ?? []).map((child) => (
              <label key={child.key} className="entity-form-field">
                <span>{child.label}</span>
                {renderField(child, obj[child.key], (next) => onChange({ ...obj, [child.key]: next }))}
              </label>
            ))}
          </fieldset>
        );
      }
      case "objectList":
        return (
          <ObjectListEditor
            value={asRows(value)}
            fieldKeys={(field.fields ?? []).map((f) => f.key)}
            onChange={(rows) => onChange(rows as FrontmatterValue)}
            renderRow={(row, patch) => (
              <div className="object-list-fields">
                {(field.fields ?? []).map((child) => (
                  <label key={child.key} className="entity-form-field">
                    <span>{child.label}</span>
                    {renderField(child, row[child.key] as FrontmatterValue, (next) =>
                      patch({ ...row, [child.key]: next }),
                    )}
                  </label>
                ))}
              </div>
            )}
          />
        );
      default:
        return null;
    }
  }

  const hidden = managedKeys(descriptor);

  return (
    <div className="entity-form">
      {descriptor.sections.map((section) => {
        const body = section.fields.map((field) => (
          <label key={field.key} className="entity-form-field">
            <span>{field.label}</span>
            {renderField(field, frontmatter[field.key], (next) => setKey(field.key, next))}
          </label>
        ));
        return section.collapsed ? (
          <details key={section.title} className="entity-form-section">
            <summary>{section.title}</summary>
            {body}
          </details>
        ) : (
          <fieldset key={section.title} className="entity-form-section">
            <legend>{section.title}</legend>
            {body}
          </fieldset>
        );
      })}

      <details className="entity-form-advanced">
        <summary>Advanced / raw fields</summary>
        <FrontmatterEditor value={frontmatter} onChange={onFrontmatterChange} hiddenKeys={hidden} />
      </details>

      <section className="entity-editor-panel" aria-labelledby="body-heading">
        <h4 id="body-heading">Markdown body</h4>
        <textarea className="entity-body-editor" value={body} rows={24} onChange={(e) => onBodyChange(e.target.value)} />
      </section>
    </div>
  );
}
```

> NOTE: the local `const body` inside the `.map` shadows the `body` prop. Rename the local to `fields` when implementing to avoid the collision (the prop `body` is needed for the Markdown textarea below). This is intentional guidance — do not ship the shadowed name.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityForm.test.tsx && pnpm typecheck`
Expected: PASS, no type errors (the `body` shadow fixed).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/EntityForm.tsx frontend/src/routes/library/__tests__/EntityForm.test.tsx
git commit -m "feat(frontend): EntityForm renderer with Advanced fallback (#441)"
```

---

## Task 10: Wire `EntityForm` + token badge into the editor; remove `CharacterExtras`

**Files:**
- Modify: `frontend/src/routes/library/EntityEditorView.tsx` (EditorPanel `:326-363`, header `:184-208`, imports `:14-25`)
- Delete: `frontend/src/routes/library/CharacterExtras.tsx`
- Test: `frontend/src/routes/library/__tests__/EntityEditorView.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/EntityEditorView.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { EntityEditorView } from "../EntityEditorView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      getEntity: vi.fn(),
      listEntities: vi.fn().mockResolvedValue([]),
      dependents: vi.fn().mockResolvedValue([]),
    },
  };
});

describe("EntityEditorView (character)", () => {
  it("renders the structured form and a token badge", async () => {
    vi.mocked(libraryModule.libraryApi.getEntity).mockResolvedValue({
      asset_id: "alistair",
      name: "Alistair",
      path: "p.md",
      version: 1,
      frontmatter: { name: "Alistair", role: "major_npc" },
      body: "Body text",
    } as never);

    render(
      <MemoryRouter initialEntries={["/library/worlds/w1/characters/alistair"]}>
        <Routes>
          <Route path="/library/worlds/:worldId/:kind/:entityId/*" element={<EntityEditorView />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Identity")).toBeInTheDocument());
    expect(screen.getByText(/tokens/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityEditorView.test.tsx`
Expected: FAIL (no "Identity" section — still the generic editor).

- [ ] **Step 3: Update imports in `EntityEditorView.tsx`**

Remove the `CharacterExtras` import (`:15`). Add:

```tsx
import { TokenBadge } from "../../components/TokenBadge";
import { estimateEntityTokens } from "../../components/tokens";
import { EntityForm } from "./EntityForm";
import { getDescriptor } from "./entitySchemas";
```

- [ ] **Step 4: Replace `EditorPanel` with descriptor-aware rendering**

Replace the `EditorPanel` function (`:326-363`) body so it uses `EntityForm` when a descriptor exists, else the existing generic editor. Signature gains `worldId` and `kindPlural`:

```tsx
function EditorPanel({
  worldId,
  kindPlural,
  frontmatter,
  onFrontmatterChange,
  body,
  onBodyChange,
}: {
  worldId: string;
  kindPlural: string;
  frontmatter: Frontmatter;
  onFrontmatterChange: (next: Frontmatter) => void;
  body: string;
  onBodyChange: (next: string) => void;
}) {
  const descriptor = getDescriptor(ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural);
  if (descriptor) {
    return (
      <EntityForm
        descriptor={descriptor}
        worldId={worldId}
        frontmatter={frontmatter}
        body={body}
        onFrontmatterChange={onFrontmatterChange}
        onBodyChange={onBodyChange}
      />
    );
  }
  return (
    <div className="entity-editor-panels">
      <section className="entity-editor-panel" aria-labelledby="frontmatter-heading">
        <h4 id="frontmatter-heading">Frontmatter</h4>
        <FrontmatterEditor value={frontmatter} onChange={onFrontmatterChange} hiddenKeys={ENTITY_HIDDEN_KEYS} />
      </section>
      <section className="entity-editor-panel" aria-labelledby="body-heading">
        <h4 id="body-heading">Markdown body</h4>
        <textarea className="entity-body-editor" value={body} rows={24} onChange={(e) => onBodyChange(e.target.value)} />
      </section>
    </div>
  );
}
```

Update the `<Route index>` element (`:236-245`) to pass the new props and drop the `isCharacter`/`CharacterExtras` usage:

```tsx
<EditorPanel
  worldId={worldId}
  kindPlural={kindPlural}
  frontmatter={frontmatter}
  onFrontmatterChange={patchFrontmatter}
  body={body}
  onBodyChange={(b) => {
    setBody(b);
    setDirty(true);
  }}
/>
```

Remove the now-unused `CHARACTER_HIDDEN_KEYS` constant (`:25`) and the `isCharacter` prop threading into `EditorPanel` (the descriptor lookup replaces it; `isCharacter` is still used for the Capabilities sub-tab so keep that usage).

- [ ] **Step 5: Add the token badge to the editor header**

In `EntityEditorBody`'s header (`:184-195`), add the badge next to the name/version line:

```tsx
<small>
  <code>{entity.path}</code> · v{entity.version} ·{" "}
  <TokenBadge text={`${JSON.stringify(frontmatter)}\n${body}`} />
</small>
```

(`estimateEntityTokens` is exported for reuse by SP2 list cards; the header passes text straight to `TokenBadge`. Keep the `estimateEntityTokens` import only if used — otherwise import just `TokenBadge`.)

- [ ] **Step 6: Delete `CharacterExtras.tsx`**

```bash
git rm frontend/src/routes/library/CharacterExtras.tsx
```

Confirm nothing else imports it: `cd frontend && grep -rn "CharacterExtras" src` → no results.

- [ ] **Step 7: Run tests + typecheck**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityEditorView.test.tsx && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/library/EntityEditorView.tsx frontend/src/routes/library/__tests__/EntityEditorView.test.tsx
git commit -m "feat(frontend): structured character editor + token badge (#441)"
```

---

## Task 11: Schema-drift guard (cross-language)

A committed JSON fixture of the `Character` schema property names links the backend and front-end so a backend field rename fails CI.

**Files:**
- Create: `frontend/src/routes/library/__tests__/fixtures/character-schema-properties.json`
- Modify: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`
- Modify: `backend/tests/api/test_library_routes.py`

- [ ] **Step 1: Create the fixture (from the `Character` model fields)**

Create `frontend/src/routes/library/__tests__/fixtures/character-schema-properties.json`:

```json
[
  "id", "name", "role", "world_id", "aliases", "age", "tags", "role_tags",
  "voice", "image", "images", "structural_relationships", "description", "body",
  "file_path", "file_mtime", "version", "household_id", "privacy", "extras"
]
```

- [ ] **Step 2: Add the front-end drift test**

Append to `frontend/src/routes/library/__tests__/entitySchemas.test.ts`:

```ts
import properties from "./fixtures/character-schema-properties.json";

describe("character descriptor drift", () => {
  it("only manages keys that exist in the Character schema", () => {
    const allowed = new Set(properties as string[]);
    for (const key of managedKeys(getDescriptor("character")!)) {
      expect(allowed.has(key), `descriptor key '${key}' missing from Character schema`).toBe(true);
    }
  });
});
```

- [ ] **Step 3: Run the front-end drift test**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: PASS.

- [ ] **Step 4: Add the backend test that keeps the fixture honest**

Append to `backend/tests/api/test_library_routes.py`:

```python
def test_entity_schema_character_matches_frontend_fixture(client) -> None:
    """The committed front-end fixture must list exactly the Character schema's
    property names — so a backend field rename forces a fixture update, which in
    turn re-checks the descriptor (frontend entitySchemas.test.ts)."""
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/routes/library/__tests__/fixtures/character-schema-properties.json"
    )
    expected = set(json.loads(fixture.read_text()))
    props = set(client.get("/api/library/entity-schemas/character").json()["properties"].keys())
    assert props == expected
```

- [ ] **Step 5: Run the backend test**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py::test_entity_schema_character_matches_frontend_fixture -v`
Expected: PASS. (If it fails, the `Character` model changed — update the fixture, then re-run the front-end drift test.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/__tests__/fixtures/character-schema-properties.json frontend/src/routes/library/__tests__/entitySchemas.test.ts backend/tests/api/test_library_routes.py
git commit -m "test: cross-language schema-drift guard for character descriptor (#441)"
```

---

## Task 12: Full verification + styles

**Files:**
- Modify: `frontend/src/<the library stylesheet>` (locate via `grep -rn "entity-editor-panel" frontend/src` — add minimal styles for `.entity-form-section`, `.tags-input-chip`, `.token-badge`, `.object-list-row`, `.map-editor`)

- [ ] **Step 1: Add minimal styles**

Find the stylesheet that defines existing `.entity-editor-*` / `.character-card` classes (`grep -rn "entity-editor-panel" frontend/src`). Add lightweight rules so the new widgets are legible (chips, fieldset spacing, badge as a muted inline pill). Match the surrounding style conventions; no new design system.

- [ ] **Step 2: Run the full front-end gate**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm vitest run`
Expected: all PASS.

- [ ] **Step 3: Run the full backend gate for touched files**

Run: `cd backend && uv run ruff check && uv run ruff format --check && uv run pytest tests/api/test_library_routes.py -v`
Expected: all PASS.

- [ ] **Step 4: Manual smoke (optional but recommended)**

Use the `/run` skill or `scripts/run.sh`; open a world → a character → confirm the structured form, the Advanced section preserving extra keys, and the token badge updating as you type.

- [ ] **Step 5: Commit any style changes**

```bash
git add -A
git commit -m "style(frontend): styles for structured entity form widgets (#441)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** descriptor system (T8), `<EntityForm>` + Advanced fallback (T9), widgets incl. extracted StringListEditor (T4–T7), token estimate + badge in editor header (T2,T3,T10), `entity-schemas` route + drift guard (T1,T11), `CharacterExtras` removal (T10). `images`/`privacy` intentionally land in Advanced (no widget) per spec non-goals.
- **Type consistency:** `getDescriptor`/`managedKeys`/`FieldDescriptor.widget` names are stable across T8→T9→T10; `RefPicker` props (`worldId`,`refKinds`,`value`,`onChange`) match T6 and T9; `estimateEntityTokens` exported in T2 and referenced (optionally) in T10.
- **Known sharp edge:** the `body` shadow in T9 Step 3 is flagged with a fix instruction.
