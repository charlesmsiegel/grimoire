# Mechanics Phase 8 — module authoring UI

Full design for Phase 8 of the Mechanics & Dice milestone (issue #829),
superseding `2026-07-12-mechanics-phase8-authoring-ui-draft.md`. The last
phase: in-app scaffold/edit of user-library modules across every pack file —
manifest, field groups, sheet types (incl. creation/advancement), checks,
rules docs, statted content, layout, and theme — plus module duplication and
zip export/import. Depends on Phases 1, 3, 6, 7 (all landed); every format
the editor touches is stable.

## Decisions (settled 2026-07-13)

| Decision | Choice | Why |
|---|---|---|
| Scope | **Full draft scope**: every pack file editable in-app, plus duplicate and zip export/import | User call. One phase finishes the milestone; statted content and display files are in, not deferred. |
| Save model | **Reject invalid saves** — every write stages the whole pack, re-validates, and refuses to land anything that would introduce pack `errors` | `resolve()` refuses packs with `errors`, so an invalid save silently disables mechanics for every bound campaign. Draft/publish machinery is a whole second lifecycle for no stated need; allow-and-warn breaks live campaigns on a typo. The on-disk module is therefore *always* valid. `display_errors` (cosmetic) never block a save — the Phase 6 fatal/cosmetic split is preserved. |
| Mid-campaign schema edits | **Rename-aware migration**: renames rewrite existing world/campaign sheets; removals flag-invalid as today | Edits are gentler than module switches (the Phase 1 flag-invalid call was for switches). Renaming `strength` → `might` must not strand every sheet for hand-fixing; removing a field *should* stay visible (values would be lost silently otherwise). |
| Rename mechanism | **Explicit rename operations**, not diff inference | The server cannot reliably tell "renamed" from "deleted A, added B" by diffing a submitted document; an explicit op (`rename group X → Y`) makes the fan-out (internal refs + sheet migration) deterministic. |
| API shape | **Section PUTs + rename ops** | Per-record routes match the list/detail forms (save one group, one check); each save still validates the *whole* staged pack, so cross-file invariants hold. Renames get their own transactional endpoints. A whole-pack PUT ships everything on every save and still needs out-of-band rename declarations; a pure operation API is a much larger route surface where most ops are just "replace this subtree". |
| Layout/theme editing | **Layout: validated JSON text + live preview. Theme: form controls + the same preview** | A drag/drop arranger for arbitrary nested node trees is a project of its own, for an optional cosmetic file. `theme.json` is a flat token whitelist — real form controls (color inputs, enum selects) are cheap and better than JSON. The preview renders the existing `SheetLayout` against a sample sheet, so there is exactly one rendering path. |
| Impact preview | **`dry_run` on every writer; breaking edits confirm with affected counts** | User call. The same staged validation that gates saves also computes "used by 3 sheet types; 12 sheets migrate; 4 become invalid" — one flag powers both live inline validation and the confirm step. |
| Sharing | **Zip export (any module) + zip import (into the user library), validated-or-rejected** | Modules are pure data packs — sharing never ships code. stdlib `zipfile`, raw-bytes upload (no new multipart dependency, per the Android base-deps rule). An invalid or malicious zip never lands in the library. |
| Duplicate | `duplicate_module(mid, name) -> new_mid` copies any pack into the user library under a **new** slug | The styles precedent (`duplicate_style` + immutable builtins). Builtins stay read-only; Duplicate is the customize path. Same-id shadow copies (user pack shadowing a builtin id) remain a filesystem-only trick, not offered in the UI — a shadow copy silently retargets every campaign bound to the builtin. |
| Expression rewriting | Rename ops rewrite **scope-resolved** expressions by **word-boundary text replacement**, then the whole-pack validation re-parses everything | Field keys are unique only within an assembled sheet type (Phase 1), so two disjoint groups may both define `strength` — a blind global rewrite would corrupt the other group's expressions. Each expression's scope is resolved first (see Rename operations); only expressions whose scope binds the name to the renamed definition are rewritten. Within a selected expression, `\bold\b` can only match a `Name` (the language has no strings, attributes, or comments; function names and keywords are barred as field keys). Text replacement preserves the author's formatting; `ast.unparse` would reformat every expression and requires Python ≥ 3.9 (Android runtime not guaranteed). The staged validation gate re-parses and re-scopes every expression afterwards, so a bad rewrite cannot land. (Codex adversarial round 1.) |
| Transactional model | **Whole-directory swap + journal + idempotent recovery**; sheet-migrating renames take every campaign lock *before* the swap | Per-file `os.replace` can crash mid-swap and leave a cross-file-invalid pack on disk permanently (`resolve()` ⇒ `None`, mechanics off for every bound campaign); publishing the schema before locking lets a stale client write race migration and silently drop the renamed key via `_checked_write`'s unknown-key filter. (Codex adversarial round 1.) |

## Backend: `store/module_edit.py`

New module (pure stdlib, pydantic-free, filesystem via the same
`modules.user_dir()`/`pack_root()` resolution — Android-safe like
`modules.py`/`expressions.py`). `modules.py` keeps loading/validation;
`module_edit.py` owns mutation. Every writer:

- raises `ModuleError` when the target resolves to a builtin (only
  user-library packs are editable — same posture as `delete_module`);
- serializes on a **single global module-edit lock** — one
  `threading.Lock` for the whole store, not per-module (Codex adversarial
  round 2: per-module locks let edit A's start-of-edit recovery scan
  classify edit B's in-flight journal as crash debris and reclaim it mid-
  swap, orphaning B's module; module edits are rare and human-paced, so
  global serialization costs nothing and makes
  staging-cleanup/journal/swap/migration/recovery mutually exclusive by
  construction). Same single-process caveats as Phase 7's advance lock;
- follows the **stage → validate → swap** primitive below. Create,
  duplicate, import, **and the existing `delete_module`** all run under
  the same global lock, recovery-first (Codex adversarial round 3: an
  unserialized `shutil.rmtree` racing a staged edit can remove the live
  dir mid-swap, after which recovery would resurrect the deleted module
  from staging — `delete_module` moves into, or is wrapped by,
  `module_edit`'s locked path).

### Stage → validate → swap

1. **Stage**: copy the live pack directory to
   `<GRIMOIRE_HOME>/.module-staging/<nonce>/<mid>/` — outside `modules/`
   so the registry `_scan` can never pick a staging copy up as a real
   module, same filesystem so `os.replace` stays atomic. Leftover staging
   dirs (crash debris) are deleted opportunistically on the next edit.
2. **Apply** the edit to the staged copy (JSON subtree replacement, file
   add/remove/rename, reference rewriting for rename ops).
3. **Validate**: run the pack loader against the staged root.
   `modules.load_pack(mid)` refactors into a thin wrapper over
   `load_pack_at(root, mid)` (pure refactor — same output, root made
   explicit) so staging validates through the *identical* code path that
   `resolve()` trusts. Non-empty `errors` ⇒ the edit is rejected with
   those messages, staging is deleted, the live pack is untouched.
   `display_errors` never reject.
4. **Swap — whole directory, never per file** (Codex adversarial
   round 1: per-file replacement can crash between two mutually
   dependent files, e.g. after `sheets.json` but before `checks.json`,
   leaving a cross-file-invalid pack that `resolve()` permanently
   refuses). The staged copy is already a complete pack, so the swap is
   two directory renames: write a **journal** file
   (`<GRIMOIRE_HOME>/.module-staging/<nonce>.journal.json` — per-nonce,
   so concurrent edits of *different* modules never share one; contents:
   mid, nonce, phase, and — for sheet-migrating renames — the pending
   migration op), then `os.rename(live, trash)` and
   `os.rename(staging, live)` (trash also lives under
   `.module-staging/<nonce>/`; same filesystem, both renames atomic).
   Readers therefore see the complete old pack or the complete new one —
   never a mix. The only crash window leaves the live dir briefly absent
   with journal + both complete copies on disk.
5. Run sheet migration if the op requires it (see Sheet migration), then
   delete the journal, trash, and staging remnants. The journal outlives
   the swap exactly until migration completes.

**Recovery**: at app startup (in the lifespan hook, before requests are
served) and at the start of any `module_edit` operation — always under
the global module-edit lock — leftover journals are replayed
idempotently. A journal is only ever written *after* staging validated,
so the cases are exact: live dir present and staging present ⇒ the
renames never started — discard staging **and its pending migration**,
clear the journal (the edit is simply lost; the user retries; stored
sheets stay byte-identical — Codex adversarial round 2: replaying a
migration whose schema was discarded would rename keys under the *old*
schema and corrupt every affected sheet). Live dir missing ⇒ crash
between the two renames — rename staging into place, delete trash, run
the pending migration. Live dir present with leftover trash ⇒ crash
after the swap — delete trash, run the pending migration. Migration
replays only when the journal's phase proves the new pack was (or is
now) published. Between a crash and recovery the module reads as
missing (`resolve()` ⇒ `None` with the existing missing-module
warning) — degraded but self-healing, never permanent.

### Section writers

One staged edit each; "upsert" = create-or-replace keyed by id:

- `set_manifest(mid, name, description, version, dice, notes)` — `notes`
  is the `module.md` body (authoring notes).
- `upsert_group(mid, gid, group)` / `delete_group(mid, gid)`
- `upsert_sheet_type(mid, tid, sheet_type)` / `delete_sheet_type(mid, tid)`
  — the full type def incl. `creation`/`advancement` blocks.
- `upsert_check(mid, check_id, check)` / `delete_check(mid, check_id)`;
  `set_check_defaults(mid, defaults)` for the `_defaults` block.
- `upsert_rule(mid, slug, flags, body)` / `delete_rule(mid, slug)`.
- `upsert_content(mid, kind, cid_, name, body, keys, fields, sheet)` /
  `delete_content(mid, kind, cid_)` — `sheet` optional
  `{sheet_type, fields}`, written/removed as the `.sheet.json` sidecar.
- `set_layout(mid, layout)` / `set_theme(mid, theme)` — whole-file
  replacement of the raw objects (they are single documents, not record
  collections). Layout/theme problems are `display_errors`, so these
  writers cannot be rejected by *their own* content — but they still run
  the full gate (a layout write must not land if the pack is somehow
  otherwise broken on disk).

**Delete semantics — cascade cosmetic, reject fatal.** Deleting something
still referenced by a *fatal* consumer (a group in a sheet type's `groups`
or a check's `requires`; a field/derived name in a derived expression,
check roll, creation pool, or advancement cost; a rules doc in a check's
`rules`) fails the staged validation and the rejection names the referee —
the user unhooks the reference first. *Cosmetic* references cascade:
deleting a field/derived/group also prunes the staged `layout.json` of
nodes referencing it (entries removed from `fields`/`derived` arrays,
`group` nodes dropped, emptied containers dropped, fragments included) so
a schema delete never leaves display damage behind.

### Rename operations

`rename(mid, kind, address, to)`, one staged transaction each. `to` must
be a valid slug and not collide in its namespace (staged validation
catches collisions; the op also pre-checks for a clean error message).
Address forms:

| kind | address | pack rewrites | sheet migration |
|---|---|---|---|
| `group` | `{from}` | `groups` key; every sheet type's `groups` list; `creation.pools` keys; check `requires`; layout `group` nodes (incl. fragments) | none — sheet files store field keys, not group ids |
| `field` | `{owner, from}` where `owner` is `{"group": gid}` or `{"sheet_type": tid}` | field `key`; **scope-bound** expressions and cost keys (see below), incl. the implicit `<key>_max` name for `resource` fields; layout `fields` entries of composing sheet types; `fields` keys in content stat sidecars of composing sheet types (sidecars are pack files — rewritten in staging, where validation checks them) | rewrite the `fields` key in every stored sheet whose sheet type includes the field |
| `derived` | `{owner, from}` | the `derived` map key; scope-bound expressions naming it; layout `derived` entries of composing sheet types | none — derived values are computed, never stored |
| `sheet_type` | `{from}` | `sheet_types` key; rules frontmatter `sheet_types` flags; layout `sheet_types` key; content sidecar `sheet_type` values (checks reference groups, never sheet types — nothing to rewrite there) | rewrite the `sheet_type` value in every stored sheet of that type |
| `check` | `{from}` | `checks` key | none — check ids are not persisted outside the pack |
| `rule` | `{from}` | file rename `rules/<from>.md` → `rules/<to>.md`; check `rules` lists | none |
| `content` | `{kind, from}` | file(+sidecar) rename under `content/<kind>/`; `<kind>:module:<from>` entries in other content entries' stat-sidecar `ref` values (pack files, rewritten in staging) | rewrite `<kind>:module:<from>` entries in every stored `ref` field value |

**Scope-bound rewriting** (Codex adversarial round 1: field keys are
unique only within an *assembled sheet type*, so two disjoint groups may
both define `strength` — a blind global rewrite would corrupt or falsely
reject expressions belonging to the other group). A rename rewrites an
expression or cost key only when the renamed definition is what that
name *binds to* in the expression's own scope:

- a group's `derived` → its own group's fields;
- a sheet type's `derived`, `advancement.costs` (keys and expressions) →
  that type's assembled set, so only types composing the renamed
  definition's owner group (or owning the field directly) are touched;
- `creation.pools[gid].costs` keys → that pool's group only;
- a check's `roll` placeholders → the union of its `requires` groups
  (plus the ambient `difficulty`/`modifier` names) — rewritten only when
  the renamed field's owner group is in `requires`. (If two `requires`
  groups define the same key the name was already ambiguous; the rewrite
  applies and staged validation adjudicates the result.)

