# Inference Settings Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make model selection a one-stop shop. A new Settings → Inference
group holds three pages:

- **Providers**, with a real `/providers/new`.
- **Models**: a read-only summary, plus one edit form where each role gets
  provider ▾ / model ▾ / preset ▾, an optional fallback, and a collapsed
  Advanced section for per-task overrides. Token rates are folded in.
- **Presets**: a page of its own.

**Architecture:** The backend changes only for reading. `GET
/api/inference/settings` gains a `rate` on each role, route and the Embedding
card, and a `problem` on each provider. Every write still goes through the
existing `PUT /api/inference/settings`. On the frontend:

- `routes/ModelsView.tsx` is rewritten as summary (`/models`) plus edit form
  (`/models/edit`), built from small components under `components/models/`.
- A new select-based `ModelSelect` reuses the existing picker's capability
  logic, which is extracted into a hook so both share one copy.
- `SamplerPresetEditor` is split into a routed `PresetsView`.
- ConfigView loses three panes and gains a link group.

**Tech Stack:** FastAPI + pytest (backend), React 18 + react-router 6 +
vitest + Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-10-09-inference-settings-group-design.md`.
Read it before starting; this plan argues from it, and section numbers below
(§3.2 etc.) are the spec's.

## Global Constraints

- The repo is public. Fixtures use placeholder names only: Saltmarch, Realm,
  Mara, Seraphine, Winifred. Never a real library's name, and never a count
  of anything from `~/.grimoire`.
- pydantic stays v1/v2-agnostic. Do not use `model_dump()`, `Field` or
  validators (CLAUDE.md, Android).
- Backend imports stay at module scope. A cross-package import binds a
  submodule (`from .. import pricing`), never a name off it
  (`test_import_guard.py`).
- No new `keydown` listener. Keys go through `src/shortcuts/` (eslint
  `no-restricted-syntax`).
- Run vitest **from** `frontend/` (`cd frontend && npx vitest run <file>`),
  never with `--prefix`.
- Work in a worktree under `.worktrees/`, not `.claude/worktrees/` (user
  memory). In a worktree, pass
  `PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` to
  `make`, and set `PYTHONPATH` to the worktree's `backend/src` for pytest.
- The lint gates are ratcheted. A fixed finding fails the gate until
  `make baseline` is run and the smaller baseline is committed with the fix.
- "A price nobody reported is never rendered as zero" (CLAUDE.md, Costs).
  `source: "none"` renders words, never a figure. A real `0` rate renders
  through `components/cost.tsx`'s `perMillionRate`, the formatter every cost
  surface uses, as `$0/M` (spec 3.5, amended 2026-10-09).
- Nothing a settings page does may spend unasked. The test call keeps its
  preview-then-confirm dialog, and an Embedding change keeps its
  `confirm_embedding` question.
- Native decisions are never modelled (`store/usage.py:896`), so a native
  Decision shows `source: "native"` and no figures.
- Provider **health** comes from `GET /api/llm-connections`
  (`LLMConnection.health`), never from the settings view.
- Task hashes are `#task-${encodeURIComponent(key)}`, decoded only through
  `taskFromHash`, which never throws.
- Providers is removed from the Library: from `LIBRARY_SECTIONS`, from the
  Library rail badge, and from the Library rail match (user decision,
  2026-10-09).

## Review Focus

These are what a person will hit that no other task's tests cover. Each line's
test is added to the task named.

1. **A provider deleted while a role still names it.** The summary and the
   form must show the id as "(missing provider)" and keep it until changed,
   never blanking the role. Task 5 (summary) and Task 3 (`ModelSelect`).
2. **The rates table unreadable** (`GET /api/pricing` answers
   `unreadable: true`). The Token rates block must say it could not be read
   and offer no editable form, while the role rows still render their server
   `rate`. Task 4.
3. **A slow capability list when the provider changes.** An answer for the
   old provider that lands after the switch must not fill the new provider's
   model list. Task 3.
4. **Saving with nothing changed.** Save sends no request and simply returns
   to `/models`. An empty `InferenceWrite` is a write request nobody asked
   for, and the activity middleware stamps writes it sees succeed. Task 6.
5. **A model id with `/` or `#` in it, through Set rate.** It must arrive in
   the rates editor intact. Task 4.

---

## File Structure

**Backend**
- Modify `backend/src/grimoire/store/inference/settings.py`: `_rate`,
  `_prices`, `rate` on cards and rows, `problem` on providers.
- Modify `backend/src/grimoire/routes/todo.py`: the Housekeeping links go to
  the new addresses.
- Test `backend/tests/test_inference_settings_rates.py` (new) and
  `backend/tests/test_todo_route.py` (updated expectations).

**Frontend: types and shared pieces**
- Modify `frontend/src/api/types.ts`: `RateInfo`, plus `rate` on `RoleCard`,
  `RouteRow` and `EmbeddingCard`, and `problem` on `InferenceProvider`.
- Create `frontend/src/components/inference/useModelList.ts`: the capability
  list, typed-id verdict and re-ask, extracted from `ProviderModelPicker`.
- Modify `frontend/src/components/inference/ProviderModelPicker.tsx` to use
  the hook (no behaviour change).
- Create `frontend/src/components/models/ModelSelect.tsx`: the provider ▾
  model ▾ pair with "Other model id…" and Test.
- Create `frontend/src/components/models/health.tsx`: `useProviderHealth`,
  `healthWord` and `HealthDot`.
- Create `frontend/src/components/models/selections.ts`: `GENERATIVE`,
  `EMPTY_SEL` and `sameSel`, the leaf shared by the form and the overrides.
- Create `frontend/src/components/models/rates.ts`: `rateWords`, `setRateHref`.
- Create `frontend/src/components/models/notes.tsx`: `WARNINGS`,
  `DECIDE_WORDS`, `useWarning`, `Warning`, `Problem`, `DecideNote`, moved
  out of `ModelsView`.
- Create `frontend/src/components/models/taskHash.ts`: `taskHash`,
  `taskFromHash`.
- Create `frontend/src/components/inference/InferenceNav.tsx`: the
  "Inference" `ColumnSection` the three pages share.

**Frontend: the Models page**
- Create `frontend/src/components/models/TokenRates.tsx`: the read-only rate
  table plus Edit.
- Modify `frontend/src/components/PricingEditor.tsx`: `addModel` and
  `onSaved` props.
- Rewrite `frontend/src/routes/ModelsView.tsx`: summary or edit, by the `edit`
  prop.
- Create `frontend/src/components/models/RoleRow.tsx`: one summary row.
- Create `frontend/src/components/models/ModelsEditForm.tsx`: the
  every-role form.
- Create `frontend/src/components/models/TaskOverrides.tsx`: the Advanced
  section.

**Frontend: the Presets page**
- Create `frontend/src/components/presets/presetForm.ts` (`Draft`, `BLANK`,
  `toDraft`, `toParams`, `encodeStop`, `decodeStop`, `shown`).
- Create `frontend/src/components/presets/ImportReport.tsx`,
  `PresetView.tsx`, `PresetForm.tsx`, `PresetImport.tsx` and `usedBy.ts`.
- Create `frontend/src/routes/PresetsView.tsx`.
- Delete `frontend/src/components/SamplerPresetEditor.tsx`, after its tests
  move to `PresetsView.test.tsx`.

**Frontend: Providers, Settings and shell**
- Modify `frontend/src/routes/ProvidersView.tsx`: `/providers/new`,
  `returnTo`, `chipFor` targets, and `InferenceNav`.
- Modify `frontend/src/routes/ConfigView.tsx`: `LINKS`, `MOVED`, three panes
  removed, Retries to Timeouts, recall to Context.
- Create `frontend/src/routes/ModelsRedirect.tsx`: the old `/models/route/:key`
  pages redirect to their task row.
- Modify `frontend/src/App.tsx` (routes), `frontend/src/shell/rail.ts`
  (match, titles), `frontend/src/librarySections.ts` (Providers out) and
  `frontend/src/components/AppPaletteSource.tsx` (Providers and Presets
  entries).
- Modify `frontend/src/index.css`: the few new classes.

---
### Task 1: Backend — `rate` on every card and row, `problem` on every provider

**Files:**
- Modify: `backend/src/grimoire/store/inference/settings.py:57-71` (imports),
  `:164-314` (`_role_card`, `_route_row`, `_embedding_card`, `_providers`,
  `view`)
- Modify: `frontend/src/api/types.ts` (`RateInfo`, plus the new fields) and
  every frontend test fixture that builds these objects
- Test: `backend/tests/test_inference_settings_rates.py` (new)

**Interfaces:**
- Produces, in `GET /api/inference/settings` and
  `GET /api/campaigns/{cid}/inference`:
  - `roles.<role>.rate`, `routes[i].rate` and `roles.embedding.rate`, each
    either `null` or
    `{"source": "provider"|"table"|"none"|"native", "entry"?: {...PricingEntry}}`;
  - `providers[i].problem`: `str | null`, `null` exactly when `usable`.
- Python: `settings._rate(sel: dict | None, prices: tuple[dict, dict], *,
  native: bool = False) -> dict | None` and
  `settings._prices() -> tuple[dict, dict]`.
