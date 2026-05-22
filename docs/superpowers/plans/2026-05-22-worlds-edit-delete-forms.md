# Worlds: edit + delete + no-JSON-inputs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add delete buttons to worlds, entities, and nested calendar items; replace every JSON textarea in the library worlds area (the three world-meta JSON fields and the FrontmatterEditor JsonField fallback) with structured forms.

**Architecture:** Two shared primitives (`StructuredValueEditor` for arbitrary JSON-shaped values, `ConfirmDestructiveDialog` for delete confirmations with dependents + optional typed confirm) ship first. Delete buttons get wired into every entry point next. Finally three dedicated world-meta forms (`WorldCalendarForm`, `WorldAtmosphereForm`, `WorldDefaultsForm`) replace the JSON textareas, and `FrontmatterEditor` uses `StructuredValueEditor` for its nested-value fallback. Frontend-only — backend `PATCH`/`DELETE` endpoints already exist.

**Tech Stack:** React 18 / TypeScript / Vitest + Testing Library / Vite (frontend). All commands run from `frontend/` unless stated otherwise.

**Spec:** `docs/superpowers/specs/2026-05-22-worlds-edit-delete-forms-design.md`.

**Branch / worktree:** Not yet created. The executing skill (subagent-driven-development or executing-plans) should create a worktree at `.worktrees/2026-05-22-worlds-edit-delete-forms/` before starting. All paths below are repo-relative.

---

## Task 1: `ConfirmDestructiveDialog` component

**Why first:** Used by every delete button later. Subsumes today's inline `ConfirmEditDialog` in `EntityEditorView.tsx`, which Task 2 then refactors to use it (proving the abstraction before we depend on it more widely).

**Files:**
- Create: `frontend/src/routes/library/ConfirmDestructiveDialog.tsx`
- Create: `frontend/src/routes/library/__tests__/ConfirmDestructiveDialog.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/ConfirmDestructiveDialog.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ConfirmDestructiveDialog } from "../ConfirmDestructiveDialog";

describe("ConfirmDestructiveDialog", () => {
  function renderOpen(props: Partial<React.ComponentProps<typeof ConfirmDestructiveDialog>> = {}) {
    return render(
      <ConfirmDestructiveDialog
        open
        title="Delete thing?"
        onConfirm={props.onConfirm ?? vi.fn()}
        onCancel={props.onCancel ?? vi.fn()}
        {...props}
      />,
    );
  }

  it("does not render when open=false", () => {
    render(
      <ConfirmDestructiveDialog
        open={false}
        title="Delete thing?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByText("Delete thing?")).not.toBeInTheDocument();
  });

  it("confirm fires when no dependents and no typed confirmation", () => {
    const onConfirm = vi.fn();
    renderOpen({ onConfirm });
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("confirm is disabled while dependents=undefined", () => {
    renderOpen({ dependents: undefined, body: "Loading dependents…" });
    expect(screen.getByRole("button", { name: /^Delete$/ })).toBeDisabled();
  });

  it("renders dependent list when populated", () => {
    renderOpen({
      dependents: [
        { id: "c1", name: "First Campaign" },
        { id: "c2", name: "Second Campaign" },
      ],
    });
    expect(screen.getByText("First Campaign")).toBeInTheDocument();
    expect(screen.getByText("Second Campaign")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Delete$/ })).toBeEnabled();
  });

  it("typed confirmation gates the confirm button case-sensitively", () => {
    const onConfirm = vi.fn();
    renderOpen({
      onConfirm,
      dependents: [],
      typedConfirmation: { expected: "sakura-high", label: "Type id to confirm" },
    });
    const input = screen.getByLabelText(/Type id to confirm/);
    const confirm = screen.getByRole("button", { name: /^Delete$/ });
    expect(confirm).toBeDisabled();

    fireEvent.change(input, { target: { value: "Sakura-High" } }); // wrong case
    expect(confirm).toBeDisabled();

    fireEvent.change(input, { target: { value: "sakura-high" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("shows busy state and disables confirm when busy=true", () => {
    renderOpen({ busy: true, dependents: [] });
    const confirm = screen.getByRole("button", { name: /Deleting…/ });
    expect(confirm).toBeDisabled();
  });

  it("renders error inside the dialog and stays open", () => {
    renderOpen({ dependents: [], error: "boom" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("cancel fires onCancel", () => {
    const onCancel = vi.fn();
    renderOpen({ onCancel });
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd frontend && pnpm test src/routes/library/__tests__/ConfirmDestructiveDialog.test.tsx
```

Expected: FAIL with "Cannot find module '../ConfirmDestructiveDialog'".

- [ ] **Step 3: Implement `ConfirmDestructiveDialog`**

Create `frontend/src/routes/library/ConfirmDestructiveDialog.tsx`:

```tsx
import { useState } from "react";

import type { CampaignRef } from "../../api/library";

interface Props {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  /** `undefined` = lookup in flight; `[]` = no dependents. */
  dependents?: CampaignRef[];
  typedConfirmation?: { expected: string; label: string };
  confirmLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDestructiveDialog({
  open,
  title,
  body,
  dependents,
  typedConfirmation,
  confirmLabel = "Delete",
  busyLabel = "Deleting…",
  busy = false,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const [typed, setTyped] = useState("");
  if (!open) return null;

  const dependentsLoading = dependents === undefined;
  const typedOk = !typedConfirmation || typed === typedConfirmation.expected;
  const confirmDisabled = busy || dependentsLoading || !typedOk;

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-destructive-title"
    >
      <div className="modal">
        <h4 id="confirm-destructive-title">{title}</h4>
        {body && <div>{body}</div>}
        {dependents && dependents.length > 0 && (
          <>
            <p>
              Affects {dependents.length} campaign
              {dependents.length === 1 ? "" : "s"}:
            </p>
            <ul>
              {dependents.map((c) => (
                <li key={c.id}>{c.name || c.id}</li>
              ))}
            </ul>
          </>
        )}
        {typedConfirmation && (
          <label>
            <span>{typedConfirmation.label}</span>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              autoFocus
            />
          </label>
        )}
        {error && (
          <p className="library-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={confirmDisabled}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd frontend && pnpm test src/routes/library/__tests__/ConfirmDestructiveDialog.test.tsx
```

Expected: PASS (8 tests).

- [ ] **Step 5: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/ConfirmDestructiveDialog.tsx \
        frontend/src/routes/library/__tests__/ConfirmDestructiveDialog.test.tsx
git commit -m "feat(library): add ConfirmDestructiveDialog with dependents + typed-confirm"
```

---

## Task 2: Replace `ConfirmEditDialog` in `EntityEditorView`

**Why next:** Proves the new dialog handles the existing save-warn case before any new delete callers depend on it.

**Files:**
- Modify: `frontend/src/routes/library/EntityEditorView.tsx`

- [ ] **Step 1: Read current callers**

Open `frontend/src/routes/library/EntityEditorView.tsx`. Locate two call sites that render `ConfirmEditDialog` (one inside `EntityEditorBody`, one inside `GreetingEditorBody`) plus the function definition at the bottom of the file (around lines 320–360).

- [ ] **Step 2: Replace the call sites**

In both `EntityEditorBody` and `GreetingEditorBody`, replace the existing `<ConfirmEditDialog … />` block (the one inside `{confirmEdit && (…)}`) with:

```tsx
{confirmEdit && pendingSave && (
  <ConfirmDestructiveDialog
    open
    title="Save edit to library?"
    body={
      <>
        <p>
          This entity is referenced by {confirmEdit.dependents.length} campaign
          {confirmEdit.dependents.length === 1 ? "" : "s"}:
        </p>
        <ul>
          {confirmEdit.dependents.map((c) => (
            <li key={c.id}>{c.name || c.id}</li>
          ))}
        </ul>
        <p>
          Pinned campaigns will continue to see the previous version until they explicitly upgrade.
          Tracking-latest campaigns pick up the change immediately.
        </p>
      </>
    }
    busy={saving}
    busyLabel="Saving…"
    confirmLabel="Save anyway"
    onConfirm={() => void performSave()}
    onCancel={() => {
      setConfirmEdit(null);
      setPendingSave(false);
    }}
  />
)}
```

- [ ] **Step 3: Delete the `ConfirmEditDialog` function**

Remove the entire `function ConfirmEditDialog({ dependents, busy, onConfirm, onCancel, pending }: { … }) { … }` block from `EntityEditorView.tsx`.

- [ ] **Step 4: Add the import**

Near the other route-local imports at the top of the file, add:

```tsx
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
```

- [ ] **Step 5: Run typecheck + tests**

```bash
cd frontend && pnpm typecheck && pnpm test
```

Expected: typecheck clean; all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/EntityEditorView.tsx
git commit -m "refactor(library): EntityEditorView uses ConfirmDestructiveDialog for save-warn"
```

---

## Task 3: `StructuredValueEditor` — scalars + null placeholder

**Why now:** Smallest slice of the editor. Builds out the file skeleton + scalar input rendering + the "(empty)" placeholder for `null`. Later tasks add arrays, objects, and the type-change confirm.