Within a selected expression the mechanism is word-boundary text
replacement (see Decisions), after which the staged validation re-parses
and re-scopes everything — a rewrite that somehow produced garbage
rejects the whole op.

**Shared layout fragments** need one extra rule (Codex adversarial
round 2): a fragment `use`d by sheet types both inside and outside the
rename's scope cannot be rewritten globally — two disjoint types can
each define a same-spelled field, and the shared fragment's
`fields: ["strength"]` binds to a *different* definition under each
type. When a rename (or cascade prune) would alter a fragment whose
users straddle the scope boundary, the op **specializes over the
transitive `use` graph** (fragments nest — Phase 6): clone the affected
fragment *and every fragment on a `use` path from an in-scope sheet
type's tree down to it*, rewrite/prune the clones, and repoint the
in-scope roots (and the cloned ancestors' `use` nodes) to the clones;
out-of-scope users keep the originals untouched. Fragments reachable
only from in-scope types are rewritten in place, no cloning.

**Reserved contextual names**: the reserved-key set extends to the
ambient expression names — `difficulty`, `modifier` (check scope) and
`new` (advancement scope) — alongside the existing function-name and
`<key>_max` rules, applied to **field keys and derived names alike**
(Codex adversarial round 2: a *derived* named `new` is silently
shadowed at advancement time by the proposed value, and
`difficulty`/`modifier` are overwritten in check resolution — the
round-1 fix covered only `_validate_field`, leaving `_validate_derived`
open to the same collision via ordinary saves, not just renames).
Neither built-in ships such a name, so no existing pack breaks.

### Sheet migration

For the sheet-migrating rename kinds (`field`, `sheet_type`, `content`),
ordering is **lock → swap → migrate → release** (Codex adversarial
round 1: swapping the pack *before* excluding sheet writers opens a
window where a stale client PUT against a not-yet-migrated file passes
CAS, gets its now-unknown renamed key silently filtered by
`_checked_write`, and the value is gone before migration arrives):

- Before the directory swap, the op enumerates all campaigns and acquires
  `sheets.lock_for(cid)` for **every** campaign, in sorted-cid order (a
  fixed order so two concurrent multi-lock holders cannot deadlock) —
  the lock-everything-first discipline from the Phase 5 rebind fix,
  where locking only a pre-enumerated "bound" subset proved stale.
  Whether each campaign actually resolves to this module is re-checked
  under its lock; non-matching campaigns' sheets are left untouched (but
  their locks are held for the swap's duration — cheap, and simpler than
  a correct-but-racy subset).
