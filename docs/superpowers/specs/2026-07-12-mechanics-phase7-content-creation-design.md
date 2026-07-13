# Mechanics Phase 7 — content browsers + creation wizard

Full design for Phase 7 of the Mechanics & Dice milestone (issue #164),
superseding `2026-07-12-mechanics-phase7-content-creation-draft.md`. Depends
on Phase 1 (`2026-07-12-mechanics-phase1-modules-design.md`, landed) and
Phase 3 (`2026-07-12-mechanics-phase3-sheets-design.md`, landed). Independent
of Phases 4-6 (play integration, absorb validation, sheet display) — none of
those are prerequisites for browsing content, attaching refs to a sheet, or
spending points/XP.

## Scope

Four sub-features, all in this phase:

1. **Content browsers** — browse a resolved module's `content/<kind>/`
   merged into the existing world/campaign entity lists; instantiate a
   module template into the world or campaign as an owned copy.
2. **Attach content to sheets** — a new `ref` field type: sheet fields that
   point at world/campaign entities (known spells, inventory, disciplines).
3. **Creation wizard** — module-declared point-buy/dot-budget pools drive a
   guided sheet-creation flow, per sheet type.
4. **Advancement** — spend an XP-like resource to raise sheet fields after
   creation, at a cost that scales with the new rating.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Content customization | **Instantiation-only** — no module→world shadow/override layer | Module content is a read-only template pool; customizing means copying it in, then it's an ordinary world/campaign entity. Avoids a third overlay layer and id-collision rules across every module × world combination. |
| Ref field scope | `ref` fields may only target kinds in `entities.ENTITY_KINDS` (locations, lore, items, groups, creatures) — never characters/pcs | The stated use cases (spells, inventory, disciplines) are all entity-kind content. Characters/pcs live in a different store with different resolution code; pulling them in doubles the resolution surface for a use case ("known allies") nobody asked for yet. |
| Ref target | A ref can point at an **instantiated entity or directly at uninstantiated module content** (two address forms, disambiguated by a marker) | Codex adversarial review, round 1: restricting refs to instantiated entities only dropped the draft's actual use case (a spell list linking straight to module content) and would force instantiating every module entry a sheet merely references. Direct content refs need no instantiation and no bulk-instantiate escape hatch. |
| Creation budgets | **Per-group pools**: a sheet type's `creation.pools` maps group ids to `{budget, costs}` | Matches real priority-style chargen (primary/secondary/tertiary attribute pools), not just a single flat point total. |
| Creation budget enforcement | **Server-validated at a dedicated creation-write endpoint**, not just a UI constraint | Codex adversarial review, round 1: a client-side-only budget check is bypassable by any stale client or direct API call. The general sheet PUT stays intentionally unconstrained (GMs already freely hand-edit any field post-creation, per Phase 3) — only the wizard's write path enforces pool math. |
| Advancement pools | **Single pool per sheet type** (not per-group) | One XP-like currency is enough for advancement; per-group advancement pools would double the design surface (two different multi-pool systems) for a want nobody's stated. Multi-pool advancement is a clean future extension of the same schema shape if needed. |
| Advancement cost | **Formula, evaluated against the new rating** (`store/expressions.py` + a synthetic `new` name), constrained to a **positive integer** | Real systems (classic White Wolf, etc.) price a raise by the rating you're buying, not a flat per-step cost. Codex adversarial review, round 1: the shared expression evaluator otherwise admits negative/zero/float/bool results, which would award XP or corrupt the resource on write. |
| Advancement concurrency | `advance()` is serialized by a **per-campaign in-process lock** around its read-modify-write, with an atomic temp-file+`os.replace` write | Codex adversarial review, round 1: the read-then-write shape is a real race under a double-click or two tabs — both requests can pass the same balance check before either writes. Reuses the per-campaign in-process lock pattern already specified for Phase 4's roll proposals (same single-process app, same discipline). |
| Content browsing UI | Merge into the existing EntityEditor list/detail rail (already receives a `module` prop with unused `content`) | No new page; module templates and your own records live in the one place users already look, distinguished by a template marker and an Instantiate action instead of Edit. |
| Wizard entry point | New **"+ New with sheet…"** button beside the existing "+ New", wherever `SheetPanel` already renders | Keeps sheet-backed creation where creation already happens; gated the same way SheetPanel already is (module resolved + sheet type exists for the kind). |
| Advancement cost preview | **None** — the price only appears in the round-trip result of the spend | Previewing would require a second expression evaluator in the frontend. The store recomputes each step fresh from current values, so this is also safer than a client-predicted running total. |

## Content: read + instantiate

`store/modules.py` gains:

```python
def read_content(mid: str, kind: str, id: str) -> dict
```

Reads `content/<kind>/<id>.md` (+ its `.sheet.json` sidecar if present) and
returns `{"kind", "id", "name", "body", "keys", **entity_schema fields
present in frontmatter, "sheet_type", "fields"}` — the same shape
`_load_content` already partially builds, but with the markdown body
included (the summary list in `load_pack()["content"]` deliberately omits
it). Raises `ModuleNotFound` (bad `mid`) or a new `ContentNotFound` (bad
`kind`/`id`).

Instantiation is **not** a new cross-cutting store function. The route reads
the content payload via `read_content` and calls the same primitives manual
creation already uses:

- World: `entities.create_entity(world_root, kind, name, body, keys=...,
  fields=...)`, then `sheets.write_world(wid, mid, kind, new_id, sheet_type,
  fields)` if the content was statted.
- Campaign: `overlay.create_entity(cid, kind, name, body, keys=...,
  fields=...)`, then `sheets.write(cid, kind, new_id, sheet_type, fields)`.

`create_entity` already uniquifies the id from `name`, so instantiating the
same template twice (or a template whose name collides with an existing
record) just gets a second id — no special collision handling needed.

## `ref` field type

Added to `FIELD_TYPES`. Descriptor: `{"key": ..., "label": ..., "type":
"ref", "ref_kind": "<one of entities.ENTITY_KINDS>"}`.

- **Validation** (module load time, `_validate_field`): `ref_kind` is
  required and must be one of `entities.ENTITY_KINDS`. `ref` fields are
  *not* addressable in expressions (`numeric_names` skips them, same as
  `list`/`text`) — a spell list has no numeric meaning.
- **Storage — two address forms**, both lists of strings mixed freely in
  the same field value:
  - `"<ref_kind>:<id>"` — an instantiated world/campaign entity. Same
    colon convention `loreOwners.ts` already uses for owner refs (distinct
    from overlay's slash-separated tombstone refs; this is the same
    "clickable chip that navigates" idiom, reused, not overlay machinery).
  - `"<ref_kind>:module:<id>"` — a direct pointer at a `content/<ref_kind>/<id>.md`
    entry of the sheet's **currently resolved module** (no module id
    stored; a ref is only ever read back in the context of a resolved
    module, same as everything else in this system). This closes the round-1
    Codex finding: the draft's "known spells" use case needs to link
    straight to module content without first instantiating every entry a
    character merely knows about — instantiation stays available (and is
    still required to *customize* an entry) but is no longer mandatory just
    to *reference* one.
  - No id collision is possible between the two forms: entity ids come from
    `slugify()`, which never produces a colon, so splitting a ref string on
    `:` always yields exactly 2 segments (`ref_kind:id`, entity form) or
    exactly 3 with a literal `module` middle segment (content form) —
    unambiguous by segment count alone, even if an entity happens to be
    named (and thus slugified to) `module`.
