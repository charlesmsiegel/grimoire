# Inference settings group: Providers, Models, Presets

Date: 2026-10-09
Status: design approved in conversation, awaiting written-spec review

## Problem

Choosing which model does what is spread over four surfaces that do not say
how they relate:

- **Settings → Models** (`routes/ConfigView.tsx`, section `models`) is a
  read-only summary of the roles plus three unrelated fields (retries and the
  two recall knobs). It links out to `/models` and `/providers`.
- **`/models`** (`routes/ModelsView.tsx`) is the only place a role can be
  changed, behind its own column of Roles and an "Advanced" Routing list,
  with a detail page and a form per role (`/models/role/:role`) and per route
  (`/models/route/:key`). A model is picked from radio groups ("Fits",
  "Unverified", "Typed id") rather than a list.
- **Settings → Presets** (section `samplers`) edits sampler presets in a pane
  under "What the model sees", far from the roles that use them.
- **Settings → Token rates** (section `pricing`) edits the fallback per-token
  table under "The install", far from the models it prices.

The user's expectation, which this design adopts: for each of Primary, Fast,
Decision and Embedding, choose a provider from a dropdown, then a model from
that provider, then a preset — on an edit page, so the summary stays clean —
with providers, presets and rates one click away.

## Design summary

1. A new Settings column group, **Inference**, at the top of the column, with
   three entries. Each is its own full page:
   - **Providers** → `/providers` (exists; gains `/providers/new`)
   - **Models** → `/models` (rebuilt; absorbs Token rates)
   - **Presets** → `/presets` (new page; the `samplers` pane moves here)
2. The Settings panes `models`, `samplers` and `pricing` are removed; their
   ids join `MOVED` and redirect to the new pages. The three draft fields the
   `models` pane owned move: `llm_retries` to **Timeouts** (relabelled
   "Timeouts & retries"), `semantic_recall_depth` and
   `semantic_recall_threshold` to **Context**.
3. `/models` becomes one screen with no record column: a read-only
   **summary** (the default), an explicit **Edit models** form at
   `/models/edit`, and a **Token rates** block.
4. Two backend additions to `GET /api/inference/settings`, both read-only:
   - each role, route and the Embedding card carries the rate that would price
     its calls, and where that rate comes from (3.5);
   - each provider carries `problem`, the reason it cannot send, beside the
     existing `usable` (3.2).

   Provider **health** is not added to the view. It lives in the app's
   in-memory health registry (`routes/config.py`, `get_health`), and
   `GET /api/llm-connections` already serves it on each provider. The Models
   page reads that list beside the settings view, keyed by provider id. A
   provider missing from it shows no dot.

Nothing about how inference *resolves* changes. Every write still goes
through `PUT /api/inference/settings` (`store/inference/settings.write`) with
the body it accepts today. Every refusal (`not_migrated`, `newer_format`,
`confirm_embedding`, `incapable`) keeps its server wording.

**Nothing visible today is dropped silently.** Every warning, readout,
diagnostic and explanatory paragraph on the surfaces being replaced has a
named destination. The full list is in section 5.

## 1. The Inference group in the Settings column

`ConfigView.tsx` gains a new group, `"Inference"`, placed first in the
column. Its three entries are **links, not panes**. They live in their own
table, so they can never be mistaken for a pane:

```ts
type LinkDef = { key: "inference-providers" | "inference-models" | "inference-presets";
                 group: "Inference"; label: string; to: string };
const LINKS: LinkDef[] = [ /* Providers → /providers, Models → /models, Presets → /presets */ ];
```

The column renders `LINKS` as router links, then `SECTIONS` as today. A link
owns no draft fields, so it never carries an unsaved dot. It never selects a
pane, and its `key` never takes part in `?section=` resolution.

`"models"`, `"samplers"` and `"pricing"` are removed from both `SectionId` and
`SECTIONS`. With no pane matching them, an old `?section=models` falls through
to `MOVED`.

