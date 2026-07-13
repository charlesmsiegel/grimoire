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
| Expression rewriting | Rename ops rewrite expression strings by **word-boundary text replacement**, then the whole-pack validation re-parses everything | The expression language has no strings, attributes, or comments, so `\bold\b` can only match a `Name` (function names and keywords are barred as field keys, and a key that shadows a keyword could never have parsed as a Name). Text replacement preserves the author's formatting; `ast.unparse` would reformat every expression and requires Python ≥ 3.9 (Android runtime not guaranteed). The staged validation gate re-parses and re-scopes every expression afterwards, so a bad rewrite cannot land. |

## Backend: `store/module_edit.py`

New module (pure stdlib, pydantic-free, filesystem via the same
`modules.user_dir()`/`pack_root()` resolution — Android-safe like
`modules.py`/`expressions.py`). `modules.py` keeps loading/validation;
`module_edit.py` owns mutation. Every writer:

- raises `ModuleError` when the target resolves to a builtin (only
  user-library packs are editable — same posture as `delete_module`);
- serializes on a per-module in-process lock via the `_lock_for(mid)`
  gets-or-creates-under-a-registry-guard pattern already used in
  `sheets.py` (single-process deployment invariant, same caveats as
  Phase 7's advance lock);
- follows the **stage → validate → swap** primitive below.

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
4. **Swap**: move each changed/added file into the live pack with
   `os.replace`; delete removed files. The in-process lock serializes
   writers; concurrent readers (a `GET` mid-swap) can observe a
   mixed-generation pack for the instant between file moves — accepted,
   same single-user, single-process posture as every other store write,
   and both generations are individually valid files.
5. Delete staging.

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
| `field` | `{owner, from}` where `owner` is `{"group": gid}` or `{"sheet_type": tid}` | field `key`; every expression that names it — group/type `derived`, check `roll` placeholders, advancement cost expressions (incl. the implicit `<key>_max` name for `resource` fields); `creation.pools[*].costs` keys; `advancement.costs` keys; layout `fields` entries | rewrite the `fields` key in every stored sheet whose sheet type includes the field |
| `derived` | `{owner, from}` | the `derived` map key; expressions naming it (type-level derived, advancement costs); layout `derived` entries | none — derived values are computed, never stored |
| `sheet_type` | `{from}` | `sheet_types` key; rules frontmatter `sheet_types` flags; layout `sheet_types` key; content sidecar `sheet_type` values (checks reference groups, never sheet types — nothing to rewrite there) | rewrite the `sheet_type` value in every stored sheet of that type |
| `check` | `{from}` | `checks` key | none — check ids are not persisted outside the pack |
| `rule` | `{from}` | file rename `rules/<from>.md` → `rules/<to>.md`; check `rules` lists | none |
| `content` | `{kind, from}` | file(+sidecar) rename under `content/<kind>/` | rewrite `<kind>:module:<from>` entries in every stored `ref` field value |

Expression rewriting is word-boundary text replacement (see Decisions),
after which the staged validation re-parses and re-scopes every
expression — a rewrite that somehow produced garbage rejects the whole op.

### Sheet migration

Runs only after the staged pack validates and swaps, for the rename kinds
above (`field`, `sheet_type`, `content`):

- **World sheets**: every `<world>/sheets/<mid>/<kind>--<id>.json` —
  worlds hold starting sheets for a module regardless of binding.
- **Campaign sheets**: enumerate all campaigns; for each, take
  `sheets.lock_for(cid)` and *re-check* `modules.resolve(cid) == mid`
  under the lock before touching its files (the lock-then-recheck
  discipline from the Phase 5 rebind fix — enumeration outside the lock
  can go stale).
- Each migrated file is rewritten atomically (temp + `os.replace`, the
  existing `_checked_write` posture) with its `gen` bumped, so any
  in-flight client CAS write against the pre-rename sheet fails cleanly
  instead of resurrecting the old key.
- Only sheets whose `sheet_type` (post-rename) actually carries the
  renamed field are touched for `field` renames; `content` renames touch
  only sheets holding a matching ref value.

Migration is deliberately **best-effort per file after the pack swap** —
a sheet file that fails to parse is skipped and reported in the op result
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
schema but fail against the staged one (re-run `validate_sheet_values`
per sheet file — a full scan, fine for a local single-user store);
`dangling_refs` = `ref` entries that would point at removed content
(informational — ref validation is shape-only, dangling refs are
display-only fallout, per Phase 7). Non-dry-run responses include the
same `impact` block for the record. The frontend uses dry-run for
debounced live validation and shows the confirm step when `impact`
reports migrations or new invalidations.

For `sheets.json` dry-runs the response also carries a **sample
computation**: per sheet type, the assembled field defs with schema
defaults and every derived value evaluated against those defaults — the
draft's "evaluate against a sample sheet", giving expression authors real
numbers, not just "parses".

### Duplicate, export, import

- `duplicate_module(mid, name) -> new_mid` — copy the pack dir (builtin
  or user source) into `user_dir()` under `slugify(name)` deduped against
  existing ids (the `create_entity` dedup idiom). No binding changes.
- `export_module(mid) -> bytes` — stdlib `zipfile` of the pack dir, one
  top-level directory named `<mid>/`. Any module, builtins included
  (exporting a builtin is how you share a tweak-base).
- `import_module(data: bytes) -> new_mid` — safety checks first: total
  size cap (16 MB) and per-entry sanity, exactly one top-level directory,
  every entry a plain file whose normalized path stays inside it (no
  absolute paths, no `..`, no symlink entries). Extract to staging, take
  the id from the top-level dir name (deduped like duplicate), validate
  via `load_pack_at` — non-empty `errors` rejects the import with the
  messages (an invalid pack never lands, consistent with the save model),
  then move into `user_dir()`.

## Routes

All in `routes.py`; bodies are plain v1/v2-agnostic pydantic models
dumped via `routes._dump`. Builtin targets ⇒ 400 with the `ModuleError`
message; unknown ids ⇒ 404. Every *editing* route (manifest, groups,
sheet-types, checks, check-defaults, rules, content, layout, theme,
rename) accepts `dry_run` (body flag) and returns the dry-run/impact
shape above; duplicate/export/import have no dry-run (nothing staged to
preview).

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
Import reads the raw request body (`await request.body()`) — no
multipart, no new dependency, Android-installable base deps unchanged.

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
  placeholders, and a key that is a substring of another key (word
  boundary — renaming `str` must not touch `strength`); rename collision
  rejected; sheet migration — field rename rewrites world + campaign
  sheets with gen bumps, group rename migrates zero sheets, sheet-type
  rename rewrites `sheet_type` values, content rename rewrites
  `kind:module:id` refs, unparseable sheet file skipped and reported;
  the campaign lock-then-recheck (a campaign that no longer resolves to
  the module under its lock is untouched); dry-run returns impact
  (migrated / newly-invalid / dangling counts, sample derived values)
  and writes nothing; duplicate (new deduped id, builtin source ok);
  export→import round-trip; import rejections (traversal entry, absolute
  path, symlink, >1 top-level dir, oversize, invalid pack with its
  validation messages); import id dedup.
- **Routes**: happy path + 400/404 mapping per route; builtin 400s;
  dry_run plumbed; export content-type/attachment; import raw-bytes body;
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