- **Value validation** (`validate_sheet_values`): shape only — each entry
  parses as one of the two address forms with a `ref_kind` prefix matching
  the field's declared `ref_kind`. No existence check against a live store:
  `modules.validate_sheet_values` is pack-level and scope-free (no
  `cid`/`wid` to resolve against), and a dangling ref (the target was since
  deleted, or a module-content id that no longer exists in a newer pack
  version) is display-only fallout, not a write-time error — mirrors how an
  unknown lore-owner ref already just renders its raw id instead of
  erroring.
- **Resolving a ref for display** (routes/frontend, not `modules.py`): for
  the `<ref_kind>:<id>` form, look up the entity the normal
  overlay/world-read way; for the `<ref_kind>:module:<id>` form, call the
  same `modules.read_content(mid, ref_kind, id)` the content browser uses.
  Both land on the same read-only preview affordance (see Frontend), so a
  module-content chip and an instantiated-entity chip differ only in
  whether an Instantiate action is offered.

## Creation: per-group budget pools

Sheet types gain an optional `creation` block:

```json
"creation": {
  "pools": {
    "attributes": {"budget": 15, "costs": {"strength": 1, "dexterity": 1}},
    "abilities": {"budget": 10, "costs": {"melee": 1, "stealth": 2}}
  }
}
```