**Files:**
- Create: `frontend/src/routes/library/StructuredValueEditor.tsx`
- Create: `frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { StructuredValueEditor } from "../StructuredValueEditor";

describe("StructuredValueEditor — scalars", () => {
  it("string renders as text input; typing fires onChange with the new string", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value="hello" onChange={onChange} />);
    const input = screen.getByDisplayValue("hello");
    fireEvent.change(input, { target: { value: "hi" } });
    expect(onChange).toHaveBeenLastCalledWith("hi");
  });

  it("number renders as number input; typing fires onChange with a number", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={42} onChange={onChange} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "7" } });
    expect(onChange).toHaveBeenLastCalledWith(7);
  });

  it("boolean renders as a checkbox", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={true} onChange={onChange} />);
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it("null shows (empty) placeholder and a type picker that initializes a default", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={null} onChange={onChange} />);
    expect(screen.getByText(/\(empty\)/)).toBeInTheDocument();
    const picker = screen.getByLabelText(/Type/);
    fireEvent.change(picker, { target: { value: "text" } });
    expect(onChange).toHaveBeenLastCalledWith("");

    onChange.mockClear();
    fireEvent.change(picker, { target: { value: "list" } });
    expect(onChange).toHaveBeenLastCalledWith([]);

    onChange.mockClear();
    fireEvent.change(picker, { target: { value: "object" } });
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("readOnly disables scalar inputs", () => {
    render(<StructuredValueEditor value="x" onChange={vi.fn()} readOnly />);
    expect(screen.getByDisplayValue("x")).toHaveAttribute("readonly");
  });
});
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: FAIL with "Cannot find module '../StructuredValueEditor'".

- [ ] **Step 3: Implement scalar layouts**

Create `frontend/src/routes/library/StructuredValueEditor.tsx`:

```tsx
import { useId } from "react";

export type StructuredValue =
  | string
  | number
  | boolean
  | null
  | StructuredValue[]
  | { [key: string]: StructuredValue };

interface Props {
  value: unknown;
  onChange: (next: StructuredValue) => void;
  readOnly?: boolean;
}

type Kind = "text" | "number" | "boolean" | "list" | "object" | "null";

function kindOf(v: unknown): Kind {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return "text";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  if (Array.isArray(v)) return "list";
  return "object";
}

function defaultFor(kind: Kind): StructuredValue {
  switch (kind) {
    case "text":
      return "";
    case "number":
      return 0;
    case "boolean":
      return false;
    case "list":
      return [];
    case "object":
      return {};
    case "null":
      return null;
  }
}