- **World sheets have no lock today** (`write_world` locks nothing).
  `sheets.py` gains a world-scope lock from the same `_lock_for` registry
  (keyed `"world:<wid>"`), taken by **every world-sheet mutator** —
  `write_world`, `write_world_creation`, `delete_world`, the world
  instantiate path, and campaign-creation **seeding** while it copies
  world sheets — and by migration (Codex adversarial round 2: locking
  only `write_world`/`delete_world` left the creation route and seeding
  free to publish an old-schema sheet after migration passed).
- **One global lock order, acquired up front** (Codex adversarial
  round 4: acquiring a late-discovered campaign's lock *while already
  holding* higher-sorted locks deadlocks against any other multi-lock
  holder, e.g. the world-rebind route). The op acquires every campaign
  lock in sorted-cid order, then every world lock in sorted-wid order —
  and any multi-lock holder in the codebase (this op, the world-rebind
  route) must follow the same campaigns-then-worlds sorted discipline.
  After acquiring, it **re-enumerates**: if a campaign appeared since
  enumeration, it releases *everything* and reacquires the full union in
  sorted order, repeating until the enumeration is stable under the
  locks (campaign creation is rare — this converges immediately). Locks
  never grow while held.
- **Campaigns created after the stable acquisition** cannot escape
  either: seeding copies world sheets under the world-scope lock, which
  this op holds until migration completes — so any such campaign blocks
  and then seeds from already-migrated world sheets.