- Each pool key must be one of the sheet type's referenced `groups`.
- `costs` keys must be fields belonging to *that specific group* (not other
  groups, not the sheet type's own fields) — keeps pool ownership
  unambiguous and lets `assembled_fields` continue to be the single source
  of truth for what fields exist.
- `budget`: an int, or a `store/expressions.py` expression string evaluated
  with an **empty scope** (nothing exists yet pre-creation, so only
  constants/arithmetic are meaningful — e.g. no reason to reject `"10 + 5"`
  but a bare field name is always an error here).
- `costs` values: positive ints — a flat cost-per-step, deliberately not an
  expression (that's what distinguishes chargen pricing from advancement
  pricing, per the Decisions table).
- Every listed field starts at its schema floor (0 for `dots`/`track`,
  `min` or 0 for `number`) and can be raised toward its `max`; fields not
  named in any pool's `costs` stay fixed at their schema `default`.

**Validation** (load time, alongside `_validate_sheets`): pool group refs
exist in the type's `groups`; every `costs` key is a field of that group;
`budget` parses and evaluates against an empty scope; `costs` values are
positive ints. Both reference modules must still validate clean after
fleshing.

**Server-side enforcement.** The wizard's spend choices are not just a UI
guardrail — a stale client or a direct API call must not be able to write a
sheet that violates its own declared budget. `store/sheets.py` gains:

```python
def write_creation(cid: str, kind: str, eid: str, sheet_type: str,
                   spends: dict[str, dict[str, int]]) -> None
```

`spends` is `{pool_id: {field_key: chosen_value}}` — the *final* value for
each cost-listed field, not raw points spent. For every pool in the sheet
type's `creation.pools`, spend is computed over **every field the pool's
`costs` lists, not just the ones present in `spends`** — a field costed by
the pool but omitted from `spends` is *not* filled from
`sheets.default_fields` (round-2 Codex finding: a costed field's schema
`default` can sit above its floor, so silently defaulting it would let a
caller skip paying for value it still receives); it resolves to the pool's
**floor** instead — `0` for `dots`/`track`, `min` or `0` for `number`,
ignoring the field's own schema `default` entirely. A costed field's
schema `default`, if any, is simply not consulted by `write_creation` —
authoring a nonzero `default` on a field that's also pool-costed is
meaningless for chargen (it still matters for `SheetPanel`'s plain
"Create", used when no wizard runs). For every field in `costs`: resolve
`value = spends.get(pool_id, {}).get(field_key, floor)`; verify it's
within the field's declared range (`0..max` for `dots`, `min..max`/`0..max`
for `number`); accumulate `(value - floor) * costs[field]`. The pool's
accumulated total must not exceed the evaluated `budget`. `SheetError` on
any violation, naming the offending pool. Fields **not listed in any
pool's `costs`** (i.e. not part of the budgeted set at all) *do* fall back
to `sheets.default_fields`, unchanged from a budget-free creation. The
resulting full field map is then validated and written through the same
`_checked_write` helper `write()` already uses — one shared write path,
two entry points (budget-checked and not). `write_world_creation(wid, mid,
kind, eid, sheet_type, spends)` is the world-starting-sheet equivalent.

This makes the *creation-time* write budget-checked while leaving the
general `write()` (used by the plain sheet editor after creation)
intentionally unconstrained, matching Phase 3's existing posture that a
GM or player can freely hand-edit any field once a sheet exists — Phase 7
doesn't add ongoing budget policing, only a validated on-ramp.

## Advancement: single pool, formula cost

Sheet types gain an optional `advancement` block:

```json
"advancement": {"pool": "xp", "costs": {"strength": "new * 4"}}
```

- `pool` must name a `resource`-type field in the sheet type's *assembled*
  set (group or own). Its `current` is the spendable balance; `max` is
  untouched by spending. Awarding XP is not a new mechanism — it's editing
  that field in the existing sheet editor, same as any other resource.
