# Mechanics Phase 5 — narrated-event validation

Full design for Phase 5 of the Mechanics & Dice milestone (roadmap issue
#826), superseding `2026-07-12-mechanics-phase5-absorb-validation-draft.md`.
Depends on Phase 4 (landed). When a campaign resolves to a module, the
end-scene absorb pass also audits the transcript against the sheets and the
roll log: it flags narration that contradicts mechanics, and proposes sheet
deltas through the existing StagedEdit review flow.

## Decisions (settled 2026-07-12; hardened by Codex adversarial rounds 1–6)

| Decision | Choice | Why |
|---|---|---|
| Architecture | A second, focused LLM call after the main absorb call; best-effort for the *prose* absorb (an audit failure never fails absorb) but **never silent**: the audit reports an explicit status, and parsing **fails closed** on schema-invalid output | The absorb system prompt is already one dense block with ~13 output keys; numeric bookkeeping against sheets + a roll log is a different cognitive task from narrative summarization. Codex rounds 3–4: a swallowed audit failure — including a reply that parses as JSON but lacks the required shape — must not masquerade as "audited clean". |
| Warnings | Ephemeral: rendered in the absorb panel, gone when the panel closes; never persisted | Warnings are advisory — the player reads them, maybe fixes something by hand, moves on. No bookkeeping. |
| XP / advancement | No exception to the mutable-only rule. XP *awards* are ordinary resource deltas (advancement pools are structurally `resource` fields — `sheets.advance` requires `{current, max}`); *raises* stay behind the manual Advance button | The rule stays clean with zero special cases; narration-driven stat raises were the questionable half and remain out of scope. |
| Scene-start baseline | Every scene captures a snapshot of all campaign sheets at creation (`sheet_baselines.json`), stamped with the resolved **module id**, a **schema stamp** (content hash + `sheets.json` mtime) of the pack's sheets definition, and each sheet's **generation nonce** (`gen`) + `sheet_type`. An entity without a *valid* baseline is report-only: warnings allowed, **sheet deltas suppressed at materialize and re-checked inside the write lock at apply** | Codex rounds 1+3+4+6: current-only values are ambiguous ("current 4 + narration spends 2" cannot distinguish already-applied from still-pending); assuming current-as-start can re-propose an already-applied change whose CAS would then succeed; and a baseline captured under a different module, schema revision, sheet type, or a deleted-and-recreated sheet is an unrelated value that must not authorize writes. No sound before-value → no writes. |
| Sheet identity: generation nonce | Every campaign/world sheet file carries `"gen": "<uuid4 hex>"`, minted on creation and on any type-changing whole-sheet write, preserved by value writes (`write` same-type, `advance`, `set_field`). Baseline validity requires `gen` equality with the live sheet | Codex rounds 4–6: cross-store invalidation hooks (the round-4 design) created a capture-vs-invalidate resurrection race and an authorize-then-lock TOCTOU. An identity carried *in the sheet file itself* is immune to both: delete/recreate or type change mint a new `gen`, so a stale baseline can never match the replacement sheet — checked atomically inside the write lock, no bookkeeping to race. |
| Sheet write discipline | **Every** campaign-sheet mutator (`write`, `write_creation`, `advance`, `set_field`, `delete`) serializes on the per-campaign sheet lock; every campaign whole-sheet replacement carries a **mandatory** CAS on the full `{sheet_type, fields, gen}` snapshot (`expected=None` asserts "no sheet exists yet"); `set_field` is a strict per-field CAS. All four callers inventoried and updated (see Write discipline) | Codex rounds 2–4+8: `sheets.write` took no lock and replaced the whole map (a pre-existing `write`-vs-`advance` hole); an *optional* CAS left a stale last-write-wins path open; a fields-only compare could silently revert a concurrent `sheet_type` change; and the content-instantiate route also calls `sheets.write`, so a "required parameter" change must update it or it breaks at runtime. Round 8: without `gen` in the snapshot, a delete/recreate or A→B→A type change with identical fields is an ABA a value compare cannot see. |
| Apply-time authorization | Client-supplied `"sheet"` edits are re-authorized **inside the sheet lock, immediately before the CAS write**: scene scope, baseline validity (module + schema stamp + `gen` + type), and mutability are all recomputed from `cid`/`sid` and the live file within one critical section that **resolves the module once at entry**; module-rebind routes serialize on the same lock | Codex rounds 2+4+6–7: chronicle-PUT edits are client-supplied; a check performed before lock acquisition is a TOCTOU (delete/recreate could pass CAS against an unrelated sheet), and an unserialized rebind could split the section across two modules. One critical section with a single module resolution closes both structurally. Manual editing of arbitrary sheets stays where it belongs — the sheet PUT. |
| Apply semantics | `set_field` per-field strict CAS: only the approved field is written; live ≠ expect is **always** a visible conflict — including live == proposed-value, which reads "already applied or independently changed". **Every** failed approved sheet edit — CAS conflict, re-authorization failure, or any other `SheetError` — is returned with its id and reason; nothing is silently skipped | Codex rounds 1–2+5: plain absolute overwrite loses concurrent updates; treating `live == value` as a confirmed retry can mask an independent same-value mutation (two XP awards collapsing into one); and a schema-drift or validation `SheetError` swallowed by the generic best-effort skip would let the user close the panel believing an approved XP/damage update landed. Strict CAS + full failure reporting needs no operation ledger. |
| Audit visibility | The absorb response carries `mechanics: {status: "ok"\|"degraded"\|"failed"\|"skipped", reason, warnings, dropped}`; `failed` and `degraded` render a notice with a **Retry validation** action backed by a standalone audit endpoint. **"ok" means the full scope was audited and every model item survived**: an invalid scoped sheet, a malformed item, or a materialize-rejected delta (other than a benign no-op) makes the status `degraded` with each exclusion listed; a scope whose sheets are *all* invalid is `failed`, never `skipped` | Codex rounds 3+5+6: an empty warnings list must mean "audited clean", never "the audit died", "the audit's findings were quietly thrown away", or "the audit could not see half the cast". Retry re-runs only the audit — not the whole absorb (and its dossier calls). |
| Sheet-row review UI | Sheet edit rows are **read-only** (approve/reject only); the displayed before/after strings are rendered from the payload that will be applied | Codex round 1: ordinary absorb rows edit `after` in a textarea, but the sheet apply branch writes `payload.value` — an editable row would let the reviewer approve one value while another lands. Read-only keeps display and payload the same fact. Typed value editing can come later if wanted. |
| Sheet scope | Present sheeted cast + the sheeted current location — the same scope as Phase 4's `mechanics_sheets` context section. The model sees **full** compact sheet blocks (static stats included, so contradictions involving them are visible) with mutable fields explicitly marked as the only delta-eligible ones | Letting the model propose deltas against sheets it never saw is guesswork. Item sheets wait for item presence tracking (future); location wards are covered because the sheeted current location is in scope. |
| Delta granularity | One StagedEdit per (entity, field), independently approvable | Matches how the panel works; a rejected essence spend shouldn't drag down an approved XP award. |
| No event ledger | Correctness comes from the baseline (prompt side) + strict CAS inside one authorize-and-write critical section (write side) + suppression wherever the baseline is missing or invalid, not a mutation ledger | Considered (Codex rounds 1–6) and rejected as disproportionate for a local single-process app: re-running absorb sees `start → current` and doesn't re-propose an applied change; a missing/invalid baseline suppresses deltas at both materialize and apply; every racy or replayed apply fails its CAS and is *reported*. No ambiguity is ever silently resolved in either direction. The sheet `gen` nonce is the "generation identifier" a ledger would provide, without the ledger. |

## Scene-start sheet baselines

- `<campaign>/sheet_baselines.json` — one JSON object keyed by scene id
  (whole-file IO, atomic write, never-raise reads):

  ```json
  {"<sid>": {"module": "<mid>",
             "schema": {"hash": "<sha256>", "mtime": <mtime_ns>},
             "sheets": {"<kind>--<eid>": {"sheet_type": ..., "gen": ..., "fields": {...}}}}}
  ```

  The schema stamp pairs a `sha256` over the canonical JSON dump
  (`sort_keys=True`) of `load_pack(mid)["sheets"]` with the
  **`st_mtime_ns` of the pack's `sheets.json`**, and validity requires
  both to match. The hash alone catches an **in-place pack edit** — same
  module id, changed field semantics — but a content *reversion*
  (schema A → B → back to A) would restore the hash while sheets were
  edited under B; the mtime moves monotonically forward on every write,
  so the reverted file still invalidates every baseline captured before
  the excursion. (Restoring a backup with a preserved old mtime is the
  residual — that is deliberate file surgery on a local store, on par
  with hand-editing `sheet_baselines.json` itself.)
- **Capture**: `scenes.create_scene` lazily calls
  `audit.capture_baseline(cid, sid)` (the existing lazy-import pattern),
  which — when `modules.resolve(cid)` is non-`None` — snapshots every
  campaign sheet file verbatim (including each file's `gen`), stamped with
  the module id and schema hash; a no-op otherwise. This covers every
  creation path (routes, ingest CLI) without the callers knowing.
- **Validity.** An entity's baseline for a scene is *valid* iff: the scene
  entry exists; its `module` equals the currently resolved module; its
  `schema` hash equals the current pack's; the entity's entry exists; and
  its recorded `sheet_type` **and `gen`** equal the live sheet's. One
  predicate — `audit.baseline_field(cid, sid, kind, eid, field_key)`
  (returns the baseline value, or `None` when the baseline is
  invalid/absent or the field is absent from it) — backs prompt
  construction, materialize, and the in-lock apply re-check, so the
  layers cannot drift. The `gen` check is what makes delete/recreate and
  type changes self-invalidating: the replacement sheet carries a fresh
  nonce, so the old baseline simply stops matching — **no cross-store
  invalidation hook exists to race with capture** (the round-4
  invalidation design and its resurrection race are gone).
- **Module rebinds.** The two routes that change a campaign's effective
  binding — the campaign-module PUT, and the world default-module PUT
  (for each campaign of that world without its own override) — call
  `audit.clear_baselines(cid)`, dropping every scene's baseline for the
  affected campaigns: in-flight scenes become report-only. A rebind
  mid-scene is rare and mechanically disruptive anyway; losing audit
  deltas (not warnings) for those scenes is the safe, proportionate
  answer, and it closes the A→B→A rebind case a module-id compare alone
  would miss.

  **Rebind serialization.** The campaign-module PUT performs its
  meta-write **and** `clear_baselines` while holding that campaign's
  sheet lock — the same lock the apply critical section holds — so a
  rebind either completes before an apply's authorization (module
  mismatch → reject) or waits for the in-flight apply to finish. The
  world default-module PUT writes the world meta first, then takes each
  affected campaign's sheet lock in turn to clear its baselines (never
  holding two locks at once — no ordering hazard). The mid-window there
  is safe because the apply critical section **resolves the module
  exactly once at entry** (see Apply): an apply entering after the world
  write resolves B against an A-stamped baseline → module mismatch →
  reject; an apply that entered before it runs consistently under A and
  serializes as apply-then-rebind.
