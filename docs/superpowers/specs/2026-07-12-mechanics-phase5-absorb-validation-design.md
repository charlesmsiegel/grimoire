# Mechanics Phase 5 — narrated-event validation

Full design for Phase 5 of the Mechanics & Dice milestone (roadmap issue
#826), superseding `2026-07-12-mechanics-phase5-absorb-validation-draft.md`.
Depends on Phase 4 (landed). When a campaign resolves to a module, the
end-scene absorb pass also audits the transcript against the sheets and the
roll log: it flags narration that contradicts mechanics, and proposes sheet
deltas through the existing StagedEdit review flow.

## Decisions (settled 2026-07-12; hardened by Codex adversarial rounds 1–3)

| Decision | Choice | Why |
|---|---|---|
| Architecture | A second, focused LLM call after the main absorb call; best-effort for the *prose* absorb (an audit failure never fails absorb) but **never silent**: the audit reports an explicit status | The absorb system prompt is already one dense block with ~13 output keys; numeric bookkeeping against sheets + a roll log is a different cognitive task from narrative summarization. Codex round 3: a swallowed audit failure must not masquerade as "no contradictions found". |
| Warnings | Ephemeral: rendered in the absorb panel, gone when the panel closes; never persisted | Warnings are advisory — the player reads them, maybe fixes something by hand, moves on. No bookkeeping. |
| XP / advancement | No exception to the mutable-only rule. XP *awards* are ordinary resource deltas (advancement pools are structurally `resource` fields — `sheets.advance` requires `{current, max}`); *raises* stay behind the manual Advance button | The rule stays clean with zero special cases; narration-driven stat raises were the questionable half and remain out of scope. |
| Scene-start baseline | Every scene captures a snapshot of all campaign sheets at creation (`sheet_baselines.json`); the audit prompt shows each mutable field as `start → current`. **No baseline entry for an entity → warnings only; its sheet deltas are suppressed at materialize** | Codex rounds 1+3: current-only values are ambiguous ("current 4 + narration spends 2" cannot distinguish already-applied from still-pending), and *assuming* current-as-start can re-propose an already-applied change whose CAS would then succeed — a silent double-apply. Without a baseline the audit cannot form a sound before-value, so it doesn't get to propose writes at all. |
| Sheet write discipline | **Every** campaign-sheet mutator (`write`, `write_creation`, `advance`, `set_field`, `delete`) serializes on the per-campaign sheet lock; every campaign whole-sheet replacement carries a **mandatory** CAS on the full `{sheet_type, fields}` snapshot (`expected=None` asserts "no sheet exists yet"); `set_field` is a strict per-field CAS | Codex rounds 2–3: `sheets.write` took no lock and replaced the whole map (a pre-existing `write`-vs-`advance` hole), an *optional* CAS left a stale last-write-wins path open, and a fields-only compare could silently revert a concurrent `sheet_type` change. Mandatory full-snapshot CAS closes all three; there is no unsafe replacement path left on campaign sheets. |
| Apply semantics | `set_field` per-field strict CAS: only the approved field is written; live ≠ expect is **always** a visible conflict (reported, never silently overwritten and never silently no-op'd) — including live == proposed-value, which reads "already applied or independently changed" | Codex rounds 1–2: plain absolute overwrite loses concurrent updates, and treating `live == value` as a confirmed retry can mask an independent same-value mutation (two XP awards collapsing into one). Strict CAS needs no operation ledger: every ambiguous case degrades to a reported skip, never wrong state. |
| Write-time authority | `set_field` re-resolves the live sheet schema and enforces the mutable-only rule itself (field exists in the sheet's own type, type ∈ resource/track/list, value validates, resource `max` untouchable) — materialize's checks are a convenience, not the boundary | Codex round 2: chronicle-PUT edits are client-supplied; enforcement only at materialize time would let a crafted or stale client mutate static fields and bypass the manual-advancement invariant. The write boundary is authoritative. |
| Audit visibility | The absorb response carries `mechanics: {status: "ok"\|"failed"\|"skipped", reason, warnings}`; `failed` renders a degraded-validation notice with a **Retry validation** action backed by a standalone audit endpoint | Codex round 3: an empty warnings list must mean "audited clean", never "the audit died". Retry re-runs only the audit — not the whole absorb (and its dossier calls). |
| Sheet-row review UI | Sheet edit rows are **read-only** (approve/reject only); the displayed before/after strings are rendered from the payload that will be applied | Codex round 1: ordinary absorb rows edit `after` in a textarea, but the sheet apply branch writes `payload.value` — an editable row would let the reviewer approve one value while another lands. Read-only keeps display and payload the same fact. Typed value editing can come later if wanted. |
| Sheet scope | Present sheeted cast + the sheeted current location — the same scope as Phase 4's `mechanics_sheets` context section. The model sees **full** compact sheet blocks (static stats included, so contradictions involving them are visible) with mutable fields explicitly marked as the only delta-eligible ones | Letting the model propose deltas against sheets it never saw is guesswork. Item sheets wait for item presence tracking (future); location wards are covered because the sheeted current location is in scope. Scope is a materialize-time filter on model output (prompt trust), not a write-time authorization: the write boundary enforces mutability, and the local single-user client may legitimately edit any sheet through the editor anyway. |
| Delta granularity | One StagedEdit per (entity, field), independently approvable | Matches how the panel works; a rejected essence spend shouldn't drag down an approved XP award. |
| No event ledger | Correctness comes from the baseline (prompt side) + strict CAS (write side) + delta suppression when the baseline is missing, not a mutation ledger | Considered (Codex rounds 1–3) and rejected as disproportionate for a local single-process app: re-running absorb sees `start → current` and doesn't re-propose an applied change; a missing baseline suppresses deltas outright; every racy or replayed apply fails its CAS and is *reported*. No ambiguity is ever silently resolved in either direction — a ledger would only convert some reported skips into automatic decisions. |

## Scene-start sheet baselines

- `<campaign>/sheet_baselines.json` — one JSON object keyed by scene id
  (rolls.json-style whole-file IO, atomic write, never-raise reads):
  `{"<sid>": {"<kind>--<eid>": {"sheet_type": ..., "fields": {...}}}}`.
- **Capture**: `scenes.create_scene` lazily calls
  `audit.capture_baseline(cid, sid)` (the existing lazy-import pattern),
  which snapshots every campaign sheet file verbatim when
  `modules.resolve(cid)` is non-`None` and is a no-op otherwise. This covers
  every creation path (routes, ingest CLI) without the callers knowing.
- **Reads**: `audit.read_baseline(cid, sid) -> dict`. A missing scene entry
  or missing per-entity entry (scene predates Phase 5, module bound after
  creation, sheet created mid-scene) means the audit **cannot form a sound
  scene-start value for that entity**: its block is rendered with current
  values marked `(no scene baseline — report only)`, the model is told it
  may raise warnings about that entity but must not propose deltas for it,
  and `materialize` **drops any delta for a baseline-less entity
  regardless of what the model returns** (assuming current-as-start could
  re-propose an already-applied change, whose CAS would then succeed — a
  silent double-apply; suppression is the only sound answer).
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
- **`sheet_blocks(cid, sid) -> list[str]`** — one compact block per present
  sheeted cast member plus the sheeted current location, rendered like
  Phase 4's sheet summaries (header `kind:eid — Type (Name)`; one line per
  field), with each mutable field (`resource`, `track`, `list`) marked
  delta-eligible and shown as `start → current` against the scene baseline
  (or the `report only` marker above), and everything else marked static.
  Invalid sheets (non-empty `errors`) are skipped — absorb must not
  propose deltas against a sheet the engine itself can't read.
- **`roll_lines(cid, sid) -> list[str]`** — the scene's `rolls.json`
  entries (`entry["scene"] == sid`), one line each: label, notation,
  total/successes, tier when present.
- **`parse_output(text) -> dict`** — same `_obj` posture as absorb (find
  the outermost JSON object, tolerate garbage, never raise): `warnings`
  coerced to a list of non-empty strings, `sheet_deltas` to dicts with
  `id`/`field` strings, `value` kept as-is for materialize to validate,
  `note` string.
- **`materialize(cid, sid, parsed) -> list[dict]`** — the deterministic
  gate (a mirror of the write-time checks plus the scope and baseline
  filters; the authoritative mutability enforcement lives in `set_field`).
  For each proposed delta, in order, drop it unless:
  - the `id` parses as `kind:eid` and is within the shown scope (present
    sheeted cast or the sheeted current location) with a readable,
    error-free sheet;
  - the entity has a baseline entry for this scene (no baseline → no
    deltas, see above);
  - `field` exists in that entity's **own sheet type's** assembled field
    set, with type `resource`, `track`, or `list`;
  - the **canonical value** (see below) passes
    `modules.validate_sheet_values` for the sheet's stored fields overlaid
    with this one change;
  - the canonical value differs from the stored value (no-ops dropped).

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
- **`run(cid, sid, parsed_or_error) -> dict`** — assembles the
  `mechanics` result object (see Route) so the absorb route and the retry
  route share one code path.

## Sheet write discipline — one lock, mandatory CAS at every entry

All in `sheets.py`:

- **Locking.** `write`, `write_creation`, `advance`, `set_field`, and
  `delete` all run their read-validate-write under `_lock_for(cid)` (the
  existing per-campaign lock `advance` already takes). This fixes a
  pre-existing hole — the editor PUT's `sheets.write` could interleave
  with `advance` — and guarantees no writer ever sees a torn
  read-modify-write. World-sheet writes (`write_world*`) are unchanged:
  they have a single writer (the world editor) and no engine writers.
- **Mandatory whole-sheet CAS.** `write` and `write_creation` gain a
  required `expected: dict | None` parameter — the full
  `{"sheet_type": ..., "fields": {...}}` snapshot the caller last read:
  - `expected=None` asserts **no sheet exists yet** (creation); if a file
    exists → `SheetConflict`.
  - `expected` given: compared (under the lock) against the stored
    snapshot — `sheet_type` **and** `fields`; any mismatch →
    `SheetConflict`. Comparing the type too means a concurrent type
    change can never be silently reverted by a stale save whose field
    maps happen to match.
  - There is **no omission path**: every campaign whole-sheet replacement
    states what it believes it is replacing. The only writer that skips
    the check is `seed` (campaign creation — the sheets directory cannot
    have concurrent writers before the campaign exists).
  - Routes: the campaign sheet PUT and the creation-wizard POST require
    the client to send `expected` (the snapshot as loaded; `null` when
    creating) and map `SheetConflict` to **409**; the SheetEditor and
    CreationWizard send it and on 409 reload with a "changed elsewhere —
    reloaded" notice.
- **`set_field(cid, kind, eid, field_key, value, expect) -> None`** — the
  absorb apply primitive, strict per-field CAS under the same lock:
  1. resolve the module and read the live sheet file (missing either →
     `SheetError`);
  2. **enforce mutability at the write boundary**: `field_key` must exist
     in the live sheet's own type's assembled field set with type
     `resource`, `track`, or `list` — anything else raises `SheetError`
     regardless of what the client sent (chronicle-PUT edits are
     client-supplied; materialize is not the boundary, this is);
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
     atomically. Concurrent edits to *other* fields always survive.
- **`SheetConflict(SheetError)`** — carries entity, field/type context,
  expected and live values for the route/UI to report.

## Apply — the `"sheet"` edit kind

`absorb.apply_edits` gains a `"sheet"` branch calling `sheets.set_field`
with `payload["value"]`/`payload["expect"]`. `SheetConflict` is caught and
recorded (edit id + human-readable reason); any other `SheetError` is
skipped like every other broken target. `apply_edits` returns
`(applied, conflicts)` — `conflicts` a list of `{"id", "reason"}` — and
`PUT .../chronicle`'s response gains a `"conflicts"` key. `"sheet"` is
**not** added to `_BROWSABLE_KINDS` — changes.json tracks browsable prose
records, not sheets.

## Routes

- `POST /api/campaigns/{cid}/scenes/{sid}/absorb`: after the main absorb
  call (and its materialize), run the audit step and merge its result:
  `edits` is extended with the audit's materialized StagedEdits, and the
  response gains a `"mechanics"` object:
  - `{"status": "skipped", "reason": "no module" | "no sheeted scope",
    "warnings": []}` — module-less campaign or empty scope; zero extra
    LLM calls;
  - `{"status": "ok", "reason": null, "warnings": [str]}` — the audit ran
    and parsed (an empty `warnings` now genuinely means "audited clean");
  - `{"status": "failed", "reason": <human-readable cause: LLM error,
    unparseable output>, "warnings": []}` — the audit died; the prose
    absorb result is returned intact (best-effort contract), but the
    failure is explicit, never disguised as a clean audit. Unparseable
    output (an audit reply with no recoverable JSON object) is `failed`,
    not "zero findings".
- `POST /api/campaigns/{cid}/scenes/{sid}/audit` — **retry validation
  standalone**: re-runs only the audit call (not the prose absorb, not
  the dossiers) and returns `{"mechanics": {...}, "edits": [...]}` with
  freshly materialized sheet edits (fresh `expect` values). 400 when the
  module is absent. Registered before generic `{kind}` catch-alls per
  house rule.
- `PUT .../chronicle` applies approved `"sheet"` edits through
  `apply_edits` like every other kind and reports `"conflicts"`.

## Frontend

- `SceneAbsorb` gains `mechanics: {status, reason, warnings}`; the absorb
  panel renders, above the edits list:
  - `ok` + warnings → a ⚠ warnings section (informational);
  - `ok` + no warnings → a one-line "mechanics audited clean" hint;
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
- `saveAbsorb` reads `conflicts` from the PUT response and surfaces a
  notice ("N sheet change(s) skipped — the field changed while the panel
  was open"), listing the affected labels.
- `SheetEditor` and `CreationWizard` send `expected` on save and handle
  409 by reloading with a notice.

## Testing

- **Baselines**: capture on create_scene (module-bound) / no-op (unbound);
  read fallback for missing scene / missing entity; repoint via
  `scene_refs.repoint`; malformed file tolerated; **regression: an entity
  with no baseline entry yields zero StagedEdits no matter what the model
  proposes** (the already-applied-change double-propose path).
- **`audit.py` unit**: parse tolerance (garbage, non-dict, missing keys →
  empty); materialize gates one by one — unknown entity, out-of-scope
  entity, unsheeted entity, invalid sheet, baseline-less entity, unknown
  field, static field (`number`/`dots`/`text`), bad value (validation
  reject), resource `max` tamper ignored (canonical value takes the live
  `max`), no-op dropped; happy round-trips for resource / track / list;
  XP-pool award accepted as a plain resource delta; `before`/`after`
  strings render from `payload.expect`/`payload.value`; canonical forms
  everywhere (a `{current}`-only model value never survives materialize
  uncanonicalized).
- **`sheets.set_field`**: happy path per type; write-boundary enforcement
  with materialize bypassed — static field (`number`/`dots`/`text`),
  unknown field, field from another sheet type → `SheetError`; resource
  `max` tamper ignored (live `max` wins); single-field isolation (an
  unrelated field changed between materialize and apply survives the
  write); conflict cases — live ≠ expect with live ≠ value, **and** live
  == value (duplicate-save/independent-mutation ambiguity) — both raise
  `SheetConflict`; validation reject leaves the file untouched.
- **`sheets.write`/`write_creation` CAS**: `expected` snapshot match →
  write; `fields` mismatch → `SheetConflict`; **`sheet_type` mismatch with
  identical fields → `SheetConflict`** (the silent type-revert case);
  `expected=None` with an existing file → `SheetConflict`;
  `expected=None` with no file → creates.
- **Locking races** (threaded): `set_field` vs `advance` — both complete,
  neither write lost; `set_field` vs editor `write` with a **stale
  snapshot** — the stale write 409s and the delta survives; editor
  `write` vs `advance` (the pre-existing hole) — serialized, nothing
  lost.
- **`apply_edits`**: sheet branch happy path; `SheetConflict` → recorded
  in `conflicts`, other edits still applied; `SheetError` → skipped;
  sheet edits absent from changes.json.
- **Routes**: module-bound absorb fires exactly two LLM calls and returns
  merged edits + `mechanics.status == "ok"`; module-less absorb fires one
  and returns `status "skipped"`; audit LLM failure and unparseable audit
  output → `status "failed"` with a reason while the prose absorb result
  is intact; `POST .../audit` returns fresh mechanics + edits (and 400
  without a module); PUT applies an approved sheet edit and the sheet
  reads back changed; PUT with a conflicting edit returns it in
  `"conflicts"` and the live value survives; **double-save of the same
  panel** — the second save reports the edit in `"conflicts"` (already
  applied) and the sheet value is unchanged; **crafted PUT** with a
  static-field / unknown-field / foreign-entity `"sheet"` edit → not
  applied (write-boundary rejection), other edits unaffected; sheet PUT
  with a stale `expected` (fields **or** sheet_type) → 409, and without
  `expected` → 422.
- **Frontend**: warnings section renders and clears; "audited clean" hint
  on ok+empty; degraded notice + Retry validation flow (failed → retry →
  fresh warnings and sheet rows replace the old ones); sheet edit row is
  read-only (no textarea) and approve/reject round-trips through save;
  conflict notice renders from the PUT response; SheetEditor 409 → reload
  + notice; a test asserting the displayed after string matches the
  persisted sheet value after save.
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
CAS plus baseline-suppression report every ambiguous case instead of
auto-resolving it); revision tokens (the full-snapshot compare *is* the
revision check for a single-process local store).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