- `costs` keys are fields anywhere in the assembled set (any group or own
  field) — unlike creation pools, advancement doesn't partition by group.
  Values are expressions evaluated against a **tentative post-raise
  scope**, not the sheet's current stored scope: build a tentative field
  map (`{**stored_fields, field_key: new}`), run the *same* derived
  computation `_compute_derived` already does over that tentative map, and
  evaluate the cost expression against `{**tentative_numeric_scope,
  **tentative_derived, "new": new}`. **This matters whenever a cost
  formula references a derived name that itself depends on the field being
  raised** (round-4 Codex finding): e.g. raising `strength` with a group
  derived `combat = strength + dexterity` and cost `"combat * 2"` must
  price using `combat` as it will read *after* the raise, not its
  pre-raise value — evaluating derived names from the stored (pre-raise)
  scope while only `new` reflects the raise would silently undercharge.
  `"new * 4"` (no derived-name dependency) is the common case and behaves
  identically either way; the tentative-recompute rule only changes
  behavior for formulas that lean on a derived name, and makes that case
  correct instead of ambiguous.
- **Validation** (load time): `pool` resolves to a `resource` field;
  `costs` keys are assembled fields of a raisable type (`number` or
  `dots` — not `resource`/`track`/`text`/`list`/`ref`); each cost
  expression parses and its names are a subset of (assembled numeric names
  ∪ group/type derived names ∪ `{"new"}`); the expression's *result type*
  is constrained to a **positive integer** — checked at load time by
  evaluating it against a representative sample scope (every numeric name,
  including `new`, set to `1`) and rejecting a non-`int` (covers `bool`,
  which is an `int` subtype in Python — `isinstance(x, bool)` must also be
  excluded) or non-positive result. A sample of `1` doesn't prove the
  formula stays positive for every rating, so the same check runs again at
  spend time (below) against the real values, closing the gap a load-time
  sample alone would leave (e.g. `"5 - new"` passes the `new=1` sample but
  goes non-positive at `new=6`).

`store/sheets.py` gains:

```python
def advance(cid: str, kind: str, eid: str, field_key: str) -> dict
```

Raises `SheetError` if: no module resolves, the sheet has no
`advancement` block, `field_key` isn't in its `costs`, the field is
already at `max`, the computed cost isn't a positive integer (same check
as load time, now against real values — belt-and-suspenders against a
formula that only misbehaves for ratings the load-time sample didn't
cover), or the pool's `current` balance is less than the computed cost
(message names both numbers, e.g. "needs 12 xp, have 8").