- The pending migration (op kind + address + `to`) is recorded in the
  journal before the swap; migration then rewrites every affected file;
  the journal is cleared only after migration completes. A crash
  mid-migration is therefore resumed by recovery (see Swap/Recovery), and
  the rewrite is **idempotent** — rename the key if the old one is
  present, skip if already renamed — so replaying is safe. A stored
  sheet holding **both** keys (reachable: removals preserve orphaned
  values, so an earlier-removed `might` can sit beside `strength` when
  `strength → might` is requested) is a value collision neither
  overwrite nor skip resolves losslessly — the pre-swap scan detects it
  and **rejects the rename**, listing the affected sheet paths so the
  user resolves the orphaned values first (Codex adversarial round 4).
- Scope: every `<world>/sheets/<mid>/<kind>--<id>.json` (worlds hold
  starting sheets for a module regardless of binding) and the campaign
  sheets of every campaign that resolved to `mid` under its lock. For
  `field` renames only sheets whose `sheet_type` carries the field are
  touched; `content` renames touch only sheets holding a matching ref
  value.
- Each migrated file is rewritten atomically (temp + `os.replace`, the
  existing `_checked_write` posture) with its `gen` bumped, so any client
  CAS write built against the pre-rename sheet fails with the existing
  409 conflict instead of resurrecting the old key.