export function StructuredValueEditor({ value, onChange, readOnly = false }: Props) {
  const kind = kindOf(value);

  if (kind === "null") {
    return <NullRow onChange={onChange} readOnly={readOnly} />;
  }
  if (kind === "text") {
    return (
      <input
        type="text"
        value={value as string}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (kind === "number") {
    return (
      <input
        type="number"
        value={value as number}
        readOnly={readOnly}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    );
  }
  if (kind === "boolean") {
    return (
      <input
        type="checkbox"
        checked={value as boolean}
        disabled={readOnly}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  // list + object layouts arrive in later tasks
  return <p className="library-status">(complex value — editor coming)</p>;
}

function NullRow({
  onChange,
  readOnly,
}: {
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  const id = useId();
  return (
    <div className="structured-null">
      <span>(empty)</span>
      <label htmlFor={id} className="structured-type-label">
        Type
      </label>
      <select
        id={id}
        disabled={readOnly}
        defaultValue="null"
        onChange={(e) => {
          const k = e.target.value as Kind;
          if (k === "null") return;
          onChange(defaultFor(k));
        }}
      >
        <option value="null">(choose)</option>
        <option value="text">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="list">list</option>
        <option value="object">object</option>
      </select>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/StructuredValueEditor.tsx \
        frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx
git commit -m "feat(library): StructuredValueEditor scalars + null placeholder"
```

---

## Task 4: `StructuredValueEditor` — arrays

**Files:**
- Modify: `frontend/src/routes/library/StructuredValueEditor.tsx`
- Modify: `frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx`

- [ ] **Step 1: Add the failing array tests**

Append to the existing test file:

```tsx
describe("StructuredValueEditor — arrays", () => {
  it("list renders numbered rows and a single + add item button", () => {
    render(<StructuredValueEditor value={["a", "b"]} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("a")).toBeInTheDocument();
    expect(screen.getByDisplayValue("b")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add item/i })).toBeInTheDocument();
  });

  it("clicking + add item appends a null row", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add item/i }));
    expect(onChange).toHaveBeenLastCalledWith(["a", null]);
  });

  it("per-row delete button removes that item", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a", "b", "c"]} onChange={onChange} />);
    const deletes = screen.getAllByRole("button", { name: /^Remove item 2$/ });
    fireEvent.click(deletes[0]);
    expect(onChange).toHaveBeenLastCalledWith(["a", "c"]);
  });

  it("editing an item bubbles the new array up", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a", "b"]} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("b"), { target: { value: "B" } });
    expect(onChange).toHaveBeenLastCalledWith(["a", "B"]);
  });

  it("nested object inside a list propagates edits", () => {
    const onChange = vi.fn();
    render(
      <StructuredValueEditor
        value={[{ name: "January", days: 31 }]}
        onChange={onChange}
      />,
    );
    const daysInput = screen.getByDisplayValue("31");
    fireEvent.change(daysInput, { target: { value: "30" } });
    expect(onChange).toHaveBeenLastCalledWith([{ name: "January", days: 30 }]);
  });
});
```

(The `nested object` test will only pass after Task 5 adds object support. Mark it as failing if needed — actually skip it for now; we'll un-skip in Task 5.)

Use `it.skip` for the last test in this task; un-skip in Task 5:

```tsx
  it.skip("nested object inside a list propagates edits", () => {
```

- [ ] **Step 2: Run the tests and verify the four non-skipped fail**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: FAIL on the four new array tests (the placeholder "complex value — editor coming" renders instead).

- [ ] **Step 3: Add the array layout**

In `frontend/src/routes/library/StructuredValueEditor.tsx`, replace the `(complex value — editor coming)` placeholder branch with an arrays branch. The whole component body becomes:

```tsx
  if (kind === "null") return <NullRow onChange={onChange} readOnly={readOnly} />;
  if (kind === "text" || kind === "number" || kind === "boolean") {
    return (
      <ScalarRow value={value as string | number | boolean} kind={kind} onChange={onChange} readOnly={readOnly} />
    );
  }
  if (kind === "list") {
    return (
      <ArrayRows
        items={value as StructuredValue[]}
        onChange={onChange}
        readOnly={readOnly}
      />
    );
  }
  // object branch arrives in Task 5
  return <p className="library-status">(object editor coming)</p>;
}
```

Extract the existing scalar inline JSX into a `ScalarRow` helper (no behavior change), then add the new components below the file:

```tsx
function ScalarRow({
  value,
  kind,
  onChange,
  readOnly,
}: {
  value: string | number | boolean;
  kind: "text" | "number" | "boolean";
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  if (kind === "text") {
    return (
      <input
        type="text"
        value={value as string}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (kind === "number") {
    return (
      <input
        type="number"
        value={value as number}
        readOnly={readOnly}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    );
  }
  return (
    <input
      type="checkbox"
      checked={value as boolean}
      disabled={readOnly}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

function ArrayRows({
  items,
  onChange,
  readOnly,
}: {
  items: StructuredValue[];
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  const updateAt = (i: number, next: StructuredValue) => {
    const out = items.slice();
    out[i] = next;
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = items.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () => onChange([...items, null]);
  return (
    <ol className="structured-list">
      {items.map((item, i) => (
        <li key={i} className="structured-list-row">
          <StructuredValueEditor
            value={item}
            onChange={(next) => updateAt(i, next)}
            readOnly={readOnly}
          />
          {!readOnly && (
            <button
              type="button"
              className="structured-remove"
              onClick={() => removeAt(i)}
              aria-label={`Remove item ${i + 1}`}
            >
              ×
            </button>
          )}
        </li>
      ))}
      {!readOnly && (
        <li>
          <button type="button" className="structured-add" onClick={append}>
            + add item
          </button>
        </li>
      )}
    </ol>
  );
}
```

- [ ] **Step 4: Run the tests**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: PASS (5 scalar + 4 array tests = 9; 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/StructuredValueEditor.tsx \
        frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx
git commit -m "feat(library): StructuredValueEditor array rows with add/remove"
```

---

## Task 5: `StructuredValueEditor` — objects + duplicate-key validation

**Files:**
- Modify: `frontend/src/routes/library/StructuredValueEditor.tsx`
- Modify: `frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx`

- [ ] **Step 1: Add the failing object tests + un-skip the nested test from Task 4**

In `frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx`, change the `it.skip("nested object inside a list propagates edits", …)` from Task 4 to `it(…)`. Then append:

```tsx
describe("StructuredValueEditor — objects", () => {
  it("object renders one row per key with value editors", () => {
    render(<StructuredValueEditor value={{ hair: "brown", eyes: "green" }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("brown")).toBeInTheDocument();
    expect(screen.getByDisplayValue("green")).toBeInTheDocument();
    expect(screen.getByDisplayValue("hair")).toBeInTheDocument();
    expect(screen.getByDisplayValue("eyes")).toBeInTheDocument();
  });

  it("clicking + add field appends an empty key with null value", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add field/i }));
    // Empty-key rows do not propagate to onChange until they are renamed.
    expect(onChange).not.toHaveBeenCalled();
  });

  it("typing a new key fires onChange with the renamed key", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("a"), { target: { value: "alpha" } });
    expect(onChange).toHaveBeenLastCalledWith({ alpha: "1" });
  });

  it("editing a value bubbles the new object up", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ hair: "brown" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("brown"), { target: { value: "red" } });
    expect(onChange).toHaveBeenLastCalledWith({ hair: "red" });
  });

  it("per-row delete removes that key", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1", b: "2" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove field a$/ }));
    expect(onChange).toHaveBeenLastCalledWith({ b: "2" });
  });

  it("renaming a key to a duplicate shows an error hint and does not fire onChange", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1", b: "2" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("b"), { target: { value: "a" } });
    expect(screen.getByText(/already exists/i)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: FAIL on the object tests + the un-skipped nested test.

- [ ] **Step 3: Implement the object layout**

In `frontend/src/routes/library/StructuredValueEditor.tsx`, replace the `(object editor coming)` placeholder with an `<ObjectRows />` branch, and add the component below `ArrayRows`:

```tsx
  if (kind === "object") {
    return (
      <ObjectRows
        record={value as Record<string, StructuredValue>}
        onChange={onChange}
        readOnly={readOnly}
      />
    );
  }
  return null;
}
```

```tsx
function ObjectRows({
  record,
  onChange,
  readOnly,
}: {
  record: Record<string, StructuredValue>;
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  // Holds pending (unsaved) empty-key rows so the user can type a name before
  // we commit them upstream. We also track a per-key transient "draft" name so
  // the user can hold an empty / duplicate value mid-rename without it
  // hitting onChange.
  const entries = Object.entries(record);
  const updateValue = (key: string, next: StructuredValue) => {
    onChange({ ...record, [key]: next });
  };
  const removeKey = (key: string) => {
    const next = { ...record };
    delete next[key];
    onChange(next);
  };
  const renameKey = (oldKey: string, newKey: string) => {
    const trimmed = newKey.trim();
    if (!trimmed || trimmed === oldKey) return { ok: false as const, reason: trimmed ? null : "empty" };
    if (trimmed in record) return { ok: false as const, reason: "duplicate" };
    const next: Record<string, StructuredValue> = {};
    for (const [k, v] of Object.entries(record)) {
      next[k === oldKey ? trimmed : k] = v;
    }
    onChange(next);
    return { ok: true as const, reason: null };
  };
  const addEmpty = () => {
    // Empty-key rows live in local component state only; they don't propagate
    // until the user types a non-duplicate name. We model this with an extra
    // sentinel by re-rendering the editor with the same record + pending row
    // count — simpler: use a separate state for pendingRows.
  };

  return (
    <div className="structured-object">
      {entries.map(([key, val]) => (
        <ObjectRow
          key={key}
          name={key}
          value={val}
          existingKeys={new Set(Object.keys(record))}
          onRename={(next) => renameKey(key, next)}
          onValueChange={(next) => updateValue(key, next)}
          onRemove={() => removeKey(key)}
          readOnly={readOnly}
        />
      ))}
      <PendingObjectRow
        existingKeys={new Set(Object.keys(record))}
        onCommit={(name) => onChange({ ...record, [name]: null })}
        readOnly={readOnly}
      />
    </div>
  );
}

function ObjectRow({
  name,
  value,
  existingKeys,
  onRename,
  onValueChange,
  onRemove,
  readOnly,
}: {
  name: string;
  value: StructuredValue;
  existingKeys: Set<string>;
  onRename: (next: string) => { ok: boolean; reason: string | null };
  onValueChange: (next: StructuredValue) => void;
  onRemove: () => void;
  readOnly: boolean;
}) {
  const [draft, setDraft] = useState(name);
  const [err, setErr] = useState<string | null>(null);
  // Reflect external rename (key changed underneath us).
  if (draft !== name && !err) {
    // Only sync when there is no pending error draft.
    setDraft(name);
  }
  return (
    <div className="structured-object-row">
      <input
        type="text"
        value={draft}
        readOnly={readOnly}
        aria-label={`Key for ${name}`}
        onChange={(e) => {
          const next = e.target.value;
          setDraft(next);
          if (!next.trim()) {
            setErr(null);
            return;
          }
          if (next !== name && existingKeys.has(next)) {
            setErr("key already exists");
            return;
          }
          const result = onRename(next);
          setErr(result.reason === "duplicate" ? "key already exists" : null);
        }}
      />
      <div className="structured-object-value">
        <StructuredValueEditor value={value} onChange={onValueChange} readOnly={readOnly} />
      </div>
      {!readOnly && (
        <button
          type="button"
          className="structured-remove"
          onClick={onRemove}
          aria-label={`Remove field ${name}`}
        >
          ×
        </button>
      )}
      {err && <p className="frontmatter-error">{err}</p>}
    </div>
  );
}

function PendingObjectRow({
  existingKeys,
  onCommit,
  readOnly,
}: {
  existingKeys: Set<string>;
  onCommit: (name: string) => void;
  readOnly: boolean;
}) {
  const [draft, setDraft] = useState("");
  if (readOnly) return null;
  const trimmed = draft.trim();
  const valid = trimmed.length > 0 && !existingKeys.has(trimmed);
  return (
    <div className="structured-object-pending">
      <input
        type="text"
        value={draft}
        placeholder="add field…"
        onChange={(e) => setDraft(e.target.value)}
      />
      <button
        type="button"
        className="structured-add"
        disabled={!valid}
        onClick={() => {
          onCommit(trimmed);
          setDraft("");
        }}
      >
        + add field
      </button>
    </div>
  );
}
```

Also add the missing `useState` import at the top:

```tsx
import { useId, useState } from "react";
```

- [ ] **Step 4: Run the tests**

```bash
cd frontend && pnpm test src/routes/library/__tests__/StructuredValueEditor.test.tsx
```

Expected: PASS (all 15+ tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/StructuredValueEditor.tsx \
        frontend/src/routes/library/__tests__/StructuredValueEditor.test.tsx
git commit -m "feat(library): StructuredValueEditor object rows + duplicate-key guard"
```

---

## Task 6: Add minimal CSS for `StructuredValueEditor`

**Why:** Component renders functionally but unstyled. Borrow the existing `.frontmatter-*` aesthetics.

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Append styles**

Append at the end of `frontend/src/index.css`:

```css
/* ----- StructuredValueEditor ----- */

.structured-null {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--fg-muted);
}

.structured-type-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.structured-list {
  list-style: decimal inside;
  margin: 0;
  padding-left: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.structured-list-row {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
}

.structured-list-row > :first-child {
  flex: 1;
}

.structured-object {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.structured-object-row,
.structured-object-pending {
  display: grid;
  grid-template-columns: minmax(8rem, 12rem) 1fr auto;
  gap: var(--space-2);
  align-items: center;
}

.structured-add {
  background: transparent;
  border: 1px dashed var(--border);
  padding: var(--space-1) var(--space-2);
  font-size: 0.85rem;
}

.structured-remove {
  border: none;
  background: transparent;
  color: var(--fg-muted);
  font-size: 1.05rem;
  line-height: 1;
  padding: 0 var(--space-1);
  align-self: center;
}

.structured-remove:hover {
  color: var(--danger);
  background: transparent;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(library): add CSS for StructuredValueEditor rows"
```

---

## Task 7: `fetchWorldDependents` helper extracted from `WorldDependentsView`

**Why:** World-delete and the existing dependents tab both need the same composition fan-out. Extract once.

**Files:**
- Modify: `frontend/src/api/library.ts` (or wherever the appropriate spot is — `libraryApi` object)
- Modify: `frontend/src/routes/library/WorldDependentsView.tsx`
- Create: `frontend/src/api/__tests__/world-dependents.test.ts`

- [ ] **Step 1: Read the existing inline fan-out**

Open `frontend/src/routes/library/WorldDependentsView.tsx`. The `useEffect` at lines ~41–76 lists `/api/campaigns`, fetches each `/api/campaigns/{id}/composition`, filters to refs whose `world_id` matches. The helper will encapsulate exactly this loop.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/api/__tests__/world-dependents.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchWorldDependents } from "../library";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("fetchWorldDependents", () => {
  it("returns campaigns whose composition references the given world", async () => {
    const responses: Record<string, unknown> = {
      "/api/campaigns": [
        { id: "c1", name: "First" },
        { id: "c2", name: "Second" },
        { id: "c3", name: "Third" },
      ],
      "/api/campaigns/c1/composition": { worlds: [{ world_id: "sakura-high", priority: 1 }] },
      "/api/campaigns/c2/composition": { worlds: [{ world_id: "other", priority: 1 }] },
      "/api/campaigns/c3/composition": { worlds: [{ world_id: "sakura-high", priority: 2 }] },
    };
    globalThis.fetch = vi.fn(async (url: string) => {
      const body = responses[url];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const result = await fetchWorldDependents("sakura-high");
    expect(result.map((c) => c.id).sort()).toEqual(["c1", "c3"]);
  });

  it("skips campaigns whose composition lookup fails", async () => {
    globalThis.fetch = vi.fn(async (url: string) => {
      if (url === "/api/campaigns") {
        return new Response(JSON.stringify([{ id: "c1", name: "First" }]), { status: 200 });
      }
      return new Response("nope", { status: 500 });
    }) as unknown as typeof fetch;
    const result = await fetchWorldDependents("sakura-high");
    expect(result).toEqual([]);
  });
});
```

- [ ] **Step 3: Run and verify it fails**

```bash
cd frontend && pnpm test src/api/__tests__/world-dependents.test.ts
```

Expected: FAIL — `fetchWorldDependents` is not exported.

- [ ] **Step 4: Add the helper**

In `frontend/src/api/library.ts`, append (before the final `export const libraryApi = …` block, or wherever helpers live in the file — locate the `CampaignRef` import / type first):

```ts
interface _CampaignSummary {
  id: string;
  name?: string;
}
interface _WorldRef {
  world_id: string;
}
interface _Composition {
  worlds?: _WorldRef[];
}

/**
 * Lists campaigns whose composition references `worldId`. Fans out across
 * `/api/campaigns` and each composition; failures on individual compositions
 * are silently skipped (the campaign just doesn't appear in the result).
 */
export async function fetchWorldDependents(worldId: string): Promise<CampaignRef[]> {
  const campaigns = await request<_CampaignSummary[]>("GET", `/campaigns`);
  const out: CampaignRef[] = [];
  for (const c of campaigns) {
    try {
      const comp = await request<_Composition>(
        "GET",
        `/campaigns/${encodeURIComponent(c.id)}/composition`,
      );
      if (comp.worlds?.some((r) => r.world_id === worldId)) {
        out.push({ id: c.id, name: c.name ?? "" });
      }
    } catch {
      // skip
    }
  }
  return out;
}
```

If `request` is a private helper in the file, use whatever the existing module-internal fetcher is (check the file). If the file's `request` accepts a path without `/api` prefix (look at `listWorlds` — it uses `/library/worlds`, so prefix is added by `request`), follow the same pattern.

- [ ] **Step 5: Run typecheck + the test**

```bash
cd frontend && pnpm typecheck && pnpm test src/api/__tests__/world-dependents.test.ts
```

Expected: pass.

- [ ] **Step 6: Replace the inline fan-out in `WorldDependentsView`**

In `frontend/src/routes/library/WorldDependentsView.tsx`, simplify the `useEffect` to use the helper. Keep the `DependentRow` shape (which extends `CampaignRef` with the matching `ref`) — the helper returns only `CampaignRef`, so this view keeps its own loop OR we extend the helper to optionally return the matching ref. **Decision:** keep `WorldDependentsView` using its own fan-out for the ref details (it needs `priority` / `bound_at_version` / `track_latest` / `include`) but factor out a `_fetchCompositions(worldId)` helper colocated in the same file. The exported `fetchWorldDependents(worldId)` for the delete dialog only needs `CampaignRef[]` — leave it as written.

Concretely: no changes to `WorldDependentsView.tsx` in this task. Note in the commit message that the view's richer needs justify keeping its inline loop.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/library.ts \
        frontend/src/api/__tests__/world-dependents.test.ts
git commit -m "feat(library): add fetchWorldDependents helper for world-delete dialog"
```

---

## Task 8: `WorldsListView` per-card delete

**Files:**
- Modify: `frontend/src/routes/library/WorldsListView.tsx`
- Create: `frontend/src/routes/library/__tests__/WorldsListView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/WorldsListView.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { WorldsListView } from "../WorldsListView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listWorlds: vi.fn(),
      deleteWorld: vi.fn(),
    },
    fetchWorldDependents: vi.fn(),
  };
});

describe("WorldsListView delete", () => {
  it("opens the confirm dialog with dependents and deletes on confirm", async () => {
    vi.mocked(libraryModule.libraryApi.listWorlds).mockResolvedValue([
      { id: "sakura-high", name: "Sakura High", description: "", tags: [], genre: "", calendar: {}, atmosphere: {}, defaults: {}, version: 1 },
    ]);
    vi.mocked(libraryModule.fetchWorldDependents).mockResolvedValue([
      { id: "camp1", name: "Camp One" },
    ]);
    vi.mocked(libraryModule.libraryApi.deleteWorld).mockResolvedValue(undefined);

    render(
      <MemoryRouter>
        <WorldsListView />
      </MemoryRouter>,
    );

    await waitFor(() => screen.getByText("Sakura High"));
    fireEvent.click(screen.getByRole("button", { name: /Delete world/i }));

    // Dialog opens, dependents resolve, typed-confirm needs the id.
    await waitFor(() => screen.getByText(/Camp One/));
    const typed = screen.getByLabelText(/type id/i);
    fireEvent.change(typed, { target: { value: "sakura-high" } });
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));

    await waitFor(() =>
      expect(libraryModule.libraryApi.deleteWorld).toHaveBeenCalledWith("sakura-high"),
    );
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/WorldsListView.test.tsx
```

Expected: FAIL — there is no Delete world button on the cards.

- [ ] **Step 3: Wire delete into `WorldsListView`**

In `frontend/src/routes/library/WorldsListView.tsx`, add state and a dialog:

```tsx
// Add near the top, alongside other imports:
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { fetchWorldDependents } from "../../api/library";
import type { CampaignRef } from "../../api/library";

// Inside the component, after the existing useState block, add:
const [deleting, setDeleting] = useState<{
  worldId: string;
  worldName: string;
  dependents: CampaignRef[] | undefined;
  busy: boolean;
  err: string | null;
} | null>(null);

async function openDelete(worldId: string, worldName: string) {
  setDeleting({ worldId, worldName, dependents: undefined, busy: false, err: null });
  try {
    const deps = await fetchWorldDependents(worldId);
    setDeleting((d) => (d && d.worldId === worldId ? { ...d, dependents: deps } : d));
  } catch {
    setDeleting((d) => (d && d.worldId === worldId ? { ...d, dependents: [] } : d));
  }
}

async function confirmDelete() {
  if (!deleting) return;
  setDeleting({ ...deleting, busy: true, err: null });
  try {
    await libraryApi.deleteWorld(deleting.worldId);
    setDeleting(null);
    reload();
  } catch (err) {
    setDeleting({
      ...deleting,
      busy: false,
      err: err instanceof ApiError ? err.message : String(err),
    });
  }
}
```

Inside each list `<li>` (next to the `<Link>`), append a delete button:

```tsx
<button
  type="button"
  className="library-card-action"
  onClick={(e) => {
    e.preventDefault();
    void openDelete(s.id, s.name || s.id);
  }}
>
  Delete world
</button>
```

Below the `<AsyncBoundary>` block, render the dialog:

```tsx
{deleting && (
  <ConfirmDestructiveDialog
    open
    title={`Delete world "${deleting.worldName}"?`}
    body={
      <p>
        This permanently removes the world directory and all its entities. Cannot be undone.
      </p>
    }
    dependents={deleting.dependents}
    typedConfirmation={{ expected: deleting.worldId, label: `Type id "${deleting.worldId}" to confirm` }}
    busy={deleting.busy}
    error={deleting.err}
    onConfirm={() => void confirmDelete()}
    onCancel={() => setDeleting(null)}
  />
)}
```

- [ ] **Step 4: Run typecheck + the test**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/WorldsListView.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldsListView.tsx \
        frontend/src/routes/library/__tests__/WorldsListView.test.tsx
git commit -m "feat(library): WorldsListView per-card delete with typed confirm"
```

---

## Task 9: `WorldDetailView` "Delete world" header button

**Files:**
- Modify: `frontend/src/routes/library/WorldDetailView.tsx`

- [ ] **Step 1: Add the header button + dialog**

In `frontend/src/routes/library/WorldDetailView.tsx`, mirror the state from Task 8. Reuse the same `ConfirmDestructiveDialog` config; on success, `navigate("/library/worlds")`.

Add the imports:

```tsx
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { fetchWorldDependents } from "../../api/library";
import type { CampaignRef } from "../../api/library";
```

Add state inside the component (near `const [forkErr, setForkErr] …`):

```tsx
const [deleting, setDeleting] = useState<{
  dependents: CampaignRef[] | undefined;
  busy: boolean;
  err: string | null;
} | null>(null);
```

Two handlers:

```tsx
async function openDelete() {
  setDeleting({ dependents: undefined, busy: false, err: null });
  try {
    const deps = await fetchWorldDependents(worldId);
    setDeleting((d) => (d ? { ...d, dependents: deps } : d));
  } catch {
    setDeleting((d) => (d ? { ...d, dependents: [] } : d));
  }
}

async function confirmDelete() {
  if (!deleting) return;
  setDeleting({ ...deleting, busy: true, err: null });
  try {
    await libraryApi.deleteWorld(worldId);
    navigate("/library/worlds");
  } catch (err) {
    setDeleting({
      ...deleting,
      busy: false,
      err: err instanceof ApiError ? err.message : String(err),
    });
  }
}
```

Add a button next to "Fork world" / "Import character card":

```tsx
<button
  type="button"
  className="world-delete-button"
  onClick={() => void openDelete()}
>
  Delete world
</button>
```

Render the dialog above the `<Outlet />`:

```tsx
{deleting && (
  <ConfirmDestructiveDialog
    open
    title={`Delete world "${data?.name || worldId}"?`}
    body={
      <p>
        This permanently removes the world directory and all its entities. Cannot be undone.
      </p>
    }
    dependents={deleting.dependents}
    typedConfirmation={{ expected: worldId, label: `Type id "${worldId}" to confirm` }}
    busy={deleting.busy}
    error={deleting.err}
    onConfirm={() => void confirmDelete()}
    onCancel={() => setDeleting(null)}
  />
)}
```

- [ ] **Step 2: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: clean.

- [ ] **Step 3: Manual smoke (dev server)**

Start the dev server (`pnpm dev`), open `/library/worlds/<some-world>`, click Delete world, verify the dialog opens with dependents (if any) and the typed-confirm gating works. Cancel — don't actually delete a real world.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/WorldDetailView.tsx
git commit -m "feat(library): WorldDetailView Delete-world button"
```

---

## Task 10: `EntityListView` per-card delete

**Files:**
- Modify: `frontend/src/routes/library/EntityListView.tsx`
- Create: `frontend/src/routes/library/__tests__/EntityListView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/EntityListView.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { EntityListView } from "../EntityListView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listEntities: vi.fn(),
      deleteEntity: vi.fn(),
      dependents: vi.fn(),
    },
  };
});

describe("EntityListView delete", () => {
  it("opens dialog with dependents and deletes on confirm", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
      {
        id: "worlds/w/characters/ochaco",
        world_id: "w",
        kind: "character",
        asset_id: "ochaco",
        name: "Ochaco",
        path: "x.md",
        frontmatter: { name: "Ochaco" },
        body: "",
        tags: [],
      } as never,
    ]);
    vi.mocked(libraryModule.libraryApi.dependents).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.deleteEntity).mockResolvedValue(undefined);

    render(
      <MemoryRouter initialEntries={["/library/worlds/w/characters"]}>
        <Routes>
          <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => screen.getByText("Ochaco"));
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));
    await waitFor(() => screen.getByText(/permanently removes/i));
    // No dependents loaded ⇒ confirm enabled.
    const confirm = screen.getAllByRole("button", { name: /^Delete$/ }).at(-1)!;
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(libraryModule.libraryApi.deleteEntity).toHaveBeenCalledWith("w", "characters", "ochaco"),
    );
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/EntityListView.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Wire delete into `EntityListView`**

In `frontend/src/routes/library/EntityListView.tsx`, mirror the state and handlers from Task 8 but for entities. The dialog's `body` mentions the entity name; no typed-confirm. Each card gets a per-row `Delete` button next to the existing `Convert` button (only present for `lore`).

Add imports:

```tsx
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import type { CampaignRef } from "../../api/library";
```

State:

```tsx
const [deleting, setDeleting] = useState<{
  entityId: string;
  entityName: string;
  dependents: CampaignRef[] | undefined;
  busy: boolean;
  err: string | null;
} | null>(null);
```

Handlers:

```tsx
async function openDelete(entityId: string, entityName: string) {
  setDeleting({ entityId, entityName, dependents: undefined, busy: false, err: null });
  try {
    const deps = await libraryApi.dependents(worldId, kindPlural, entityId);
    setDeleting((d) => (d && d.entityId === entityId ? { ...d, dependents: deps } : d));
  } catch {
    setDeleting((d) => (d && d.entityId === entityId ? { ...d, dependents: [] } : d));
  }
}

async function confirmDelete() {
  if (!deleting) return;
  setDeleting({ ...deleting, busy: true, err: null });
  try {
    await libraryApi.deleteEntity(worldId, kindPlural, deleting.entityId);
    setDeleting(null);
    reload();
  } catch (err) {
    setDeleting({
      ...deleting,
      busy: false,
      err: err instanceof ApiError ? err.message : String(err),
    });
  }
}
```

Card button (next to Convert):

```tsx
<button
  type="button"
  className="library-card-action"
  onClick={(ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    openDelete(id, name);
  }}
>
  Delete
</button>
```

Dialog (below the existing `<ConvertModal />` block):

```tsx
{deleting && (
  <ConfirmDestructiveDialog
    open
    title={`Delete ${singular} "${deleting.entityName}"?`}
    body={
      <p>
        This permanently removes <code>{deleting.entityId}</code> from this world. Cannot be undone.
      </p>
    }
    dependents={deleting.dependents}
    busy={deleting.busy}
    error={deleting.err}
    onConfirm={() => void confirmDelete()}
    onCancel={() => setDeleting(null)}
  />
)}
```

- [ ] **Step 4: Run the tests**

```bash
cd frontend && pnpm test src/routes/library/__tests__/EntityListView.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/EntityListView.tsx \
        frontend/src/routes/library/__tests__/EntityListView.test.tsx
git commit -m "feat(library): EntityListView per-card delete"
```

---

## Task 11: `EntityEditorView` Delete header button

**Files:**
- Modify: `frontend/src/routes/library/EntityEditorView.tsx`

- [ ] **Step 1: Add state, handler, button, dialog**

In `EntityEditorBody` (the function that takes `entity` etc.), add `useNavigate` import + state + handler + button + dialog. The handler uses the existing `dependents` resource and on success navigates back to the parent kind list.

Imports:

```tsx
import { useNavigate } from "react-router-dom";
```

Inside `EntityEditorBody`, add:

```tsx
const navigate = useNavigate();
const [deleting, setDeleting] = useState<{
  busy: boolean;
  err: string | null;
} | null>(null);

async function confirmDelete() {
  if (!deleting) return;
  setDeleting({ ...deleting, busy: true, err: null });
  try {
    await libraryApi.deleteEntity(worldId, kindPlural, entityId);
    navigate(`/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}`);
  } catch (err) {
    setDeleting({
      ...deleting,
      busy: false,
      err: err instanceof ApiError ? err.message : String(err),
    });
  }
}
```

Button in the editor header (next to Save):

```tsx
<button
  type="button"
  className="entity-editor-delete"
  onClick={() => setDeleting({ busy: false, err: null })}
>
  Delete
</button>
```

Dialog (above the existing `<ConfirmEditDialog />` replacement from Task 2 — or below it; either is fine):

```tsx
{deleting && (
  <ConfirmDestructiveDialog
    open
    title={`Delete ${ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural} "${entity.name || entity.asset_id}"?`}
    body={
      <p>
        This permanently removes <code>{entity.path}</code>. Cannot be undone.
      </p>
    }
    dependents={dependents.data ?? undefined}
    busy={deleting.busy}
    error={deleting.err}
    onConfirm={() => void confirmDelete()}
    onCancel={() => setDeleting(null)}
  />
)}
```

Repeat the same additions in `GreetingEditorBody` (the second function) — same code, with `kindPlural` literal `"greetings"`.

- [ ] **Step 2: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/library/EntityEditorView.tsx
git commit -m "feat(library): EntityEditorView Delete button in editor header"
```

---

## Task 12: `WorldAtmosphereForm`

**Files:**
- Create: `frontend/src/routes/library/WorldAtmosphereForm.tsx`
- Create: `frontend/src/routes/library/__tests__/WorldAtmosphereForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/WorldAtmosphereForm.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { WorldAtmosphereForm } from "../WorldAtmosphereForm";

describe("WorldAtmosphereForm", () => {
  it("renders known fields as labeled inputs and edits propagate", () => {
    const onChange = vi.fn();
    render(
      <WorldAtmosphereForm
        value={{ default_register: "warm", default_palette: "pink" }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/default register/i), {
      target: { value: "cold" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      default_register: "cold",
      default_palette: "pink",
    });
  });

  it("round-trips unknown extra keys via StructuredValueEditor", () => {
    const onChange = vi.fn();
    render(
      <WorldAtmosphereForm
        value={{
          default_register: "",
          default_palette: "",
          custom_note: "weather is hot",
        }}
        onChange={onChange}
      />,
    );
    expect(screen.getByDisplayValue("weather is hot")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/WorldAtmosphereForm.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement the form**

Create `frontend/src/routes/library/WorldAtmosphereForm.tsx`:

```tsx
import { StructuredValueEditor } from "./StructuredValueEditor";

const KNOWN_KEYS = ["default_register", "default_palette"] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

interface Props {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}

export function WorldAtmosphereForm({ value, onChange }: Props) {
  const known: Record<KnownKey, string> = {
    default_register: typeof value.default_register === "string" ? value.default_register : "",
    default_palette: typeof value.default_palette === "string" ? value.default_palette : "",
  };
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (!(KNOWN_KEYS as readonly string[]).includes(k)) extras[k] = v;
  }

  function patch(key: KnownKey, next: string) {
    onChange({ ...value, [key]: next });
  }
  function setExtras(next: unknown) {
    const out: Record<string, unknown> = { ...known };
    if (next && typeof next === "object" && !Array.isArray(next)) {
      for (const [k, v] of Object.entries(next)) out[k] = v;
    }
    onChange(out);
  }

  return (
    <fieldset className="world-meta-fieldset">
      <legend>Atmosphere</legend>
      <label>
        <span>Default register</span>
        <input
          type="text"
          value={known.default_register}
          onChange={(e) => patch("default_register", e.target.value)}
        />
      </label>
      <label>
        <span>Default palette</span>
        <input
          type="text"
          value={known.default_palette}
          onChange={(e) => patch("default_palette", e.target.value)}
        />
      </label>
      <fieldset className="world-meta-extras">
        <legend>Other fields</legend>
        <StructuredValueEditor value={extras} onChange={setExtras} />
      </fieldset>
    </fieldset>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/WorldAtmosphereForm.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldAtmosphereForm.tsx \
        frontend/src/routes/library/__tests__/WorldAtmosphereForm.test.tsx
git commit -m "feat(library): WorldAtmosphereForm replaces atmosphere JSON textarea"
```

---

## Task 13: `WorldDefaultsForm` with library selects

**Files:**
- Create: `frontend/src/routes/library/WorldDefaultsForm.tsx`
- Create: `frontend/src/routes/library/__tests__/WorldDefaultsForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/WorldDefaultsForm.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WorldDefaultsForm } from "../WorldDefaultsForm";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listStyleGuides: vi.fn(),
      listImagePresets: vi.fn(),
    },
  };
});

describe("WorldDefaultsForm", () => {
  it("populates style-guide + image-preset selects from libraryApi", async () => {
    vi.mocked(libraryModule.libraryApi.listStyleGuides).mockResolvedValue([
      { asset_id: "shoujo-romance", name: "Shoujo Romance" } as never,
    ]);
    vi.mocked(libraryModule.libraryApi.listImagePresets).mockResolvedValue([
      { asset_id: "anime", name: "Anime" } as never,
    ]);

    render(
      <WorldDefaultsForm
        value={{
          starting_location: "classroom",
          default_style_guide_id: "shoujo-romance",
          default_image_preset_id: "anime",
        }}
        onChange={vi.fn()}
      />,
    );
    await waitFor(() => screen.getByRole("option", { name: /Shoujo Romance/ }));
    expect(screen.getByRole("option", { name: /Anime/ })).toBeInTheDocument();
  });

  it("editing starting_location fires onChange", () => {
    vi.mocked(libraryModule.libraryApi.listStyleGuides).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.listImagePresets).mockResolvedValue([]);
    const onChange = vi.fn();
    render(
      <WorldDefaultsForm
        value={{ starting_location: "a", default_style_guide_id: "", default_image_preset_id: "" }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/starting location/i), { target: { value: "b" } });
    expect(onChange).toHaveBeenLastCalledWith({
      starting_location: "b",
      default_style_guide_id: "",
      default_image_preset_id: "",
    });
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/WorldDefaultsForm.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement the form**

Create `frontend/src/routes/library/WorldDefaultsForm.tsx`. Use `useResource` (project convention) or a plain `useEffect`; here, plain useEffect for clarity:

```tsx
import { useEffect, useState } from "react";

import { libraryApi, type LibraryEntity } from "../../api/library";
import { StructuredValueEditor } from "./StructuredValueEditor";

const KNOWN_KEYS = [
  "starting_location",
  "default_style_guide_id",
  "default_image_preset_id",
] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

interface Props {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}

export function WorldDefaultsForm({ value, onChange }: Props) {
  const known: Record<KnownKey, string> = {
    starting_location: typeof value.starting_location === "string" ? value.starting_location : "",
    default_style_guide_id:
      typeof value.default_style_guide_id === "string" ? value.default_style_guide_id : "",
    default_image_preset_id:
      typeof value.default_image_preset_id === "string" ? value.default_image_preset_id : "",
  };
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (!(KNOWN_KEYS as readonly string[]).includes(k)) extras[k] = v;
  }

  const [styleGuides, setStyleGuides] = useState<LibraryEntity[]>([]);
  const [imagePresets, setImagePresets] = useState<LibraryEntity[]>([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([libraryApi.listStyleGuides(), libraryApi.listImagePresets()]).then(
      ([sgs, ips]) => {
        if (cancelled) return;
        setStyleGuides(sgs);
        setImagePresets(ips);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  function patch(key: KnownKey, next: string) {
    onChange({ ...value, [key]: next });
  }
  function setExtras(next: unknown) {
    const out: Record<string, unknown> = { ...known };
    if (next && typeof next === "object" && !Array.isArray(next)) {
      for (const [k, v] of Object.entries(next)) out[k] = v;
    }
    onChange(out);
  }

  return (
    <fieldset className="world-meta-fieldset">
      <legend>Defaults</legend>
      <label>
        <span>Starting location</span>
        <input
          type="text"
          value={known.starting_location}
          onChange={(e) => patch("starting_location", e.target.value)}
        />
      </label>
      <label>
        <span>Default style guide</span>
        <select
          value={known.default_style_guide_id}
          onChange={(e) => patch("default_style_guide_id", e.target.value)}
        >
          <option value="">(none)</option>
          {styleGuides.map((sg) => (
            <option key={sg.asset_id} value={sg.asset_id}>
              {sg.name || sg.asset_id}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Default image preset</span>
        <select
          value={known.default_image_preset_id}
          onChange={(e) => patch("default_image_preset_id", e.target.value)}
        >
          <option value="">(none)</option>
          {imagePresets.map((ip) => (
            <option key={ip.asset_id} value={ip.asset_id}>
              {ip.name || ip.asset_id}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="world-meta-extras">
        <legend>Other fields</legend>
        <StructuredValueEditor value={extras} onChange={setExtras} />
      </fieldset>
    </fieldset>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/WorldDefaultsForm.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldDefaultsForm.tsx \
        frontend/src/routes/library/__tests__/WorldDefaultsForm.test.tsx
git commit -m "feat(library): WorldDefaultsForm with style-guide + image-preset selects"
```

---

## Task 14: `WorldCalendarForm` — scalars + week_day_names

**Files:**
- Create: `frontend/src/routes/library/WorldCalendarForm.tsx`
- Create: `frontend/src/routes/library/__tests__/WorldCalendarForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/WorldCalendarForm.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { WorldCalendarForm } from "../WorldCalendarForm";

const SAKURA_CALENDAR = {
  epoch: "2025-04-08",
  days_per_week: 7,
  week_day_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  months: [{ name: "January", days: 31 }],
  seasons: [],
  holidays: [],
};

describe("WorldCalendarForm — scalars", () => {
  it("renders epoch, days_per_week, week_day_names", () => {
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={vi.fn()} />);
    expect(screen.getByLabelText(/epoch/i)).toHaveValue("2025-04-08");
    expect(screen.getByLabelText(/days per week/i)).toHaveValue(7);
    expect(screen.getByDisplayValue("Mon")).toBeInTheDocument();
  });

  it("editing days_per_week fires onChange with updated calendar", () => {
    const onChange = vi.fn();
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/days per week/i), { target: { value: "8" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...SAKURA_CALENDAR, days_per_week: 8 });
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/WorldCalendarForm.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement the scalar layout**

Create `frontend/src/routes/library/WorldCalendarForm.tsx`:

```tsx
import { StructuredValueEditor } from "./StructuredValueEditor";

interface MonthRow {
  name: string;
  days: number;
}
interface SeasonRow {
  name: string;
  start_month: number;
  start_day: number;
  palette: string;
  weather_bias: Record<string, number>;
}
interface HolidayRow {
  name: string;
  month: number;
  day: number;
  description: string;
  tags: string[];
}

export interface WorldCalendar {
  epoch: string;
  days_per_week: number;
  week_day_names: string[];
  months: MonthRow[];
  seasons: SeasonRow[];
  holidays: HolidayRow[];
  extras: Record<string, unknown>;
}

const CANONICAL_KEYS = new Set([
  "epoch",
  "days_per_week",
  "week_day_names",
  "months",
  "seasons",
  "holidays",
]);

export function parseCalendar(raw: unknown): WorldCalendar {
  const obj = (raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (!CANONICAL_KEYS.has(k)) extras[k] = v;
  }
  return {
    epoch: typeof obj.epoch === "string" ? obj.epoch : "",
    days_per_week: typeof obj.days_per_week === "number" ? obj.days_per_week : 7,
    week_day_names: Array.isArray(obj.week_day_names)
      ? obj.week_day_names.filter((s): s is string => typeof s === "string")
      : [],
    months: Array.isArray(obj.months) ? (obj.months as MonthRow[]) : [],
    seasons: Array.isArray(obj.seasons) ? (obj.seasons as SeasonRow[]) : [],
    holidays: Array.isArray(obj.holidays) ? (obj.holidays as HolidayRow[]) : [],
    extras,
  };
}

export function serializeCalendar(cal: WorldCalendar): Record<string, unknown> {
  return {
    epoch: cal.epoch,
    days_per_week: cal.days_per_week,
    week_day_names: cal.week_day_names,
    months: cal.months,
    seasons: cal.seasons,
    holidays: cal.holidays,
    ...cal.extras,
  };
}

interface Props {
  value: WorldCalendar | Record<string, unknown>;
  onChange: (next: WorldCalendar) => void;
}

export function WorldCalendarForm({ value, onChange }: Props) {
  const cal: WorldCalendar = "extras" in (value as object) && Array.isArray((value as WorldCalendar).months)
    ? (value as WorldCalendar)
    : parseCalendar(value);

  const patch = (next: Partial<WorldCalendar>) => onChange({ ...cal, ...next });

  return (
    <fieldset className="world-meta-fieldset">
      <legend>Calendar</legend>
      <label>
        <span>Epoch</span>
        <input
          type="date"
          value={cal.epoch}
          onChange={(e) => patch({ epoch: e.target.value })}
        />
      </label>
      <label>
        <span>Days per week</span>
        <input
          type="number"
          min={1}
          max={20}
          value={cal.days_per_week}
          onChange={(e) => patch({ days_per_week: Number(e.target.value) })}
        />
      </label>
      <fieldset>
        <legend>Week day names</legend>
        <StructuredValueEditor
          value={cal.week_day_names}
          onChange={(next) =>
            patch({
              week_day_names: Array.isArray(next)
                ? next.filter((s): s is string => typeof s === "string")
                : [],
            })
          }
        />
      </fieldset>
      <fieldset className="world-meta-extras">
        <legend>Other calendar fields</legend>
        <StructuredValueEditor
          value={cal.extras}
          onChange={(next) =>
            patch({
              extras:
                next && typeof next === "object" && !Array.isArray(next)
                  ? (next as Record<string, unknown>)
                  : {},
            })
          }
        />
      </fieldset>
      {/* months, seasons, holidays arrive in Task 15 */}
    </fieldset>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/WorldCalendarForm.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldCalendarForm.tsx \
        frontend/src/routes/library/__tests__/WorldCalendarForm.test.tsx
git commit -m "feat(library): WorldCalendarForm scalars + week_day_names + extras"
```

---

## Task 15: `WorldCalendarForm` — months, seasons, holidays

**Files:**
- Modify: `frontend/src/routes/library/WorldCalendarForm.tsx`
- Modify: `frontend/src/routes/library/__tests__/WorldCalendarForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to the existing test file:

```tsx
describe("WorldCalendarForm — months/seasons/holidays", () => {
  it("editing a month's days propagates", () => {
    const onChange = vi.fn();
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("31"), { target: { value: "30" } });
    expect(onChange).toHaveBeenLastCalledWith({
      ...SAKURA_CALENDAR,
      months: [{ name: "January", days: 30 }],
    });
  });

  it("clicking + add holiday appends an empty holiday row", () => {
    const onChange = vi.fn();
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add holiday/i }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...SAKURA_CALENDAR,
      holidays: [{ name: "", month: 1, day: 1, description: "", tags: [] }],
    });
  });

  it("clicking remove on a month deletes that row", () => {
    const onChange = vi.fn();
    render(
      <WorldCalendarForm
        value={{
          ...SAKURA_CALENDAR,
          months: [
            { name: "January", days: 31 },
            { name: "February", days: 28 },
          ],
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Remove month 1$/ }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...SAKURA_CALENDAR,
      months: [{ name: "February", days: 28 }],
    });
  });
});
```

- [ ] **Step 2: Run and verify they fail**

```bash
cd frontend && pnpm test src/routes/library/__tests__/WorldCalendarForm.test.tsx
```

Expected: FAIL (no months/seasons/holidays UI yet).

- [ ] **Step 3: Add the three sub-forms**

In `frontend/src/routes/library/WorldCalendarForm.tsx`, before the closing `</fieldset>` of `WorldCalendarForm`'s outermost `fieldset` (above the "Other calendar fields" extras), insert:

```tsx
      <MonthsRows months={cal.months} onChange={(next) => patch({ months: next })} />
      <SeasonsRows seasons={cal.seasons} onChange={(next) => patch({ seasons: next })} />
      <HolidaysRows holidays={cal.holidays} onChange={(next) => patch({ holidays: next })} />
```

Then append the three helper components to the same file:

```tsx
function MonthsRows({
  months,
  onChange,
}: {
  months: MonthRow[];
  onChange: (next: MonthRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<MonthRow>) => {
    const out = months.slice();
    out[i] = { ...out[i], ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = months.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () => onChange([...months, { name: "", days: 30 }]);
  return (
    <fieldset>
      <legend>Months</legend>
      {months.map((m, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input
              type="text"
              value={m.name}
              onChange={(e) => updateAt(i, { name: e.target.value })}
            />
          </label>
          <label>
            <span>Days</span>
            <input
              type="number"
              min={1}
              max={400}
              value={m.days}
              onChange={(e) => updateAt(i, { days: Number(e.target.value) })}
            />
          </label>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove month ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add month
      </button>
    </fieldset>
  );
}

function SeasonsRows({
  seasons,
  onChange,
}: {
  seasons: SeasonRow[];
  onChange: (next: SeasonRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<SeasonRow>) => {
    const out = seasons.slice();
    out[i] = { ...out[i], ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = seasons.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () =>
    onChange([
      ...seasons,
      { name: "", start_month: 1, start_day: 1, palette: "", weather_bias: {} },
    ]);
  return (
    <fieldset>
      <legend>Seasons</legend>
      {seasons.map((s, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input type="text" value={s.name} onChange={(e) => updateAt(i, { name: e.target.value })} />
          </label>
          <label>
            <span>Start month</span>
            <input
              type="number"
              min={1}
              max={12}
              value={s.start_month}
              onChange={(e) => updateAt(i, { start_month: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Start day</span>
            <input
              type="number"
              min={1}
              max={31}
              value={s.start_day}
              onChange={(e) => updateAt(i, { start_day: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Palette</span>
            <input
              type="text"
              value={s.palette}
              onChange={(e) => updateAt(i, { palette: e.target.value })}
            />
          </label>
          <fieldset>
            <legend>Weather bias</legend>
            <StructuredValueEditor
              value={s.weather_bias}
              onChange={(next) =>
                updateAt(i, {
                  weather_bias:
                    next && typeof next === "object" && !Array.isArray(next)
                      ? (next as Record<string, number>)
                      : {},
                })
              }
            />
          </fieldset>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove season ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add season
      </button>
    </fieldset>
  );
}

function HolidaysRows({
  holidays,
  onChange,
}: {
  holidays: HolidayRow[];
  onChange: (next: HolidayRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<HolidayRow>) => {
    const out = holidays.slice();
    out[i] = { ...out[i], ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = holidays.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () =>
    onChange([...holidays, { name: "", month: 1, day: 1, description: "", tags: [] }]);
  return (
    <fieldset>
      <legend>Holidays</legend>
      {holidays.map((h, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input type="text" value={h.name} onChange={(e) => updateAt(i, { name: e.target.value })} />
          </label>
          <label>
            <span>Month</span>
            <input
              type="number"
              min={1}
              max={12}
              value={h.month}
              onChange={(e) => updateAt(i, { month: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Day</span>
            <input
              type="number"
              min={1}
              max={31}
              value={h.day}
              onChange={(e) => updateAt(i, { day: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Description</span>
            <input
              type="text"
              value={h.description}
              onChange={(e) => updateAt(i, { description: e.target.value })}
            />
          </label>
          <label>
            <span>Tags (comma separated)</span>
            <input
              type="text"
              value={h.tags.join(", ")}
              onChange={(e) =>
                updateAt(i, {
                  tags: e.target.value.split(",").map((t) => t.trim()).filter(Boolean),
                })
              }
            />
          </label>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove holiday ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add holiday
      </button>
    </fieldset>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/WorldCalendarForm.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldCalendarForm.tsx \
        frontend/src/routes/library/__tests__/WorldCalendarForm.test.tsx
git commit -m "feat(library): WorldCalendarForm months/seasons/holidays sub-forms"
```

---

## Task 16: Wire the three forms into `WorldMetaView`; drop JSON textareas

**Files:**
- Modify: `frontend/src/routes/library/WorldMetaView.tsx`

- [ ] **Step 1: Replace the body**

Rewrite `frontend/src/routes/library/WorldMetaView.tsx` so the three JSON textareas (calendar / atmosphere / defaults) are replaced with the new forms and the `JSON.parse` step in `save()` is gone:

```tsx
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, libraryApi, type WorldMeta } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { WorldAtmosphereForm } from "./WorldAtmosphereForm";
import { WorldCalendarForm, parseCalendar, serializeCalendar, type WorldCalendar } from "./WorldCalendarForm";
import { WorldDefaultsForm } from "./WorldDefaultsForm";

const FIELDS: { key: keyof WorldMeta; label: string; type: "text" | "textarea" | "tags" }[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "genre", label: "Genre", type: "text" },
  { key: "description", label: "Description", type: "textarea" },
  { key: "tags", label: "Tags (comma separated)", type: "tags" },
];

export function WorldMetaView() {
  const { worldId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getWorld(worldId), [worldId]),
  );

  const [draft, setDraft] = useState<Partial<WorldMeta>>({});
  const [calendar, setCalendar] = useState<WorldCalendar>(parseCalendar({}));
  const [atmosphere, setAtmosphere] = useState<Record<string, unknown>>({});
  const [defaults, setDefaults] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data) return;
    setDraft({
      name: data.name,
      genre: data.genre,
      description: data.description,
      tags: data.tags,
    });
    setCalendar(parseCalendar(data.calendar ?? {}));
    setAtmosphere((data.atmosphere ?? {}) as Record<string, unknown>);
    setDefaults((data.defaults ?? {}) as Record<string, unknown>);
    setDirty(false);
  }, [data]);

  function patch<K extends keyof WorldMeta>(key: K, value: WorldMeta[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setSaveErr(null);
    try {
      await libraryApi.updateWorld(worldId, {
        ...draft,
        calendar: serializeCalendar(calendar),
        atmosphere,
        defaults,
      });
      setDirty(false);
      reload();
    } catch (err) {
      setSaveErr(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="world-meta">
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        <div className="library-form" aria-label="World metadata">
          {FIELDS.map((field) => (
            <label key={field.key}>
              <span>{field.label}</span>
              {field.type === "textarea" ? (
                <textarea
                  rows={3}
                  value={(draft[field.key] as string) ?? ""}
                  onChange={(e) => patch(field.key as "description", e.target.value)}
                />
              ) : field.type === "tags" ? (
                <input
                  type="text"
                  value={(draft.tags ?? []).join(", ")}
                  onChange={(e) =>
                    patch(
                      "tags",
                      e.target.value.split(",").map((t) => t.trim()).filter(Boolean),
                    )
                  }
                />
              ) : (
                <input
                  type="text"
                  value={(draft[field.key] as string) ?? ""}
                  onChange={(e) => patch(field.key as "name", e.target.value)}
                />
              )}
            </label>
          ))}

          <WorldCalendarForm
            value={calendar}
            onChange={(next) => {
              setCalendar(next);
              setDirty(true);
            }}
          />
          <WorldAtmosphereForm
            value={atmosphere}
            onChange={(next) => {
              setAtmosphere(next);
              setDirty(true);
            }}
          />
          <WorldDefaultsForm
            value={defaults}
            onChange={(next) => {
              setDefaults(next);
              setDirty(true);
            }}
          />

          {saveErr && (
            <p className="library-error" role="alert">
              {saveErr}
            </p>
          )}
          <button onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </AsyncBoundary>
    </section>
  );
}
```

- [ ] **Step 2: Run typecheck + all tests**

```bash
cd frontend && pnpm typecheck && pnpm test
```

Expected: clean.

- [ ] **Step 3: Manual smoke**

Start the dev server. Open `/library/worlds/sakura-high/meta`. Verify:
- All three sections render with the seeded values.
- Editing a holiday name → Save → hard-reload → the change persists.
- The page contains no `<textarea>` for calendar/atmosphere/defaults (`document.querySelectorAll('.world-meta textarea').length` should be 1 — just the description field).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/WorldMetaView.tsx
git commit -m "feat(library): WorldMetaView uses dedicated forms instead of JSON textareas"
```

---

## Task 17: Replace `JsonField` in `FrontmatterEditor` with `StructuredValueEditor`

**Files:**
- Modify: `frontend/src/routes/library/FrontmatterEditor.tsx`
- Create: `frontend/src/routes/library/__tests__/FrontmatterEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/library/__tests__/FrontmatterEditor.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FrontmatterEditor } from "../FrontmatterEditor";

describe("FrontmatterEditor — no JSON textareas", () => {
  it("renders a nested-object field via StructuredValueEditor, not a textarea", () => {
    const { container } = render(
      <FrontmatterEditor
        value={{ appearance: { hair: "brown", eyes: "green" } }}
        onChange={vi.fn()}
      />,
    );
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByDisplayValue("brown")).toBeInTheDocument();
  });

  it("renders a list field via StructuredValueEditor, not a textarea", () => {
    const { container } = render(
      <FrontmatterEditor value={{ skills: ["sword", "stealth"] }} onChange={vi.fn()} />,
    );
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByDisplayValue("sword")).toBeInTheDocument();
  });

  it("editing inside a nested object propagates the whole frontmatter up", () => {
    const onChange = vi.fn();
    render(
      <FrontmatterEditor
        value={{ appearance: { hair: "brown" } }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByDisplayValue("brown"), { target: { value: "red" } });
    expect(onChange).toHaveBeenLastCalledWith({ appearance: { hair: "red" } });
  });

  it("Add-field offers list and object (not json)", () => {
    render(<FrontmatterEditor value={{}} onChange={vi.fn()} />);
    const select = screen.getByRole("combobox");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(options).toContain("list");
    expect(options).toContain("object");
    expect(options).not.toContain("json");
  });
});
```

- [ ] **Step 2: Run and verify it fails**

```bash
cd frontend && pnpm test src/routes/library/__tests__/FrontmatterEditor.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Modify `FrontmatterEditor.tsx`**

In `frontend/src/routes/library/FrontmatterEditor.tsx`, delete the `JsonField` component entirely, and replace the `kind === "json"` branch in `FrontmatterField` plus the `AddFieldRow` kind handling:

```tsx
import { StructuredValueEditor } from "./StructuredValueEditor";

// ...

function fieldKind(v: FrontmatterValue): "string" | "number" | "boolean" | "list" | "object" {
  if (typeof v === "string") return "string";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  if (Array.isArray(v)) return "list";
  return "object";
}

// ...inside FrontmatterField, replace the `kind === "json"` branch with:
      {(kind === "list" || kind === "object") && (
        <StructuredValueEditor
          value={value as unknown}
          onChange={(next) => onChange(next as FrontmatterValue)}
          readOnly={readOnly}
        />
      )}
```

Update the `add` helper signature and `AddFieldRow` to accept `"list" | "object"` instead of `"json"`:

```tsx
const add = (key: string, kind: "string" | "number" | "boolean" | "list" | "object") => {
  if (readOnly || !key || key in value || hidden.has(key)) return;
  const initial: FrontmatterValue =
    kind === "string"
      ? ""
      : kind === "number"
        ? 0
        : kind === "boolean"
          ? false
          : kind === "list"
            ? []
            : {};
  onChange({ ...value, [key]: initial });
};
```

And in `AddFieldRow`:

```tsx
function AddFieldRow({
  onAdd,
  existingKeys,
}: {
  onAdd: (key: string, kind: "string" | "number" | "boolean" | "list" | "object") => void;
  existingKeys: Set<string>;
}) {
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<"string" | "number" | "boolean" | "list" | "object">("string");
  const trimmed = key.trim();
  const valid = trimmed.length > 0 && !existingKeys.has(trimmed);
  return (
    <div className="frontmatter-add">
      <input
        type="text"
        placeholder="add field…"
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
      <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
        <option value="string">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="list">list</option>
        <option value="object">object</option>
      </select>
      <button
        type="button"
        disabled={!valid}
        onClick={() => {
          if (!valid) return;
          onAdd(trimmed, kind);
          setKey("");
        }}
      >
        Add
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Run tests + typecheck**

```bash
cd frontend && pnpm typecheck && pnpm test src/routes/library/__tests__/FrontmatterEditor.test.tsx
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/FrontmatterEditor.tsx \
        frontend/src/routes/library/__tests__/FrontmatterEditor.test.tsx
git commit -m "feat(library): FrontmatterEditor uses StructuredValueEditor; no JSON inputs"
```

---

## Task 18: Final lint + typecheck + full test suite + manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full lint + typecheck + test suite**

```bash
cd frontend && pnpm lint && pnpm typecheck && pnpm test
```

Expected: all clean. Fix any lint warnings (often `react/jsx-key` or unused vars) inline before continuing.

- [ ] **Step 2: Backend smoke**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/api/test_library_routes.py -q
```

Expected: 35+ tests pass — no backend changes were made, so this just confirms nothing else regressed.

- [ ] **Step 3: Manual end-to-end verification**

Start backend (`backend/.venv/Scripts/python.exe -m uvicorn grimoire.main:create_app --factory --reload`) and frontend (`cd frontend && pnpm dev`). With a seeded world (e.g. `sakura-high`) loaded, verify:

  - **WorldsListView** has a "Delete world" button per card; clicking shows the typed-confirm dialog.
  - **WorldDetailView** header has a "Delete world" button next to Fork / Import.
  - **WorldMetaView** has no JSON textareas. Calendar, Atmosphere, Defaults render as structured forms. Editing a holiday name + Save + hard-reload persists.
  - **EntityListView** has a "Delete" button per card on every kind (characters, items, locations, lore, factions, greetings).
  - **EntityEditorView** has a "Delete" button in the editor header.
  - **EntityEditorView** with an entity that has a nested-object frontmatter field (e.g. character `appearance: {hair, eyes}`) shows structured rows, no `<textarea>`.

- [ ] **Step 4: Confirm no orphan JSON inputs**

In the running app, navigate through all six entity kinds and the meta tab. Search the DOM with `document.querySelectorAll("textarea[class*=json]")` — should return zero matches.

- [ ] **Step 5: Final commit (if any lint fixes)**

If Step 1 produced lint fixups, commit them:

```bash
git add -A
git commit -m "chore(library): lint/format fixups after worlds-edit-delete-forms"
```

---

## Self-review

After writing this plan, re-read the spec and check that every requirement maps to a task.

| Spec requirement | Task |
|---|---|
| `StructuredValueEditor` recursive form | Tasks 3, 4, 5 |
| Object key duplicate-key guard | Task 5 |
| Type picker that initializes default for `null` | Task 3 |
| `ConfirmDestructiveDialog` with dependents + typed confirm | Task 1 |
| Lift inline `ConfirmEditDialog` | Task 2 |
| `WorldCalendarForm` (canonical fields + extras) | Tasks 14, 15 |
| `WorldAtmosphereForm` | Task 12 |
| `WorldDefaultsForm` with selects | Task 13 |
| `fetchWorldDependents` helper | Task 7 |
| `WorldsListView` delete | Task 8 |
| `WorldDetailView` delete | Task 9 |
| `EntityListView` delete | Task 10 |
| `EntityEditorView` delete | Task 11 |
| `WorldMetaView` rewrite without JSON | Task 16 |
| `FrontmatterEditor` rewrite without JSON | Task 17 |
| 404 on delete treated as success | not yet covered — small enough to roll into Tasks 8/10/11 inline if it comes up |
| Type-change destructive confirm inside `StructuredValueEditor` | **gap — see note below** |
| Lint / typecheck / manual verification | Task 18 |

**Known gap — type-change destructive confirm:** the spec calls for an inline "Changing list → text will discard 3 items. Continue?" guard inside `StructuredValueEditor`. The current Task 5 only enforces this for keys (duplicate guard). The type picker added in Task 3 is only on `null` rows. Implementing destructive type-change confirms requires showing the picker on every row, not just `null`, and is a moderate amount of extra UI. Defer this to a follow-up — the existing per-row delete + add-new-row-with-different-type covers the same use case at the cost of one extra click.

The spec's "Convert to text" button on complex values is similarly deferred — same reasoning. If you do want it now, add a Task 5a that wires a small "[change type ▼]" picker into `ObjectRow` and array rows, with an inline confirm when the source value is non-empty.

Make this an explicit follow-up rather than fail to ship — better to land the bulk of the spec cleanly than block on it.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-22-worlds-edit-delete-forms.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