- **Reads without a valid baseline** degrade to report-only: the entity's
  block is rendered with current values marked
  `(no scene baseline — report only)`, the model is told it may raise
  warnings about that entity but must not propose deltas for it, and
  `materialize` **drops any delta for it regardless of what the model
  returned**.
- **Locking.** Capture, rebind clearing, and repointing are
  read-modify-write transactions on one JSON object, so all three run
  under a per-campaign baseline lock (the `_LOCKS`/`_LOCKS_GUARD` pattern
  from rolls/proposals); atomic replacement alone would still allow lost
  updates between concurrent captures. Lock ordering is fixed as **sheet
  lock → baseline lock**: `capture_baseline` takes the sheet lock first
  (so the multi-file sheet snapshot is not torn by a concurrent write)
  and the baseline lock inside it; other baseline ops take only the
  baseline lock. No path acquires the pair in reverse order, so it cannot
  deadlock.
- **Repointing**: `audit.repoint_scenes(cid, mapping)` re-keys entries,
  registered as the sixth store in `scene_refs.repoint`. Entries for
  deleted scenes are harmless orphans (same posture as rolls/changes).

## Backend — `store/audit.py` + `templates/audit/`

New module mirroring `absorb.py`'s shape: prompt/parse/materialize only; the
LLM call lives in the route; prompt text in `templates/audit/system.j2` +
`user.j2`.