- Gen bumps only reject writers that carry a CAS snapshot for an
  *existing* sheet, which leaves two stale-write holes (Codex adversarial
  round 3): world sheet writes have no CAS today, and a create-new write
  (`expected gen = None`) sails through — in both cases
  `_checked_write`'s silent unknown-key filter then drops the stale
  payload's renamed key, silently losing the migrated value. Two
  closures: **world sheet writes adopt the same mandatory gen CAS as
  campaign writes** (Phase 5 parity — `write_world` is currently plain),
  and **an unknown field key in a submitted sheet payload becomes a
  validation error (`SheetError` → 400) instead of a silent filter** —
  every writer's payload is either schema-correct or visibly rejected,
  including creates. **World sheet deletion requires `expected_gen`
  too** (Codex adversarial round 4: campaign delete already carries CAS
  from Phase 5; a pre-rename world DELETE queued on the world lock would
  otherwise unlink the freshly migrated sheet) — full world/campaign CAS
  parity: write, create, and delete alike. Migration's own rewrites are
  built from known keys, and the Phase 5 absorb path submits existing
  keys under its lock, so neither regresses.

A sheet file that fails to parse is skipped and reported in the op result
(`{"migrated": N, "skipped": [paths]}`), never blocks the rename. Scene
audit baselines need no explicit clearing: `audit.schema_stamp` hashes
`sheets.json` content+mtime, so any schema edit already invalidates
baselines per-entry, exactly as a rebind does. Removals (delete field /
delete sheet type) migrate nothing: affected sheets flag invalid on their
next read via the existing `validate_sheet_values` path, per the
Decisions table.

### Dry-run and impact

Every writer and rename op takes `dry_run: bool`. Dry-run performs the
full stage + validate (steps 1–3) and returns without swapping:

```json
{"ok": true, "errors": [], "display_errors": [],
 "impact": {"sheet_types": ["warden", "medium"],
            "sheets_migrated": 12, "sheets_newly_invalid": 4,
            "dangling_refs": 0}}
```

`impact` is computed only for edits that can touch stored sheets
(`sheets.json` writers, `field`/`sheet_type`/`content` renames and
deletes): `sheet_types` = types composing the edited group / containing
the edited field; `sheets_migrated` = files a rename would rewrite;
`sheets_newly_invalid` = stored sheets that validate against the live
schema but fail against the staged one; `dangling_refs` = `ref` entries
that would point at removed content (informational — ref validation is
shape-only, dangling refs are display-only fallout, per Phase 7).

The newly-invalid scan must judge a sheet **exactly as a read would**
(Codex adversarial rounds 1–2: `validate_sheet_values` alone misses
sheet-type existence and kind mismatches, and `_validate_instance`
alone misses derived-evaluation failures a read reports — e.g. a new
derived `10 // strength` passes the sample-defaults check but errors on
a stored sheet whose `strength` is 0). The full read-time judgment —
sheet type exists, targets the file's kind, value validation, **and
derived computation against the sheet's stored values** — factors into
a helper reusable against an arbitrary pack dict, and the impact scan
calls that against the staged pack — a full scan over stored sheets,
fine for a local single-user store. The dangling-ref count scans the
same stored sheets **plus every content stat sidecar** (sidecars carry
`ref` values too and are named in the rename fan-out; a
content-to-content ref going dangling must surface in the confirm).

Non-dry-run responses include the same `impact` block, recomputed at
save. The counts are **advisory**: a sheet written between preview and
confirm can shift them, and this design deliberately adds no
preview-digest/409 machinery — single-user app, same last-write-wins
posture as every other store surface (noted, Codex adversarial round 1).
The frontend uses dry-run for debounced live validation and shows the
confirm step when `impact` reports migrations, new invalidations, or
dangling refs.

For `sheets.json` dry-runs the response also carries a **sample
computation**: per sheet type, the assembled field defs with schema
defaults and every derived value evaluated against those defaults — the
draft's "evaluate against a sample sheet", giving expression authors real
numbers, not just "parses".

### Duplicate, export, import