- TS: `RateInfo`, `RoleCard.rate`, `RouteRow.rate`, `EmbeddingCard.rate` and
  `InferenceProvider.problem`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_inference_settings_rates.py`:

```python
"""What the Models screen says prices each role's and route's calls (spec 3.5),
and why a provider cannot send (spec 3.2).

`rate` is `pricing.rate_for_call` asked about the RESOLVED selection -- the
ledger's own precedence, so the screen cannot drift from what Costs will
apply -- with where it came from. A native decision is never modelled
(`usage.py`), so it carries no figures. Placeholder names only.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import pricing
from grimoire.store.inference import facts

from . import inference_baseline as base
from . import inference_fixtures as fx

BOTH = {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}
ZERO = {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _view(client) -> dict:
    got = client.get("/api/inference/settings")
    assert got.status_code == 200, got.text
    return got.json()


def _expected(provider: str, model: str) -> dict | None:
    return pricing.rate_for_call(pricing.read_pricing(), pricing.provider_rates(),
                                 provider_id=provider, model=model)


def test_a_rate_the_provider_states_is_the_providers(client):
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate == {"source": "provider", "entry": _expected("openrouter", "vendor/active")}
    assert rate["entry"] == BOTH


def test_a_table_entry_is_the_tables_including_a_wildcard(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/*": BOTH})
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate == {"source": "table", "entry": _expected("openrouter", "vendor/active")}


def test_no_rate_anywhere_is_none_never_zero(client):
    fx.format2(client)
    assert _view(client)["roles"]["primary"]["rate"] == {"source": "none"}


def test_a_zero_rate_keeps_its_zeros(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/active": ZERO})
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate["source"] == "table"
    assert rate["entry"]["prompt_usd_per_1k"] == 0.0
    assert rate["entry"]["completion_usd_per_1k"] == 0.0


def test_a_half_entry_reads_as_none(client):
    fx.format2(client)
    # Written as a hand edit would leave it -- `write_pricing` itself drops a
    # half entry on the way in -- so this is the READ side: pricing keeps an
    # entry only with both base rates (`pricing.entry`), and the view says
    # nothing prices the model rather than drawing half of it.
    pricing.pricing_path().write_text(
        json.dumps({"vendor/active": {"prompt_usd_per_1k": 0.001}}), encoding="utf-8")
    assert _view(client)["roles"]["primary"]["rate"] == {"source": "none"}


def test_a_route_row_carries_its_resolved_rate(client):
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    row = next(r for r in _view(client)["routes"] if r["key"] == "scene")
    assert row["rate"] == {"source": "provider", "entry": BOTH}


def test_a_native_decision_has_no_figures(client):
    fx.decide_only(client, fallback=False)
    pricing.write_pricing({"": BOTH})       # a catch-all that WOULD price it
    got = _view(client)
    assert got["roles"]["decision"]["decision_mode"] == "native"
    assert got["roles"]["decision"]["rate"] == {"source": "native"}
    native_rows = [r for r in got["routes"] if r["decision_mode"] == "native"]
    assert native_rows and all(r["rate"] == {"source": "native"} for r in native_rows)


def test_an_embedding_that_does_not_resolve_has_no_rate(client):
    fx.format2(client)
    card = _view(client)["roles"]["embedding"]
    assert card["resolves"] is None and card["rate"] is None


def test_an_embedding_that_resolves_is_priced_like_any_model(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/embed": BOTH})
    got = client.put("/api/inference/settings", json={
        "roles": {"embedding": {"selection": {"provider": "spare", "model": "vendor/embed"}}},
        "confirm_embedding": True})
    assert got.status_code == 200, got.text
    card = _view(client)["roles"]["embedding"]
    assert card["on"] is True
    assert card["rate"] == {"source": "table", "entry": _expected("spare", "vendor/embed")}


def test_a_campaign_view_carries_rates_too(client):
    cid = base._fresh(client)["cid"]
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text
    assert got.json()["roles"]["primary"]["rate"]["source"] == "provider"


def test_each_provider_says_why_it_cannot_send(client):
    fx.format2(client)
    made = client.post("/api/llm-connections", json={"kind": "openrouter", "name": "keyless"})
    assert made.status_code == 200, made.text
    providers = {p["id"]: p for p in _view(client)["providers"]}
    keyless = providers[made.json()["id"]]
    assert keyless["usable"] is False
    assert isinstance(keyless["problem"], str) and keyless["problem"]
    for p in providers.values():
        assert (p["problem"] is None) == p["usable"]
```

- [ ] **Step 2: Run them to verify they fail**

Run, from the repo root (in a worktree, with `PYTHONPATH` set to that tree's
`backend/src`):
`cd backend && .venv/Scripts/python.exe -m pytest tests/test_inference_settings_rates.py -q`

Expected: the suite FAILs, because the view carries no `rate` and no
`problem` yet. Which exception each test meets is not prescribed.

- [ ] **Step 3: Implement in `settings.py`**

1. Add `pricing` to the `from .. import (...)` block, keeping it sorted. Add
   `from ... import decisions` above that block; `store/inference/probes.py:42`
   imports it the same way.

2. Add these helpers above `_role_card`:

```python
def _prices() -> tuple[dict, dict]:
    """The rate table and every provider's stated rates, read once per view:
    `_rate` is asked for every card and row, and each read is a file read."""
    return pricing.read_pricing(), pricing.provider_rates()


def _rate(sel: dict | None, prices: tuple[dict, dict], *, native: bool = False) -> dict | None:
    """What a call answered under `sel`'s configured model would be priced at
    (spec 3.5): `pricing.rate_for_call`, the one precedence function the ledger
    and `in_use.unpriced` use, and where that rate came from. None when nothing
    resolves -- a stored choice that does not resolve prices nothing.

    It is the rate for a call answered under the CONFIGURED name: a real row
    may answer as a dated snapshot and match a different table entry, which is
    why the screen says "would be priced at".

    A native decision is never modelled (`usage.py`: a native row has no
    estimate whatever rate exists), so it is `native` with no figures rather
    than a number nothing uses."""
    if sel is None:
        return None
    if native:
        return {"source": "native"}
    table, stated = prices
    provider, model = sel["provider"], sel["model"]
    entry = pricing.rate_for_call(table, stated, provider_id=provider, model=model)
    if entry is None:
        return {"source": "none"}
    own = model in stated.get(provider, {})
    return {"source": "provider" if own else "table", "entry": entry}
```

3. In `_role_card`, add a `prices: tuple[dict, dict]` parameter at the end.
   Compute `sel = _sel(resolved)` once, use it for `"resolves": sel`, and add
   this as the dict's last entry:

```python
            "rate": _rate(sel, prices,
                          native=resolved.decision_mode == decisions.NATIVE_BACKEND)}
```

4. In `_route_row`, add `prices: tuple[dict, dict]` after `uses`, and make
   the same two changes: `sel = _sel(resolved)`, then the same `"rate"` entry.

5. In `_embedding_card`, add `prices: tuple[dict, dict]` at the end, and add
   `"rate": _rate(resolves, prices)` to the returned dict.

6. Replace `_providers` so the problem is computed once per record and
   `usable` derives from it:

```python
def _providers() -> list[dict]:
    """Every provider, with why it cannot send (`problem`: `resolve.problem`,
    the seam's credential rule, asked of a masked record -- `key_set` stands in
    for the key it deliberately does not carry; None when it can) and
    `usable`, which is exactly `problem is None`; and `own_model`, the model
    its record names (`facts.model_of`): what a reroll naming the provider
    alone runs on a store still at format 1 (`resolve._overridden`, spec 5.6);
    "" when it names none."""
    out = []
    for c in llm_connections.list_connections():
        problem = resolve.problem({**c, "api_key": "x" if c.get("key_set") else ""})
        out.append({"id": c["id"], "name": str(c.get("name") or c["id"]),
                    "kind": str(c.get("kind") or ""),
                    "preset": str(c.get("preset") or "") or providers.infer(c).id,
                    "usable": problem is None, "problem": problem,
                    "own_model": facts.model_of(c)})
    return out
```

7. In `view`, add `prices = _prices()` right after `lookup = ...`, and pass
   `prices` into the three builders.

- [ ] **Step 4: Run the new tests and the neighbours**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_inference_settings_rates.py tests/test_inference_settings.py tests/test_inference_decide.py tests/test_import_guard.py -q`
Expected: all pass.

- [ ] **Step 5: Add the frontend types**

In `frontend/src/api/types.ts`, add above `RoleCard`:

```ts
/** What would price a role's or route's calls (spec 3.5): `pricing.rate_for_call`
 *  on the resolved selection, and where it came from. `none` is no rate at all
 *  (calls read unpriced; never drawn as $0); `native` is a native decision,
 *  which is never modelled. `entry` is per 1,000 tokens, as the ledger keeps it. */
export type RateInfo = {
  source: "provider" | "table" | "none" | "native";
  entry?: PricingEntry;
};
```

Then add the fields:
- `rate: RateInfo | null;` to `RoleCard`, `RouteRow` and `EmbeddingCard`;
- `problem: string | null;` to `InferenceProvider`, with the comment
  `/** Why it cannot send (the seam's own sentence); null exactly when usable. */`.

Next, update every frontend fixture that builds these objects:
1. Find them with `cd frontend && grep -rln "fallback_missing\|usable: true" src --include=*.test.tsx`.
2. In each builder, add `rate: null` to cards and rows, and `problem: null` to
   providers.
3. `ModelsView.test.tsx`'s builders change again in Task 5. Here, only add
   the fields.

- [ ] **Step 6: Typecheck and run the frontend suite**

Run: `cd frontend && npx tsc --noEmit -p . && npx vitest run`
Expected: tsc reports no errors, and the suite is as green as it was before
this task. Record any failures that were already present, and do not fix
them here.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/inference/settings.py backend/tests/test_inference_settings_rates.py frontend/src
git commit -m "Settings view: each role and route says what would price it; each provider why it cannot send"
```

---

### Task 2: Housekeeping links point at the new addresses

**Files:**
- Modify: `backend/src/grimoire/routes/todo.py:470,512,549,1033,1123,1129`
- Test: `backend/tests/test_todo_route.py:1188,1267-1276,1497-1499,1547,1629-1668`

**Interfaces:**
- Produces two chore `fix` hrefs:
  - `"/models#rates"`, replacing `"/config?section=pricing"`;
  - `"/models/edit"`, replacing `"/models/role/embedding"`.

  The `fix_label` `"Pricing"` becomes `"Token rates"`.

- [ ] **Step 1: Update the expectations first**

In `test_todo_route.py`:
- replace every `"/config?section=pricing"` with `"/models#rates"`;
- replace every `"/models/role/embedding"` with `"/models/edit"`;
- where a test asserts the label `"Pricing"`, change it to `"Token rates"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_todo_route.py -q`
Expected: the edited assertions FAIL, showing the old hrefs.

- [ ] **Step 3: Make the same substitutions in `todo.py`**

Change the six literals and the `"Pricing"` label at the lines listed above.

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_todo_route.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes/todo.py backend/tests/test_todo_route.py
git commit -m "Housekeeping: token-rate and embedding chores link to the Models page"
```

---

### Task 3: `ModelSelect` — provider ▾ then model ▾, sharing the picker's logic

**Why a new component rather than editing `ProviderModelPicker`:** the picker is
also used by the reroll popover, Response actions, the preset preview and the
campaign Inspector, all of which are out of scope (spec §6). The Models page
gets a select-based control. The picker's list logic moves into a hook that
both use, so there is still one copy of `combine`, `probesFor` and the
typed-id verdict.

**Files:**
- Create: `frontend/src/components/inference/useModelList.ts`
- Modify: `frontend/src/components/inference/ProviderModelPicker.tsx:16-88,128-212`
  (delete what moved, and use the hook)
- Create: `frontend/src/components/models/health.tsx`
- Create: `frontend/src/components/models/ModelSelect.tsx`
- Test: `frontend/src/components/models/ModelSelect.test.tsx` (new), and the
  picker's existing tests as the regression check

**Interfaces:**
- Produces, from `useModelList.ts`:
  - `type Placement`;
  - `type Listed = { reason: string | null; placements: Map<string, Placement> }`;
  - `combine(answers: ModelCapabilities[]): Listed`, moved verbatim;
  - `probesFor(needs: CapabilityNeed[], row: CapabilityModel | null): TestableCapability[]`,
    moved verbatim;
  - `useModelList(provider: string, needs: CapabilityNeed[], model: string)`,
    which returns
    `{ listed: Listed | null; failed: unknown; known: Placement | undefined; needsVerdict: boolean; verdict: Placement | null; reask: (clear: boolean) => void }`.
- Produces, from `health.tsx`:
  - `useProviderHealth(): ReadonlyMap<string, ProviderHealth>`;
  - `healthWord(h: ProviderHealth | undefined): string`, which returns
    `"working"`, `"failing"`, `"not checked"`, or `""` when there is no
    health;
  - `HealthDot({ health }: { health: ProviderHealth | undefined })`.
- Produces, from `ModelSelect.tsx`: `ModelSelect` with props
  `{ label: string; needs: CapabilityNeed[]; value: { provider: string; model: string }; onChange: (v: { provider: string; model: string }) => void; providers: InferenceProvider[]; health: ReadonlyMap<string, ProviderHealth>; emptyLabel: string; disabled?: boolean }`.
  - Its accessible names are: the group is `label`; the selects are
    `` `${label} provider` `` and `` `${label} model` ``; the typed box is
    `` `${label} model id` ``.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/models/ModelSelect.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api, type CapabilityNeed, type InferenceProvider, type ProviderHealth } from "../../api/client";
import { forgetModelTests } from "../inference/TestCallDialog";
import { ModelSelect } from "./ModelSelect";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: {
    readConnectionCapabilities: vi.fn(), previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});

const PRESET = {
  id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "", url_locked: true,
  billing: "metered", reports_price: true, always: [], possible: [], never: [],
};
type Value = "yes" | "no" | "unknown";
let CAPS: Record<string, Record<string, Value>>;

function answer(need: CapabilityNeed, model?: string) {
  const out = { provider_preset: PRESET, need, reason: null as string | null,
                groups: { fits: [] as unknown[], unverified: [] as unknown[] },
                hidden: [] as { id: string; reason: string }[] };
  for (const id of model ? [model] : Object.keys(CAPS)) {
    const v: Value = CAPS[id]?.[need] ?? "unknown";
    const row = { id, name: id, context: null, prompt: null, completion: null,
                  reason: v === "yes" ? "listed" : "not known yet",
                  capabilities: Object.fromEntries(Object.entries(CAPS[id] ?? {})
                    .map(([k, x]) => [k, { value: x, source: "catalog" }])) };
    if (v === "yes") out.groups.fits.push(row);
    else if (v === "unknown") out.groups.unverified.push(row);
    else out.hidden.push({ id, reason: `${id} cannot ${need}` });
  }
  return out;
}

const PROVIDERS: InferenceProvider[] = [
  { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
    usable: true, problem: null },
  { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom",
    usable: false, problem: "Realm Local has no key set" },
];
const health = (state: ProviderHealth["state"]): ProviderHealth =>
  ({ state, kind: "", detail: "", at: "" });
const HEALTH = new Map([["saltmarch", health("ok")], ["realm", health("error")]]);

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  CAPS = {
    "vendor/m": { generate: "yes", vision: "yes" },
    "vendor/eye": { generate: "yes", vision: "unknown" },
    "vendor/embed": { generate: "no", vision: "no" },
  };
  (api.readConnectionCapabilities as any).mockImplementation(
    (_p: string, need: CapabilityNeed, model?: string) => Promise.resolve(answer(need, model)));
});

/** A holder that keeps the value, as the form does, and reports each change. */
function Holder({ start, needs = ["generate"] as CapabilityNeed[], seen }:
  { start: { provider: string; model: string }; needs?: CapabilityNeed[];
    seen: { provider: string; model: string }[] }) {
  const [v, setV] = useState(start);
  return <ModelSelect label="Primary" needs={needs} value={v} providers={PROVIDERS}
                      health={HEALTH} emptyLabel="Not set"
                      onChange={(next) => { seen.push(next); setV(next); }} />;
}

const provider = () => screen.getByRole("combobox", { name: "Primary provider" });
const model = () => screen.getByRole("combobox", { name: "Primary model" });

test("each provider says its health, and one that cannot send stays choosable with why", async () => {
  render(<Holder start={{ provider: "", model: "" }} seen={[]} />);
  const options = within(provider()).getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(["Not set", "Saltmarch Router (working)",
                           "Realm Local (failing) — cannot send: Realm Local has no key set"]);
  expect(within(provider()).getByRole("option", { name: /Realm Local/ })).not.toBeDisabled();
});

test("choosing an unusable provider warns beside the row", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "", model: "" }} seen={seen} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  expect(seen.at(-1)).toEqual({ provider: "realm", model: "" });
  expect(await screen.findByText("Realm Local cannot send: Realm Local has no key set"))
    .toBeInTheDocument();
});

test("a provider the list no longer holds is kept, never blanked", async () => {
  render(<Holder start={{ provider: "gone", model: "vendor/m" }} seen={[]} />);
  expect(provider()).toHaveValue("gone");
  expect(within(provider()).getByRole("option", { name: "gone (missing provider)" }))
    .toBeInTheDocument();
});

test("models come grouped: fits, then unverified; a known no is not offered", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={[]} />);
  const fits = await screen.findByRole("group", { name: "Fits this role" });
  expect(within(fits).getAllByRole("option").map((o) => o.textContent)).toEqual(["vendor/eye", "vendor/m"]);   // `combine` sorts by id
  expect(within(model()).queryByRole("option", { name: "vendor/embed" })).toBeNull();
});

test("two needs combine: a model unverified for one lands under Unverified", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "" }} needs={["generate", "vision"]} seen={[]} />);
  const unverified = await screen.findByRole("group", { name: "Unverified" });
  expect(within(unverified).getByRole("option", { name: "vendor/eye" })).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "Fits this role" }))
    .getByRole("option", { name: "vendor/m" })).toBeInTheDocument();
});

test("a stored model the list does not hold is kept and says so", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "vendor/ghost" }} seen={[]} />);
  expect(await screen.findByRole("option", { name: "vendor/ghost (not in this provider's list)" }))
    .toBeInTheDocument();
  expect(model()).toHaveValue("vendor/ghost");
});

test("Other model id… takes a typed id, and that id is judged on its own", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={seen} />);
  await screen.findByRole("group", { name: "Fits this role" });
  fireEvent.change(model(), { target: { value: "\u0000other" } });
  const box = screen.getByRole("textbox", { name: "Primary model id" });
  fireEvent.change(box, { target: { value: " vendor/typed " } });
  fireEvent.click(screen.getByRole("button", { name: "Use this id" }));
  expect(seen.at(-1)).toEqual({ provider: "saltmarch", model: "vendor/typed" });
  // Not listed, so it is asked about alone: the unknown answer is Unverified.
  expect(await screen.findByText(/Unverified: not known yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Test vendor/typed" })).toBeInTheDocument();
});

test("changing the provider clears the model", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "saltmarch", model: "vendor/m" }} seen={seen} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  expect(seen.at(-1)).toEqual({ provider: "realm", model: "" });
});

test("an unverified choice offers Test…, and a fitting one does not", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "vendor/eye" }} needs={["generate", "vision"]} seen={[]} />);
  expect(await screen.findByRole("button", { name: "Test vendor/eye" })).toBeInTheDocument();
  fireEvent.change(model(), { target: { value: "vendor/m" } });
  expect(screen.queryByRole("button", { name: /^Test / })).toBeNull();
});

test("closing the test dialog after a test landed puts focus back on the model select", async () => {
  // As the picker's own test does (ProviderModelPicker.test.tsx:301): the run
  // lands "works", the re-asked list moves vendor/eye into Fits, and the
  // Test… the dialog came from is gone by the time it closes.
  (api.previewModelTest as any).mockResolvedValue({
    provider: "Saltmarch Router", provider_id: "saltmarch", model: "vendor/eye",
    sends: [], estimated_cost_usd: null });
  (api.runModelTest as any).mockImplementation(async () => {
    CAPS["vendor/eye"].vision = "yes";
    return { provider: "saltmarch", model: "vendor/eye", rev: "r2",
             results: { vision: { ok: true } }, recorded: true };
  });
  render(<Holder start={{ provider: "saltmarch", model: "vendor/eye" }} needs={["generate", "vision"]} seen={[]} />);
  const opener = await screen.findByRole("button", { name: "Test vendor/eye" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByText("vision: works");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Test vendor/eye" })).toBeNull());
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(model()).toHaveFocus());
});

test("a slow answer for the provider just left never fills the new one's list", async () => {
  let release!: () => void;
  (api.readConnectionCapabilities as any).mockImplementation((p: string, need: CapabilityNeed) =>
    p === "saltmarch"
      ? new Promise((resolve) => { release = () => resolve(answer(need)); })
      : Promise.resolve({ ...answer(need), groups: { fits: [], unverified: [] }, hidden: [] }));
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={[]} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  await act(async () => { release(); });
  expect(screen.queryByRole("option", { name: "vendor/m" })).toBeNull();
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/models/ModelSelect.test.tsx`
Expected: FAIL with `Failed to resolve import "./ModelSelect"`.

- [ ] **Step 3: Extract the hook**

Create `frontend/src/components/inference/useModelList.ts`. Move these from
`ProviderModelPicker.tsx`, verbatim: `Placement` (now exported), `combine`,
`knownNo`, `probesFor` (now exported) and `ask`. Their doc comments move with
them. Then add:

```ts
export type Listed = ReturnType<typeof combine>;

/** One provider's models for a set of needs, and the chosen model's own
 *  verdict when no row lists it (spec 6.3) -- the logic every model control
 *  shares, so the picker and the Models page's select cannot disagree.
 *
 *  An answer is used only while the provider and needs it was asked for are
 *  still the ones shown: a slow list for a provider the reader has left
 *  never fills the next one's. `reask(true)` clears the list first (the
 *  picker's radios are redrawn from nothing); `reask(false)` keeps it on
 *  screen until the new one lands, so a control with focus is not unmounted. */
export function useModelList(provider: string, needs: CapabilityNeed[], model: string) {
  const [listed, setListed] = useState<Listed | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [verdict, setVerdict] = useState<Placement | null>(null);
  const [asked, setAsked] = useState(0);
  // Held as a key, so a caller passing a fresh array each render asks once.
  const needKey = needs.join(",");

  useEffect(() => { setListed(null); setFailed(null); }, [provider, needKey]);

  useEffect(() => {
    if (!provider || !needKey) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[])
      .then((answers) => { if (current) { setListed(combine(answers)); setFailed(null); } })
      .catch((err: unknown) => { if (current) setFailed(err); });
    return () => { current = false; };
  }, [provider, needKey, asked]);

  const known = listed?.placements.get(model);
  const needsVerdict = !!listed && !!model && listed.reason === null && !known;
  useEffect(() => {
    setVerdict(null);
    if (!needsVerdict) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[], model)
      .then((answers) => {
        if (!current) return;
        const { reason, placements } = combine(answers);
        setVerdict(placements.get(model)
          ?? { group: "hidden", id: model, reason: reason ?? "Nothing is known of this id." });
      })
      .catch(() => { if (current) setVerdict(null); });
    return () => { current = false; };
  }, [needsVerdict, provider, needKey, model, asked]);

  const reask = useCallback((clear: boolean) => {
    if (clear) setListed(null);
    setAsked((n) => n + 1);
  }, []);

  return { listed, failed, known, needsVerdict, verdict, reask };
}
```

Imports for the new file:

```ts
import { useCallback, useEffect, useState } from "react";
import {
  api, type CapabilityModel, type CapabilityNeed, type CapabilityValue,
  type ModelCapabilities, type TestableCapability,
} from "../../api/client";
```

Then edit `ProviderModelPicker.tsx` to use the hook:
1. Import `{ combine, probesFor, useModelList, type Placement }` from
   `"./useModelList"`, and keep `export { combine } from "./useModelList";`
   for any existing importer. Check for importers with
   `grep -rn "combine" src --include=*.ts*`.
2. Delete the moved declarations, along with the `listed`, `failed`,
   `typedVerdict` and `asked` state and the two ask effects (old lines
   129-131, 140 and 162-189).
3. In their place, add:

```ts
  const { listed, failed, known, needsVerdict, verdict: typedVerdict, reask } =
    useModelList(provider, needs, model);
```

4. In the `useModelTests` callback, replace `setListed(null); setAsked((n) => n + 1);`
   with `reask(true);`. Keep the comment above it.
5. Delete the picker's own `const known = ...` and
   `const needsVerdict = ...` lines, which now come from the hook. Keep
   `const needKey = needs.join(",");`: the picker's focus-restore effect
   still reads it (`:197`, `:206`). Only the list-fetching effects moved.

Run: `cd frontend && npx vitest run src/components/inference`
Expected: the picker's existing tests pass unchanged. This step changes no
behaviour.

- [ ] **Step 4: Write `health.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type ProviderHealth } from "../../api/client";
import { onConfigChanged } from "../../appEvents";

/** Each provider's health, by id, from `GET /api/llm-connections` -- the one
 *  place it is served (the app's health registry; spec 1). Read again on any
 *  model-settings change. A failed read is an empty map: a provider with no
 *  health shows no dot, never a guessed one. */
export function useProviderHealth(): ReadonlyMap<string, ProviderHealth> {
  const [map, setMap] = useState<ReadonlyMap<string, ProviderHealth>>(() => new Map());
  const [asked, setAsked] = useState(0);
  useEffect(() => onConfigChanged(() => setAsked((n) => n + 1)), []);
  useEffect(() => {
    let live = true;
    api.listConnections()
      .then((list) => { if (live) setMap(new Map(list.map((c) => [c.id, c.health]))); })
      .catch(() => { if (live) setMap(new Map()); });
    return () => { live = false; };
  }, [asked]);
  return map;
}

/** A health state in a word, as an `<option>` can carry it (no colour there). */
export function healthWord(h: ProviderHealth | undefined): string {
  if (!h) return "";
  if (h.state === "ok") return "working";
  if (h.state === "error") return "failing";
  return "not checked";
}

/** The dot, and its word for a reader who cannot see colour. Nothing for a
 *  provider whose health is not known at all. */
export function HealthDot({ health }: { health: ProviderHealth | undefined }) {
  if (!health) return null;
  const cls = health.state === "ok" ? "ok" : health.state === "error" ? "bad" : "off";
  return (
    <>
      <span className={"conn-dot " + cls} aria-hidden> ●</span>
      <span className="sr-only"> {healthWord(health)}</span>
    </>
  );
}
```

- [ ] **Step 5: Write `ModelSelect.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import type { CapabilityModel, CapabilityNeed, InferenceProvider, ProviderHealth } from "../../api/client";
import { errorText } from "../../api/errors";
import { TestCallDialog, useModelTests } from "../inference/TestCallDialog";
import { probesFor, useModelList } from "../inference/useModelList";
import { healthWord } from "./health";

/** The model select's value for "Other model id…": a byte no model id holds. */
const OTHER = "\u0000other";

function providerLabel(p: InferenceProvider, health: ProviderHealth | undefined): string {
  const word = healthWord(health);
  return `${p.name}${word ? ` (${word})` : ""}`
    + (p.usable ? "" : ` — cannot send: ${p.problem ?? "it cannot send"}`);
}

/** Provider ▾ then model ▾ (spec 3.2): the Models page's one model control.
 *
 *  - A provider that cannot send stays choosable -- a stored choice must be
 *    adjustable while its key is fixed on Providers -- and says why.
 *  - A stored provider or model the lists no longer hold is kept as the
 *    selected option, so opening the form never changes a value.
 *  - Models come from `useModelList` (the picker's own logic): Fits this
 *    role, then Unverified; a model known not to fit is not offered.
 *  - "Other model id…" takes any id; it is then judged on its own, and gets
 *    Test… when that verdict is unverified, as a listed unverified one does.
 *  - Test… opens `TestCallDialog`, which previews and waits for a yes; a run
 *    outlives its dialog and is rejoined (`useModelTests`). */
export function ModelSelect({ label, needs, value, onChange, providers, health, emptyLabel,
                              disabled = false }:
  { label: string; needs: CapabilityNeed[]; value: { provider: string; model: string };
    onChange: (v: { provider: string; model: string }) => void;
    providers: InferenceProvider[]; health: ReadonlyMap<string, ProviderHealth>;
    emptyLabel: string; disabled?: boolean }) {
  const { provider, model } = value;
  const list = useModelList(provider, needs, model);
  const [other, setOther] = useState(false);
  const [draft, setDraft] = useState("");
  const [testing, setTesting] = useState<{ model: string; row: CapabilityModel | null } | null>(null);
  const modelRef = useRef<HTMLSelectElement>(null);
  // A landed test can move the model out of Unverified, taking the Test…
  // that had focus with it: focus goes back to the model select.
  const refocus = useRef(false);
  const tests = useModelTests(() => {
    refocus.current = document.activeElement instanceof HTMLElement
      && !!document.activeElement.closest(".model-select");
    list.reask(false);
  });
  useEffect(() => {
    if (!refocus.current || testing) return;
    if (document.activeElement && document.activeElement !== document.body) return;
    refocus.current = false;
    modelRef.current?.focus();
  }, [list.listed, testing]);
  useEffect(() => { setOther(false); setDraft(""); }, [provider]);

  const chosen = providers.find((p) => p.id === provider);
  const rows = list.listed ? [...list.listed.placements.values()] : [];
  const fits = rows.flatMap((p) => (p.group === "fits" ? [p.row] : []));
  const unverified = rows.flatMap((p) => (p.group === "unverified" ? [p.row] : []));
  const offered = new Set([...fits, ...unverified].map((r) => r.id));
  const chosenRow = list.known?.group === "unverified" ? list.known.row
    : list.verdict?.group === "unverified" ? list.verdict.row : null;

  function takeDraft() {
    const id = draft.trim();
    if (!id || disabled) return;
    onChange({ provider, model: id });
    setOther(false);
    setDraft("");
  }

  return (
    <div className="model-select" role="group" aria-label={label}>
      <select aria-label={`${label} provider`} value={provider} disabled={disabled}
              onChange={(e) => onChange({ provider: e.target.value, model: "" })}>
        <option value="">{emptyLabel}</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id} title={p.problem ?? undefined}>
            {providerLabel(p, health.get(p.id))}
          </option>
        ))}
        {provider && !chosen && <option value={provider}>{provider} (missing provider)</option>}
      </select>
      {chosen && !chosen.usable && (
        <p className="field-hint field-warning" role="note">
          {chosen.name} cannot send: {chosen.problem ?? "it cannot send"}
        </p>
      )}
      {provider && (
        <>
          <select ref={modelRef} aria-label={`${label} model`} value={other ? OTHER : model}
                  disabled={disabled}
                  onChange={(e) => {
                    if (e.target.value === OTHER) { setOther(true); return; }
                    setOther(false);
                    onChange({ provider, model: e.target.value });
                  }}>
            <option value="">Choose a model…</option>
            {fits.length > 0 && (
              <optgroup label="Fits this role">
                {fits.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
              </optgroup>
            )}
            {unverified.length > 0 && (
              <optgroup label="Unverified">
                {unverified.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
              </optgroup>
            )}
            {model && !offered.has(model) && (
              <option value={model}>
                {model}{list.listed ? " (not in this provider's list)" : ""}
              </option>
            )}
            <option value={OTHER}>Other model id…</option>
          </select>
          {other && (
            <span className="model-typed">
              <input aria-label={`${label} model id`} value={draft} disabled={disabled}
                     placeholder="vendor/model-name"
                     onChange={(e) => setDraft(e.target.value)} />
              <button type="button" className="subtle" disabled={disabled || !draft.trim()}
                      onClick={takeDraft}>
                Use this id
              </button>
            </span>
          )}
          {list.failed !== null && (
            <p className="field-hint">Couldn't list this provider's models: {errorText(list.failed)}</p>
          )}
          {list.listed?.reason && <p className="field-hint">{list.listed.reason}</p>}
          {list.needsVerdict && (
            <p className="field-hint">
              {list.verdict === null ? "Checking what is known of this id…"
                : list.verdict.group === "hidden" ? `Known not to fit: ${list.verdict.reason}`
                : list.verdict.group === "fits" ? "Fits"
                : `Unverified: ${list.verdict.row.reason}`}
            </p>
          )}
          {model && chosenRow && (
            <button type="button" className="subtle" aria-label={`Test ${model}`}
                    disabled={disabled} onClick={() => setTesting({ model, row: chosenRow })}>
              Test…
            </button>
          )}
        </>
      )}
      {testing && (
        <TestCallDialog key={`${provider}\u0000${testing.model}`}
                        provider={provider} model={testing.model}
                        capabilities={probesFor(needs, testing.row)}
                        tests={tests} onClose={() => {
                          // The dialog hands focus back to its Test… -- unless
                          // a landed test redrew it away, and then this does.
                          refocus.current = true;
                          setTesting(null);
                        }} />
      )}
    </div>
  );
}
```

`known.group === "unverified"` covers a listed unverified choice, and
`verdict.group === "unverified"` covers a typed one. Together they are
exactly where the picker offers Test… today (spec §3.2).

Focus is restored in two places, as the picker restores it:
- A test that lands while focus is inside the control marks `refocus`.
- Closing the dialog marks it too, because focus was in the dialog, which
  is portalled outside `.model-select`.

Either way, once the redrawn list settles and nothing else holds focus, the
model select takes it.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/components/models/ModelSelect.test.tsx src/components/inference`
Expected: all PASS. If the `"Fits this role"` group query fails because
jsdom does not expose `<optgroup>` as a role, switch the query to
`screen.getByRole("group", { name: ... })`. jsdom maps `optgroup` to `group`,
so the query should work as written. Do not change what the component
renders.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/inference/useModelList.ts frontend/src/components/inference/ProviderModelPicker.tsx frontend/src/components/models
git commit -m "ModelSelect: provider then model as dropdowns, sharing the picker's capability logic"
```

---

### Task 4: Token rates — wording, the Set-rate link, and the rate table block

**Files:**
- Create: `frontend/src/components/models/rates.ts`
- Modify: `frontend/src/components/PricingEditor.tsx:126-160,212-260`, adding
  the `addModel` and `onSaved` props
- Create: `frontend/src/components/models/TokenRates.tsx`
- Test: `frontend/src/components/models/rates.test.ts` and
  `frontend/src/components/models/TokenRates.test.tsx` (new), plus
  `frontend/src/components/PricingEditor.test.tsx` (existing, kept green)

**Interfaces:**
- Consumes: `RateInfo` (Task 1) and `perMillionRate` from
  `components/cost.tsx`, which every cost surface already formats through.
- Produces, from `rates.ts`:
  - `rateWords(rate: RateInfo | null, opts?: { promptOnly?: boolean }): string | null`;
  - `setRateHref(model: string): string`, which is
    `` `/models?add=${encodeURIComponent(model)}#rates` ``;
  - `addedModel(search: string): string`, which decodes `?add=`.
- Produces, from `PricingEditor`: new optional props
  `addModel?: string; onSaved?: () => void`.
- Produces, from `TokenRates.tsx`: `TokenRates` with props
  `{ addModel?: string; onSaved: () => void }`. It renders
  `<section id="rates" aria-labelledby="rates-title">` with heading
  "Token rates".

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/models/rates.test.ts`:

```ts
import { addedModel, rateWords, setRateHref } from "./rates";

const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };

test("a stated rate names its source and quotes per million", () => {
  expect(rateWords({ source: "provider", entry: BOTH }))
    .toBe("would be priced at $3/M in · $15/M out (provider)");
  expect(rateWords({ source: "table", entry: BOTH }))
    .toBe("would be priced at $3/M in · $15/M out (your rates)");
});

test("an embedding shows only what it is charged for", () => {
  expect(rateWords({ source: "table", entry: BOTH }, { promptOnly: true }))
    .toBe("would be priced at $3/M (your rates)");
});

test("no rate is words, never a zero; a real zero is a zero", () => {
  expect(rateWords({ source: "none" })).toBe("no rate: calls unpriced");
  expect(rateWords({ source: "table", entry: { prompt_usd_per_1k: 0, completion_usd_per_1k: 0 } }))
    .toBe("would be priced at $0/M in · $0/M out (your rates)");
});

test("a native decision says why it has no figure", () => {
  expect(rateWords({ source: "native" }))
    .toBe("native decisions: priced only if the provider reports a cost");
});

test("an entry missing a side reads as no rate, not as a zero", () => {
  expect(rateWords({ source: "table", entry: { prompt_usd_per_1k: 0.003 } }))
    .toBe("no rate: calls unpriced");
});

test("nothing resolved, nothing said", () => {
  expect(rateWords(null)).toBeNull();
});

test("Set rate carries any model id through the address intact", () => {
  for (const id of ["vendor/model", "odd#id?x=1", "with space", "vendor/m:free"]) {
    const href = setRateHref(id);
    expect(href.endsWith("#rates")).toBe(true);
    const search = href.slice(href.indexOf("?"), href.indexOf("#rates"));
    expect(addedModel(search)).toBe(id);
  }
});
```

`frontend/src/components/models/TokenRates.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "../../api/client";
import { TokenRates } from "./TokenRates";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { getPricing: vi.fn(), setPricing: vi.fn() } };
});

const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };
const TABLE = { rates: { "vendor/m": BOTH, "": BOTH } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPricing).mockResolvedValue(TABLE as any);
  vi.mocked(api.setPricing).mockImplementation(async (rates) => ({ rates }));
});

const block = () => within(screen.getByRole("region", { name: "Token rates" }));

test("the table is read-only until Edit", async () => {
  render(<TokenRates onSaved={() => {}} />);
  expect(await block().findByRole("cell", { name: "vendor/m" })).toBeInTheDocument();
  expect(block().getByRole("cell", { name: "Every other model" })).toBeInTheDocument();
  expect(block().queryByRole("spinbutton")).toBeNull();
  fireEvent.click(block().getByRole("button", { name: "Edit rates" }));
  expect(await block().findByRole("button", { name: "Save rates" })).toBeInTheDocument();
});

test("the moved explanations come with it", async () => {
  render(<TokenRates onSaved={() => {}} />);
  await block().findByRole("cell", { name: "vendor/m" });
  expect(block().getByText(/is not the same as setting them\s+to zero/)).toBeInTheDocument();
  expect(block().getByText(/never added to what a provider actually charged/)).toBeInTheDocument();
});

test("Set rate opens the editor on a new row for that model, caret in Input", async () => {
  render(<TokenRates addModel="vendor/new" onSaved={() => {}} />);
  const id = await block().findByDisplayValue("vendor/new");
  expect(id).toBeInTheDocument();
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/new" }))
    .toHaveFocus());
});

test("Set rate on a model already in the table edits that entry instead", async () => {
  render(<TokenRates addModel="vendor/m" onSaved={() => {}} />);
  await block().findByRole("button", { name: "Save rates" });
  expect(block().getAllByDisplayValue("vendor/m")).toHaveLength(1);
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/m" }))
    .toHaveFocus());
});

test("a second Set rate while the editor is open adds that model too", async () => {
  const { rerender } = render(<TokenRates addModel="vendor/a" onSaved={() => {}} />);
  await block().findByDisplayValue("vendor/a");
  rerender(<TokenRates addModel="vendor/b" onSaved={() => {}} />);
  expect(await block().findByDisplayValue("vendor/b")).toBeInTheDocument();
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/b" }))
    .toHaveFocus());
});

test("a saved table goes back to reading, and says so to the page", async () => {
  const onSaved = vi.fn();
  render(<TokenRates onSaved={onSaved} />);
  fireEvent.click(await block().findByRole("button", { name: "Edit rates" }));
  fireEvent.click(await block().findByRole("button", { name: "Save rates" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(await block().findByRole("button", { name: "Edit rates" })).toBeInTheDocument();
});

test("a table that could not be read offers nothing to edit", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ rates: {}, unreadable: true } as any);
  render(<TokenRates onSaved={() => {}} />);
  expect(await block().findByText(/Could not read the rate table/)).toBeInTheDocument();
  expect(block().queryByRole("button", { name: "Edit rates" })).toBeNull();
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/models/rates.test.ts src/components/models/TokenRates.test.tsx`
Expected: FAIL, because the modules are missing.

- [ ] **Step 3: Write `rates.ts`**

```ts
import type { RateInfo } from "../../api/client";
import { perMillionRate } from "../cost";

/** What would price a role's or route's calls, in words (spec 3.5). Quoted
 *  per million through `perMillionRate`, the one formatter every cost surface
 *  uses. "Would be priced at", because it is the rate for a call answered
 *  under the configured name; a dated snapshot can match another entry.
 *
 *  A price nobody reported is never rendered as zero: `none`, and an entry
 *  missing a side (which pricing never uses), are words. A real 0 is `$0/M`. */
export function rateWords(rate: RateInfo | null, { promptOnly = false } = {}): string | null {
  if (!rate) return null;
  if (rate.source === "native") return "native decisions: priced only if the provider reports a cost";
  const input = rate.entry?.prompt_usd_per_1k;
  const output = rate.entry?.completion_usd_per_1k;
  if (rate.source === "none" || input === undefined || output === undefined) {
    return "no rate: calls unpriced";
  }
  const where = rate.source === "provider" ? "provider" : "your rates";
  const figure = promptOnly ? perMillionRate(input)
    : `${perMillionRate(input)} in · ${perMillionRate(output)} out`;
  return `would be priced at ${figure} (${where})`;
}

/** Where Set rate goes: the model rides the query, encoded, and the fragment
 *  only scrolls -- so an id holding `/`, `?` or `#` arrives whole. */
export function setRateHref(model: string): string {
  return `/models?add=${encodeURIComponent(model)}#rates`;
}

/** The model a Set rate link asked for, from a location's `search`. */
export function addedModel(search: string): string {
  return new URLSearchParams(search).get("add") ?? "";
}
```

`perMillionRate(0.003)` is `$3/M`, so the per-model figures read
"$3/M in · $15/M out". If the existing `cost.tsx` formatter renders differently
under jsdom's locale, adjust the test's expected strings to what
`perMillionRate` returns. Do not adjust the formatter.

- [ ] **Step 4: Give `PricingEditor` its two props**

In `PricingEditor.tsx`:

1. Change the signature to
   `export function PricingEditor({ addModel, onSaved }: { addModel?: string; onSaved?: () => void } = {}) {`.

2. Add a ref and a one-shot flag after the existing state:

```ts
  /** The Input box Set rate puts the caret in (spec 3.5). */
  const focusRef = useRef<HTMLInputElement>(null);
  const [focusKey, setFocusKey] = useState<number | null>(null);
  /** The Set rate model already placed, so a re-run places a NEW one (a
   *  second Set rate while the editor is open) and never the same one twice. */
  const placed = useRef<string | null>(null);
```

3. In the load effect's `.then`, after `setRows(...)`, place the requested
   model:

```ts
        const loaded = t.unreadable ? [] : toRows(t.rates);
        // Set rate: edit the model's own entry when it has one, else add a
        // row for it -- never a second row claiming the same model.
        if (addModel && !t.unreadable && placed.current !== addModel) {
          placed.current = addModel;
          const at = loaded.find((r) => !r.isDefault && r.id === addModel);
          if (at) {
            setFocusKey(at.key);
          } else {
            const row = { key: nextKey++, id: addModel, isDefault: false, rates: emptyRates() };
            loaded.push(row);
            setFocusKey(row.key);
          }
        }
        setRows(loaded);
```

   This replaces the existing `setRows(t.unreadable ? [] : toRows(t.rates));`.
   Change the effect's dependency list to `[reload, addModel]`. `placed`
   keeps a re-run from adding the same row twice, and still lets a second
   Set rate add its own model. The re-run re-reads the table, which discards
   an unsaved draft. That is accepted: Set rate is a fresh request.

4. Add an effect that focuses once the row is drawn:

```ts
  useEffect(() => {
    if (focusKey === null || !focusRef.current) return;
    focusRef.current.focus();
    setFocusKey(null);
  }, [focusKey, rows]);
```

5. On the `RateFields` in the row map, pass
   `inputRef={row.key === focusKey ? focusRef : undefined}`.

6. In `save()`, after `setSaved(true);`, add `onSaved?.();`.

7. Add `useRef` to the React import.

- [ ] **Step 5: Write `TokenRates.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type PricingEntry } from "../../api/client";
import { perMillionRate } from "../cost";
import { PricingEditor } from "../PricingEditor";

type Read = { rates: Record<string, PricingEntry>; unreadable: boolean } | null;

/** The fallback rate table on the Models page (spec 3.5): read-only, with an
 *  explicit Edit that mounts the existing editor, which saves itself.
 *  `addModel` (from Set rate) opens straight into the editor on that model. */
export function TokenRates({ addModel, onSaved }: { addModel?: string; onSaved: () => void }) {
  const [mode, setMode] = useState<"view" | "edit">(addModel ? "edit" : "view");
  const [read, setRead] = useState<Read>(null);
  const [asked, setAsked] = useState(0);

  useEffect(() => { if (addModel) setMode("edit"); }, [addModel]);
  useEffect(() => {
    let live = true;
    api.getPricing()
      .then((t) => { if (live) setRead({ rates: t.rates, unreadable: !!t.unreadable }); })
      .catch(() => { if (live) setRead({ rates: {}, unreadable: true }); });
    return () => { live = false; };
  }, [asked]);

  const rows = read ? Object.entries(read.rates)
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b))) : [];

  return (
    <section id="rates" className="token-rates" aria-labelledby="rates-title">
      <h3 id="rates-title">Token rates</h3>
      <p className="config-copy">
        Your fallback table, for models whose provider states no price. A model's own
        rates, set on its provider's page, come first.
      </p>
      <p className="config-copy">
        Grimoire records what each provider says a call cost. OpenRouter
        says; an OpenAI-compatible endpoint you host yourself says nothing
        at all, and those calls read as <em>not reported</em> everywhere costs
        are shown — which is honest, and no use for answering what a
        campaign has cost. Rates here fill that gap.
      </p>
      <p className="config-copy">
        What comes out of them is an <strong>estimate, and is labelled as
        one</strong>: a modelled figure is reported in its own column, is
        never added to what a provider actually charged, and is never
        charged against a campaign's budget. A model with no entry of its
        own falls back to a <code>provider/*</code> wildcard, then to the
        catch-all. Rates are dollars per 1,000 tokens; the per-million
        figure most price sheets quote is shown under each box.
      </p>
      <p className="config-copy">
        Leaving the two cache boxes empty is not the same as setting them
        to zero: cached tokens are part of the prompt the provider counted,
        so an empty box prices them at the input rate. Fill them in only
        for a provider that discounts them.
      </p>
      {mode === "edit" ? (
        <PricingEditor addModel={addModel}
                       onSaved={() => { setMode("view"); setAsked((n) => n + 1); onSaved(); }} />
      ) : read === null ? (
        <p className="field-hint">Reading rates…</p>
      ) : read.unreadable ? (
        <p className="field-hint error">
          Could not read the rate table. Nothing is shown and nothing can be
          saved from here — an empty form saved over rates that failed to load
          would delete them.
        </p>
      ) : (
        <>
          {rows.length === 0 ? (
            <p className="field-hint">No rates set.</p>
          ) : (
            <table className="rates-table">
              <thead><tr><th scope="col">Model</th><th scope="col">Input</th><th scope="col">Output</th></tr></thead>
              <tbody>
                {rows.map(([id, e]) => (
                  <tr key={id}>
                    <td>{id === "" ? "Every other model" : id}</td>
                    <td>{e.prompt_usd_per_1k === undefined ? "—" : perMillionRate(e.prompt_usd_per_1k)}</td>
                    <td>{e.completion_usd_per_1k === undefined ? "—" : perMillionRate(e.completion_usd_per_1k)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="form-actions">
            <button className="subtle" onClick={() => setMode("edit")}>Edit rates</button>
          </div>
        </>
      )}
    </section>
  );
}
```

The three explanatory paragraphs are the Settings → Token rates pane's
(`ConfigView.tsx:897-919`), moved verbatim, apart from the sentence about a
model's own rates, which the first paragraph now carries. Task 10 deletes
them from ConfigView.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/components/models/rates.test.ts src/components/models/TokenRates.test.tsx src/components/PricingEditor.test.tsx`
Expected: all PASS. The existing `PricingEditor.test.tsx` passes untouched,
because both props are optional.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/models/rates.ts frontend/src/components/models/rates.test.ts frontend/src/components/models/TokenRates.tsx frontend/src/components/models/TokenRates.test.tsx frontend/src/components/PricingEditor.tsx
git commit -m "Token rates: per-role rate words, Set rate link, and a read-first rate table"
```

---

### Task 5: The Models summary (`/models`)

**Files:**
- Create: `frontend/src/components/models/notes.tsx`. Move
  `ModelsView.tsx:37-126` there: `WARNINGS`, `DECIDE_WORDS`, `warningOf`,
  `useWarning`, `Warning`, `Problem` and `DecideNote`, all exported.
- Create: `frontend/src/components/models/taskHash.ts` and
  `frontend/src/components/models/taskHash.test.ts`
- Create: `frontend/src/components/inference/InferenceNav.tsx`
- Create: `frontend/src/components/models/RoleRow.tsx`
- Rewrite: `frontend/src/routes/ModelsView.tsx`
- Rewrite: `frontend/src/routes/ModelsView.test.tsx`

**Interfaces:**
- Consumes:
  - `useProviderHealth` and `HealthDot` (Task 3);
  - `rateWords`, `setRateHref`, `addedModel` and `TokenRates` (Task 4);
  - `RateInfo` (Task 1).
- Produces:
  - `taskHash(key: string): string`, giving `` `#task-${encodeURIComponent(key)}` ``;
  - `taskFromHash(hash: string): string | null`, which never throws;
  - `ADVANCED_HASH = "#advanced"`;
  - `InferenceNav({ current }: { current: "providers" | "models" | "presets" })`,
    a `ColumnSection` labelled "Inference";
  - `RoleRow({ role, settings, health })` for a generative role, and
    `EmbeddingRow({ card, health })`. Each is a `role="group"` named after
    the role;
  - `SelectionLine({ provider, providerName, model, preset, presetName, health, withPreset })`,
    the line every summary uses;
  - `ModelsView({ edit }: { edit?: boolean })`, the default export. In this
    task `edit` is accepted and ignored; Task 6 renders the form.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/models/taskHash.test.ts`:

```ts
import { taskFromHash, taskHash } from "./taskHash";

test("a task key round-trips through the hash, whatever it holds", () => {
  for (const key of ["summary", "scene break", "odd#key", "100%"]) {
    expect(taskFromHash(taskHash(key))).toBe(key);
  }
});

test("a malformed escape is no task, never an exception", () => {
  expect(taskFromHash("#task-%")).toBeNull();
  expect(taskFromHash("#task-%ZZ")).toBeNull();
});

test("a hash that names no task is no task", () => {
  expect(taskFromHash("")).toBeNull();
  expect(taskFromHash("#rates")).toBeNull();
  expect(taskFromHash("#task-")).toBeNull();
});
```

Replace `frontend/src/routes/ModelsView.test.tsx` in full. Keep the old file's
lines 16-59 unchanged: `PRESET`, `Value`, `NAMES`, `CAPS`, `BASE_CAPS` and
`answer`, which are the capabilities double. Everything else is the code
below. Where the code says "lines 16-60 of the old file", paste those lines.

```tsx
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, PRESET_CLEAR, type CapabilityNeed } from "../api/client";
import { forgetModelTests } from "../components/inference/TestCallDialog";
import ModelsView from "./ModelsView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getInferenceSettings: vi.fn(), putInferenceSettings: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewControls: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(), dismissRetiredNote: vi.fn(),
    listConnections: vi.fn(), getPricing: vi.fn(), setPricing: vi.fn(),
  } };
});

// ...the old file's lines 16-59 (PRESET … answer), unchanged...

const sel = (provider = "", model = "", preset = "") => ({ provider, model, preset });
const resolved = (over: Record<string, unknown> = {}) => ({
  provider: "saltmarch", provider_name: "Saltmarch Router", model: "vendor/m",
  preset: "", preset_name: "", via: "role", scope: "global", ...over,
});
const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };
const card = (over: Record<string, unknown> = {}) => ({
  stored: sel(), fallback: sel(), resolves: resolved(), inherits: resolved(), problem: null,
  fallback_missing: [], fallback_problem: null, decision_mode: "", decides_natively: "unknown",
  rate: { source: "provider", entry: BOTH }, ...over,
});
const route = (over: Record<string, unknown>) => ({
  hint: "", tasks: [], operation: "generate", default_role: "fast", requires: [],
  campaign_scoped: true, use: "", pin: sel(), preset: "", resolves: resolved(),
  inherits: resolved(), problem: null, fallback_missing: [], fallback_problem: null,
  decision_mode: "", decides_natively: "unknown", role: "fast", uses: "fast",
  rate: { source: "provider", entry: BOTH }, ...over,
});

const ROUTES = [
  route({ key: "scene", label: "Scene prose", default_role: "primary", role: "primary", uses: "primary" }),
  route({ key: "summary", label: "Rolling summary", hint: "Keeps the running summary." }),
  route({ key: "image", label: "Image descriptions", requires: ["vision"] }),
  route({ key: "scene break", label: "Scene break", operation: "decide", default_role: "decision",
          role: "fast", uses: "decision" }),
];

function settings(over: Record<string, unknown> = {}) {
  return {
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {
      primary: card({
        stored: sel("saltmarch", "vendor/m", "balanced"),
        resolves: resolved({ preset: "balanced", preset_name: "Balanced" }), inherits: null }),
      fast: card(),
      decision: card(),
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false,
                   problem: "No provider chosen", rate: null },
    },
    routes: ROUTES,
    providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null },
      { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom",
        usable: true, problem: null },
    ],
    presets: [{ id: "balanced", name: "Balanced" }, { id: "tight", name: "Tight" }],
    preset_clear: PRESET_CLEAR,
    retirement_notes: [],
    ...over,
  };
}
const health = (state: string) => ({ state, kind: "", detail: "", at: "" });

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  CAPS = BASE_CAPS();
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.putInferenceSettings as any).mockResolvedValue(settings());
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed, model?: string) => Promise.resolve(answer(need, model)));
  (api.previewControls as any).mockResolvedValue({ requested: {}, effective: {}, controls: {} });
  (api.listConnections as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Router", health: health("ok") },
    { id: "realm", name: "Realm Local", health: health("error") },
  ]);
  (api.getPricing as any).mockResolvedValue({ rates: { "vendor/m": BOTH } });
  (api.setPricing as any).mockImplementation(async (rates: unknown) => ({ rates }));
});

function Where() {
  const { pathname, search, hash } = useLocation();
  return <div data-testid="where">{pathname + search + hash}</div>;
}

function open(at = "/models") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/models" element={<ModelsView />} />
        <Route path="/models/edit" element={<ModelsView edit />} />
        <Route path="/providers/*" element={<div>the providers page</div>} />
        <Route path="/presets/*" element={<div>the presets page</div>} />
        <Route path="/config" element={<div>settings</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const main = () => within(screen.getByRole("main"));
const row = (name: string) => within(main().getByRole("group", { name }));
async function openSummary(at = "/models") {
  open(at);
  await main().findByRole("group", { name: "Primary" });
}
const putBody = (n = 0) => (api.putInferenceSettings as any).mock.calls[n];

afterEach(cleanup);

// ---- the summary ----
test("each role reads provider · model · preset, and nothing on it is an input", async () => {
  await openSummary();
  expect(row("Primary").getByRole("link", { name: "Saltmarch Router" }))
    .toHaveAttribute("href", "/providers/saltmarch");
  expect(row("Primary").getByText(/vendor\/m/)).toBeInTheDocument();
  expect(row("Primary").getByRole("link", { name: "Balanced" }))
    .toHaveAttribute("href", "/presets/balanced");
  expect(main().queryByRole("combobox")).toBeNull();
  expect(main().queryByRole("textbox")).toBeNull();
});

test("the provider's dot is its health from the connections list", async () => {
  await openSummary();
  await waitFor(() => expect(row("Primary").getByText("working")).toBeInTheDocument());
});

test("an unset Decision reads as the role it inherits", async () => {
  await openSummary();
  expect(row("Decision").getByText(/^Same as Fast — /)).toBeInTheDocument();
});

test("Decision lists every task it answers, inherited ones included, folded", async () => {
  await openSummary();
  const fold = row("Decision").getByText(/^Tasks answered by Decision/).closest("details")!;
  expect(fold).not.toHaveAttribute("open");
  // Opened as a reader would; jsdom toggles <details> on a summary click.
  // If this jsdom does not, set `fold.open = true` instead.
  fireEvent.click(row("Decision").getByText(/^Tasks answered by Decision/));
  await waitFor(() => expect(fold).toHaveAttribute("open"));
  const list = row("Decision").getByRole("list", { name: "Tasks answered by Decision" });
  const link = within(list).getByRole("link", { name: "Scene break" });
  expect(link).toHaveAttribute("href", "/models/edit#task-scene%20break");
});

test("Embedding off says so, with why, and draws no rate", async () => {
  await openSummary();
  expect(row("Embedding").getByText("Off — nothing is embedded, and semantic recall is not used."))
    .toBeInTheDocument();
  expect(row("Embedding").getByText("No provider chosen")).toBeInTheDocument();
  expect(row("Embedding").queryByText(/priced/)).toBeNull();
});

test("each row says what would price it, and an unpriced one offers Set rate", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ rate: { source: "table", entry: BOTH } }),
    decision: card({ rate: { source: "none" }, resolves: resolved({ model: "vendor/odd#1" }) }),
  } }));
  await openSummary();
  expect(row("Primary").getByText(/would be priced at .* \(provider\)/)).toBeInTheDocument();
  expect(row("Fast").getByText(/would be priced at .* \(your rates\)/)).toBeInTheDocument();
  expect(row("Decision").getByText(/no rate: calls unpriced/)).toBeInTheDocument();
  expect(row("Decision").getByRole("link", { name: "Set rate" }))
    .toHaveAttribute("href", "/models?add=vendor%2Fodd%231#rates");
});

test("a native Decision says why it has no figure", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    decision: card({ decision_mode: "native", rate: { source: "native" },
                     stored: sel("saltmarch", "vendor/decider") }),
  } }));
  await openSummary();
  expect(row("Decision").getByText(/native decisions: priced only if the provider reports a cost/))
    .toBeInTheDocument();
  expect(row("Decision").queryByRole("link", { name: "Set rate" })).toBeNull();
});

test("a role naming a deleted provider keeps it on screen", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ stored: sel("gone", "vendor/m"), resolves: null, rate: null,
                 problem: "The provider gone no longer exists." }),
  } }));
  await openSummary();
  expect(row("Fast").getByText(/gone \(missing provider\)/)).toBeInTheDocument();
  expect(row("Fast").getByText("The provider gone no longer exists.")).toBeInTheDocument();
});

test("what a role's preset sends is there, folded", async () => {
  await openSummary();
  const fold = row("Primary").getByText("What this sends").closest("details")!;
  expect(fold).not.toHaveAttribute("open");
});

test("task overrides are counted only when there are some", async () => {
  await openSummary();
  expect(main().queryByText(/task override/)).toBeNull();
  cleanup();
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], { ...ROUTES[1], preset: "tight" }, ROUTES[2], ROUTES[3]] }));
  await openSummary();
  expect(main().getByRole("link", { name: "1 task override active" }))
    .toHaveAttribute("href", "/models/edit#advanced");
});

test("the actions: Edit models, add a provider, provider status", async () => {
  await openSummary();
  expect(main().getByRole("link", { name: "+ Add provider" })).toHaveAttribute("href", "/providers/new");
  expect(main().getByRole("link", { name: "Provider status →" })).toHaveAttribute("href", "/providers");
  fireEvent.click(main().getByRole("button", { name: "Edit models" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit");
});

test("Edit models waits while a newer build owns the settings", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  await openSummary();
  expect(main().getByRole("button", { name: "Edit models" })).toBeDisabled();
});

test("the column is the Inference group, with Models current", async () => {
  await openSummary();
  const nav = within(screen.getByRole("complementary", { name: "Models" }));
  expect(nav.getByRole("link", { name: "Models" })).toHaveAttribute("aria-current", "page");
  expect(nav.getByRole("link", { name: "Providers" })).toHaveAttribute("href", "/providers");
  expect(nav.getByRole("link", { name: "Presets" })).toHaveAttribute("href", "/presets");
  expect(nav.getByRole("link", { name: "← All settings" })).toHaveAttribute("href", "/config");
});

test("Set rate lands in the rate editor with the model filled in, slashes and all", async () => {
  await openSummary("/models?add=vendor%2Fodd%231#rates");
  expect(await main().findByDisplayValue("vendor/odd#1")).toBeInTheDocument();
});

test("what was not carried over sits under the heading until dismissed", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ retirement_notes: [{
    id: "n1", scope: "global", scope_name: "", subject: "primary", provider_id: "saltmarch",
    effort: "", kind: "unrepresentable", text: "A thing — this was not carried over." }] }));
  (api.dismissRetiredNote as any).mockResolvedValue({});
  await openSummary();
  expect(main().getByText(/this was not carried over/)).toBeInTheDocument();
});
```

The retirement-note test may already exist in the old file in fuller form.
If it does, keep the old version's body, since it also exercises the dismiss
button, and only adjust its `open()` call.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/models/taskHash.test.ts src/routes/ModelsView.test.tsx`
Expected: FAIL. `taskHash` does not exist yet, and the summary tests fail
because the old page has cards, not groups.

- [ ] **Step 3: Write `taskHash.ts`, `notes.tsx` and `InferenceNav.tsx`**

`taskHash.ts`:

```ts
/** The edit form's address for one task's override row (spec 3.3): the key
 *  encoded whole, as every route link has always encoded it. */
export const taskHash = (key: string) => `#task-${encodeURIComponent(key)}`;

/** The Advanced section itself, for "N task overrides active". */
export const ADVANCED_HASH = "#advanced";

/** The task a hash names, or null -- for a hash that names none, and for one
 *  whose escape is malformed (`#task-%ZZ`), which `decodeURIComponent` would
 *  throw on: a bad link opens the page, never crashes it. */
export function taskFromHash(hash: string): string | null {
  if (!hash.startsWith("#task-")) return null;
  const raw = hash.slice("#task-".length);
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}
```

`notes.tsx`: cut `ModelsView.tsx` lines 37-126 into it unchanged, with
`export` on `WARNINGS`, `DECIDE_WORDS`, `useWarning`, `Warning`, `Problem`
and `DecideNote`. Its imports are `useEffect`/`useState` from react;
`api`, `CapabilityNeed`, `DecidesNatively`, `DecisionMode` and
`ModelCapabilities` from `"../../api/client"`; and `onConfigChanged` from
`"../../appEvents"`.

`InferenceNav.tsx`:

```tsx
import { Link } from "react-router-dom";
import { ColumnSection } from "../PageShell";

const PAGES = [
  { key: "providers", label: "Providers", to: "/providers" },
  { key: "models", label: "Models", to: "/models" },
  { key: "presets", label: "Presets", to: "/presets" },
] as const;

/** Settings → Inference's three pages, at the top of each one's column
 *  (spec 1): the column indexing the page's neighbourhood. The rail still
 *  answers which page of the app this is (it lights Settings on all three). */
export function InferenceNav({ current }: { current: (typeof PAGES)[number]["key"] }) {
  return (
    <ColumnSection label="Inference">
      {PAGES.map((p) => (
        <Link key={p.key} to={p.to} aria-current={p.key === current ? "page" : undefined}
              className={"column-row" + (p.key === current ? " active" : "")}>
          <span className="column-row-label">{p.label}</span>
        </Link>
      ))}
      <Link to="/config" className="column-row">
        <span className="column-row-label">← All settings</span>
      </Link>
    </ColumnSection>
  );
}
```

- [ ] **Step 4: Write `RoleRow.tsx`**

```tsx
import { Link } from "react-router-dom";
import type {
  EmbeddingCard, GenerativeRole, InferenceSettings, ProviderHealth, RouteRow,
} from "../../api/client";
import { providerPath } from "../../providerPaths";
import { ControlsReadout } from "../inference/ControlsReadout";
import { describe, droppedFallbackWords, ROLE_LABEL, ROLE_NEEDS } from "../inference/selection";
import { HealthDot } from "./health";
import { DecideNote, Problem, useWarning, Warning } from "./notes";
import { rateWords, setRateHref } from "./rates";
import { taskHash } from "./taskHash";

/** How an unset role reads (spec 4.4): the role it inherits. */
export const SAME_AS: Partial<Record<GenerativeRole, string>> = {
  fast: "Same as Primary", decision: "Same as Fast",
};

/** provider · model · preset, as one summary line: the provider is a record
 *  of its own (a link, with its health), and so is the preset. A provider the
 *  list no longer holds is named as missing, never dropped. */
export function SelectionLine({ provider, providerName, model, preset = "", presetName = "",
                                health, withPreset = true }:
  { provider: string; providerName: string | null; model: string; preset?: string;
    presetName?: string; health: ReadonlyMap<string, ProviderHealth>; withPreset?: boolean }) {
  return (
    <>
      {providerName === null
        ? <span>{provider} (missing provider)</span>
        : <Link to={providerPath(provider)}>{providerName}</Link>}
      <HealthDot health={health.get(provider)} />
      {" · "}{model || "its default model"}
      {withPreset && (
        <>
          {" · "}
          {preset
            ? <Link to={`/presets/${encodeURIComponent(preset)}`}>{presetName || preset}</Link>
            : "no preset"}
        </>
      )}
    </>
  );
}

const providerName = (settings: InferenceSettings, id: string) =>
  settings.providers.find((p) => p.id === id)?.name ?? null;
const presetName = (settings: InferenceSettings, id: string) =>
  settings.presets.find((p) => p.id === id)?.name ?? id;

/** The rate line, and Set rate where nothing prices the model (spec 3.5). */
function RateLine({ rate, model, promptOnly = false }:
  { rate: import("../../api/client").RateInfo | null; model: string; promptOnly?: boolean }) {
  const words = rateWords(rate, { promptOnly });
  if (!words) return null;
  return (
    <p className="field-hint">
      {words}
      {rate?.source === "none" && model && (
        <> · <Link to={setRateHref(model)}>Set rate</Link></>
      )}
    </p>
  );
}

/** Every task Decision answers -- its own `uses`, inherited or not -- each a
 *  link to that task's row on the edit form. */
function DecisionRoutes({ routes }: { routes: RouteRow[] }) {
  const using = routes.filter((r) => r.uses === "decision");
  if (using.length === 0) return <p className="field-hint">No task uses Decision yet.</p>;
  return (
    <details className="decision-tasks">
      <summary>Tasks answered by Decision ({using.length})</summary>
      <ul aria-label="Tasks answered by Decision">
        {using.map((r) => (
          <li key={r.key}>
            <Link to={`/models/edit${taskHash(r.key)}`}>{r.label}</Link>
            {r.role && r.role !== "decision" && (
              <span className="field-hint"> — inherits {ROLE_LABEL[r.role]}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** One generative role on the summary (spec 3.1): what it runs on, its
 *  fallback, its rate, every warning the old card carried, what its preset
 *  sends (folded), and on Decision the tasks it answers. */
export function RoleRow({ role, settings, health }:
  { role: GenerativeRole; settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth> }) {
  const card = settings.roles[role];
  const sel = card.resolves;
  const decision = role === "decision";
  const label = ROLE_LABEL[role];
  const warning = useWarning(sel?.provider ?? "", sel?.model ?? "",
                             decision ? null : ROLE_NEEDS[role][0], label);
  const unset = !card.stored.provider && !card.stored.model;
  const fb = card.fallback;
  const fbName = providerName(settings, fb.provider);
  const dropped = droppedFallbackWords(card.fallback_missing, label,
                                       fb.provider && fb.model ? `${fbName ?? fb.provider} ▸ ${fb.model}` : "",
                                       card.fallback_problem);
  return (
    <div className="role-row" role="group" aria-label={label}>
      <div className="role-row-name">{label}</div>
      <div className="role-row-body">
        {unset && SAME_AS[role] ? (
          <p>{SAME_AS[role]} — {describe(card.inherits)}</p>
        ) : sel ? (
          <p><SelectionLine provider={sel.provider} providerName={sel.provider_name || sel.provider}
                            model={sel.model} preset={sel.preset} presetName={sel.preset_name}
                            health={health} /></p>
        ) : card.stored.provider ? (
          <p><SelectionLine provider={card.stored.provider}
                            providerName={providerName(settings, card.stored.provider)}
                            model={card.stored.model} preset={card.stored.preset}
                            presetName={presetName(settings, card.stored.preset)} health={health} /></p>
        ) : (
          <p>Not set.</p>
        )}
        {fb.provider && (
          <p className="role-row-fallback">
            Fallback:{" "}
            <SelectionLine provider={fb.provider} providerName={fbName} model={fb.model}
                           preset={fb.preset} presetName={presetName(settings, fb.preset)}
                           health={health} />
          </p>
        )}
        <RateLine rate={card.rate} model={sel?.model ?? ""} />
        <Problem text={card.problem} />
        <Warning text={warning} />
        {decision && <DecideNote mode={card.decision_mode} decidesNatively={card.decides_natively} />}
        <Problem text={dropped} />
        {sel && (
          <details className="what-it-sends">
            <summary>What this sends</summary>
            <ControlsReadout presetId={sel.preset} provider={sel.provider} model={sel.model}
                             operation={decision ? "decide" : undefined} />
          </details>
        )}
        {decision && <DecisionRoutes routes={settings.routes} />}
      </div>
    </div>
  );
}

/** The Embedding role (spec 3.1): what embeds, or today's off sentence with
 *  why; its rate is the input figure alone, and none when it is off. */
export function EmbeddingRow({ card, settings, health }:
  { card: EmbeddingCard; settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth> }) {
  const { provider, model } = card.stored;
  const warning = useWarning(provider, model, "embed", ROLE_LABEL.embedding);
  const on = card.on && card.resolves;
  return (
    <div className="role-row" role="group" aria-label="Embedding">
      <div className="role-row-name">Embedding</div>
      <div className="role-row-body">
        {on && card.resolves ? (
          <>
            <p><SelectionLine provider={card.resolves.provider}
                              providerName={providerName(settings, card.resolves.provider)
                                ?? card.resolves.provider_name}
                              model={card.resolves.model} health={health} withPreset={false} /></p>
            <RateLine rate={card.rate} model={card.resolves.model} promptOnly />
          </>
        ) : (
          <>
            <p>Off — nothing is embedded, and semantic recall is not used.</p>
            <Problem text={card.problem ?? null} />
          </>
        )}
        <Warning text={warning} />
      </div>
    </div>
  );
}
```

Replace the inline `import("../../api/client").RateInfo` with a top-level
`type RateInfo` import. It is written inline here only to keep the snippet
readable.

- [ ] **Step 5: Rewrite `ModelsView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type GenerativeRole, type InferenceSettings } from "../api/client";
import { ErrorNote } from "../components/ErrorNote";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { InferenceNav } from "../components/inference/InferenceNav";
import { migrationBanner } from "../components/inference/migration";
import { RetiredNotes } from "../components/inference/RetiredNotes";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import { useProviderHealth } from "../components/models/health";
import { addedModel } from "../components/models/rates";
import { EmbeddingRow, RoleRow } from "../components/models/RoleRow";
import { ADVANCED_HASH } from "../components/models/taskHash";
import { TokenRates } from "../components/models/TokenRates";
import { PageShell } from "../components/PageShell";

const GENERATIVE: GenerativeRole[] = ["primary", "fast", "decision"];

/** Routes this scope overrides: its own `use` or its own `preset`. */
export function overrideCount(settings: InferenceSettings): number {
  return settings.routes.filter((r) => r.use !== "" || r.preset !== "").length;
}

/** `/models` (spec 3): which provider, model and preset each role runs on,
 *  read-only, and what would price it; `/models/edit` (`edit`) is the one
 *  form for every role and, under Advanced, every task.
 *
 *  Settings → Inference's middle page. Library-wide only: a campaign's
 *  overrides are its Inspector's. Every "resolves", problem and rate is the
 *  server's; health is the connections list's. */
export default function ModelsView({ edit = false }: { edit?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { settings, error, install } = useInferenceSettings();
  const health = useProviderHealth();
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  // Gated on the layout, not on the migration finishing (unchanged rule).
  const blocked = !!settings && (settings.newer || settings.format !== "2");
  const banner = migrationBanner(settings);
  const add = addedModel(location.search);

  // `#rates` scrolls the rate table in once the page is drawn.
  useEffect(() => {
    if (location.hash !== "#rates" || !settings) return;
    document.getElementById("rates")?.scrollIntoView?.({ block: "start" });
  }, [location.hash, settings]);

  function ratesSaved() {
    api.getInferenceSettings().then(install).catch(() => {});
    if (add) navigate({ pathname: "/models", hash: "#rates" }, { replace: true });
  }

  let body;
  if (!settings) {
    body = error != null ? null : <p className="field-hint">Reading the model settings…</p>;
  } else {
    const n = overrideCount(settings);
    body = (
      <>
        <section className="models-roles" aria-label="Roles">
          {GENERATIVE.map((r) => <RoleRow key={r} role={r} settings={settings} health={health} />)}
          {settings.roles.embedding && (
            <EmbeddingRow card={settings.roles.embedding} settings={settings} health={health} />
          )}
        </section>
        {n > 0 && (
          <p><Link to={`/models/edit${ADVANCED_HASH}`}>
            {n} task override{n === 1 ? "" : "s"} active
          </Link></p>
        )}
        <div className="form-actions models-actions">
          <button className="primary" disabled={blocked} onClick={() => navigate("/models/edit")}>
            Edit models
          </button>
          <Link className="button subtle" to="/providers/new" state={{ returnTo: "/models" }}>
            + Add provider
          </Link>
          <Link to="/providers">Provider status →</Link>
        </div>
        <TokenRates addModel={add || undefined} onSaved={ratesSaved} />
      </>
    );
  }

  return (
    <PageShell column={<InferenceNav current="models" />} columnLabel="Models">
      <div className="page view-anim">
        <div className="page-head"><h1 className="page-h1">Models</h1></div>
        <InferenceBanner status={banner} />
        {settings && (
          <RetiredNotes
            notes={(settings.retirement_notes ?? []).filter((n) => !dismissed.has(n.id))}
            onDismissed={(id) => setDismissed((prev) => new Set(prev).add(id))} />
        )}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        {body}
      </div>
    </PageShell>
  );
}
```

`edit` is unused in this task, and eslint may flag it. If so, reference it
with `void edit;` and a comment, `// the form arrives in Task 6`. Task 6
removes that line.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/components/models src/routes/ModelsView.test.tsx`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/models frontend/src/components/inference/InferenceNav.tsx frontend/src/routes/ModelsView.tsx frontend/src/routes/ModelsView.test.tsx
git commit -m "Models: one read-only summary of every role, with health, rates and the token-rate table"
```

---

### Task 6: The edit form (`/models/edit`) — every role, one Save

**Files:**
- Create: `frontend/src/components/models/selections.ts` (a leaf holding
  `GENERATIVE`, `EMPTY_SEL` and `sameSel`, which Task 7 shares)
- Create: `frontend/src/components/models/ModelsEditForm.tsx`
- Modify: `frontend/src/routes/ModelsView.tsx`, which renders the form when
  `edit`
- Test: `frontend/src/routes/ModelsView.test.tsx` (append)

**Interfaces:**
- Consumes:
  - `ModelSelect` (Task 3);
  - `useWarning`, `Warning`, `Problem` and `DecideNote` (Task 5);
  - `PresetSelect`, `ControlsReadout`, `ROLE_LABEL`, `ROLE_NEEDS`,
    `droppedFallbackWords`, `wantsModel` and `CHOOSE_A_MODEL` (existing);
  - `SAME_AS` (Task 5, `RoleRow.tsx`).
- Produces:
  - `ModelsEditForm`, with props
    `{ settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean; onSaved: (next: InferenceSettings) => void; onCancel: () => void }`;
  - from `selections.ts`: `GENERATIVE`, `EMPTY_SEL` and
    `sameSel(a: InferenceSelection, b: InferenceSelection): boolean`;
  - `roleBody(drafts, settings): InferenceWrite["roles"]`, exported for
    Task 7 to extend.
  - Accessible names Task 7 and the tests rely on:
    - each role is a `fieldset` named after the role;
    - the selects are named "<Role> provider", "<Role> model" and
      "<Role> preset";
    - each fallback is named "<Role> fallback", with selects
      "<Role> fallback provider", "<Role> fallback model" and
      "<Role> fallback preset", and its remove button is
      "Remove <Role> fallback";
    - the buttons are "Save" and "Cancel".

- [ ] **Step 1: Write the failing tests** (append to `ModelsView.test.tsx`)

```tsx
// ---- the edit form ----
const form = () => within(main().getByRole("form", { name: "Edit models" }));
/** The form; `hash` opens part of it (`#advanced` unfolds the per-task rows,
 *  which a test must do before it can reach them -- folded is the default). */
async function openForm(hash = "") {
  open(`/models/edit${hash}`);
  await main().findByRole("form", { name: "Edit models" });
}
const pick = (name: string, value: string) =>
  fireEvent.change(form().getByRole("combobox", { name }), { target: { value } });

test("every role has provider, model and preset on one form", async () => {
  await openForm();
  for (const role of ["Primary", "Fast", "Decision"]) {
    expect(form().getByRole("combobox", { name: `${role} provider` })).toBeInTheDocument();
  }
  expect(form().getByRole("combobox", { name: "Primary model" })).toHaveValue("vendor/m");
  expect(form().getByRole("combobox", { name: "Primary preset" })).toHaveValue("balanced");
  expect(form().getByRole("combobox", { name: "Embedding provider" })).toBeInTheDocument();
  expect(form().queryByRole("combobox", { name: "Embedding preset" })).toBeNull();
});

test("saving nothing sends nothing and goes back to the summary", async () => {
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/));
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

test("one changed role is all a save sends", async () => {
  await openForm();
  pick("Fast provider", "realm");
  await form().findByRole("option", { name: "Vendor M" });
  pick("Fast model", "vendor/m");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { fast: { selection: sel("realm", "vendor/m", "") } } }]);
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/));
});

test("Same as Fast clears provider, model and preset alike", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, decision: card({ stored: sel("saltmarch", "vendor/m", "tight") }) } }));
  await openForm();
  pick("Decision provider", "");
  expect(form().queryByRole("combobox", { name: "Decision preset" })).toBeNull();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalled());
  expect(putBody()[0]).toEqual({ roles: { decision: { selection: sel("", "", "") } } });
});

test("a provider with no model holds Save and says why", async () => {
  await openForm();
  pick("Fast provider", "realm");
  expect(form().getAllByText("Choose a model for this provider to save.").length).toBeGreaterThan(0);
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});

test("a fallback is added, sent, and removed as an empty one", async () => {
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "+ Fallback for Fast" }));
  pick("Fast fallback provider", "realm");
  await form().findAllByRole("option", { name: "Vendor M" });
  pick("Fast fallback model", "vendor/m");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ roles: { fast: { fallback: sel("realm", "vendor/m", "") } } });
});

test("removing a stored fallback writes an empty one", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, fast: card({ fallback: sel("realm", "vendor/m", "") }) } }));
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "Remove Fast fallback" }));
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ roles: { fast: { fallback: sel("", "", "") } } });
});

test("an Embedding that would re-embed asks first, then sends the yes", async () => {
  await openForm();
  pick("Embedding provider", "saltmarch");
  await form().findAllByRole("option", { name: "Vendor Embed" });
  pick("Embedding model", "vendor/embed");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  const ask = await form().findByRole("group", { name: "Confirm the re-embedding" });
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { embedding: { selection: { provider: "saltmarch", model: "vendor/embed" } } } },
                             { confirmEmbedding: true }]);
});

test("turning Embedding off asks nothing up front", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                 resolves: resolved({ model: "vendor/embed" }), problem: null, rate: null } } }));
  await openForm();
  pick("Embedding provider", "");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { embedding: { selection: { provider: "", model: "" } } } }]);
});

test("the server's re-embedding question is asked in its words, and the resend carries the yes", async () => {
  (api.putInferenceSettings as any).mockRejectedValueOnce({
    detail: "Changing the embedding model re-embeds your library through Saltmarch Router, "
            + "which may cost money — confirm to change it.",
    kind: "confirm_embedding" });
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                 resolves: resolved({ model: "vendor/embed" }), problem: null, rate: null } } }));
  await openForm();
  pick("Embedding provider", "");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  const ask = await form().findByRole("group", { name: "Confirm the re-embedding" });
  expect(ask).toHaveTextContent(/re-embeds your library through Saltmarch Router/);
  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(2));
  expect(putBody(1)[1]).toEqual({ confirmEmbedding: true });
});

test("any other refusal is shown above the form in the server's words, the form kept", async () => {
  (api.putInferenceSettings as any).mockRejectedValueOnce({ detail: "no such preset: tight", kind: "" });
  await openForm();
  pick("Primary preset", "tight");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  expect(await main().findByText(/no such preset: tight/)).toBeInTheDocument();
  expect(form().getByRole("combobox", { name: "Primary preset" })).toHaveValue("tight");
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit");
});

test("Cancel discards the draft and lands on the summary, even from a direct load", async () => {
  await openForm();
  pick("Primary preset", "tight");
  fireEvent.click(form().getByRole("button", { name: "Cancel" }));
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/);
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

test("an edit row carries the role's problem, and a fallback row why it is dropped", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/embed"), problem: "vendor/embed cannot generate text." }),
    fast: card({ fallback: sel("realm", "vendor/embed"), fallback_missing: ["generate"] }),
    decision: card({ fallback: sel("realm", "vendor/m"), fallback_problem: "Realm Local has no key set" }),
  } }));
  await openForm();
  expect(within(form().getByRole("group", { name: "Primary" }))
    .getByText("vendor/embed cannot generate text.")).toBeInTheDocument();
  expect(within(form().getByRole("group", { name: "Fast fallback" }))
    .getByText(/is known not to fit Fast \(it cannot generate text\)/)).toBeInTheDocument();
  expect(within(form().getByRole("group", { name: "Decision fallback" }))
    .getByText(/cannot be sent \(Realm Local has no key set\)/)).toBeInTheDocument();
});

test("an Embedding model known not to embed is warned of on the form", async () => {
  await openForm();
  pick("Embedding provider", "saltmarch");
  fireEvent.change(form().getByRole("combobox", { name: "Embedding model" }),
                   { target: { value: "\u0000other" } });
  fireEvent.change(form().getByRole("textbox", { name: "Embedding model id" }),
                   { target: { value: "vendor/m" } });
  fireEvent.click(form().getByRole("button", { name: "Use this id" }));
  expect(await within(form().getByRole("group", { name: "Embedding" }))
    .findByText("This model can't create embeddings.")).toBeInTheDocument();
});

test("a newer build's store holds the whole form", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  await openForm();
  expect(form().getByRole("combobox", { name: "Primary provider" })).toBeDisabled();
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ModelsView.test.tsx`
Expected: the new tests FAIL. There is no form named "Edit models" yet.

- [ ] **Step 3: Write `selections.ts`, then `ModelsEditForm.tsx`**

`selections.ts` is a leaf with no component imports, so both the form and
Task 7's overrides can use it without a module cycle:

```ts
import type { GenerativeRole, InferenceSelection } from "../../api/client";

export const GENERATIVE: GenerativeRole[] = ["primary", "fast", "decision"];
export const EMPTY_SEL: InferenceSelection = { provider: "", model: "", preset: "" };
export const sameSel = (a: InferenceSelection, b: InferenceSelection) =>
  a.provider === b.provider && a.model === b.model && a.preset === b.preset;
```

`ModelsEditForm.tsx`:

```tsx
import { type ReactNode, useState } from "react";
import {
  api, type GenerativeRole, type InferenceSelection, type InferenceSettings,
  type InferenceWrite, type ProviderHealth,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { ErrorNote } from "../ErrorNote";
import { ControlsReadout } from "../inference/ControlsReadout";
import { PresetSelect } from "../inference/PresetSelect";
import {
  CHOOSE_A_MODEL, droppedFallbackWords, ROLE_LABEL, ROLE_NEEDS, wantsModel,
} from "../inference/selection";
import { ModelSelect } from "./ModelSelect";
import { Problem, useWarning, Warning } from "./notes";
import { SAME_AS } from "./RoleRow";
import { EMPTY_SEL, GENERATIVE, sameSel } from "./selections";

export type RoleDraft = { sel: InferenceSelection; fallback: InferenceSelection; fallbackOpen: boolean };
export type Drafts = Record<GenerativeRole, RoleDraft>;

function startDrafts(settings: InferenceSettings): Drafts {
  const out = {} as Drafts;
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    out[role] = { sel: card.stored, fallback: card.fallback, fallbackOpen: !!card.fallback.provider };
  }
  return out;
}

/** The roles' part of the write (spec 3.4): a role's `selection` only when it
 *  moved, its `fallback` only when that moved -- a save must not put back what
 *  another tab changed in a part this form never touched. */
export function roleBody(drafts: Drafts, settings: InferenceSettings): InferenceWrite["roles"] {
  const roles: NonNullable<InferenceWrite["roles"]> = {};
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    const d = drafts[role];
    const entry: { selection?: InferenceSelection; fallback?: InferenceSelection } = {};
    if (!sameSel(d.sel, card.stored)) entry.selection = d.sel;
    if (!sameSel(d.fallback, card.fallback)) entry.fallback = d.fallback;
    if (entry.selection || entry.fallback) roles[role] = entry;
  }
  return roles;
}

/** One selection's controls: ModelSelect, then (generative) the preset, the
 *  model warning, what it sends. "Same as" / "Not set" (provider "") clears
 *  the WHOLE selection, preset included (spec 3.2), and hides model and preset. */
function SelectionControls({ label, role, value, onChange, settings, health, blocked,
                             emptyLabel, presetLabel, children }:
  { label: string; role: GenerativeRole; value: InferenceSelection;
    onChange: (next: InferenceSelection) => void; settings: InferenceSettings;
    health: ReadonlyMap<string, ProviderHealth>; blocked: boolean; emptyLabel: string;
    presetLabel: string; children?: ReactNode }) {
  const decision = role === "decision";
  const warning = useWarning(value.provider, value.model,
                             decision ? null : ROLE_NEEDS[role][0], ROLE_LABEL[role]);
  const dormant = !value.provider && !!value.preset;
  return (
    <>
      <ModelSelect label={label} needs={ROLE_NEEDS[role]} providers={settings.providers}
                   health={health} emptyLabel={emptyLabel} disabled={blocked}
                   value={{ provider: value.provider, model: value.model }}
                   onChange={(v) => onChange(v.provider ? { ...value, ...v } : EMPTY_SEL)} />
      <Warning text={warning} />
      {wantsModel(value) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
      {value.provider && (
        <label className="field">
          <span>{presetLabel}</span>
          <PresetSelect label={`${label} preset`} value={value.preset} presets={settings.presets}
                        emptyLabel="Provider defaults" disabled={blocked}
                        onChange={(preset) => onChange({ ...value, preset })} />
        </label>
      )}
      {dormant && (
        <p className="field-hint">
          A preset with no provider is not used.{" "}
          <button type="button" className="subtle" disabled={blocked}
                  onClick={() => onChange({ ...value, preset: "" })}>Clear it</button>
        </p>
      )}
      {children}
      {value.provider && value.model && (
        <details className="what-it-sends">
          <summary>What this sends</summary>
          <ControlsReadout presetId={value.preset} provider={value.provider} model={value.model}
                           operation={decision ? "decide" : undefined} />
        </details>
      )}
    </>
  );
}

/** `/models/edit` (spec 3.2-3.4): every role on one form, one Save. */
export function ModelsEditForm({ settings, health, blocked, onSaved, onCancel }:
  { settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean;
    onSaved: (next: InferenceSettings) => void; onCancel: () => void }) {
  const [drafts, setDrafts] = useState<Drafts>(() => startDrafts(settings));
  const storedEmbedding = settings.roles.embedding?.stored ?? { provider: "", model: "" };
  const [embedding, setEmbedding] = useState(storedEmbedding);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);

  const setRole = (role: GenerativeRole, next: Partial<RoleDraft>) => {
    setDrafts((d) => ({ ...d, [role]: { ...d[role], ...next } }));
    setAsking(null);
  };
  const embeddingMoved = embedding.provider !== storedEmbedding.provider
    || embedding.model !== storedEmbedding.model;
  // Today's Embedding form warns of a model known not to embed (`warn=embed`).
  const embeddingWarning = useWarning(embedding.provider, embedding.model, "embed",
                                      ROLE_LABEL.embedding);

  function body(): InferenceWrite {
    const roles = roleBody(drafts, settings) ?? {};
    const out: InferenceWrite = {};
    const withEmbedding = embeddingMoved
      ? { ...roles, embedding: { selection: { provider: embedding.provider, model: embedding.model } } }
      : roles;
    if (Object.keys(withEmbedding).length) out.roles = withEmbedding;
    return out;
  }

  const incomplete = GENERATIVE.some((r) => wantsModel(drafts[r].sel)
      || (drafts[r].fallbackOpen && wantsModel(drafts[r].fallback)));

  async function send(confirm: boolean) {
    setSaving(true);
    setError(null);
    try {
      const next = confirm
        ? await api.putInferenceSettings(body(), { confirmEmbedding: true })
        : await api.putInferenceSettings(body());
      onSaved(next);
    } catch (err: unknown) {
      const kind = typeof err === "object" && err !== null
        ? (err as { kind?: unknown }).kind : undefined;
      // The server's question, in its words, is authoritative (spec 3.4).
      if (kind === "confirm_embedding" && !confirm) setAsking(errorText(err));
      else setError(err);
      setSaving(false);
    }
  }

  function save() {
    // Nothing moved: nothing is sent (an empty PUT is a write nobody asked for).
    if (Object.keys(body()).length === 0) { onCancel(); return; }
    // Asked up front only for a complete, changed selection -- today's rule;
    // turning embedding off asks nothing, and the server covers the rest.
    if (embeddingMoved && embedding.provider && embedding.model) {
      const name = settings.providers.find((p) => p.id === embedding.provider)?.name
        ?? embedding.provider;
      setAsking(`Changing the embedding model re-embeds your library through ${name}, `
                + "which may cost money.");
      return;
    }
    void send(false);
  }

  return (
    <form className="models-edit" aria-label="Edit models" onSubmit={(e) => e.preventDefault()}>
      <h2>Edit models</h2>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      {GENERATIVE.map((role) => {
        const label = ROLE_LABEL[role];
        const card = settings.roles[role];
        const d = drafts[role];
        const fbName = settings.providers.find((p) => p.id === card.fallback.provider)?.name
          ?? card.fallback.provider;
        const dropped = droppedFallbackWords(card.fallback_missing, label,
          card.fallback.provider && card.fallback.model ? `${fbName} ▸ ${card.fallback.model}` : "",
          card.fallback_problem);
        return (
          <fieldset key={role} className="models-edit-role" aria-label={label}>
            <legend>{label}</legend>
            <SelectionControls label={label} role={role} value={d.sel} settings={settings}
                               health={health} blocked={blocked} presetLabel="Preset"
                               emptyLabel={SAME_AS[role] ?? "Not set"}
                               onChange={(sel) => setRole(role, { sel })}>
              <Problem text={card.problem} />
            </SelectionControls>
            {d.fallbackOpen ? (
              <fieldset className="models-edit-fallback" aria-label={`${label} fallback`}>
                <legend>Fallback, tried once when {label} cannot answer</legend>
                <SelectionControls label={`${label} fallback`} role={role} value={d.fallback}
                                   settings={settings} health={health} blocked={blocked}
                                   presetLabel="Fallback preset" emptyLabel="No fallback"
                                   onChange={(fallback) => setRole(role, { fallback })}>
                  <Problem text={dropped} />
                </SelectionControls>
                <button type="button" className="subtle" aria-label={`Remove ${label} fallback`}
                        disabled={blocked}
                        onClick={() => setRole(role, { fallback: EMPTY_SEL, fallbackOpen: false })}>
                  ✕
                </button>
              </fieldset>
            ) : (
              <button type="button" className="subtle" aria-label={`+ Fallback for ${label}`}
                      disabled={blocked} onClick={() => setRole(role, { fallbackOpen: true })}>
                + Fallback
              </button>
            )}
          </fieldset>
        );
      })}
      {settings.roles.embedding && (
        <fieldset className="models-edit-role" aria-label="Embedding">
          <legend>Embedding</legend>
          <ModelSelect label="Embedding" needs={ROLE_NEEDS.embedding} providers={settings.providers}
                       health={health} emptyLabel="Not set — nothing is embedded"
                       disabled={blocked} value={embedding}
                       onChange={(v) => { setEmbedding(v); setAsking(null); }} />
          <Warning text={embeddingWarning} />
          {!settings.roles.embedding.on && <Problem text={settings.roles.embedding.problem ?? null} />}
        </fieldset>
      )}
      {asking !== null && (
        <div className="banner" role="group" aria-label="Confirm the re-embedding">
          {asking}{" "}
          <button type="button" className="primary" disabled={saving}
                  onClick={() => { setAsking(null); void send(true); }}>
            Re-embed and save
          </button>{" "}
          <button type="button" className="subtle" onClick={() => setAsking(null)}>Not now</button>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="subtle" onClick={onCancel}>Cancel</button>
        <button type="button" className="primary" onClick={save}
                disabled={blocked || saving || asking !== null || incomplete}>
          Save
        </button>
      </div>
    </form>
  );
}
```

A `fieldset` with `aria-label` has the role `group`, which is what the tests
query. `PresetSelect`'s `label` prop is its `aria-label`
(`PresetSelect.tsx:24`), so passing `` `${label} preset` `` gives the names
"Primary preset" and "Fast fallback preset".

- [ ] **Step 4: Render it from `ModelsView`**

In `ModelsView.tsx`:
- remove the Task 5 `void edit;` line;
- import `ModelsEditForm`;
- in the `else` branch, when `edit` is true, render this instead of the
  summary:

```tsx
      <ModelsEditForm settings={settings} health={health} blocked={blocked}
                      onSaved={(next) => { install(next); navigate("/models"); }}
                      onCancel={() => navigate("/models")} />
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/routes/ModelsView.test.tsx src/components/models`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/models/selections.ts frontend/src/components/models/ModelsEditForm.tsx frontend/src/routes/ModelsView.tsx frontend/src/routes/ModelsView.test.tsx
git commit -m "Models: one edit form for every role, with fallbacks, one Save"
```

---

### Task 7: Advanced — per-task overrides on the edit form

**Files:**
- Create: `frontend/src/components/models/TaskOverrides.tsx`
- Modify: `frontend/src/components/models/ModelsEditForm.tsx`, which gains
  the route drafts and renders the section
- Test: `frontend/src/routes/ModelsView.test.tsx` (append)

**Interfaces:**
- Consumes:
  - `ModelSelect` (Task 3);
  - `taskFromHash` and `ADVANCED_HASH` (Task 5);
  - `useWarning`, `Warning`, `Problem` and `DecideNote` (Task 5);
  - `GENERATIVE`, `EMPTY_SEL` and `sameSel` from `selections.ts` (Task 6);
  - `routePinNeeds`, `inheritedPreset`, `droppedFallbackWords`,
    `wantsModel` and `ROLE_LABEL` (existing).
- Produces:
  - `type RouteDraft = { use: RouteUse; pin: InferenceSelection; preset: string }`;
  - `startRouteDrafts(routes: RouteRow[]): Record<string, RouteDraft>`;
  - `routeBody(drafts, routes): NonNullable<InferenceWrite["routes"]>`;
  - `routesIncomplete(drafts): boolean`;
  - `TaskOverrides`, with props
    `{ settings; health; blocked; drafts: Record<string, RouteDraft>; onChange: (key: string, d: RouteDraft) => void }`.
  - Accessible names:
    - the section is `<details id="advanced">`, whose summary reads
      "Advanced: per-task overrides (N active)";
    - each task is a `group` named after its label;
    - the controls are "<label> use", "<label> pinned provider",
      "<label> pinned model", "<label> pin preset" and
      "<label> preset override".

- [ ] **Step 1: Write the failing tests** (append to `ModelsView.test.tsx`)

```tsx
// ---- Advanced: per-task overrides ----
const advanced = () => main().getByText(/^Advanced: per-task overrides/).closest("details")!;
const task = (name: string) => within(within(advanced()).getByRole("group", { name }));

test("Advanced is folded until asked for", async () => {
  await openForm();
  expect(advanced()).not.toHaveAttribute("open");
});

test("an address opens it: the section, or one task, however its key is spelt", async () => {
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).toHaveAttribute("open");
  cleanup();
  open("/models/edit#task-scene%20break");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).toHaveAttribute("open");
  expect(task("Scene break").getByRole("combobox", { name: "Scene break use" })).toBeInTheDocument();
});

test("a malformed task address opens the page with the section folded", async () => {
  open("/models/edit#task-%ZZ");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).not.toHaveAttribute("open");
});

test("choosing a role for a task marks it as overriding and sends only use", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "primary" } });
  expect(task("Rolling summary").getByText("overrides")).toBeInTheDocument();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { use: "primary" } } });
});

test("switching a pinned task to a role keeps the pin to come back to", async () => {
  const pinned = { ...ROUTES[1], use: "model", pin: sel("realm", "vendor/m", "tight") };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], pinned, ROUTES[2], ROUTES[3]] }));
  await openForm("#advanced");
  const use = task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" });
  fireEvent.change(use, { target: { value: "fast" } });
  fireEvent.change(use, { target: { value: "model" } });
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pinned provider" }))
    .toHaveValue("realm");
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pin preset" }))
    .toHaveValue("tight");
  fireEvent.change(use, { target: { value: "fast" } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { use: "fast" } } });
});

test("a pin preset and a preset override are two values, and stop-inheriting round-trips", async () => {
  const pinned = { ...ROUTES[1], use: "model", pin: sel("saltmarch", "vendor/m", "balanced"),
                   preset: PRESET_CLEAR };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], pinned, ROUTES[2], ROUTES[3]] }));
  await openForm("#advanced");
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pin preset" }))
    .toHaveValue("balanced");
  const override = task("Rolling summary").getByRole("combobox", { name: "Rolling summary preset override" });
  expect(override).toHaveValue(PRESET_CLEAR);
  fireEvent.change(override, { target: { value: "tight" } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { preset: "tight" } } });
});

test("stop inheriting is sent as the sentinel, never as an empty preset", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary preset override" }),
                   { target: { value: PRESET_CLEAR } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { preset: PRESET_CLEAR } } });
});

test("a pin with no model holds Save", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "model" } });
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pinned provider" }),
                   { target: { value: "realm" } });
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});

test("each task keeps what its detail page said: vision, a dropped fallback, the decide note", async () => {
  CAPS["vendor/eye"].vision = "unknown";
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ROUTES[0],
    { ...ROUTES[1], fallback_problem: "Realm Local has no key set" },
    { ...ROUTES[2], resolves: resolved({ model: "vendor/eye" }), inherits: resolved({ model: "vendor/eye" }) },
    { ...ROUTES[3], decision_mode: "structured", decides_natively: "no" },
  ] }));
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(await task("Image descriptions").findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();
  expect(task("Rolling summary").getByText(/cannot be sent \(Realm Local has no key set\)/))
    .toBeInTheDocument();
  expect(task("Scene break").getByText("No native decision API; structured generation will be used."))
    .toBeInTheDocument();
  expect(task("Image descriptions").getByText(/Also needs: vision/)).toBeInTheDocument();
});

test("a pinned model is warned of when it is unverified for the task's images", async () => {
  const pinned = { ...ROUTES[2], use: "model", pin: sel("saltmarch", "vendor/eye", "") };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], ROUTES[1], pinned, ROUTES[3]] }));
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(await task("Image descriptions").findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();
});

test("the section counts what this form would override", async () => {
  await openForm("#advanced");
  expect(main().getByText("Advanced: per-task overrides (0 active)")).toBeInTheDocument();
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "primary" } });
  expect(main().getByText("Advanced: per-task overrides (1 active)")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ModelsView.test.tsx`
Expected: the new tests FAIL, because there is no Advanced section yet.

- [ ] **Step 3: Write `TaskOverrides.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import type {
  CapabilityNeed, InferenceSelection, InferenceSettings, InferenceWrite, ProviderHealth,
  RouteRow, RouteUse, RouteWrite,
} from "../../api/client";
import { ControlsReadout } from "../inference/ControlsReadout";
import { PresetSelect } from "../inference/PresetSelect";
import {
  CHOOSE_A_MODEL, droppedFallbackWords, inheritedPreset, ROLE_LABEL, routePinNeeds, wantsModel,
} from "../inference/selection";
import { ModelSelect } from "./ModelSelect";
import { DecideNote, Problem, useWarning, Warning } from "./notes";
import { EMPTY_SEL, GENERATIVE, sameSel } from "./selections";
import { ADVANCED_HASH, taskFromHash } from "./taskHash";

export type RouteDraft = { use: RouteUse; pin: InferenceSelection; preset: string };

export function startRouteDrafts(routes: RouteRow[]): Record<string, RouteDraft> {
  return Object.fromEntries(routes.map((r) => [r.key, { use: r.use, pin: r.pin, preset: r.preset }]));
}

/** The tasks' part of the write (spec 3.4): only the fields that moved. Leaving
 *  "Specific model…" changes `use` alone -- the stored pin is neither sent nor
 *  cleared, so coming back restores it (today's rule) -- and `pin` is sent only
 *  when its own fields moved while the task is pinned. `""` and the
 *  stop-inheriting sentinel are different presets, compared as such. */
export function routeBody(drafts: Record<string, RouteDraft>, routes: RouteRow[]):
  NonNullable<InferenceWrite["routes"]> {
  const out: NonNullable<InferenceWrite["routes"]> = {};
  for (const row of routes) {
    const d = drafts[row.key];
    if (!d) continue;
    const entry: RouteWrite = {};
    if (d.use !== row.use) entry.use = d.use;
    if (d.preset !== row.preset) entry.preset = d.preset;
    if (d.use === "model" && !sameSel(d.pin, row.pin)) entry.pin = d.pin;
    if (Object.keys(entry).length) out[row.key] = entry;
  }
  return out;
}

/** A pinned task with no provider, or a provider and no model, cannot be saved. */
export function routesIncomplete(drafts: Record<string, RouteDraft>): boolean {
  return Object.values(drafts).some((d) => d.use === "model" && (!d.pin.provider || wantsModel(d.pin)));
}

const overrides = (d: RouteDraft) => d.use !== "" || d.preset !== "";

function TaskRow({ row, draft, onChange, settings, health, blocked }:
  { row: RouteRow; draft: RouteDraft; onChange: (d: RouteDraft) => void;
    settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean }) {
  const needs = routePinNeeds(row);
  const vision: CapabilityNeed | null = row.requires.includes("vision") ? "vision" : null;
  // The model a role choice would send this task's images to; a pin warns
  // through its own select.
  const target = draft.use === "model" ? null
    : draft.use ? settings.roles[draft.use].resolves : row.inherits;
  const warning = useWarning(target?.provider ?? "", target?.model ?? "", vision, row.label);
  // A pinned model is warned of from its own fields, as `RouteForm` did
  // (`SelectionFields warn={vision}`).
  const pinWarning = useWarning(draft.use === "model" ? draft.pin.provider : "",
                                draft.use === "model" ? draft.pin.model : "", vision, row.label);
  const decides = row.operation === "decide";
  return (
    <fieldset className="task-row" aria-label={row.label} data-task={row.key}>
      <legend>
        {row.label}
        {overrides(draft) && <> <span className="chip on">overrides</span></>}
      </legend>
      {row.hint && <p className="field-hint">{row.hint}</p>}
      <label className="field">
        <span>Use</span>
        <select aria-label={`${row.label} use`} value={draft.use} disabled={blocked}
                onChange={(e) => onChange({ ...draft, use: e.target.value as RouteUse })}>
          <option value="">Role default ({ROLE_LABEL[row.default_role]})</option>
          {GENERATIVE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          <option value="model">Specific model…</option>
        </select>
      </label>
      {draft.use === "model" ? (
        <>
          <ModelSelect label={`${row.label} pinned`} needs={needs} providers={settings.providers}
                       health={health} emptyLabel="Choose a provider…" disabled={blocked}
                       value={{ provider: draft.pin.provider, model: draft.pin.model }}
                       onChange={(v) => onChange({ ...draft,
                         pin: v.provider ? { ...draft.pin, ...v } : EMPTY_SEL })} />
          <Warning text={pinWarning} />
          {wantsModel(draft.pin) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
          {draft.pin.provider && (
            <label className="field">
              <span>Pin preset</span>
              <PresetSelect label={`${row.label} pin preset`} value={draft.pin.preset}
                            presets={settings.presets} emptyLabel="Provider defaults"
                            disabled={blocked}
                            onChange={(preset) => onChange({ ...draft, pin: { ...draft.pin, preset } })} />
            </label>
          )}
        </>
      ) : <Warning text={warning} />}
      <label className="field">
        <span>Preset override</span>
        <PresetSelect label={`${row.label} preset override`} value={draft.preset}
                      presets={settings.presets} allowClear disabled={blocked}
                      emptyLabel={`Inherit (resolves to ${inheritedPreset(row.inherits)})`}
                      onChange={(preset) => onChange({ ...draft, preset })} />
      </label>
      {row.requires.length > 0 && (
        <p className="field-hint">Also needs: {row.requires.join(", ")}</p>
      )}
      <Problem text={row.problem} />
      {decides && <DecideNote mode={row.decision_mode} decidesNatively={row.decides_natively} />}
      <Problem text={droppedFallbackWords(row.fallback_missing, row.label, "", row.fallback_problem)} />
      {row.resolves && (
        <details className="what-it-sends">
          <summary>What this sends</summary>
          <ControlsReadout presetId={row.resolves.preset} provider={row.resolves.provider}
                           model={row.resolves.model} operation={decides ? "decide" : undefined} />
        </details>
      )}
    </fieldset>
  );
}

/** Advanced (spec 3.3): one row per task, folded unless the address asks for
 *  the section (`#advanced`) or for one task (`#task-<encoded key>`). A task
 *  set here overrides its role's model, its preset, or both -- the resolver's
 *  precedence, unchanged; the form only labels it. */
export function TaskOverrides({ settings, health, blocked, drafts, onChange }:
  { settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean;
    drafts: Record<string, RouteDraft>; onChange: (key: string, d: RouteDraft) => void }) {
  const { hash } = useLocation();
  const asked = taskFromHash(hash);
  const target = asked !== null && settings.routes.some((r) => r.key === asked) ? asked : null;
  const [open, setOpen] = useState(hash === ADVANCED_HASH || target !== null);
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (target === null) return;
    const el = [...(root.current?.querySelectorAll<HTMLElement>("[data-task]") ?? [])]
      .find((n) => n.dataset.task === target);
    el?.scrollIntoView?.({ block: "start" });
  }, [target]);
  const n = Object.values(drafts).filter(overrides).length;
  return (
    <details id="advanced" ref={root} className="task-overrides" open={open}
             onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>Advanced: per-task overrides ({n} active)</summary>
      <p className="field-hint">
        Every task runs on its role unless it is set here. A task's own choice of
        model, its preset override, or both, win over the role for that task.
      </p>
      {settings.routes.map((row) => (
        <TaskRow key={row.key} row={row} draft={drafts[row.key]} settings={settings}
                 health={health} blocked={blocked} onChange={(d) => onChange(row.key, d)} />
      ))}
    </details>
  );
}
```

- [ ] **Step 4: Wire it into the form**

In `ModelsEditForm.tsx`:

1. `import { routeBody, routesIncomplete, startRouteDrafts, TaskOverrides, type RouteDraft } from "./TaskOverrides";`

2. Add the route state:

```ts
  const [routes, setRoutes] = useState<Record<string, RouteDraft>>(
    () => startRouteDrafts(settings.routes));
```

3. In `body()`, before `return out;`:

```ts
    const routeWrites = routeBody(routes, settings.routes);
    if (Object.keys(routeWrites).length) out.routes = routeWrites;
```

4. Append `|| routesIncomplete(routes)` to `incomplete`.

5. Render the section just before the confirmation banner:

```tsx
      <TaskOverrides settings={settings} health={health} blocked={blocked} drafts={routes}
                     onChange={(key, d) => { setRoutes((r) => ({ ...r, [key]: d })); setAsking(null); }} />
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/routes/ModelsView.test.tsx src/components/models`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/models frontend/src/routes/ModelsView.test.tsx
git commit -m "Models: per-task overrides under a folded Advanced section"
```

---

### Task 8: Presets gets its own page (`/presets`)

**Files:**
- Create the following under `frontend/src/components/presets/`:
  - `presetForm.ts`: move `SamplerPresetEditor.tsx:30-78` verbatim
    (`encodeStop`, `decodeStop`, `Draft`, `BLANK`, `toDraft`, `toParams`,
    `shown`), all exported;
  - `ImportReport.tsx`: move `SamplerPresetEditor.tsx:80-114` verbatim,
    exported;
  - `PresetView.tsx`, from `:304-363`;
  - `PresetForm.tsx`, from `:366-423`;
  - `PresetImport.tsx`, from `:125-132`, `:179-194`, `:235-259` and
    `:425-461`;
  - `usedBy.ts`.
- Create: `frontend/src/routes/PresetsView.tsx`
- Create: `frontend/src/routes/PresetsView.test.tsx`, porting every test from
  `frontend/src/components/SamplerPresetEditor.test.tsx`, plus the new ones
  below
- Do not delete `frontend/src/components/SamplerPresetEditor.tsx` or its
  test yet. ConfigView still imports the component. Task 10 removes that
  import and deletes both files.

**Interfaces:**
- Consumes:
  - `InferenceNav` (Task 5);
  - `taskHash` (Task 5);
  - `GENERATIVE` (Task 6, `selections.ts`);
  - `useInferenceSettings`, `InferenceBanner`, `ProviderModelPicker` and
    `ControlsReadout` (existing).
- Produces:
  - `presetUses(settings: InferenceSettings, id: string): { label: string; to: string }[]`;
  - `PresetsView`, the default export, mounted by Task 11 at `/presets`,
    `/presets/new`, `/presets/import` and `/presets/:id`;
  - accessible names: the column is "Presets"; the column actions are links
    "+ New preset" and "Import…"; the sidebar list is
    `aria-label="Used by"`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/PresetsView.test.tsx`. Port every test from
`SamplerPresetEditor.test.tsx`:
- render with `open("/presets/<id>")` (helper below), not a bare
  `<SamplerPresetEditor />`;
- click a column link where the old test clicked a rail button;
- reach `+ New preset` and `Import from SillyTavern…` through the column
  links, "+ New preset" and "Import…".

Keep each test's assertions as they are. Then add:

```tsx
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, PRESET_CLEAR } from "../api/client";
import PresetsView from "./PresetsView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listSamplerPresets: vi.fn(), createSamplerPreset: vi.fn(), updateSamplerPreset: vi.fn(),
    deleteSamplerPreset: vi.fn(), importSamplerPreset: vi.fn(), getInferenceSettings: vi.fn(),
    previewControls: vi.fn(), readConnectionCapabilities: vi.fn(),
  } };
});

const TABLE = [
  { name: "temperature", label: "Temperature", kind: "float", min: 0, max: 2 },
  { name: "top_k", label: "Top-k", kind: "int", min: 0, max: 200 },
];
const PRESETS = [
  { id: "balanced", name: "Balanced", notes: "", source: "manual", params: { temperature: 0.7 } },
  { id: "tight", name: "Tight", notes: "", source: "manual", params: {} },
];
const sel = (provider = "", model = "", preset = "") => ({ provider, model, preset });
const card = (over = {}) => ({ stored: sel(), fallback: sel(), resolves: null, inherits: null,
  problem: null, fallback_missing: [], fallback_problem: null, decision_mode: "",
  decides_natively: "unknown", rate: null, ...over });
const route = (key: string, label: string, over = {}) => ({ key, label, hint: "", tasks: [],
  operation: "generate", default_role: "fast", requires: [], campaign_scoped: true, use: "",
  pin: sel(), preset: "", resolves: null, inherits: null, problem: null, fallback_missing: [],
  fallback_problem: null, decision_mode: "", decides_natively: "unknown", role: "fast",
  uses: "fast", rate: null, ...over });
function settings(over = {}) {
  return { format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: { primary: card({ stored: sel("saltmarch", "vendor/m", "balanced") }),
             fast: card({ fallback: sel("realm", "vendor/m", "balanced") }),
             decision: card() },
    routes: [route("summary", "Rolling summary", { preset: "balanced" }),
             route("scene break", "Scene break", { use: "model", pin: sel("realm", "vendor/m", "balanced") })],
    providers: [], presets: PRESETS.map(({ id, name }) => ({ id, name })),
    preset_clear: PRESET_CLEAR, retirement_notes: [], ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listSamplerPresets as any).mockResolvedValue({ presets: PRESETS, params: TABLE });
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.previewControls as any).mockResolvedValue({ requested: {}, effective: {}, controls: {} });
  (api.updateSamplerPreset as any).mockImplementation(async (id: string, b: any) => ({ ...b, id, source: "manual" }));
  (api.createSamplerPreset as any).mockImplementation(async (b: any) => ({ ...b, id: "new-one", source: "manual" }));
  (api.deleteSamplerPreset as any).mockResolvedValue({});
});

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.hash}</div>; }
function open(at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/presets" element={<PresetsView />} />
        <Route path="/presets/new" element={<PresetsView />} />
        <Route path="/presets/import" element={<PresetsView />} />
        <Route path="/presets/:id" element={<PresetsView />} />
      </Routes>
    </MemoryRouter>);
}
const column = () => within(screen.getByRole("complementary", { name: "Presets" }));
const main = () => within(screen.getByRole("main"));

test("a preset opens read-only, with everywhere it is used", async () => {
  open("/presets/balanced");
  expect(await main().findByRole("heading", { name: "Balanced" })).toBeInTheDocument();
  expect(main().queryByRole("spinbutton")).toBeNull();
  const used = within(await main().findByRole("list", { name: "Used by" }));
  expect(used.getByRole("link", { name: "Primary" })).toHaveAttribute("href", "/models");
  expect(used.getByRole("link", { name: "Fast fallback" })).toHaveAttribute("href", "/models");
  expect(used.getByRole("link", { name: "Rolling summary" }))
    .toHaveAttribute("href", "/models/edit#task-summary");
  expect(used.getByRole("link", { name: "Scene break (pinned model)" }))
    .toHaveAttribute("href", "/models/edit#task-scene%20break");
});

test("a preset nothing uses says so", async () => {
  open("/presets/tight");
  expect(await main().findByText("Nothing uses this preset yet.")).toBeInTheDocument();
});

test("Edit opens the form; Save returns to the view", async () => {
  open("/presets/balanced");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateSamplerPreset).toHaveBeenCalledWith("balanced", expect.anything()));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("+ New preset goes straight to the form", async () => {
  open("/presets/balanced");
  const link = await column().findByRole("link", { name: "+ New preset" });
  expect(link).toHaveAttribute("href", "/presets/new");
  fireEvent.click(link);
  expect(await main().findByRole("textbox", { name: "Name" })).toHaveValue("");
});

test("Import… opens the import form", async () => {
  open("/presets/import");
  expect(await main().findByRole("heading", { name: "Import a SillyTavern preset" })).toBeInTheDocument();
});

test("/presets opens the first preset; an empty library says so", async () => {
  open("/presets");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/presets/balanced"));
});

test("an empty library offers + New preset", async () => {
  (api.listSamplerPresets as any).mockResolvedValue({ presets: [], params: TABLE });
  open("/presets");
  expect(await main().findByText("No presets yet.")).toBeInTheDocument();
});

test("Delete asks, deletes, and leaves for the list", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  open("/presets/tight");
  fireEvent.click(await main().findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteSamplerPreset).toHaveBeenCalledWith("tight"));
});

test("a newer build's store holds every write", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  open("/presets/balanced");
  await waitFor(() => expect(main().getByRole("button", { name: "Edit" })).toBeDisabled());
  expect(main().getByRole("button", { name: "Delete" })).toBeDisabled();
  expect(column().queryByRole("link", { name: "+ New preset" })).toBeNull();
});

test("the page says what a preset is and what is dropped", async () => {
  open("/presets/balanced");
  expect(await main().findByText(/A preset sets only what it\s+names/)).toBeInTheDocument();
  expect(main().getByText(/what cannot be sent is dropped/)).toBeInTheDocument();
});

test("the column is the Inference group, Presets current", async () => {
  open("/presets/balanced");
  const nav = within(await screen.findByRole("complementary", { name: "Presets" }));
  expect(nav.getByRole("link", { name: "Presets" })).toHaveAttribute("aria-current", "page");
});
```

The jsdom name of the Name input depends on the `Field` component. If
`getByRole("textbox", { name: "Name" })` does not resolve, use the query the
ported tests already use for that input.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/PresetsView.test.tsx`
Expected: FAIL, `./PresetsView` is missing.

- [ ] **Step 3: Move the pure helpers and the import report**

Create `presets/presetForm.ts` and `presets/ImportReport.tsx` by moving
`SamplerPresetEditor.tsx:30-78` and `:80-114` verbatim, exporting every
name. `presetForm.ts` imports `type SamplerParamSpec, type SamplerPreset`
from `"../../api/client"`. `ImportReport.tsx` imports
`type SamplerImportReport` and `shown` from `./presetForm`.

`presets/usedBy.ts`:

```ts
import type { InferenceSettings } from "../../api/client";
import { ROLE_LABEL } from "../inference/selection";
import { taskHash } from "../models/taskHash";
import { GENERATIVE } from "../models/selections";

/** Every library-wide place that names preset `id` (spec 4): a role's own
 *  preset, a role's fallback's, a task's preset override, and a task's pinned
 *  model's preset -- each a link to where it is changed. A campaign's own
 *  choices are its Inspector's, and are not listed here. */
export function presetUses(settings: InferenceSettings, id: string): { label: string; to: string }[] {
  const out: { label: string; to: string }[] = [];
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    if (card.stored.preset === id) out.push({ label: ROLE_LABEL[role], to: "/models" });
    if (card.fallback.preset === id) out.push({ label: `${ROLE_LABEL[role]} fallback`, to: "/models" });
  }
  for (const r of settings.routes) {
    const to = `/models/edit${taskHash(r.key)}`;
    if (r.preset === id) out.push({ label: r.label, to });
    if (r.pin.preset === id) out.push({ label: `${r.label} (pinned model)`, to });
  }
  return out;
}
```

- [ ] **Step 4: Split the view, the form and the import into components**

`presets/PresetView.tsx`: the JSX at `SamplerPresetEditor.tsx:305-362`, as a
component. Its props are:
`{ preset: SamplerPreset; table: SamplerParamSpec[]; report: SamplerImportReport | null; settings: InferenceSettings | null; previewOn: ProviderModel; onPreviewOn: (v: ProviderModel) => void; newer: boolean; busy: boolean; onEdit: () => void; onDelete: () => void; usedBy: { label: string; to: string }[] }`.

Change one thing from the original: replace the sidebar's
"Where it applies" `side-section` (`:351-357`) with this:

```tsx
              <div className="side-section">
                <h4>Used by</h4>
                {usedBy.length === 0 ? (
                  <span className="field-hint">Nothing uses this preset yet.</span>
                ) : (
                  <ul className="chips" aria-label="Used by">
                    {usedBy.map((u) => (
                      <li key={`${u.label}\u0000${u.to}`}>
                        <Link className="chip" to={u.to}>{u.label}</Link>
                      </li>
                    ))}
                  </ul>
                )}
                <span className="field-hint">
                  A campaign can override any of these in the scene inspector.
                </span>
              </div>
```

`presets/PresetForm.tsx`: the JSX at `:367-422`. Its props are
`{ draft: Draft; onDraft: (d: Draft) => void; table: SamplerParamSpec[]; busy: boolean; newer: boolean; onSave: () => void; onCancel: () => void }`.
Every `setDraft(` becomes `onDraft(`.

`presets/PresetImport.tsx` owns the import form's own state: `file`,
`importName`, `withMax`, `fileInput`, `picked`, `retireReads` and `pickFile`,
moved from `:127-132`, `:181-183` and `:235-259` unchanged, plus the JSX at
`:426-460`. Its props are
`{ busy: boolean; newer: boolean; onImport: (name: string, data: unknown, withMax: boolean) => void; onCancel: () => void; onError: (message: string) => void }`.
`pickFile`'s `setError` calls become `onError(...)`. The Import button calls
`onImport(importName.trim() || file.name, file.data, withMax)`. Cancel calls
`retireReads(); onCancel();`.

- [ ] **Step 5: Write `PresetsView.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useMatch, useNavigate, useParams } from "react-router-dom";
import {
  api, type SamplerImportReport, type SamplerParams, type SamplerParamSpec, type SamplerPreset,
} from "../api/client";
import { errorText } from "../api/errors";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { InferenceNav } from "../components/inference/InferenceNav";
import type { ProviderModel } from "../components/inference/ProviderModelPicker";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import { ColumnSection, PageShell } from "../components/PageShell";
import { PresetForm } from "../components/presets/PresetForm";
import { BLANK, type Draft, toDraft, toParams } from "../components/presets/presetForm";
import { PresetImport } from "../components/presets/PresetImport";
import { PresetView } from "../components/presets/PresetView";
import { presetUses } from "../components/presets/usedBy";

const presetPath = (id: string) => `/presets/${encodeURIComponent(id)}`;

/** `/presets` (spec 4): Settings → Inference's sampler presets, as a page that
 *  owns the screen -- the presets are the column's records, the open one is
 *  main's, read-only until Edit; `+ New preset` and `Import…` are addresses.
 *  Each preset says where it is used. Writes are refused only by a newer
 *  build's store, so only that gates them here. */
export default function PresetsView() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isNew = useMatch("/presets/new") !== null;
  const isImport = useMatch("/presets/import") !== null;
  const { settings } = useInferenceSettings();
  const [presets, setPresets] = useState<SamplerPreset[] | null>(null);
  const [table, setTable] = useState<SamplerParamSpec[]>([]);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** An import's report, held with the preset it made: shown only there. */
  const [report, setReport] = useState<{ id: string; report: SamplerImportReport } | null>(null);
  /** Kept across presets, so flicking through them compares each on one model. */
  const [previewOn, setPreviewOn] = useState<ProviderModel>({ provider: "", model: "" });

  const reload = useCallback(() => api.listSamplerPresets().then((r) => {
    setPresets(r.presets);
    setTable(r.params);
    return r.presets;
  }), []);
  useEffect(() => { reload().catch((err: unknown) => setError(errorText(err))); }, [reload]);
  useEffect(() => { setMode("view"); setError(null); }, [id]);
  useEffect(() => { if (isNew) { setDraft(BLANK); setError(null); } }, [isNew]);
  // `/presets` alone opens the first preset, as a list page opens on a record.
  useEffect(() => {
    if (!id && !isNew && !isImport && presets && presets.length > 0) {
      navigate(presetPath(presets[0].id), { replace: true });
    }
  }, [id, isNew, isImport, presets, navigate]);

  const newer = !!settings?.newer;
  const banner = settings?.newer ? { ...settings.migration, state: "newer" as const } : null;
  const current = presets?.find((p) => p.id === id) ?? null;

  async function save() {
    if (!draft.name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body = { name: draft.name, notes: draft.notes,
                     params: toParams(draft, table) as SamplerParams };
      const saved = current && !isNew ? await api.updateSamplerPreset(current.id, body)
                                      : await api.createSamplerPreset(body);
      await reload();
      setMode("view");
      navigate(presetPath(saved.id));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current || !window.confirm(`Delete the preset “${current.name}”?`)) return;
    setBusy(true);
    try {
      await api.deleteSamplerPreset(current.id);
      await reload();
      navigate("/presets");
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function runImport(name: string, data: unknown, withMax: boolean) {
    setBusy(true);
    setError(null);
    try {
      const got = await api.importSamplerPreset({ name, data, include_max_tokens: withMax });
      await reload();
      setReport({ id: got.preset.id, report: got.report });
      navigate(presetPath(got.preset.id));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const column = (
    <>
      <InferenceNav current="presets" />
      <ColumnSection label="Presets" count={presets?.length}>
        {!newer && (
          <div className="column-actions">
            <Link className="column-primary" to="/presets/new">+ New preset</Link>
            <Link className="subtle" to="/presets/import">Import…</Link>
          </div>
        )}
        {presets?.length === 0 && <p className="column-empty">None yet.</p>}
        {presets?.map((p) => (
          <Link key={p.id} to={presetPath(p.id)}
                className={"column-row" + (p.id === id ? " active" : "")}>
            <span className="column-row-label">{p.name}</span>
          </Link>
        ))}
      </ColumnSection>
    </>
  );

  let body;
  if (isNew || (mode === "edit" && current)) {
    body = <PresetForm draft={draft} onDraft={setDraft} table={table} busy={busy} newer={newer}
                       onSave={() => void save()}
                       onCancel={() => (isNew ? navigate("/presets") : setMode("view"))} />;
  } else if (isImport) {
    body = <PresetImport busy={busy} newer={newer} onImport={(n, d, m) => void runImport(n, d, m)}
                         onCancel={() => navigate("/presets")} onError={setError} />;
  } else if (presets === null) {
    body = <p className="field-hint">Reading presets…</p>;
  } else if (presets.length === 0) {
    body = <p className="empty-state">No presets yet.</p>;
  } else if (!current) {
    body = id ? <p className="empty-state">No preset is called {id}.</p> : null;
  } else {
    body = <PresetView preset={current} table={table} settings={settings}
                       report={report?.id === current.id ? report.report : null}
                       previewOn={previewOn} onPreviewOn={setPreviewOn}
                       newer={newer} busy={busy}
                       onEdit={() => { setDraft(toDraft(current)); setMode("edit"); }}
                       onDelete={() => void remove()}
                       usedBy={settings ? presetUses(settings, current.id) : []} />;
  }

  return (
    <PageShell column={column} columnLabel="Presets">
      <div className="page view-anim">
        <div className="page-head"><h1 className="page-h1">Presets</h1></div>
        <p className="config-copy">
          A preset is a named set of temperature, top-p, top-k, min-p, the three
          penalties, a token cap, stop strings and a reasoning effort — the
          settings SillyTavern users share per model. A preset sets only what it
          names; everything it leaves blank stays at the provider's default.
        </p>
        <p className="config-copy">
          Attach one to a role or a task on the <Link to="/models">Models</Link>{" "}
          page; a campaign can override either from the scene inspector, the same
          way it overrides the model. Not every backend takes every parameter, so
          what cannot be sent is dropped — <em>Preview on…</em> below, the Models
          page and the scene inspector say which.
        </p>
        <InferenceBanner status={banner} />
        {error && <div className="banner">{error}</div>}
        {body}
      </div>
    </PageShell>
  );
}
```

The two paragraphs are the Settings → Presets pane's (`ConfigView.tsx:848-860`)
verbatim, except that "a role or a route" now reads "a role or a task".

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/routes/PresetsView.test.tsx src/components/presets`
Expected: all PASS, including every ported test.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/presets frontend/src/routes/PresetsView.tsx frontend/src/routes/PresetsView.test.tsx
git commit -m "Presets: a page of its own, with routed new/import and where each preset is used"
```

---

### Task 9: Providers — a real `/providers/new`, `returnTo`, and the new Used-by targets

**Files:**
- Modify: `frontend/src/routes/ProvidersView.tsx`:
  - `:80-93`, `chipFor`;
  - `:142-176`, the state;
  - `:240-250`, the per-id effect;
  - `:303-315`, `startNew` and `created`;
  - `:341-385`, the column and body.
- Test: `frontend/src/routes/ProvidersView.test.tsx`

**Interfaces:**
- Consumes: `InferenceNav` (Task 5) and `taskHash` (Task 5).
- Produces:
  - **Creation is driven by the route.** The page is creating when the path
    is `/providers/new` (`useMatch`).
  - **`returnTo`.** Every in-app link into creation passes router state
    `{ returnTo: string }`.
  - **`validReturn(state: unknown): string | null`**, exported for tests.
    It accepts only a string that starts with a single `/`, does not start
    with `//`, and whose pathname is not `/providers/new`.
  - **New Used-by targets.** Global uses link to `/models` for a role or
    fallback, and to `/models/edit${taskHash(key)}` for a route. Campaign
    uses are unchanged.

- [ ] **Step 1: Update the harness and write the failing tests**

In `ProvidersView.test.tsx`:
1. Make `Where` render `pathname + hash` in the `where` test id.
2. Add `<Route path="/providers/new" element={<ProvidersView />} />` to
   `open()`, before `/providers/:id`.
3. Add a `<Route path="/models" element={<div>the models page</div>} />`
   beside the existing `/models/*`.

Then make these changes:
- In "Used by chips link to the models page", expect `where` to read
  `/models/edit#task-summary`.
- In "a Used by role chip opens that role", expect `/models`.

Then add:

```tsx
import { validReturn } from "./ProvidersView";

test("+ New provider is an address of its own, and a direct load shows the form", async () => {
  open("/providers/new");
  expect(await main().findByRole("group", { name: "Provider presets" })).toBeInTheDocument();
  expect(column().getByRole("button", { name: "+ New provider" })).toBeDisabled();
});

test("+ New provider goes to /providers/new", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers\/new$/));
});

test("Cancel returns to the provider the form was opened from", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers\/saltmarch$/));
});

test("Cancel from a direct load lands on the provider list", async () => {
  open("/providers/new");
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers$/));
});

test("Cancel returns to another page that opened it, such as Models", async () => {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/providers/new", state: { returnTo: "/models" } }]}>
      <Where />
      <Routes>
        <Route path="/providers/new" element={<ProvidersView />} />
        <Route path="/models" element={<div>the models page</div>} />
      </Routes>
    </MemoryRouter>);
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("the models page")).toBeInTheDocument();
});

test("only an internal returnTo is honoured, and never the form itself", () => {
  expect(validReturn({ returnTo: "/models#rates" })).toBe("/models#rates");
  expect(validReturn({ returnTo: "//evil.example" })).toBeNull();
  expect(validReturn({ returnTo: "https://evil.example" })).toBeNull();
  expect(validReturn({ returnTo: "/providers/new" })).toBeNull();
  expect(validReturn({ returnTo: "/providers/new?x=1" })).toBeNull();
  expect(validReturn(null)).toBeNull();
  expect(validReturn({ returnTo: 7 })).toBeNull();
});

test("the column opens with the Inference group, Providers current", async () => {
  open("/providers");
  const nav = within(await screen.findByRole("complementary", { name: "Providers" }));
  expect(nav.getByRole("link", { name: "Providers" })).toHaveAttribute("aria-current", "page");
  expect(nav.getByRole("link", { name: "Models" })).toHaveAttribute("href", "/models");
});
```

The existing "+ New provider asks for a preset first" and "a locked preset's
address…" tests keep working unchanged, now through navigation. If they need
a `waitFor` after the click, wrap only the next `find…` in it.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ProvidersView.test.tsx`
Expected: the new tests and the two edited chip tests FAIL.

- [ ] **Step 3: Implement**

In `ProvidersView.tsx`:

1. Add the imports: `useLocation` and `useMatch` from react-router-dom,
   `InferenceNav`, and `taskHash` from `../components/models/taskHash`.

2. Add this beside `chipFor`:

```ts
/** Where Cancel on `/providers/new` goes: the router state an in-app link left
 *  (`{ returnTo }`), if it is a path inside the app and not the form itself;
 *  null otherwise, which lands on `/providers`. A refresh keeps the state the
 *  browser kept, so it still goes back where the form was opened from. */
export function validReturn(state: unknown): string | null {
  const to = (state as { returnTo?: unknown } | null)?.returnTo;
  if (typeof to !== "string" || !to.startsWith("/") || to.startsWith("//")) return null;
  const path = to.split(/[?#]/)[0];
  return path === "/providers/new" ? null : to;
}
```

3. Change `chipFor`'s last two lines:

```ts
  if (use.kind === "route") return { label, to: `/models/edit${taskHash(use.key)}` };
  return { label, to: "/models" };
```

4. Replace the `creating` state with these:

```ts
  const location = useLocation();
  const isNew = useMatch("/providers/new") !== null;
  /** The preset the new-provider form has been given, once one is picked. */
  const [newPreset, setNewPreset] = useState<ProviderPresetOption | null>(null);
  useEffect(() => { if (isNew) setNewPreset(null); }, [isNew]);
```

5. In the per-id effect, delete `setCreating(null);`.

6. Replace `startNew` and add `cancelNew`:

```ts
  function startNew() {
    navigate("/providers/new",
             { state: { returnTo: location.pathname + location.search + location.hash } });
  }

  function cancelNew() {
    navigate(validReturn(location.state) ?? "/providers");
  }
```

7. In `created`, delete `setCreating(null);`.

8. Make these changes to the column:
   - put `<InferenceNav current="providers" />` before the Providers
     `ColumnSection`;
   - change the + New provider button to
     `disabled={blocked || isNew} aria-current={isNew ? "page" : undefined}`;
   - change each provider row's active test to `p.id === id && !isNew`.

9. Change the body's first branch to:

```tsx
  if (isNew) {
    body = (
      <NewProvider presets={presets} presetsError={presetsError} onRetry={loadPresets}
                   choice={newPreset} onChoose={setNewPreset}
                   onCancel={cancelNew} onCreated={created} />
    );
  } else if (!id) {
```

`NewProvider` already renders a Cancel button beside its preset group
(`ProvidersView.tsx`, in `NewProvider`), wired to `onCancel`.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run src/routes/ProvidersView.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ProvidersView.tsx frontend/src/routes/ProvidersView.test.tsx
git commit -m "Providers: /providers/new is an address, Cancel returns where it came from"
```

---

### Task 10: Settings — the Inference group, three panes gone, their fields re-homed

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`:
  - imports (`:1-20`);
  - `SectionId` / `RETIRED` / `SECTIONS` (`:101-172`);
  - the helpers used only by the Models pane (`:232-277`);
  - `ConfigView`'s query handling and inference read (`:333-356`);
  - the column (`:540-582`);
  - the panes `models` (`:748-844`), `samplers` (`:846-863`), `timeouts`
    (`:865-893`), `pricing` (`:895-922`) and `context` (`:924-1052`).
- Delete: `frontend/src/components/SamplerPresetEditor.tsx` and
  `frontend/src/components/SamplerPresetEditor.test.tsx`. Task 8 already
  ported their tests.
- Test: `frontend/src/routes/ConfigView.test.tsx`

**Interfaces:**
- Consumes: the routes `/providers`, `/models` and `/presets`, which App
  mounts in Task 11. Tests here stub them with `<Route>` elements.
- Produces:
  - **`LINKS`**: a group "Inference", first in the column, holding three
    router links named "Providers", "Models" and "Presets". The Models
    link carries the ready dot.
  - **`MOVED: Map<string, string>`**, which sends an old pane id to a
    route, applied with `navigate(target, { replace: true })`:

    | Old id | Route |
    |---|---|
    | `models`, `semantic`, `connection` | `/models` |
    | `routing` | `/models/edit` |
    | `samplers` | `/presets` |
    | `pricing` | `/models#rates` |

  - **The re-homed fields.** The "Timeouts & retries" section owns
    `llm_retries`. "Context" owns `semantic_recall_depth` and
    `semantic_recall_threshold`.

`RETIRED` is deleted, as spec §1 (amended 2026-10-09) says. All three of its
entries pointed at `models`, so after this change it would be an empty table.
`MOVED` does the job, and stays a `Map` for the same reason `RETIRED` was one:
the id comes from the address bar.

- [ ] **Step 1: Update the tests first**

In `ConfigView.test.tsx`:

1. **Delete** every test whose subject is the Models pane's summary: those
   that call `openModels()` only to assert on the "Models in use" region,
   the role lines, the embedding chip or the upgrade line. ModelsView's
   suite (Tasks 5-7) now covers them. Delete `openModels` itself, and the
   `viewSel`/settings-view fixtures, once nothing uses them.

2. **Move** these tests to open the pane where their field now lives,
   asserting the same things:
   - The tests that edit Retries (around `:185-201` and `:425-430`) open
     `/^Timeouts/` instead of calling `openModels()`.
   - The tests that edit recall depth or threshold, or read
     `EMBEDDINGS_COPY` or the privacy paragraph (around `:507-590`), open
     `/^Context/`. The test at `:580` that expected the Embedding role link
     to point to `/models/role/embedding` now expects `/models/edit`.

3. **Replace** "the column indexes every section in three groups" with:

```tsx
test("the column leads with Inference's three pages, then every section in three groups", async () => {
  renderView();
  await screen.findByRole("button", { name: /^Storage/ });
  const groups = [...document.querySelectorAll(".column-section-head .section-label")];
  expect(groups.map((g) => g.textContent))
    .toEqual(["Inference", "The install", "What the model sees", "What you see"]);
  expect(screen.getByRole("link", { name: /^Providers/ })).toHaveAttribute("href", "/providers");
  expect(screen.getByRole("link", { name: /^Models/ })).toHaveAttribute("href", "/models");
  expect(screen.getByRole("link", { name: /^Presets/ })).toHaveAttribute("href", "/presets");
  for (const label of [
    /^Storage/, /^Backups/, /^Logging/, /^Timeouts & retries/, /^First-run setup/, /^Context/,
    /^Prompt layout/, /^Scene tracker/, /^System prompt/, /^Response targets/,
    /^Transcript/, /^Output processing/, /^While playing/, /^Appearance/,
  ]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
  for (const gone of [/^Models/, /^Presets/, /^Token rates/]) {
    expect(screen.queryByRole("button", { name: gone })).toBeNull();
  }
});
```

4. **Replace** the `?section=` block's `renderAt` and its three Models tests
   ("?section=semantic opens the models section", "the retired connection
   and routing sections open Models too", "a changed query follows") with:

```tsx
function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search + l.hash}</div>;
}
function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <Routes>
        <Route path="/config" element={<><ConfigView /><Link to="/config?section=timeouts">to timeouts</Link></>} />
        <Route path="/models" element={<div>the models page</div>} />
        <Route path="/models/edit" element={<div>the models form</div>} />
        <Route path="/presets" element={<div>the presets page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

test("an old pane's address goes to the page that replaced it", async () => {
  const moved: [string, string][] = [
    ["models", "/models"], ["semantic", "/models"], ["connection", "/models"],
    ["routing", "/models/edit"], ["samplers", "/presets"], ["pricing", "/models#rates"],
  ];
  for (const [id, to] of moved) {
    renderAt(`/config?section=${id}`);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe(to));
    cleanup();
  }
});

test("a changed query follows", async () => {
  renderAt("/config");
  expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "to timeouts" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Timeouts & retries" }))
    .toBeInTheDocument();
});

test("Retries lives with the timeouts, with what a retry is", async () => {
  renderView();
  await open(/^Timeouts & retries/);
  expect(screen.getByLabelText(/^retries$/i)).toHaveValue("2");
  expect(screen.getByText(/Only ever\s+before\s+the reply starts arriving/)).toBeInTheDocument();
});

test("recall lives with the context, privacy sentence and all", async () => {
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText(/^recalled entries$/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/^similarity threshold$/i)).toBeInTheDocument();
  expect(screen.getByText("This sends text to the Embedding role's provider.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Embedding role" })).toHaveAttribute("href", "/models/edit");
  expect(screen.getByText(EMBEDDINGS_COPY)).toBeInTheDocument();
});
```

   Keep "a section named like an object's own member falls back to Storage"
   and "an unknown section falls back to Storage". They now exercise `MOVED`.
   Import `Routes`, `Route` and `useLocation` from react-router-dom.

5. Remove `getInferenceSettings` from the mock list if nothing reads it any
   more, after Step 3.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ConfigView.test.tsx`
Expected: the new and moved tests FAIL, because the panes and links have not
changed yet.

- [ ] **Step 3: Change ConfigView**

1. **`SectionId`**: remove `"models"`, `"pricing"` and `"samplers"`.

2. **`SECTIONS`**:
   - delete the `models`, `pricing` and `samplers` entries and their
     comments;
   - change `timeouts` to
     `{ id: "timeouts", group: "The install", label: "Timeouts & retries", fields: ["llm_timeout", "absorb_budget", "llm_call_budget", "llm_retries"] },`;
   - append `"semantic_recall_depth", "semantic_recall_threshold"` to
     `context`'s `fields`.

3. **Replace `RETIRED`** with:

```ts
/** Ids of panes that became pages of their own (Settings → Inference), and
 *  where each went, so a link written before still lands where its setting
 *  is. A Map rather than an object literal: the id comes from the address
 *  bar, and an object answers `toString` or `constructor` with what it
 *  inherits. */
const MOVED = new Map<string, string>([
  ["models", "/models"], ["semantic", "/models"], ["connection", "/models"],
  ["routing", "/models/edit"], ["samplers", "/presets"], ["pricing", "/models#rates"],
]);

/** Settings → Inference: three pages of their own, linked from the column's
 *  first group. Links, not panes -- they own no draft field, so they never
 *  carry an unsaved dot, and their keys never take part in `?section=`. */
type LinkDef = { key: "inference-providers" | "inference-models" | "inference-presets";
                 group: "Inference"; label: string; to: string };
const LINKS: LinkDef[] = [
  { key: "inference-providers", group: "Inference", label: "Providers", to: "/providers" },
  { key: "inference-models", group: "Inference", label: "Models", to: "/models" },
  { key: "inference-presets", group: "Inference", label: "Presets", to: "/presets" },
];
```

   Update the comment above `SECTIONS` ("three groups, seventeen sections")
   so it gives the real count.

4. **Query handling**: replace the `askedSection` line and add the redirect.

```ts
  const navigate = useNavigate();
  const askedSection = SECTIONS.find((s) => s.id === asked)?.id ?? null;
  const movedTo = MOVED.get(asked) ?? null;
  useEffect(() => { if (movedTo) navigate(movedTo, { replace: true }); }, [movedTo, navigate]);
```

   Add `useNavigate` to the router import.

5. **Delete what only the Models pane used**:
   - `modelsOpened`, `inferenceView` and `inference`;
   - `banner`, `upgradeNote` and `embeddingCard`;
   - the `ROLE_LABEL`, `describeRole`, `SAME_AS`, `roleLine` and
     `embeddingChip` helpers;
   - the imports of `InferenceBanner`, `migrationBanner`, `migrationLine`,
     `describeSelection`, `useInferenceSettings`, `SamplerPresetEditor` and
     `PricingEditor`;
   - `Fragment`, if nothing else uses it;
   - `type InferenceSettings` and `type RoleSummary`, if unused.

   Keep the `EMBEDDINGS_COPY` import, which Context now uses.

6. **The column**: render the Inference group before `GROUPS.map(...)`:

```tsx
      <ColumnSection label={LINKS[0].group}>
        {LINKS.map((l) => (
          <Link key={l.key} to={l.to} className="column-row">
            <span className="column-row-label">
              {l.label}
              {/* The dot is the state, so the state is also spelled out. */}
              {l.key === "inference-models" && config && (
                <>
                  <span className={"conn-dot " + (config.ready ? "ok" : "off")} aria-hidden> ●</span>
                  <span className="sr-only">{config.ready ? " ready" : " not ready"}</span>
                </>
              )}
            </span>
          </Link>
        ))}
      </ColumnSection>
```

   Then, inside the `SECTIONS` rows, delete the old
   `{s.id === "models" && config && (...)}` dot.

7. **The panes**:
   - Delete the `models`, `samplers` and `pricing` blocks.
   - In the `timeouts` block, before its `<div className="config-fields">`,
     paste the two retry paragraphs from the old Models pane
     (`:791-803`). In the second, change "the role's fallback — set on its
     card on the Models page —" to
     "the role's fallback — set on the <Link to=\"/models\">Models</Link> page —".
   - Add the Retries `NumField` (`:805-808`) inside that block's
     `config-fields`.
   - At the end of the `context` block, before its closing `</>`, paste the
     whole recall run from the old Models pane: `:810-842`. That covers the
     recall paragraph, `EMBEDDINGS_COPY`, the two `NumField`s, the threshold
     note and the privacy paragraph. In the privacy paragraph, change
     `to="/models/role/embedding"` to `to="/models/edit"`.

8. In `valueOf`'s default-branch comment, remove the sentence about Models
   carrying the ready dot. Models is a link now, and the dot moved with it.

9. Delete `components/SamplerPresetEditor.tsx` and its test.

- [ ] **Step 4: Run the tests and the typechecker**

Run: `cd frontend && npx tsc --noEmit -p . && npx vitest run src/routes/ConfigView.test.tsx`
Expected: no type errors. All ConfigView tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src/routes/ConfigView.tsx frontend/src/routes/ConfigView.test.tsx frontend/src/components/SamplerPresetEditor.tsx frontend/src/components/SamplerPresetEditor.test.tsx
git commit -m "Settings: an Inference group of three pages; retries join timeouts, recall joins context"
```

---

### Task 11: Routes, rail, Library, palette and styles

**Files:**
- Create: `frontend/src/routes/ModelsRedirect.tsx` and
  `frontend/src/routes/ModelsRedirect.test.tsx`
- Modify: `frontend/src/App.tsx:372-381` (routes)
- Modify: `frontend/src/shell/rail.ts:177-182` (the Settings row's `match`)
  and `:328-345` (`TITLES`)
- Modify: `frontend/src/librarySections.ts:49-57` (Providers removed)
- Modify: `frontend/src/components/AppPaletteSource.tsx:72-77` (the
  Providers, Models and Presets entries)
- Modify: `frontend/src/index.css` (append)
- Test: `frontend/src/shell/rail.test.ts`,
  `frontend/src/components/LibraryColumn.test.tsx`, and the palette's test
  if one asserts Providers (find it with
  `grep -rln "section:/providers\|library section" src --include=*.test.tsx`)

**Interfaces:**
- Consumes: `ModelsView` with `edit` (Task 6), `PresetsView` (Task 8),
  `ProvidersView`'s `/providers/new` (Task 9) and `taskHash` (Task 5).
- Produces:
  - the app routes `/providers/new`, `/models/edit`, `/presets`,
    `/presets/new`, `/presets/import` and `/presets/:id`;
  - redirects from `/models/role/:role` to `/models`, and from
    `/models/route/:key` to `/models/edit#task-<encoded key>`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/routes/ModelsRedirect.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RoutePageRedirect } from "./ModelsRedirect";

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.hash}</div>; }

test("an old route page lands on its task's row, the key encoded once", () => {
  render(
    <MemoryRouter initialEntries={["/models/route/scene%20break"]}>
      <Where />
      <Routes>
        <Route path="/models/route/:key" element={<RoutePageRedirect />} />
        <Route path="/models/edit" element={<div>form</div>} />
      </Routes>
    </MemoryRouter>);
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit#task-scene%20break");
});
```

In `rail.test.ts`:
- In "Library survives its own redirect", replace
  `expect(activeIn(APP_ROWS, "/providers")).toEqual(["library"]);` with
  `expect(activeIn(APP_ROWS, "/providers")).toEqual(["config"]);`.
- Rename "/models is Settings' page, under its own title" to "Inference's
  three pages are Settings', each under its own title", and add:

```ts
  expect(activeIn(APP_ROWS, "/providers/saltmarch")).toEqual(["config"]);
  expect(activeIn(APP_ROWS, "/presets")).toEqual(["config"]);
  expect(activeIn(APP_ROWS, "/presets/balanced")).toEqual(["config"]);
  expect(activeIn(APP_ROWS, "/models/edit")).toEqual(["config"]);
  expect(titleFor("/presets/balanced")).toBe("Presets");
  expect(titleFor("/providers/new")).toBe("Providers");
  expect(activeIn(APP_ROWS, "/presets-of-my-own")).toEqual([]);
```

  Keep its existing `/models` assertions. `"/models/role/primary"` stays in
  the list at the top of the file, because the route still exists as a
  redirect.

In `LibraryColumn.test.tsx`, delete the `["Providers", "/providers"]` row
(`:45`) and the count assertion for it (`:70`). Then check the Library rail
badge expectation: it comes from `LIBRARY_SECTIONS.length`. If any test
asserts a literal section count, lower it by one.

In the palette test, if one exists: expect `section:/providers` with meta
"Settings → Inference", and `section:/presets` with meta "Settings →
Inference". No palette entry should carry the meta "library section" with
the label "Providers" any more.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/routes/ModelsRedirect.test.tsx src/shell/rail.test.ts src/components/LibraryColumn.test.tsx`
Expected: FAIL. The module is missing, and the rail still lights Library on
`/providers`.

- [ ] **Step 3: Implement**

`frontend/src/routes/ModelsRedirect.tsx`:

```tsx
import { Navigate, useParams } from "react-router-dom";
import { taskHash } from "../components/models/taskHash";

/** `/models/route/:key`, an address older links and bookmarks carry: the
 *  task's row on the edit form now. The router hands the key decoded, and
 *  `taskHash` encodes it once. */
export function RoutePageRedirect() {
  const { key = "" } = useParams();
  return <Navigate to={`/models/edit${taskHash(key)}`} replace />;
}
```

`App.tsx`:
- Import `PresetsView` and `RoutePageRedirect`.
- Replace the providers and models routes (`:372-381`) with:

```tsx
      <Route path="/providers" element={<ProvidersView />} />
      {/* Before `:id`, so `new` is never read as a provider. */}
      <Route path="/providers/new" element={<ProvidersView />} />
      <Route path="/providers/:id" element={<ProvidersView />} />
      {/* A splat: a model id carries its own slashes (`vendor/model`). */}
      <Route path="/providers/:id/models/*" element={<ProvidersView />} />
      <Route path="/connections" element={<Navigate to="/providers" replace />} />
      {/* Settings → Inference: the roles' summary, and the one form that
          edits every role and task. The old per-role and per-route pages
          are addresses older links carry. */}
      <Route path="/models" element={<ModelsView />} />
      <Route path="/models/edit" element={<ModelsView edit />} />
      <Route path="/models/role/:role" element={<Navigate to="/models" replace />} />
      <Route path="/models/route/:key" element={<RoutePageRedirect />} />
      <Route path="/presets" element={<PresetsView />} />
      <Route path="/presets/new" element={<PresetsView />} />
      <Route path="/presets/import" element={<PresetsView />} />
      <Route path="/presets/:id" element={<PresetsView />} />
```

`rail.ts`:
- Change the Settings row's `match` and its comment:

```ts
    // Settings → Inference's three pages (Providers, Models, Presets) are
    // pages of their own reached from Settings, so the reader is still in
    // Settings there (the rail gains no row for them).
    match: (p) => isUnder(p, "/config") || isUnder(p, "/models")
      || isUnder(p, "/providers") || isUnder(p, "/presets"),
```

- In `TITLES`, add `[(p) => isUnder(p, "/presets"), "Presets"],` after the
  `/models` entry.

`librarySections.ts`:
- Delete the Providers entry, along with the comment above it explaining
  why it was a library section.
- Leave a one-line comment in its place: `// Providers left the library for
  Settings → Inference (2026-10-09), with Models and Presets.`

`AppPaletteSource.tsx`:
- Replace the `section:/models` push and its comment with:

```tsx
    // Settings → Inference's three pages: neither library sections nor rail
    // rows, so this is the way in that does not need Settings open first.
    out.push({ id: "section:/providers", group: "ELSEWHERE", label: "Providers",
               meta: "Settings → Inference", to: "/providers" });
    out.push({ id: "section:/models", group: "ELSEWHERE", label: "Models",
               meta: "Settings → Inference", to: "/models" });
    out.push({ id: "section:/presets", group: "ELSEWHERE", label: "Presets",
               meta: "Settings → Inference", to: "/presets" });
```

`index.css`: append these rules. They are kept to the new classes, and they
use the tokens the file already defines (`--line`, `--muted` and so on). Use
whatever names the file actually uses; check with `grep -n "^  --" src/index.css`.

```css
/* ---- Settings → Inference: Models ---- */
.models-roles { display: grid; gap: 12px; margin-block: 12px 16px; }
.role-row { display: grid; grid-template-columns: 110px 1fr; gap: 12px;
            padding: 10px 0; border-bottom: 1px solid var(--line); }
.role-row-name { font-weight: 600; }
.role-row-body > p { margin: 0 0 4px; }
.role-row-fallback { color: var(--muted); }
.models-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.models-edit-role { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px;
                    margin-block: 10px; }
.models-edit-fallback { margin-top: 8px; border-style: dashed; }
.model-select { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.model-select select { min-width: 14rem; max-width: 100%; }
.model-typed { display: inline-flex; gap: 6px; }
.task-overrides > summary { cursor: pointer; font-weight: 600; margin-block: 12px; }
.task-row { border-top: 1px solid var(--line); padding: 10px 0; }
.rates-table { border-collapse: collapse; margin-block: 8px; }
.rates-table th, .rates-table td { text-align: left; padding: 4px 12px 4px 0; }
@media (max-width: 640px) {
  .role-row { grid-template-columns: 1fr; gap: 4px; }
  .model-select select { min-width: 0; width: 100%; }
}
```

The summary uses `<Link className="button subtle">` for "+ Add provider", and
the Presets column uses `<Link className="column-primary">`. Check that
`.button` and `a.column-primary` are styled in `index.css`
(`grep -n "\.button\b\|column-primary" src/index.css`). If a rule is scoped to
`button.column-primary` or `button.subtle`, widen it to cover `a` too. A link
that looks like a button only when it is a `<button>` is a known trap here
(user memory, "Button→Link CSS trap"). Task 12's browser check is what proves
it.

- [ ] **Step 4: Run the suite**

Run: `cd frontend && npx tsc --noEmit -p . && npx vitest run`
Expected: no type errors. The whole suite passes, with no failures beyond
those recorded in Task 1, Step 6.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/routes/ModelsRedirect.tsx frontend/src/routes/ModelsRedirect.test.tsx frontend/src/shell frontend/src/librarySections.ts frontend/src/components/AppPaletteSource.tsx frontend/src/components/LibraryColumn.test.tsx frontend/src/index.css
git commit -m "Shell: Inference's pages are Settings', Providers leaves the Library, old addresses redirect"
```

---

### Task 12: The gate, the docs, a browser pass and the review gates

**Files:**
- Modify: any non-historical doc that names a moved address, as found in
  Step 1
- Modify: `lint-baselines/*.json`, if a ratcheted count fell

- [ ] **Step 1: Find docs that name the old addresses**

Run:
`grep -rn "models/role\|models/route\|section=pricing\|section=samplers\|section=models\|Settings → Models\|Settings → Presets\|Token rates" --include=*.md . | grep -v "docs/superpowers/" | grep -v node_modules`

Update each hit to the new address and name: Settings → Inference →
Providers, Models or Presets; `/models`, `/models/edit` and `/presets`.
`docs/superpowers/` holds dated records and is never edited (user memory).

- [ ] **Step 2: Run the whole gate**

Run: `make check` (in a worktree, add
`PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`).
Expected: green, except for failures already present on `main` (user
memory: main's suite has had known pre-existing failures). Record every
failure. If one also fails on `main` at the commit this branch started from,
it is pre-existing. Anything else is this branch's to fix.

If `check-lint`, `check-mypy` or `check-eslint` fails because a count went
**down**, run `make baseline` and commit the smaller baselines:

```bash
git add lint-baselines
git commit -m "Lint baselines: the findings this change resolved"
```

- [ ] **Step 3: Drive it in a browser**

Use the `verify` skill. It launches grimoire against an isolated store with
a mocked provider. Never point this at `~/.grimoire`. Then check:

1. **Settings column.** "Inference" comes first, with Providers, Models and
   Presets as links. The Models link carries its ready dot. The rail lights
   Settings on every one of the three.
2. **`/models`.** Each role row shows provider (dot, linked), model, preset
   (linked) and rate. "What this sends" is folded. The rate table reads,
   then Edit rates opens the editor.
3. **"+ Add provider" and "+ New preset".** These now render as links. They
   must still look like buttons: compare them against "Edit models" (see
   the CSS trap in Task 11). Measure them; do not judge by eye.
4. **Edit models.** Change Fast's provider, then pick a model, and Save.
   The summary reflects it. Add a fallback, then remove it. Change
   Embedding and see the re-embed question.
5. **Advanced.** Open it and pin a task to a specific model. Switch the task
   to a role and back, and confirm the pin returns. Visit
   `/models/edit#task-%ZZ` and confirm the page renders.
6. **Set rate.** With a model that has no rate, click Set rate. The editor
   opens on that model with the caret in Input. Save, and the role's rate
   line updates.
7. **`/presets`.** Open a preset and check its Used by. Then try Edit,
   + New preset, Import… and Delete.
8. **`/providers/new` from Models.** Cancel returns to `/models`. A direct
   load followed by Cancel lands on `/providers`.
9. **Narrow width.** At 390px wide, nothing scrolls sideways.

Write down anything that looks wrong and fix it before Step 4, with a test
for any behavioural fix.

- [ ] **Step 4: Codex review of the diff (Implementation → done gate)**

`/codex:review` silently reviews GitHub rather than the local diff (user
memory). So pipe the diff instead:

```bash
git diff main...HEAD > "$SCRATCH/inference-group.patch"
cat "$SCRATCH/inference-group.patch" | codex exec --sandbox read-only --skip-git-repo-check \
  "Code review. Your shell is broken: review ONLY the diff on stdin; never run git, read files or fetch GitHub. Find correctness bugs, broken invariants and missed edge cases. Cite file:line from the diff."
```

Check that the output cites this branch's code. Fix each finding or note why
not, then re-run until it is clean.

- [ ] **Step 5: Codex adversarial review against the spec (Done → actually done gate)**

```bash
{ cat docs/superpowers/specs/2026-10-09-inference-settings-group-design.md; echo; echo "===== DIFF ====="; cat "$SCRATCH/inference-group.patch"; } \
  | codex exec --sandbox read-only --skip-git-repo-check \
  "ADVERSARIAL REVIEW. Shell is broken: review ONLY stdin (the spec, then the diff). Does the diff implement the spec? List gaps, drift, and quietly dropped requirements, section by section of the spec, citing diff lines. Ignore style."
```

Resolve the findings as in Step 4.

- [ ] **Step 6: Hand back for integration**

Use `superpowers:finishing-a-development-branch`. History stays linear:
rebase onto `main`, then `git merge --ff-only`. Never make a merge commit.