- **`build_prompt(transcript, sheet_blocks, roll_lines) -> list[dict]`.**
  The system prompt instructs: compare the narration against the sheets and
  the roll log; reply with ONLY a JSON object
  `{"warnings": [str], "sheet_deltas": [{"id", "field", "value", "note"}]}`.
  Warnings name narration that contradicts mechanics — a claimed outcome
  with no roll-log entry, a narrated spend that never hit a tracked
  resource, damage that never landed on a sheet, or a current value that
  matches neither the baseline nor the narration. Deltas cover mechanical
  state the scene visibly changed **and that the current value does not
  already reflect**: each mutable field is shown as `start → current`, and
  the model proposes the correct **end-of-scene value** only when current
  is not already there (an already-applied change gets no delta; a partial
  application gets the corrected end value; a `report only` entity gets
  warnings, never deltas). Never dispute a logged roll; never propose a
  change to a static field; never change a resource's `max`. `id` is the
  `kind:eid` header printed on the sheet block; `value` is the complete
  new field value (`{"current": n}` for resources, an int for tracks, the
  full new list for lists); `note` is one sentence of justification shown
  to the reviewer.
- **`sheet_blocks(cid, sid) -> (list[str], list[dict])`** — one compact
  block per present sheeted cast member plus the sheeted current location,
  rendered like Phase 4's sheet summaries (header `kind:eid — Type
  (Name)`; one line per field), with each mutable field (`resource`,
  `track`, `list`) marked delta-eligible and shown as `start → current`
  via `baseline_field` (or the `report only` marker), and everything else
  marked static. An **invalid sheet** (non-empty `errors`) is excluded
  from the blocks — absorb must not propose deltas against a sheet the
  engine itself can't read — but the exclusion is **returned, not
  swallowed**: the second element lists
  `{"id": "kind:eid", "reason": "sheet invalid: ..."}` entries that the
  route folds into `dropped` (status `degraded`). If the scope is
  non-empty but *every* scoped sheet is invalid, the audit is `failed`
  ("all scoped sheets invalid", no LLM call) — never `skipped`, which
  renders nothing.
- **`roll_lines(cid, sid) -> list[str]`** — the scene's `rolls.json`
  entries (`entry["scene"] == sid`), one line each: label, notation,
  total/successes, tier when present.
- **`parse_output(text) -> dict`** — **fails closed on structure, stays
  tolerant on items**. Structural requirement: the reply must contain a
  recoverable JSON object in which `warnings` and `sheet_deltas` are both
  **arrays** (a missing key, `null`, or a non-array type is a schema
  failure). On structural failure `parse_output` raises `AuditParseError`
  (carrying a human-readable reason) and the route reports
  `status: "failed"` — `{}` or `{"warnings": null}` is a degraded model,
  not a clean audit. Within valid arrays, individual malformed items
  (non-dict deltas, non-string warnings) are collected as `dropped`
  entries — not silently discarded; deltas are normalized to `id`/`field`
  strings with `value` kept as-is for materialize to validate.
- **`materialize(cid, sid, parsed) -> (list[dict], list[dict])`** — the
  deterministic gate (mirrored again inside the apply lock; see Apply).
  For each proposed delta, in order, drop it unless:
  - the `id` parses as `kind:eid` and is within the shown scope (present
    sheeted cast or the sheeted current location) with a readable,
    error-free sheet;
  - `baseline_field(cid, sid, kind, eid, field)` returns a value (valid
    baseline — module, schema hash, `gen`, type — covering this field);
  - `field` exists in that entity's **own sheet type's** assembled field
    set, with type `resource`, `track`, or `list`;
  - the **canonical value** (see below) passes
    `modules.validate_sheet_values` for the sheet's stored fields overlaid
    with this one change;
  - the canonical value differs from the stored value (no-ops dropped).

  The second return value is `dropped`: every delta that fails a gate is
  recorded as `{"id", "field", "reason"}` — except benign no-ops (the
  model proposed the value the sheet already holds; that is agreement,
  not loss). A non-empty `dropped` makes the audit status `degraded`
  (see Routes): a proposed damage or XP delta that was thrown away is a
  missing mechanical update the user must see, never a silently "clean"
  audit.

  **Canonical values.** A resource proposal is canonicalized to
  `{"current": <proposed current>, "max": <live max>}` at materialize time;
  tracks to a plain int; lists to a list. Every comparison, rendering, and
  the stored payload use the canonical form — there is no place where a
  `{current}`-only value meets a `{current, max}` value.

  Surviving deltas become StagedEdits:
  `{"id": "sheet:{kind}:{eid}:{field}", "kind": "sheet",
  "target": {"kind", "id"}, "label": "<Name> — <field label> (sheet)",
  "field": <field key>, "before": <rendered>, "after": <rendered>,
  "authored": false, "payload": {"field": key,
  "value": <canonical proposed>, "expect": <canonical live value at
  materialize time>, "note": str}}`. Rendering: resources as
  `essence 6/10`, tracks as ints, lists one item per line.
  `before`/`after` are rendered **from** `payload.expect`/`payload.value`,
  so what the reviewer reads is by construction the value the apply step
  uses.

## Sheet write discipline — one lock, mandatory CAS, sheet identity

All in `sheets.py`:

- **Generation nonce.** Every sheet file gains `"gen": "<uuid4 hex>"`
  (proposals.py precedent), minted by `_checked_write` on creation and on
  any write whose `sheet_type` differs from the stored one, and
  **preserved verbatim** by same-type value writes, `advance`, and
  `set_field`. `read`/`read_world` surface it. Legacy files without `gen`
  read as `gen: null` and gain one on their next whole-sheet write
  (`null` never equals a minted nonce, so legacy baselines simply expire
  on first replacement — no migration).
- **Locking.** `write`, `write_creation`, `advance`, `set_field`, and
  `delete` all run their read-validate-write under `_lock_for(cid)` (the
  existing per-campaign lock `advance` already takes; exposed as a public
  `lock_for(cid)` for `audit.capture_baseline` and the apply critical
  section). This fixes a pre-existing hole — the editor PUT's
  `sheets.write` could interleave with `advance` — and guarantees no
  writer ever sees a torn read-modify-write. World-sheet writes
  (`write_world*`) are unchanged: they have a single writer (the world
  editor) and no engine writers.
- **Mandatory whole-sheet CAS.** `write` and `write_creation` gain a
  required `expected: dict | None` parameter — the full
  `{"sheet_type": ..., "fields": {...}, "gen": ...}` snapshot the caller
  last read (`read` surfaces `gen` precisely so callers can echo it):
  - `expected=None` asserts **no sheet exists yet** (creation); if a file
    exists → `SheetConflict`.
  - `expected` given: compared (under the lock) against the stored
    snapshot — `sheet_type`, `fields`, **and `gen`**; any mismatch →
    `SheetConflict`. Comparing the type means a concurrent type change
    can never be silently reverted by a stale save whose field maps
    happen to match; comparing the identity nonce means a **logically
    replaced sheet** — deleted and recreated with identical type and
    default fields, or type-changed A→B→A — can never accept a stale
    editor's save either (the ABA case a value compare cannot see).
  - **Caller inventory** (every campaign-sheet writer, updated in this
    phase): the sheet PUT (`routes.py` — client sends `expected`, `null`
    when creating; `SheetConflict` → 409); the creation-wizard POST
    (same); the **content-instantiate route**
    (`POST /campaigns/{cid}/{kind}/instantiate/...`) — passes
    `expected=None` server-side (the entity was created this request, so
    no sheet can exist; a conflict is impossible short of a filesystem
    surprise, and the existing rollback path — delete the just-created
    entity — handles it); `seed` (campaign creation; exempt from CAS —
    the campaign directory has no concurrent writers before the campaign
    exists; seeded files keep the world file's `gen` verbatim, which is
    fine: gens are only ever compared within one campaign).
- **`set_field(cid, kind, eid, field_key, value, expect) -> None`** — the
  absorb apply primitive, strict per-field CAS under the same lock:
  1. resolve the module and read the live sheet file (missing either →
     `SheetError`);
  2. **enforce mutability at the write boundary**: `field_key` must exist
     in the live sheet's own type's assembled field set with type
     `resource`, `track`, or `list` — anything else raises `SheetError`
     regardless of what the client sent;
  3. canonicalize `value` against the live field (resources take the
     **live** `max`; proposed `max` is ignored — absorb never changes
     `max`);
  4. if the live field value ≠ `expect` (canonical comparison) → raise
     `SheetConflict` naming the field and all three values. This includes
     the live-already-equals-`value` case: without an operation identity
     there is no way to distinguish a duplicate save from an independent
     mutation that reached the same value (two narrated XP awards must
     not collapse into one), so it is reported — the message reads
     "already applied or independently changed" — never silently
     absorbed;
  5. otherwise build the new field map changing **only** `field_key`,
     validate the full map via the existing strict path, and write
     atomically (preserving `gen`). Concurrent edits to *other* fields
     always survive.
- **`SheetConflict(SheetError)`** — carries entity, field/type context,
  expected and live values for the route/UI to report.

## Apply — the `"sheet"` edit kind

`absorb.apply_edits` gains a `"sheet"` branch delegating to
`audit.apply_delta(cid, sid, edit)`. Because chronicle-PUT edits are
client-supplied and a check made before the lock is a TOCTOU, the entire
re-authorize-and-write sequence is **one critical section under the
campaign sheet lock** (`sheets.lock_for(cid)`; `set_field`'s body runs as
its tail via a lock-free internal, the public `set_field` wrapping the same
internal with the lock). The section **resolves the module exactly once at
entry** and threads that `mid` through every sub-step — validity check,
schema loads, validation, write — so a concurrent rebind cannot split the
section across two modules (see Rebind serialization under Baselines):

1. the target must be in the scene's sheet scope, recomputed now (present
   sheeted cast or the sheeted current location for `sid`);
2. `audit.baseline_field(cid, sid, kind, eid, field)` must return a value
   — the full validity predicate (module, schema hash, **live `gen`**,
   type) evaluated against the sheet file as it exists *inside the lock*,
   so a delete/recreate racing the apply cannot slip an unrelated sheet
   past the check (the recreated file carries a fresh `gen`);
3. the `set_field` steps (mutability + canonical CAS + single-field
   write).

**Every** failed sheet edit is reported, whatever the cause: failures of
1–2, `SheetConflict` from 3, and any other `SheetError` (schema drift, a
field gone static, module resolution failure, unreadable sheet, final
validation reject) are all recorded as
`{"id", "reason", "kind": "conflict" | "error"}` — an approved damage or
XP update must never vanish without a user-visible reason. The generic
best-effort skip that other edit kinds use does not apply to `"sheet"`
edits. `apply_edits` returns `(applied, sheet_failures)` and
`PUT .../chronicle`'s response gains a `"sheet_failures"` key. `"sheet"`
is **not** added to `_BROWSABLE_KINDS` — changes.json tracks browsable
prose records, not sheets. Manual editing of arbitrary sheets remains the
sheet PUT's job, with its own CAS.

**Retry semantics.** Sheet-edit application is retry-safe by CAS: a
replayed save can never double-apply (the second attempt reports "already
applied or independently changed" in `sheet_failures`). Idempotency of the
*rest* of the chronicle save (timeline-event appends, chronicle record,
mark-absorbed) is today's pre-existing PUT behavior, unchanged by this
phase — see Out of scope.

## Routes

- `POST /api/campaigns/{cid}/scenes/{sid}/absorb`: after the main absorb
  call (and its materialize), run the audit step and merge its result:
  `edits` is extended with the audit's materialized StagedEdits, and the
  response gains a `"mechanics"` object:
  - `{"status": "skipped", "reason": "no module" | "no sheeted scope",
    "warnings": [], "dropped": []}` — module-less campaign or a scope
    with no sheeted entities at all; zero extra LLM calls;
  - `{"status": "ok", "reason": null, "warnings": [str], "dropped": []}`
    — the audit ran over the full scope, passed structural parsing, and
    **every model item survived** (an empty `warnings` now genuinely
    means "audited clean");
  - `{"status": "degraded", "reason": "some findings could not be
    validated" | "some sheets could not be audited", "warnings": [str],
    "dropped": [{"id", "field"?, "reason"}]}` — the audit ran but one or
    more scoped sheets were invalid, items malformed, or deltas rejected
    by materialize; the surviving warnings/edits are returned, and each
    exclusion is listed with its reason;
  - `{"status": "failed", "reason": <human-readable cause: LLM error,
    schema-invalid output, all scoped sheets invalid>, "warnings": [],
    "dropped": [...]}` — the audit died, returned a reply violating the
    output schema (`AuditParseError`), or had no valid sheet to audit;
    the prose absorb result is returned intact (best-effort contract),
    but the failure is explicit, never disguised as a clean audit.
- `POST /api/campaigns/{cid}/scenes/{sid}/audit` — **retry validation
  standalone**: re-runs only the audit call (not the prose absorb, not
  the dossiers) and returns `{"mechanics": {...}, "edits": [...]}` with
  freshly materialized sheet edits (fresh `expect` values). 400 when the
  module is absent. Registered before generic `{kind}` catch-alls per
  house rule.
- `PUT .../chronicle` applies approved `"sheet"` edits through
  `apply_edits` like every other kind and reports `"sheet_failures"`.
- The campaign-module PUT and world default-module PUT call
  `audit.clear_baselines` for the affected campaigns, under the rebind
  serialization protocol (see Baselines).

## Frontend

- `SceneAbsorb` gains `mechanics: {status, reason, warnings, dropped}`;
  the absorb panel renders, above the edits list:
  - `ok` + warnings → a ⚠ warnings section (informational);
  - `ok` + no warnings → a one-line "mechanics audited clean" hint;
  - `degraded` → the warnings section plus a notice ("Some mechanics
    findings could not be validated") listing each `dropped` entry with
    its reason, and the **Retry validation** button;
  - `failed` → a degraded-validation notice ("Mechanics validation
    failed: <reason>") with a **Retry validation** button that calls
    `POST .../audit` and replaces the panel's `mechanics` + sheet-kind
    edit rows with the fresh result (other edit rows untouched);
  - `skipped` → nothing.
  All of it is ephemeral — cleared with the panel, never sent to
  `PUT .../chronicle`.
- `"sheet"` edits render as **read-only** approve/reject diff rows —
  before/after as fixed text (no textarea), the `note` as the row's hint
  line. The row offers no value editing; a reviewer who wants a different
  number rejects the edit and changes the sheet in the editor.
- `saveAbsorb` reads `sheet_failures` from the PUT response and surfaces
  a notice ("N sheet change(s) did not apply"), listing each affected
  label with its reason (conflict vs error).
- `SheetEditor` and `CreationWizard` send `expected` on save and handle
  409 by reloading with a notice.

## Testing

- **Baselines**: capture on create_scene (module-bound; module, schema
  hash, per-entity gen stamped) / no-op (unbound); `baseline_field`
  validity matrix — missing scene, missing entity, module mismatch,
  schema-hash mismatch (in-place pack edit), `gen` mismatch
  (delete/recreate; type change), sheet_type mismatch, field absent from
  baseline, legacy `gen: null` vs minted gen; `clear_baselines` on
  campaign-module PUT and world default-module PUT (campaigns with their
  own override untouched); repoint via `scene_refs.repoint`; malformed
  file tolerated; **regression: an entity without a valid baseline yields
  zero StagedEdits no matter what the model proposes** (the
  already-applied-change double-propose path), including after a
  mid-scene type change, delete/recreate, pack edit, A→B→A rebind, and
  an **A→B→A schema content reversion** (hash restored, mtime moved →
  still invalid); **locking races** (threaded) — two concurrent scene
  captures both land (no lost update); capture racing a sheet write sees
  a consistent snapshot; **apply vs campaign rebind** and **apply vs
  world-default rebind** — the apply either completes under the old
  module before the rebind or rejects on module mismatch, never a
  cross-module write.
- **Generation nonce**: minted on create and on type change; preserved by
  same-type `write`, `advance`, `set_field`; legacy file gains one on
  next whole-sheet write; seeded campaign sheets keep the world gen.
- **`audit.py` unit**: `parse_output` fail-closed matrix — no JSON, `{}`,
  `{"warnings": null}`, `sheet_deltas` as dict/string → `AuditParseError`;
  valid arrays with malformed items → items land in `dropped` (and the
  status turns `degraded`, never a clean ok); `sheet_blocks` returns
  invalid-sheet exclusions (and all-invalid scope → `failed`, no LLM
  call); materialize returns each gate rejection in `dropped` with a
  reason while a benign no-op drop stays out of it; materialize gates one
  by one — unknown entity, out-of-scope entity, unsheeted entity, invalid
  sheet, baseline-less/invalid-baseline entity, unknown field, static
  field (`number`/`dots`/`text`), bad value (validation reject), resource
  `max` tamper ignored (canonical value takes the live `max`), no-op
  dropped; happy round-trips for resource / track / list; XP-pool award
  accepted as a plain resource delta; `before`/`after` strings render
  from `payload.expect`/`payload.value`; canonical forms everywhere.
- **`sheets.set_field`**: happy path per type; write-boundary enforcement
  with materialize bypassed — static field, unknown field, field from
  another sheet type → `SheetError`; resource `max` tamper ignored;
  single-field isolation (an unrelated field changed between materialize
  and apply survives the write); conflict cases — live ≠ expect with
  live ≠ value, **and** live == value — both raise `SheetConflict`;
  validation reject leaves the file untouched.
- **`sheets.write`/`write_creation` CAS**: `expected` snapshot match →
  write; `fields` mismatch → `SheetConflict`; **`sheet_type` mismatch with
  identical fields → `SheetConflict`** (the silent type-revert case);
  **`gen` mismatch with identical type and fields → `SheetConflict`**
  (the ABA cases: stale editor vs delete/recreate with default fields,
  and vs an A→B→A type change) → 409 at the route; `expected=None` with
  an existing file → `SheetConflict`; `expected=None` with no file →
  creates; **instantiate-route regression** — sheeted content
  instantiates cleanly and the rollback path (entity deleted on sheet
  failure) still works.
- **Locking races** (threaded): `set_field` vs `advance` — both complete,
  neither write lost; `set_field` vs editor `write` with a **stale
  snapshot** — the stale write 409s and the delta survives; editor
  `write` vs `advance` (the pre-existing hole) — serialized, nothing
  lost; **apply vs delete/recreate** — an apply racing a delete +
  same-type recreate is rejected inside the lock (`gen` mismatch), the
  recreated sheet untouched.
- **`apply_edits`**: sheet branch happy path; apply-time re-authorization
  — a crafted edit with a **valid mutable field and correct `expect`** on
  an out-of-scope entity, and on a baseline-less entity, → recorded in
  `sheet_failures`, not applied; `SheetConflict` → recorded as kind
  `"conflict"`, other edits still applied; **any other `SheetError`
  (schema drift, field gone static, unreadable sheet, validation
  reject) → recorded as kind `"error"`, never silently skipped**; sheet
  edits absent from changes.json.
- **Routes**: module-bound absorb fires exactly two LLM calls and returns
  merged edits + `mechanics.status == "ok"`; module-less absorb fires one
  and returns `status "skipped"`; audit LLM failure **and schema-invalid
  audit output (`{}`, nulls, wrong types)** → `status "failed"` with a
  reason while the prose absorb result is intact; **a materialize-dropped
  delta or an invalid scoped sheet → `status "degraded"` with the drop
  listed** (never ok+empty); all scoped sheets invalid → `failed`, one
  LLM call total; `POST .../audit` returns fresh mechanics + edits (400
  without a module); PUT applies an approved sheet edit and the sheet
  reads back changed; PUT with a conflicting edit returns it in
  `"sheet_failures"` and the live value survives; **double-save of the
  same panel** — the second save reports the edit in `"sheet_failures"`
  (already applied) and the sheet value is unchanged; sheet PUT with a
  stale `expected` (fields **or** sheet_type) → 409, and without
  `expected` → 422.
- **Frontend**: warnings section renders and clears; "audited clean" hint
  on ok+empty; degraded notice lists `dropped` reasons; failed/degraded →
  Retry validation flow (retry → fresh warnings and sheet rows replace
  the old ones); sheet edit row is read-only (no textarea) and
  approve/reject round-trips through save; `sheet_failures` notice
  renders from the PUT response with per-row reasons; SheetEditor 409 →
  reload + notice; a test asserting the displayed after string matches
  the persisted sheet value after save.
- **Milestone check** (verify skill, mocked OpenRouter): scripted scene
  with a logged roll and narrated damage → absorb shows the warning +
  delta, save lands the delta on the sheet; re-running absorb on the same
  scene (baseline unchanged, current updated) proposes no second delta;
  a scripted audit failure shows the degraded notice and Retry recovers.

## Out of scope

Narration-driven stat raises (manual Advance only); deltas for sheeted
entities beyond present cast + current location (items need presence
tracking first); persisting warnings or audit status; typed in-row editing
of sheet deltas (reject-and-edit-the-sheet covers it); ingestion-time
auditing (`ingest_scene.py` runs no mechanics); auditing scenes
retroactively (the audit sees one scene at end-scene time, like absorb
itself); a sheet mutation ledger / operation ids (see Decisions — strict
CAS inside the apply critical section, plus `gen`-based baseline validity,
report or suppress every ambiguous case instead of auto-resolving it);
revision tokens (the full-snapshot compare *is* the revision check for a
single-process local store); **whole-chronicle-save idempotency** — the
pre-existing `PUT .../chronicle` re-appends timeline events and rewrites
the chronicle record on a replayed save; Phase 5 keeps its own
contribution retry-safe (CAS can never double-apply a sheet edit — a
replay reports instead) but does not redesign the save transaction, which
predates this phase and affects prose edits equally. If save idempotency
is wanted it is a milestone of its own (idempotency token on the PUT,
upserted timeline events) and is noted here as a known limitation, not
silently ignored.

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