All three id-claiming operations (create, duplicate, import) allocate
through one helper — slugify, reject the empty slug, **reserve `none`**
(it is the campaign binding's mechanics-off sentinel — a module literally
id'd `none` could never be bound), dedupe against builtin *and* user ids
— and publish **via staging + a single `os.rename` into `user_dir()`**
under the global module-edit lock, so a crash or I/O failure never
leaves a partial pack occupying a claimed id (Codex adversarial round 2:
duplicate previously copied file-by-file straight into the library).

- `duplicate_module(mid, name) -> new_mid` — copy the pack dir (builtin
  or user source) to staging, then publish as above. Content is copied
  as-is, valid or not — duplicating is also how you take a copy of a
  misbehaving pack to fix it. No binding changes.
- `export_module(mid) -> bytes` — stdlib `zipfile` of the pack dir, one
  top-level directory named `<mid>/`. Any module, builtins included
  (exporting a builtin is how you share a tweak-base).
- `import_module(path) -> new_mid` — operates on a zip already streamed
  to a temp file (see Routes; the store never sees an in-memory blob).
  Archive checks before any extraction: member count cap (2000) and
  **cumulative uncompressed** size cap (64 MB, summed over
  `ZipInfo.file_size` — the compressed transfer cap alone doesn't bound a
  zip bomb); exactly one top-level directory; every entry a plain file
  (no symlink external attributes) whose normalized path stays inside it
  (no absolute paths, no `..`); no two entries whose normalized paths
  collide **case-insensitively** (this store runs on Windows — two
  entries differing only in case would silently overwrite). Extract to
  staging, allocate the id from the top-level dir name through the
  shared helper above (safe-slug enforced — a raw zip dir name is not
  trusted as an id; `none` reserved; deduped), validate via
  `load_pack_at` — non-empty `errors` rejects the import with the
  messages (an invalid pack never lands, consistent with the save
  model), then publish via the single-rename path.

## Routes

All in `routes.py`; bodies are plain v1/v2-agnostic pydantic models
dumped via `routes._dump`. Builtin targets ⇒ 400 with the `ModuleError`
message; unknown ids ⇒ 404. Every *editing* route (manifest, groups,
sheet-types, checks, check-defaults, rules, content, layout, theme,
rename) accepts `dry_run` (body flag) and returns the dry-run/impact
shape above; duplicate/export/import have no dry-run (nothing staged to
preview).

The import route rejects an over-limit `Content-Length` up front (413)
and reads the body via `request.stream()` chunk-by-chunk into a temp
file, aborting the moment the running total passes the transfer cap
(16 MB) — never `await request.body()` into memory (Codex adversarial
round 1: the repo has no request-size middleware, so a whole-body read
buffers an arbitrarily large upload before any check runs). No
multipart, no new dependency, Android-installable base deps unchanged.

```
POST   /api/modules/{mid}/duplicate            {name} → {"id": new_mid}
PUT    /api/modules/{mid}/manifest             {name, description, version, dice, notes, dry_run}
PUT    /api/modules/{mid}/groups/{gid}         {group, dry_run}
DELETE /api/modules/{mid}/groups/{gid}         (?dry_run=1)
PUT    /api/modules/{mid}/sheet-types/{tid}    {sheet_type, dry_run}
DELETE /api/modules/{mid}/sheet-types/{tid}
PUT    /api/modules/{mid}/checks/{check_id}    {check, dry_run}
DELETE /api/modules/{mid}/checks/{check_id}
PUT    /api/modules/{mid}/check-defaults       {defaults, dry_run}
PUT    /api/modules/{mid}/rules/{slug}         {flags, body, dry_run}
DELETE /api/modules/{mid}/rules/{slug}
PUT    /api/modules/{mid}/content/{kind}/{id}  {name, body, keys, fields, sheet, dry_run}
DELETE /api/modules/{mid}/content/{kind}/{id}
PUT    /api/modules/{mid}/layout               {layout, dry_run}
PUT    /api/modules/{mid}/theme                {theme, dry_run}
POST   /api/modules/{mid}/rename               {kind, address, to, dry_run}
GET    /api/modules/{mid}/export               → application/zip download
POST   /api/modules/import                     raw application/zip body → {"id": new_mid}
```

DELETE routes carry `dry_run` as a query param (DELETE bodies are
awkward). None of these collide with existing module routes: the
read-only `GET /modules/{mid}/content/{kind}/{id}` keeps its path and
gains PUT/DELETE siblings; everything else is a new distinct segment.

**Existing-route touch-up**: `GET /modules/{mid}` additionally returns
`manifest.notes` (the `module.md` body) so the manifest editor can round-
trip it. Nothing else changes; the editor reads the pack through the
existing route.

## Frontend

### ModulesView

The rail gains **+ Import** beside "+ New module" (file input →
`importModule`; on success, select the new module). The read-only detail
(unchanged for browsing) gains sidebar actions:

- **Export** — always; downloads the zip via the export route.
- **Duplicate** — always (builtins *and* user modules); small inline name
  prompt defaulting to "<name> copy"; on success selects the copy.
- **Edit** — user-library modules only; switches the body to
  `ModuleEditor`. Builtins show Duplicate where Edit would be, with a
  hint ("built-in — duplicate to customize"), preserving the Phase 1
  read-only guarantee for builtins.

### `ModuleEditor` (new component)

Replaces the detail body while editing; a Done button returns to the
read-only view (refetching the pack). A section nav — Manifest · Groups ·
Sheet types · Checks · Rules · Content · Layout · Theme — where each
record-collection section is a mini list/detail per the CLAUDE.md
pattern: a records rail (`+ New group`, one row per record) and a
view/edit body with explicit Edit/Save/Cancel. Section specifics:

- **Manifest**: name, description, version, dice (validated live), notes
  textarea.
- **Groups**: field rows (key, label, type select, type-specific extras —
  `max` for dots/track/resource, `min`/`max`/`default` for number,
  `ref_kind` select for ref) and derived name→expression rows.
- **Sheet types**: label, kind select, ordered group-membership picker,
  own-field rows and derived rows (same widgets as Groups), plus
  `creation` (per-pool budget + cost rows, pool keyed by a group the type
  composes) and `advancement` (pool select over the type's resource
  fields, cost rows) sub-forms.
- **Checks**: label, roll template, `requires` group picker, `rules`
  doc picker, difficulty, outcomes rows; a separate `_defaults` form.
- **Rules**: body textarea + activation controls (`always`/`on_roll`
  toggles, `keys` chip input, `sheet_types` picker).
- **Content**: name/body/keys/fields form (mini EntityEditor shape) plus
  an optional stat block — sheet-type select and the same field widgets
  `SheetEditor` uses for values.
- **Layout**: JSON textarea beside a **live preview** — the existing
  `SheetLayout` rendered against a sample sheet (schema defaults) with a
  sheet-type selector; dry-run `display_errors` inline.
- **Theme**: form controls (paired bg/ink color inputs, accent/muted/rule
  colors, font/dots/corners selects) applied to the same live preview.

**Keys rename, not retype.** In edit forms, record ids and field/derived
keys render read-only with a rename affordance (a small "rename" button →
inline prompt → `POST .../rename`). Renames are immediate, independent
transactions — the form warns and blocks the rename affordance while the
form itself has unsaved changes, so a rename never races a pending save.
Labels, by contrast, are ordinary form fields.

**Live validation**: every form debounces (~500 ms) a `dry_run` save of
its current draft and renders returned `errors`/`display_errors` inline
(scoped under the form, hint-styled for display errors). The sheets
sample computation renders derived results next to their expressions.
Save submits with `dry_run: false`; server-side rejection messages land
in the form's existing `.banner` idiom.

**Breaking-edit confirm**: when the latest dry-run `impact` reports
`sheets_migrated`, `sheets_newly_invalid`, or `dangling_refs` > 0, Save
(or the rename prompt's confirm) first shows the counts — "used by 3
sheet types · migrates 12 sheets · 4 sheets become invalid" — with
Confirm/Cancel.

**Types/client** (`api/client.ts`): `ModuleDetail.manifest` gains
`notes`; new fns `duplicateModule`, `importModule`, `exportModuleUrl`,
`putModuleManifest`, `putModuleGroup`/`deleteModuleGroup`,
`putModuleSheetType`/`deleteModuleSheetType`, `putModuleCheck`/
`deleteModuleCheck`, `putModuleCheckDefaults`, `putModuleRule`/
`deleteModuleRule`, `putModuleContent`/`deleteModuleContent`,
`putModuleLayout`, `putModuleTheme`, `renameModulePart` — thin wrappers,
all carrying `dry_run`.

### `create-mechanics-module` skill

Gains a short note that the in-app editor now exists and remains the
conversational authoring path; its validate-after-each-step flow is
unchanged (it edits the same files the UI does).

## Testing

- **`test_module_edit.py`** (store): per-writer accept + reject cases
  (reject leaves the live pack byte-identical and no staging debris);
  builtin refusal on every writer; upsert/delete round-trips per section;
  delete cascade-vs-reject split (fatal referee named in the error;
  layout pruned on field/group/derived delete); rename fan-out per kind
  incl. expression rewrite, `<key>_max` for resource fields, roll-template
  placeholders, content-sidecar field keys and sidecar `ref` values, and
  a key that is a substring of another key (word boundary — renaming
  `str` must not touch `strength`); **scope-bound rewriting**: two
  disjoint groups defining the same field key — renaming one leaves the
  other group's derived, its composing types' expressions, and checks
  requiring only the other group untouched; a layout fragment shared by
  in-scope and out-of-scope types is specialized (clone rewritten and
  repointed, original untouched) while an in-scope-only fragment is
  rewritten in place; renaming `to` a reserved contextual name
  (`new`/`difficulty`/`modifier`) rejected for fields *and* derived, and
  ordinary saves reject such keys/names too; rename collision rejected;
  **swap/recovery**: a simulated crash at each journal phase
  (before rename 1, between renames, after swap mid-migration) recovers
  to a complete, valid pack; the pre-swap case discards the pending
  migration and leaves stored sheets byte-identical, the post-swap cases
  finish the journaled migration idempotently (replaying an
  already-migrated file is a no-op); recovery of one module's journal
  while another module edit is in flight is impossible by construction
  (global lock — assert edits serialize); sheet
  migration — field rename rewrites world + campaign sheets with gen
  bumps, group rename migrates zero sheets, sheet-type rename rewrites
  `sheet_type` values, content rename rewrites `kind:module:id` refs,
  unparseable sheet file skipped and reported; a stored sheet holding
  both the old and destination keys rejects the rename with its path
  listed; locks acquired for all campaigns then all worlds in one sorted
  order before the swap, a late-appearing campaign triggers
  release-and-reacquire of the full union (never lock growth while
  held), and a campaign that doesn't resolve to the module under its
  lock is untouched; every
  world-sheet mutator (`write_world`, `write_world_creation`, seeding,
  instantiate) serializes on the world-scope lock; **stale-write
  closure**: a queued pre-rename world PUT fails the new world gen CAS
  after migration, a pre-rename world DELETE fails its now-mandatory
  `expected_gen`, and a pre-rename create-new payload (`expected gen =
  None`) 400s on its unknown key instead of silently dropping it;
  deletion at each staged-swap phase serializes with edits and ends
  deleted (never resurrected by recovery); a nested/diamond fragment
  graph (wrapper fragment `use`-ing the affected one, shared across
  scopes) specializes the whole ancestor path; dry-run returns
  impact (migrated / newly-invalid / dangling counts, sample derived
  values) and writes nothing; **impact parity**: deleting a sheet type,
  changing a type's `kind`, and a derived expression that fails only
  against a stored sheet's real values all count as newly invalid (the
  full read-time judgment, not values-only), and a content delete
  leaving another sidecar's ref dangling counts in `dangling_refs`;
  duplicate (new deduped id, builtin source ok, staged single-rename
  publish); id allocation rejects `none` and unsafe slugs across
  create/duplicate/import; export→import round-trip; import rejections
  (traversal entry, absolute path, symlink, >1 top-level dir,
  member-count and cumulative-uncompressed caps, case-colliding paths,
  invalid pack with its validation messages); import id dedup.
- **Routes**: happy path + 400/404 mapping per route; builtin 400s;
  dry_run plumbed; export content-type/attachment; import streamed body,
  413 on over-limit Content-Length and abort past the transfer cap;
  `GET /modules/{mid}` carries `manifest.notes`. `GRIMOIRE_HOME`
  isolation via `monkeypatch.setenv` throughout.
- **Frontend (vitest)**: ModulesView — Import button, Export/Duplicate
  actions, Edit only on user modules with the builtin duplicate hint;
  ModuleEditor per section — row click shows view, Edit reveals form,
  `+ New` opens form directly, Save/Cancel return to view (the CLAUDE.md
  list/detail contract); debounced dry-run errors render inline; rename
  affordance calls the rename endpoint and is blocked while the form is
  dirty; breaking-edit confirm shows counts and Cancel aborts; layout
  preview renders `SheetLayout` for the selected type and updates on
  JSON edits; theme controls drive the preview vars; content stat-block
  editing round-trips.
- **Reference modules**: `d20-basic`/`pool-basic` stay untouched
  fixtures (they are builtins — the editor must refuse them).
- **End state**: scaffold a user module, and through the UI alone: fill
  the manifest, add a group with fields + a derived expression, add a
  sheet type composing it with creation/advancement blocks, add a check,
  a rules doc, a statted content entry, a layout and theme (watching the
  preview), rename a field and confirm a pre-existing campaign sheet
  migrated, delete a field and confirm the affected sheet flags invalid,
  export the module, re-import it under a deduped id, and duplicate a
  builtin and edit the copy.

## Out of scope

Renaming a module's *id* (directory name — bindings in world/campaign
frontmatter would need migration; duplicate + rebind covers the need);
draft/publish staging areas; a visual drag/drop layout arranger; module
version-compatibility machinery; editing builtins in place or same-id
shadow copies in the UI; bulk operations (multi-field editing beyond one
record per save); concurrent-editor conflict resolution beyond
last-write-wins (single-user app posture, unchanged); packaging images
or fonts in packs; sharing infrastructure beyond a zip file (no registry,
no URLs).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