**Concurrency.** The whole read-compute-write is serialized by a
per-campaign in-process lock — the same discipline the (as-yet unmerged,
branch `mechanics-phase4-spec`) Phase 4 spec already establishes for roll
proposals: "a per-campaign in-process lock ... the app is single-process;
the lock guards the read-mutate-write." That file isn't present on this
branch (Phase 7 doesn't depend on Phase 4 landing first), so this phase
re-derives the same small pattern locally in `sheets.py` rather than
importing anything — a module-level `dict[str, threading.Lock]` keyed by
`cid`, same shape, independently implemented. Without it, two near-simultaneous
`advance()` calls (a double-click, two tabs) can both read the same
balance, both pass the same check, and race the same write — the second
write wins and the first spend is silently uncharged. `advance()` acquires
a lock keyed by `cid` around the full read → compute `new` → evaluate cost
→ verify → write sequence, from a module-level per-campaign lock registry.
**The registry lookup/creation is itself guarded** — a lazy
`if cid not in _locks: _locks[cid] = threading.Lock()` is a check-then-act
race (round-3 Codex finding: two threads can both observe a missing key
and each create their own lock, so the first two concurrent `advance()`
calls for a cold campaign serialize on *different* locks and both enter
the critical section together — exactly the failure this lock exists to
prevent). The registry instead exposes a single helper, e.g. `_lock_for(cid)
-> threading.Lock`, that gets-or-creates under one small module-level
registry-guard lock (held only for the instant of lookup/insert, never for
the advance itself):

```python
_registry_guard = threading.Lock()
_campaign_locks: dict[str, threading.Lock] = {}

def _lock_for(cid: str) -> threading.Lock:
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.Lock())
```

`advance()` calls `_lock_for(cid)` then acquires the returned lock for its
critical section — two threads racing a cold campaign's first `advance()`
both get the *same* `Lock` instance back, so only one proceeds at a time.

**Scope boundary (round-4 Codex finding, deliberately not fully closed):**
this lock only serializes `advance()` against *other* `advance()` calls —
it does not exclude a concurrent plain sheet `PUT` (a GM hand-edit, or
manually awarding XP into the pool field) from racing an in-flight
`advance()`. Closing that fully would mean making the per-campaign lock a
store-wide discipline across `write()`, `write_creation()`, and `advance()`
alike. This phase doesn't take that on: **no write path anywhere in this
store has cross-writer concurrency control today** — two GMs editing the
same character in different tabs, two absorb passes, two entity edits, all
already resolve last-write-wins with no lock. `advance()`'s lock exists to
close one specific, common, and otherwise-easy-to-trigger case (a user
double-clicking the same "+" button, or the same button open in two tabs)
— it is not, and isn't meant to be, a general concurrency redesign of the
sheet store. A plain edit racing an in-flight advance is the same
pre-existing risk every other campaign file already carries, not a
regression this phase introduces. The underlying file write (shared
via `_checked_write`, used by every sheet-writing path in this module)
switches from a plain `path.write_text` to a temp-file-in-the-same-directory
+ `os.replace`, so a crash mid-write can never leave a half-written sheet
file — the same atomicity posture Phase 4 specifies for its own JSON
writes, applied here as a small store-wide hardening rather than an
advance-only special case.

An in-process lock only serializes threads within one process — it does
**not** protect against two separate OS processes racing the same file
(round-2 Codex finding). That's a real gap for a generic multi-worker
FastAPI deployment, but not for this app: every launch path runs uvicorn
with its default of one worker and no `--workers` flag
(`scripts/unix/run.sh`, `scripts/windows/run.ps1` both start plain
`uvicorn grimoire.main:app --reload`, whose `--reload` supervisor still
serves requests from a single active worker process at a time), and the
Android build embeds the server directly in the app's own process
(`android/app/src/main/python/android_entry.py`). Single-process is
already this store's load-bearing deployment invariant, not a new one
Phase 7 introduces — every other in-process guard in this codebase (and
the one Phase 4 specifies for roll proposals) rests on the same fact. If
grimoire ever grows a multi-worker deployment mode, every in-process lock
in the store — not just this one — needs revisiting together; that's out
of scope here.

On success: writes `field_key: new` and `pool: {current: pool.current -
cost, max: pool.max}` in the one locked write, and returns the refreshed
sheet dict (same shape as `read`). Recomputing the cost fresh from the
*current* stored values on every call — rather than trusting a
client-supplied running total — keeps formulas that reference other
fields correct even if something else changed the sheet between clicks;
the lock is what makes that recomputation actually race-free rather than
merely race-reducing.

## Routes

```
GET  /api/modules/{mid}/content/{kind}/{id}
POST /api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}
POST /api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}
PUT  /api/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation
PUT  /api/campaigns/{cid}/sheets/{kind}/{eid}/creation
POST /api/campaigns/{cid}/sheets/{kind}/{eid}/advance
```

- Content read: 404 on unknown module/kind/id. Doubles as the resolver for
  a `<ref_kind>:module:<id>` ref's preview (no separate route needed).
- Instantiate: 404 on unknown module/content id; 400 if the content's
  `sheet_type` no longer validates against the module (pack changed
  underneath); returns `{"id": eid}`, matching the existing
  `_entity_create`/`_campaign_entity_create` convention rather than a
  fuller entity summary — the frontend's `select(id)` after reload does
  its own `readEntity` fetch, so nothing beyond the id is load-bearing
  here (round-7 final-gate finding: an earlier draft of this doc promised
  a richer `{id, name, ...}` shape; corrected to match the shipped,
  intentional, convention-matching behavior instead of the code).
- Creation write: body `{"sheet_type": ..., "spends": {...}}` →
  `sheets.write_creation`/`write_world_creation`; 400 with the `SheetError`
  message on any pool/range violation or ordinary value validation
  failure; 404 on unknown campaign-or-world/kind/entity. This is the only
  write path the `CreationWizard` uses — it never falls back to the plain
  sheet `PUT`.
- Advance: body `{"field": "<key>"}`; 400 with the `SheetError` message on
  any rejection; 404 on unknown campaign/kind/entity. Campaign-only —
  advancement is a play-time action; world sheets are seeds, not something
  that accrues XP.

None of these collide with the existing `{kind}/{eid}` / `sheets/{kind}/{eid}`
catch-alls (different path shapes — `instantiate/{mid}/{content_id}`,
`creation`, and `advance` are extra segments), so no registration-order
change is needed; they're grouped near the other module/sheet routes for
readability.

**Entity existence (added post-implementation, final-gate round 5/6):** the
creation-write and advance routes' "404 on unknown ... entity" is backed
by an explicit check — `sheets._assert_world_entity_exists`/
`_assert_campaign_entity_exists` — that the target `kind`/`eid` resolves
to a real record (via `characters`/`pcs`/`entities` or their `overlay`
equivalents) before any sheet I/O. This is deliberately scoped to these
two **new** Phase 7 routes only. The pre-existing plain sheet-write routes
(`write()`/`write_world()`, Phase 3, already on `main`) do **not** get
this check — a PUT to `/sheets/{kind}/{eid}` for a nonexistent entity can
still create an orphaned sheet file today, exactly as it could before
Phase 7. Closing that older gap too was explicitly decided out of scope
(user call): fixing it only for the new routes leaves an asymmetry between
old and new sheet-write paths, but retrofitting the older, already-shipped
routes is a larger, separate change this phase doesn't take on.

Models stay pydantic v1/v2-agnostic, dumped via `routes._dump`.

## Frontend

**Types** (`api/client.ts`): `ModuleField` gains `ref_kind?: string`.
`ModuleSheetType` gains `creation?: {pools: Record<string, {budget: number
| string; costs: Record<string, number>}>}` and `advancement?: {pool:
string; costs: Record<string, string>}`. Both already flow through
unchanged from `GET /api/modules/{mid}` (raw `load_pack()` dict). New
client fns: `readModuleContent`, `instantiateContent` (scope-branching like
`getSheet`/`putSheet`), `putSheetCreation` (scope-branching to the
`.../creation` routes), `advanceSheet`.

**EntityEditor**: `module.content` (already fetched, currently unrendered)
merges into the existing rail, filtered to the current `kind`, marked
visually as templates (distinct row styling, no delete affordance).
Selecting one calls `readModuleContent` and shows a read-only detail view
(name/body rendered the same as any entity) with an **Instantiate** button
in place of Edit. Instantiating calls the new route, reloads the list, and
selects the newly created record — same rhythm as a normal create. This
same read-only content view is what a `<ref_kind>:module:<id>` ref chip
opens (see `SheetEditor` below) — one preview affordance for both the
browser and a ref reference.

**`CreationWizard`** (new component): opened via a **"+ New with sheet…"**
button rendered beside the existing "+ New" wherever `SheetPanel` already
renders (`EntityEditor`, `CharacterEditor`, `PCEditor`), gated identically
("module resolved AND ≥1 sheet type targets this kind"). Flow: the normal
create form (name/body/etc.) → Save → sheet-type select (reusing
`SheetPanel`'s existing type-filter-by-kind logic) → if the chosen type has
`creation.pools`, one stepper group per pool (remaining budget shown,
spend blocked from exceeding it, under-spend allowed) → `putSheetCreation`
with the chosen per-pool values (the budget-enforcing `.../creation`
route, never the plain sheet `PUT`). A type with no `creation` block still
goes through `putSheetCreation` with an empty `spends` map (server fills
schema defaults) rather than falling back to `SheetPanel`'s plain-`PUT`
path — one write path for everything the wizard creates, so there's no
second code path to keep in sync.

**`SheetEditor`**: three additions —
- `ref` field widget. View: chips — a `<ref_kind>:<id>` chip is a button
  that fires a new `onOpenRef?: (kind, id) => void` callback threaded from
  the parent exactly like `onOpenOwner` already is; a `<ref_kind>:module:<id>`
  chip instead opens the module-content preview (a small modal reusing the
  same read-only rendering as the EntityEditor content view, with its own
  Instantiate action). Edit: a checkbox picker with two groups — "In your
  world/campaign" (existing entities of `ref_kind`, visually identical to
  `EntityEditor`'s lore-owner picker) and "From `<module name>`" (the
  module's `content/<ref_kind>/` entries) — picking from either group adds
  the corresponding address form to the field's value list.
- Advancement "+" button next to each field listed in the sheet type's
  `advancement.costs`, visible only when the sheet has an `advancement`
  block. Click → `advanceSheet` → on success, refetch (fresh fields +
  derived); on failure, the `SheetError` message renders in the existing
  `.banner`. No client-side cost prediction (see Decisions).

## Reference module fleshing

Both `d20-basic` and `pool-basic` gain, on at least one sheet type each:
one `ref` field (e.g. known spells/gear pointing at `items` or `lore`), a
`creation.pools` block covering at least two groups, and an `advancement`
block with at least two cost-listed fields. Packs stay minimal and must
keep validating clean after the additions.

## Testing

- **`modules.py`**: acceptance + one rejection case per new validation rule
  — bad `ref_kind`, pool referencing an unknown group, a pool's `costs` key
  belonging to a different group, unparseable/non-empty-scope `creation`
  budget, non-positive pool cost, `advancement.pool` not a resource field,
  `advancement.costs` key of a non-raisable type, advancement cost
  expression referencing a name outside (assembled ∪ derived ∪ `new`),
  advancement cost expression that samples positive at `new=1` but is
  rejected (zero/negative/non-int/bool result). Both reference packs
  validate clean.
- **`sheets.py`** (`test_sheets_store.py`): `write_creation`/
  `write_world_creation` happy path; over-budget pool rejected; a field
  outside its declared range rejected even under budget; a `spends` field
  not belonging to its pool rejected; empty `spends` on a type with no
  `creation` block falls through to schema defaults; **a pool-costed field
  with a nonzero schema `default`, omitted from `spends`, resolves to the
  budget floor (`0`), not its schema default** (the round-2 regression
  test — proves the omission loophole stays closed). `advance()` happy
  path (balance debited, field raised, derived recomputed); insufficient
  balance; field already at max; no `advancement` block; unknown
  `field_key`; a cost formula that evaluates non-positive against real
  values is rejected even though it validated at load time; cost
  recomputed correctly against *current* values on a second call (not a
  stale client-supplied total); **two concurrent `advance()` calls against
  the same balance — only one succeeds, the loser sees an accurate
  insufficient-balance error, not a lost update** (exercises the
  per-campaign lock directly, e.g. via threads or by asserting the lock is
  held across the read-write span); **the same test repeated as the very
  first `advance()` call ever made for that campaign**, so it also
  exercises `_lock_for`'s cold-registry path, not just contention on an
  already-created lock.
- **Routes**: content read 404s (incl. resolving a `<ref_kind>:module:<id>`
  ref); instantiate round-trip for both world and campaign scope (entity
  created, sheet seeded when statted, 400 on a pack-changed-underneath
  mismatch); creation-write 400s for pool/range violations, world and
  campaign; advance 400/404 mapping; ordering vs the `{kind}/{eid}`
  catch-alls.
- **Frontend (vitest)**: EntityEditor shows template rows merged into the
  rail, Instantiate creates+selects a real record; `CreationWizard`
  end-to-end (form → type pick → pool spend, under/over-budget) and its
  no-`creation`-block fallback (still calls `putSheetCreation`); `SheetEditor`
  ref widget view/edit round-trip for both address forms incl. `onOpenRef`
  navigation and the module-content preview modal; advancement "+" button
  success and rejection paths.
- **End state**: browse a module's content merged into an Items list;
  instantiate one into a campaign, and separately attach an *uninstantiated*
  module item directly to a character's `ref` field via the sheet editor,
  confirming both chip forms render and navigate/preview correctly; create
  a new character through the wizard, spend a creation pool partially,
  save, and confirm a hand-crafted over-budget request to the creation
  route is rejected; advance one of its fields, confirming the xp pool
  debits and the field's derived values update.

## Out of scope (this phase)

Module→world content shadow/override (instantiation-only, see Decisions);
`ref` fields targeting characters/pcs; multi-pool advancement; per-field
advancement-cost preview before spending; bulk/batch instantiation (direct
module-content refs remove most of the pressure for this — a sheet can
reference module content without instantiating it first); a standalone
content-browser page (merged into existing lists instead); narrated-event-
driven advancement (absorb proposing an XP spend is Phase 5+ territory,
not this phase — this phase is a manual/UI-only spend action); budget
enforcement on the general sheet `PUT` after creation (intentionally
unconstrained, per Phase 3's existing posture); store-wide sheet-write
concurrency control (the `advance()` lock guards only advance-vs-advance
races, per its Scope boundary note above — a plain edit racing an
in-flight advance is pre-existing last-write-wins behavior, same as every
other write in this store).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