Settings selects a pane with a query parameter, `/config?section=<id>`
(`ConfigView` reads `useSearchParams().get("section")` and resolves it through
`SECTIONS`, then `RETIRED`). There is no `/config/:section` route, and none is
added.

The retired-id lookup `RETIRED` (`Map<string, SectionId>`) is **deleted**.
All three of its entries (`semantic`, `connection`, `routing`) pointed at the
`models` pane, which is gone, so it would be left as an empty table. *(Amended
2026-10-09: the plan review found that the first draft kept it empty.)* A new
`MOVED: Map<string, string>` maps an old id to a route:

| Old id | Route |
|---|---|
| `models` | `/models` |
| `samplers` | `/presets` |
| `pricing` | `/models#rates` |
| `semantic` | `/models` |
| `connection` | `/models` |
| `routing` | `/models/edit` |

When the asked id is in `MOVED`, ConfigView calls
`navigate(target, { replace: true })` instead of `setSection`. The resolution
order is `SECTIONS`, then `MOVED`, then the default pane.

`shell/rail.ts`: the Settings row's `match` already covers `/config` and
`/models`; it gains `/providers` and `/presets`, so the rail says "Settings"
on all three pages. The page-title table gains `/presets` → "Presets".

Each of the three pages starts its context column with a small **Inference**
`ColumnSection` that holds the three sibling links (current one marked) and
"← All settings". This is the column indexing the page's neighbourhood, not a
second app navigation: the rail still answers "which page of the app".

## 2. Providers (`/providers`)

Unchanged except:

- **`/providers/new`** becomes a real route.
  - `App.tsx` declares it **before** `/providers/:id`, so `new` is never
    read as a provider id.
  - Today "+ New provider" (`startNew`, `ProvidersView.tsx:303`) sets local
    `creating` state. Creation mode is now derived from the route instead:
    the page is creating when the path is `/providers/new`. The local state
    goes.
  - The column button and the Models page's **+ Add provider** both navigate
    to `/providers/new`. `created(newId)` navigates to the new provider, as
    now.
  - **Cancel** returns to where the user came from. This is a deliberate
    change: today, Cancel leaves the list's empty state.
    - Every in-app link to `/providers/new` passes router state
      `{ returnTo: <current pathname + search + hash> }`. That covers the
      column button and the Models page's two links.
    - Cancel navigates to `returnTo` if it is an internal path (it starts with
      a single `/`) and is not `/providers/new` itself. Otherwise it goes to
      `/providers`.
    - While the route is `/providers/new`, the column's **+ New provider**
      button is shown as the current page and is disabled, so it cannot
      replace the state the form was opened with.
    - A direct load (a typed URL, a bookmark, a link from outside the app)
      carries no `returnTo`, so it lands on `/providers`.
    - A refresh keeps the router state the browser kept for that entry, so
      Cancel still returns to where the form was opened from. This is
      intended: the refresh did not change where the user came from.
    - Only the router state is ever read. The history stack is not walked.
  - Reloading `/providers/new` shows the form.
- The detail's existing **Used by** chips (`used_by`, `routes/config.py:800`;
  `chipFor` in `ProvidersView`) keep their scope rule:
  - A **campaign**-scoped use still links to that campaign's Inspector,
    unchanged.
  - A **global** role use links to `/models`.
  - A **global** route use links to `/models/edit#task-<key>` (3.3), where
    today it links to `/models/route/:key`.

## 3. Models (`/models`)

### 3.1 Read view: the summary

The page's main area, top to bottom:

1. `InferenceBanner` and `RetiredNotes`, exactly as today.
2. **Roles** — one row per role, in the order Primary, Fast, Decision,
   Embedding:

   ```
   Primary    Saltmarch AI ●  · model-large   · Story   · $3 / $15 per M (provider)
   Fast       Saltmarch AI ●  · model-small   · —       · $0.25 / $1 per M (provider)
   Decision   Same as Fast
   Embedding  Saltmarch AI ●  · embed-small            · no rate — unpriced [Set rate]
   ```

   - The provider name links to `/providers/:id`. The dot is that provider's
     `health.state` (`ok`, `error`, `unknown`) from `GET /api/llm-connections`,
     the same field the provider column reads.
   - A role whose own selection is empty shows **Same as <role>** (from
     `RoleCard.inherits`), as `GenerativeSummary` does today.
   - A configured fallback adds an indented line: "Fallback: provider · model
     · preset".
   - `problem`, `fallback_problem` and `fallback_missing` show under the row,
     in today's wording (`ModelsView.tsx`'s `Warning` and
     `droppedFallbackWords`).
   - The **Decision** row keeps today's decide note (`decision_mode` /
     `decides_natively`). It also keeps `DecisionRoutes`, a disclosure
     "Tasks answered by Decision" listing every `RouteRow` whose
     `uses === "decision"`, inherited ones included. These are listed here
     whether or not anything was overridden.
   - The **Embedding** row reads `EmbeddingCard.on`. When it is `false`, the
     row shows today's sentence ("Off — nothing is embedded, and semantic
     recall is not used.") with the card's `problem`, and no rate.
   - Each generative row keeps today's read-only `ControlsReadout`, collapsed
     behind **What this sends**.
   - The preset name links to `/presets/:id`.
   - The rate column is described in 3.5.
3. A line: **"N task overrides active"** (routes whose `use` or `preset` is
   set in this scope), linking to the Advanced section of the edit form. It is
   hidden when N is 0.
4. The actions: **Edit models** (primary), **+ Add provider** (→
   `/providers/new`), and **Provider status →** (→ `/providers`).
5. **Token rates** (3.5).

The read view is the default. Nothing on it is an input.

### 3.2 Edit view: one form for every role

**Edit models** routes to `/models/edit`. The form holds every role at once:

```
Role       Provider ▾          Model ▾                  Preset ▾
Primary    [Saltmarch AI ●]    [model-large ▾]          [Story ▾]
           + Fallback
Fast       [Saltmarch AI ●]    [model-small ▾]          [Provider defaults ▾]
           ↳ Fallback  [Realm Local ●] [local-7b ▾] [— ▾]    ✕
Decision   [Same as Fast ▾]
Embedding  [Saltmarch AI ●]    [embed-small ▾]          (no presets)

▸ Advanced: per-task overrides (2 active)
                                                       [Cancel] [Save]
```

- **Provider** is a `<select>`.
  - Its first option is "Same as <inherited role>" on Fast and Decision. On
    Primary and Embedding it is "Not set". Choosing it clears the **whole**
    selection (provider, model **and** preset) to the empty selection. That is
    how inheritance is stored today, and the row hides its model and preset
    controls while it holds that option.

    This is deliberate. Today's picker merges a provider change into the
    existing selection (`ModelsView.tsx:451-453`), so emptying the provider
    can leave behind a preset nobody can see. "Same as" means the role names
    nothing of its own, so it must leave nothing behind.

    The role's **fallback** is separate and is not touched.
  - Each provider option is labelled with its health, read as in 3.1.
  - A provider that is not `usable` stays **selectable**, as it is today
    (`ProviderModelPicker.tsx:313-317`). Its label carries the new
    `InferenceProvider.problem` ("Realm Local — cannot send: no key set").
    The row shows the same reason as a warning when it is chosen. Disabling
    such options would stop a user from adjusting a stored choice while they
    fix the key elsewhere, so they are not disabled.
  - A stored provider id the list no longer contains stays as a selected
    synthetic option, as today (`ProviderModelPicker.tsx:319-321`).
- **Model** is a `<select>`. It is filled by the picker's existing query, the
  `ask()`/`combine()` pair in `ProviderModelPicker.tsx:83-87`. That sends one
  `api.readConnectionCapabilities(provider, need)` per need and combines the
  answers, so it is unchanged and moves out of the component into a hook.
  - Role rows ask with `ROLE_NEEDS[role]`. Route pins ask with
    `routePinNeeds(row)` (`selection.ts`).
  - Options come in `<optgroup>`s: **Fits this role**, then **Unverified**.
  - A final option, **Other model id…**, reveals a text input beside the
    select. The typed id is checked exactly as today: it gets its own verdict,
    and a Test action when that verdict is unverified.
  - A stored model the list does not contain is kept as the selected option,
    labelled "<id> (not in this provider's list)". Opening the form never
    silently changes a value.
  - Changing the provider clears the model.
- **Preset** is the existing `PresetSelect`, with "Provider defaults" as its
  empty option. It is disabled while no provider is chosen, as today.
  Embedding has none.
- **+ Fallback** reveals a second Provider/Model/Preset row under the role.
  **✕** removes it by writing an empty fallback. Embedding has no fallback.
- **Diagnostics on each edit row** are the same ones the summary shows,
  taken from the saved view (`RoleCard`):
  - `card.problem` under the role's own row;
  - under an open fallback row, `droppedFallbackWords(card.fallback_missing,
    …, card.fallback_problem)`;
  - the chosen provider's `InferenceProvider.problem`, when the provider is
    not usable.

  A fallback can be dropped for missing a capability even when its provider
  is usable, so the first two are independent of the third. They describe the
  saved state. A draft that has not been saved yet is judged by the server on
  Save.
- `ControlsReadout` stays available per row, collapsed behind **What this
  sends**.
- **Test.** The test-call machinery is reused, not reimplemented:
  - it is extracted from `ProviderModelPicker` into a hook, and keeps
    `probesFor` (the probe choice, including the native-decision rules),
    `useModelTests` (a run that outlives its dialog is rejoined, never
    duplicated), and the focus restore after a redraw;
  - a **Test** button appears beside the model select exactly where one is
    offered today: for an Unverified listed model, and for a typed id whose
    verdict is unverified;
  - it opens `TestCallDialog`, which previews the call and sends nothing
    until confirmed.

### 3.3 Advanced: per-task overrides

A collapsed `<details>` under the roles. It opens on load when the URL hash
names a task.

The hash is always built as `` `#task-${encodeURIComponent(key)}` ``, which is
the encoding `ModelsView.tsx:57` and `chipFor` already apply. It is compared
against `RouteRow.key` only after the suffix is decoded by a non-throwing
helper, `taskFromHash(hash): string | null`. It wraps `decodeURIComponent` in
a try/catch and returns `null` for a malformed escape, such as `#task-%` or
`#task-%ZZ`. A `null`, or a key that matches no row, leaves the section
closed and does not scroll.
Everywhere this spec writes `/models/edit#task-<key>`, it means that encoded
form.

When the hash names a task, the section opens and that row scrolls into view. One row per `RouteRow`, showing `label` and
`hint`:

- **Use** is a `<select>` of "Role default (<default_role>)" (stored as
  `use: ""`), Primary, Fast, Decision, and "Specific model…" (`use:
  "model"`). "Specific model…" reveals the Provider/Model/**Pin preset**
  trio, which edits `row.pin`, including `pin.preset`, as today's pin
  `SelectionFields` does (`ModelsView.tsx:721-723`).
- **Preset override** is a separate control that edits `row.preset`, today's
  `PresetSelect allowClear` (`ModelsView.tsx:725-729`):
  - `""` is "Inherit (resolves to …)";
  - `PRESET_CLEAR` is "No preset (stop inheriting)".

  The two are distinct values and both are kept. A route can therefore keep
  its role's model while overriding only the preset.
- **Precedence is unchanged**, because it is the resolver's and this design
  does not touch it. The form only labels it: a route's own `use` or pin
  replaces its role's model, and its `preset` (when not `""`) replaces the
  preset its model would otherwise carry. A row whose `use` or `preset` is
  set in this scope is marked **overrides**.
- Each row keeps everything `RouteDetail` shows today:
  - `requires` and the vision capability warning (`ModelsView.tsx:616-628`);
  - `problem`;
  - the dropped-fallback explanation (`fallback_missing` /
    `fallback_problem` through `droppedFallbackWords`, :630-631);
  - the decide note (:629);
  - the resolved `ControlsReadout` (:632-633), collapsed behind **What this
    sends**.

The Advanced section shows every `RouteRow` the view returns. The current
"Advanced" toggle on the Routing column, which hides rarely-used routes, goes
away. The section is already collapsed, so it does that job.

### 3.4 Saving

**Save** builds one `InferenceWrite` holding only what changed:

- each changed role's `selection` and/or `fallback`;
- each changed route's `RouteWrite`, carrying only the fields that moved.
  **Switching a route away from "Specific model…" changes `use` only**: the
  stored `pin` is neither sent nor cleared, so switching back restores it.
  This keeps today's deliberate rule (`ModelsView.tsx:695-699`, "keeps a pin
  to come back to"). `pin` is sent only when the pin's own fields were edited
  while `use` is `"model"`.
- `roles.embedding.selection` when Embedding changed.

It sends that as one `api.putInferenceSettings` call. On success it installs
the returned view and goes back to `/models`. **Cancel** discards the draft
and **always** navigates to `/models`, as today's Cancel always returns to
the model view. It never consults history, so a direct load or a refresh of
`/models/edit` behaves the same way. There is no navigation guard for a draft, as there is none on
`/models` or in Settings today. Adding one is out of scope.

Errors:

- **Embedding confirmation**, with exactly today's two triggers
  (`ModelsView.tsx:521-541`):
  - before sending, only when the Embedding provider or model changed **and**
    both are non-empty;
  - after sending, on a 400 `confirm_embedding` refusal, which stays
    authoritative and covers every other case.

  Turning embedding off asks nothing up front. The inline confirmation and
  its **Re-embed and save**, which resends the same body with
  `confirm_embedding: true`, are today's.
- **409 `not_migrated` / `newer_format`**: the banner's text above the form,
  and the form stays filled.
- **Any other refusal**: the server's `detail` is shown above the form, in
  the server's words, and the form stays filled. No row is highlighted. The
  refusals carry no structured role or route, and guessing a row from the
  wording would tie the UI to text the server owns.

While `blocked` (`settings.newer || settings.format !== "2"`, as today) the
**Edit models** button is disabled, with the banner explaining why.

### 3.5 Token rates on the Models page

**Per-row rate.** `RoleCard` and `RouteRow` each gain:

```ts
rate: {
  source: "provider" | "table" | "none" | "native";
  entry?: PricingEntry;   // as stored: prompt_usd_per_1k, completion_usd_per_1k, ...
} | null                  // null when nothing resolves
```

`EmbeddingCard` gains the same field.

The server computes it in `store/inference/settings.py` from the
**resolved** selection (`resolves.provider`, `resolves.model`), never from a
stored choice that does not resolve. When `resolves` is null, `rate` is null
and no rate is drawn.

- `entry` is `pricing.rate_for_call(pricing.read_pricing(),
  pricing.provider_rates(), provider_id=resolves.provider,
  model=resolves.model)`. That is the one precedence function the ledger and
  `in_use.unpriced` use. It is asked the configured model as the model that
  answered, with no `requested_model`.

  That makes it the rate for **a call answered under the configured name**. A
  real ledger row can name a dated snapshot as its answering model, with the
  configured name as `requested_model`, and can then match a different
  table entry. So the page says "would be priced at", not "is charged".
- `source` is `provider` when `provider_rates()[provider_id]` has an entry
  for the model, `table` when it does not but `entry` is set, and `none` when
  `entry` is `None`.

The page converts per-1k to per-million for display. The data stays in the
ledger's own unit.

A Decision card or decide route whose `decision_mode` is `native` gets
`source: "native"` and no `entry`. A native decision row is never modelled:
`usage.py:896` returns no estimate for a row whose `decision_mode` is
`decisions.NATIVE_BACKEND`, whatever rate exists. A rate shown there would be
a number nothing uses. The row reads "native decisions: priced only if the
provider reports a cost".

The summary renders the field as follows:

- `provider`: "$a / $b per M (provider)".
- `table`: "$a / $b per M (your rates)".
- `none`: "no rate: calls unpriced", with a **Set rate** link to
  `/models?add=<encodeURIComponent(model)>#rates`. The query carries the
  model, and the fragment only scrolls, so a model id holding `/`, `?` or `#`
  survives.
- `native`: the sentence above.

The Embedding row shows only the prompt figure ("$a per M"). The entry stays
a normal `PricingEntry`; only the display differs. An embedding generates
nothing, and the ledger reads its absent completion count as zero
(`usage._completion_count`).

A rate of 0 renders through `components/cost.tsx`'s `perMillionRate`, as every cost surface does (`$0/M`), never as "none". *(Amended 2026-10-09: the first draft said "$0.00", which no cost surface prints.)* An entry the pricing code
would not use, such as a half-entry missing one side, is drawn as `none`,
because `rate_for_call` returns what `pricing.entry` accepted.

**Token rates block.** This is the existing `PricingEditor` component, moved
unchanged in behaviour under an `<h3 id="rates">Token rates</h3>` with a
one-line explanation: "Your fallback table, for models whose provider states
no price. A model's own rates on its provider come first." It keeps its own
**Edit** / **Save**, independent of Edit models.

`PricingEditor` gains an optional `addModel` prop, read from the `add` query
parameter.

- When it is set, the editor opens in edit mode with a new row prefilled
  with that model key, and focuses the row's first rate input.
- When the table already has an exact entry for that key, the editor opens
  in edit mode on that entry instead of adding a second one.

After a rate save, the page re-reads the settings view, so each row's `rate`
updates, and the `add` parameter is removed from the URL.

The pane's existing explanatory text moves with the editor, unchanged:
estimates are kept apart from reported spend, how wildcard (`prefix*`)
entries take precedence, and that an empty cache rate is not a zero.

## 4. Presets (`/presets`)

`components/SamplerPresetEditor.tsx` already has the shape this page needs:
a list, a read-only view (`open`), **Edit** (`startEdit`), **+ New preset**
(`startNew`), **Import** (`startImport`, `runImport`, `ImportReport`),
**Delete** (`remove`), a "preview on" model picker feeding `ControlsReadout`,
and `newer`-gated controls (`SamplerPresetEditor.tsx:116-419`). The new page
re-hosts it. Its behaviour does not change.

`routes/PresetsView.tsx` uses the list/detail pattern for a page that owns
the screen (CLAUDE.md: "the ledger, `routes/SheetsView.tsx`"):

- **Context column:**
  - the Inference section (1);
  - a **Presets** `ColumnSection` with **+ New preset**, **Import…**, and one
    row per preset from `api.listSamplerPresets`.
- **Main area at `/presets/:id`:** today's read-only view, unchanged. That
  covers the parameters the preset sets, "Sets nothing: every parameter is
  left at the provider's default." for an empty one, and the preview-on
  picker with `ControlsReadout`.
- **Sidebar:**
  - **Edit** and **Delete**, as today.
  - A new **Used by** list: every global place that names this preset.
    - A role's `stored.preset`, labelled with the role ("Primary").
    - A role's `fallback.preset`, labelled with the role's fallback
      ("Primary fallback").
    - A route's `preset`, labelled with the route's label.
    - A route's `pin.preset`, labelled "<route label> (pinned model)". It is computed client-side from the
    inference settings view the editor already loads. Each entry links to
    `/models` (a role) or `/models/edit#task-<key>` (a route).
- **`/presets/new` and `/presets/import`:** today's new and import forms,
  routed rather than held in local state.
- **Save** returns to the preset's view. **Cancel** returns to the view, or
  to `/presets` for a new preset.
- **`/presets` with no id** shows the first preset. With no presets at all,
  it shows today's empty state.

The implementation splits `SamplerPresetEditor.tsx` along its seam:

- the list becomes the column;
- the view, the form, the import flow and their helpers (`toDraft`,
  `toParams`, `encodeStop`/`decodeStop`, `ImportReport`) move to
  `components/presets/`, unchanged.

The parts that stay exactly as they are:

- validation;
- the `newer`/`newer_format` gating (`SamplerPresetEditor.tsx:155-160`);
- `announceModels` after a write: `createSamplerPreset` and
  `updateSamplerPreset` chain `.then(announceModels)` (`api/client.ts:2820`,
  :2822-2823);
- import reports;
- delete;
- every explanatory paragraph the pane shows today, including that a preset
  sets only the parameters it names and that a parameter the provider does
  not support is dropped.

## 5. What is removed, and where everything visible goes

Removed:

- From `ModelsView`: the Roles and Routing columns, `RoleDetail`,
  `RoleForm`, `RouteDetail` and `RouteForm`.
  - The routes `/models/role/:role` and `/models/route/:key` redirect to
    `/models` and `/models/edit#task-<key>`.
- From `ProviderModelPicker`: its radio groups, replaced by the select in
  3.2.
  - Its capability query, typed-id verdict, `probesFor`, `useModelTests`
    and focus restore are extracted and kept.
- The ConfigView panes `models`, `samplers` and `pricing`.

Where what they showed goes:

| Shown today | Where it goes |
|---|---|
| `InferenceBanner`, `RetiredNotes` | Models page top (3.1) |
| Role `problem`, fallback problems, `Warning` | Models summary rows (3.1) and edit rows (3.2) |
| Decision note, `DecisionRoutes` | Decision summary row (3.1) |
| Embedding off sentence and reason | Embedding summary row (3.1) |
| Role and route `ControlsReadout` | "What this sends" disclosures (3.1, 3.2, 3.3) |
| Route vision warning, dropped-fallback words, decide note | Advanced rows (3.3) |
| Models pane: retries happen only before streaming starts, and a fallback gets one attempt | Beside **Retries** in "Timeouts & retries" |
| Models pane: how semantic recall works, threshold tuning, and that recalled text is sent to the Embedding provider | Beside the recall fields in **Context**. The privacy sentence is kept verbatim, and its Embedding role link moves from `/models/role/embedding` to `/models/edit`. |
| Pricing pane: estimates kept apart from spend, wildcard precedence, empty cache rate is not zero | Token rates block (3.5) |
| Samplers pane: the preset notes | Presets page (4) |
| `/models` Routing column's "Advanced" toggle | Gone; the Advanced section is collapsed (3.3) |
| Settings → Models links to `/models` and `/providers` | The Inference group (1) |

## 6. Out of scope

- Per-campaign overrides (`components/inference/CampaignModels.tsx`, in the
  campaign Inspector). These are unchanged, though they reuse the new model
  select if that falls out naturally.
- The settings migration's safety backup (it archives the whole store,
  images included, reports no progress, and starts again after an
  interrupted run). This will be filed as a separate issue.
- The page not re-polling after a `failed` migration
  (`useInferenceSettings`). Same issue.

## 7. Testing

### Frontend (vitest; the list/detail rules in CLAUDE.md)

**`ModelsView.test.tsx`, rewritten.**

Summary:
- Each role shows its provider, model, preset and rate, and nothing on it is
  an input.
- Provider links point to `/providers/:id`, and the health dot comes from the
  connections list.
- An inheriting Decision shows "Same as Fast".
- Decision lists the routes whose `uses === "decision"`, including an
  inherited one.
- Embedding with `on: false` shows the off sentence, its problem, and no rate.
- Each `rate.source` gets its own wording; `0` shows as `$0/M`.
- `native` shows the native sentence, and Embedding shows only the prompt
  figure.

Edit form:
- **Edit models** opens the form.
- An unusable provider can be selected and its label carries `problem`.
- A stored provider or model that is missing from the list is kept.
- Model options are grouped. A route pin asks with `routePinNeeds`.
- "Other model id…" reveals the typed-id input along with its verdict.
- A Test button appears for exactly the unverified choices. A run already in
  flight is rejoined rather than sent again.
- Save sends only the changed roles and routes in a single PUT.
- Embedding confirmation fires up front only for a complete changed
  selection; turning it off sends with no prompt. A server
  `confirm_embedding` brings up the confirmation, and the resend carries the
  flag.
- Cancel discards the draft and lands on `/models`, including after a direct
  load of `/models/edit`.
- An edit row shows `card.problem` for a capability-refused primary. A
  fallback row shows the dropped-fallback words for both `fallback_missing`
  and `fallback_problem`.
- "Same as Fast" on a Decision that had provider, model and preset writes the
  empty selection, with no preset left behind.
- A refusal other than `confirm_embedding` is shown above the form in the
  server's words.

Advanced section:
- It opens from an encoded `#task-<key>`, including a key with a space or `#`
  in it. A malformed `#task-%ZZ` renders the page with the section closed.
- Setting Use marks the row "overrides" and sends a `RouteWrite` that has
  `use` and no `pin`.
- Pin, then Fast, then Pin again restores the earlier pin, and the payload
  never clears it.
- A route can hold both `pin.preset` and `preset`, and `PRESET_CLEAR`
  round-trips.
- A vision-unverified route shows its warning; a dropped route fallback shows
  its words.

Rates:
- **Set rate** goes to `/models?add=<encoded id>#rates` and opens the rate
  editor with that model prefilled. A model id containing `/` survives the
  trip.
- If the table already has an exact entry, that entry is edited instead.

**`PresetsView.test.tsx`.**
- Clicking a row shows the read-only view with no input, plus its Used by
  list. The list covers all four kinds: a role preset, a fallback preset, a
  route preset and a route `pin.preset`.
- **Edit** reveals the form; **+ New preset** and **Import…** open their forms
  directly.
- Delete works.
- Controls are disabled when `newer`.
- The explanatory notes render.

**`ProvidersView.test.tsx`.**
- A direct load of `/providers/new` renders the form, and "+ New provider"
  navigates there.
- Cancel returns to the `returnTo` it was opened with: from a provider, from
  `/models`, and to `/providers` on a direct load. A `returnTo` that is not
  internal is ignored. Remounting with the same history entry (a refresh)
  keeps its `returnTo`. On `/providers/new`, the column button is disabled.
- A campaign-scoped Used by chip still opens the campaign; a global route chip
  opens `/models/edit#task-<key>`.

**`ConfigView.test.tsx`.**
- The Inference group lists three links, and they navigate.
- `?section=models`, `samplers`, `pricing`, `semantic`, `connection` and
  `routing` each redirect to their route.
- Retries renders under Timeouts with its retry prose.
- The recall fields render under Context with the recall prose and the
  verbatim privacy sentence. That sentence's Embedding link points to
  `/models/edit`.
- The Token rates prose renders on the Models page, including the
  empty-cache-is-not-zero sentence.

### Backend (pytest)

`rate` on the settings view:
- A provider-stated rate gives `provider`.
- A table entry gives `table`, including a `prefix*` match.
- Neither gives `none`.
- A zero rate keeps its zeros.
- A half-entry gives `none`.
- A native Decision gives `native` with no entry.
- An Embedding that does not resolve gives `null`.
- Each case is asserted against `pricing.rate_for_call` itself, so the view
  cannot drift from the ledger's function.

`problem` on each provider:
- `null` when the provider is `usable`.
- Otherwise it is `resolve.problem`'s own sentence, and it is `null` exactly
  when `usable` is true.

Fixtures use the codebase's placeholder names only (Saltmarch, Realm, …).
