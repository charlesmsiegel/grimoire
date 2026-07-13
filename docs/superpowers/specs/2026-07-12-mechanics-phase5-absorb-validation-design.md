# Mechanics Phase 5 — narrated-event validation

Full design for Phase 5 of the Mechanics & Dice milestone (roadmap issue
#826), superseding `2026-07-12-mechanics-phase5-absorb-validation-draft.md`.
Depends on Phase 4 (landed). When a campaign resolves to a module, the
end-scene absorb pass also audits the transcript against the sheets and the
roll log: it flags narration that contradicts mechanics, and proposes sheet
deltas through the existing StagedEdit review flow.

## Decisions (settled 2026-07-12; hardened by Codex adversarial review round 1)

| Decision | Choice | Why |
|---|---|---|
| Architecture | A second, focused LLM call after the main absorb call; best-effort (dossier pattern: any failure leaves absorb untouched) | The absorb system prompt is already one dense block with ~13 output keys; numeric bookkeeping against sheets + a roll log is a different cognitive task from narrative summarization. The route already fires extra best-effort calls (dossiers). Costs one extra call per end-scene, module-bound campaigns only. |
| Warnings | Ephemeral: rendered in the absorb panel, gone when the panel closes; never persisted | Warnings are advisory — the player reads them, maybe fixes something by hand, moves on. No bookkeeping. |
| XP / advancement | No exception to the mutable-only rule. XP *awards* are ordinary resource deltas (advancement pools are structurally `resource` fields — `sheets.advance` requires `{current, max}`); *raises* stay behind the manual Advance button | The rule stays clean with zero special cases; narration-driven stat raises were the questionable half and remain out of scope. |
| Scene-start baseline | Every scene captures a snapshot of all campaign sheets at creation (`sheet_baselines.json`); the audit prompt shows each mutable field as `start → current` | Codex adversarial review (2026-07-12): current-only values are ambiguous — "current 4 + narration spends 2" cannot distinguish already-applied from still-pending, so the model could double-apply or falsely warn. With the baseline the model sees whether the change already landed. |
| Apply semantics | Per-field compare-and-set under the campaign sheet lock (shared with advancement): only the approved field is written; a same-field concurrent change makes the edit **skip visibly** (reported as a conflict), never silently overwrite | Codex adversarial review (2026-07-12) overturned plain absolute overwrite: whole-map `sheets.write` could revert a concurrent advancement or editor save, including unrelated fields. CAS + single-field write closes both; conflicts are reported, not swallowed. |
| Sheet-row review UI | Sheet edit rows are **read-only** (approve/reject only); the displayed before/after strings are rendered from the payload that will be applied | Codex adversarial review (2026-07-12): ordinary absorb rows edit `after` in a textarea, but the sheet apply branch writes `payload.value` — an editable row would let the reviewer approve one value while another lands. Read-only keeps display and payload the same fact. Typed value editing can come later if wanted. |
| Sheet scope | Present sheeted cast + the sheeted current location — the same scope as Phase 4's `mechanics_sheets` context section. The model sees **full** compact sheet blocks (static stats included, so contradictions involving them are visible) with mutable fields explicitly marked as the only delta-eligible ones | Letting the model propose deltas against sheets it never saw is guesswork. Item sheets wait for item presence tracking (future); location wards are covered because the sheeted current location is in scope. |
| Delta granularity | One StagedEdit per (entity, field), independently approvable | Matches how the panel works; a rejected essence spend shouldn't drag down an approved XP award. |
| No event ledger | Idempotency comes from absolute end-values + the baseline + apply-time CAS, not a mutation ledger | Considered (Codex round 1 suggested one) and rejected as disproportionate: deltas are absolute target values, so re-applying the same edit is a value no-op; re-running absorb sees `start → current` and doesn't re-propose an applied change; a stale panel's same-field save loses the CAS and reports. grimoire is a local single-process app — same posture as Phase 4's crash-window disclosure. |

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
  creation, sheet created mid-scene) degrades to "start unknown": the
  prompt shows the current value alone marked `(start unknown)` and the
  model is told to treat it as the scene-start value.
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
  application gets the corrected end value). Never dispute a logged roll;
  never propose a change to a static field; never change a resource's
  `max`. `id` is the `kind:eid` header printed on the sheet block; `value`
  is the complete new field value (`{"current": n}` for resources, an int
  for tracks, the full new list for lists); `note` is one sentence of
  justification shown to the reviewer.
- **`sheet_blocks(cid, sid) -> list[str]`** — one compact block per present
  sheeted cast member plus the sheeted current location, rendered like
  Phase 4's sheet summaries (header `kind:eid — Type (Name)`; one line per
  field), with each mutable field (`resource`, `track`, `list`) marked
  delta-eligible and shown as `start → current` against the scene baseline
  (or `(start unknown)`), and everything else marked static. Invalid
  sheets (non-empty `errors`) are skipped — absorb must not propose deltas
  against a sheet the engine itself can't read.
- **`roll_lines(cid, sid) -> list[str]`** — the scene's `rolls.json`
  entries (`entry["scene"] == sid`), one line each: label, notation,
  total/successes, tier when present.
- **`parse_output(text) -> dict`** — same `_obj` posture as absorb (find
  the outermost JSON object, tolerate garbage, never raise): `warnings`
  coerced to a list of non-empty strings, `sheet_deltas` to dicts with
  `id`/`field` strings, `value` kept as-is for materialize to validate,
  `note` string.
- **`materialize(cid, sid, parsed) -> list[dict]`** — the deterministic
  gate. For each proposed delta, in order, drop it unless:
  - the `id` parses as `kind:eid` and is within the shown scope (present
    sheeted cast or the sheeted current location) with a readable,
    error-free sheet;
  - `field` exists in that entity's **own sheet type's** assembled field
    set, with type `resource`, `track`, or `list`;
  - the new value passes `modules.validate_sheet_values` for the sheet's
    stored fields overlaid with this one change (for resources the proposed
    value sets `current` only; `max` is copied from the stored field);
  - the result differs from the stored value (no-ops dropped).

  Surviving deltas become StagedEdits:
  `{"id": "sheet:{kind}:{eid}:{field}", "kind": "sheet",
  "target": {"kind", "id"}, "label": "<Name> — <field label> (sheet)",
  "field": <field key>, "before": <rendered>, "after": <rendered>,
  "authored": false, "payload": {"field": key, "value": <structured>,
  "expect": <structured live value at materialize time>, "note": str}}`.
  Rendering: resources as `essence 6/10`, tracks as ints, lists one item
  per line. `before`/`after` are rendered **from** `payload.expect` /
  `payload.value`, so what the reviewer reads is by construction the value
  the apply step uses.

## Apply — `sheets.set_field` + the `"sheet"` edit kind

- **`sheets.set_field(cid, kind, eid, field_key, value, expect) -> None`**,
  new in `sheets.py`, runs entirely under `_lock_for(cid)` — the **same**
  per-campaign lock `advance` takes, so it can never interleave with an
  advancement's read-modify-write:
  1. read the live sheet file (module must resolve; sheet must exist);
  2. if the live field value equals `value` already → return (no-op —
     an idempotent retry, not a conflict);
  3. if the live field value differs from `expect` → raise
     `SheetConflict(SheetError)` naming the field and both values —
     someone changed this field since the panel was materialized;
  4. otherwise build the new field map by changing **only** `field_key`
     (for resources: `current` from `value`, `max` from the live field),
     validate the full map via the existing strict write path, and write
     atomically. Concurrent edits to *other* fields always survive.
- **`absorb.apply_edits` gains a `"sheet"` branch** calling `set_field`
  with `payload["value"]`/`payload["expect"]`. `SheetConflict` is caught
  and recorded (edit id + reason); any other `SheetError` is skipped like
  every other broken target. `apply_edits` returns
  `(applied, conflicts)` — `conflicts` a list of `{"id", "reason"}` —
  and `PUT .../chronicle`'s response gains a `"conflicts"` key. `"sheet"`
  is **not** added to `_BROWSABLE_KINDS` — changes.json tracks browsable
  prose records, not sheets.

## Route

`POST /api/campaigns/{cid}/scenes/{sid}/absorb`: after the main absorb call
(and its materialize), when `modules.resolve(cid)` is not `None` and the
scope is non-empty, build and fire the audit call, extend `edits` with its
materialized StagedEdits, and add `"mechanics_warnings": [str]` to the
response (empty list when the module is absent, the scope is empty, or the
audit call fails — an audit failure of any kind must never fail absorb, same
contract as dossiers). `PUT .../chronicle` applies approved `"sheet"` edits
through `apply_edits` like every other kind and reports `"conflicts"`.
Module-less campaigns see zero extra calls and zero behavior change.

## Frontend

- `SceneAbsorb` gains `mechanics_warnings: string[]`; the absorb panel
  renders a warnings section (⚠ per line, informational) above the edits
  list when non-empty. Cleared with the panel; never sent to
  `PUT .../chronicle`.
- `"sheet"` edits render as **read-only** approve/reject diff rows —
  before/after as fixed text (no textarea), the `note` as the row's hint
  line. The row offers no value editing; a reviewer who wants a different
  number rejects the edit and changes the sheet in the editor.
- `saveAbsorb` reads `conflicts` from the PUT response and surfaces a
  notice ("N sheet change(s) skipped — the field changed while the panel
  was open"), listing the affected labels.

## Testing

- **Baselines**: capture on create_scene (module-bound) / no-op (unbound);
  read fallback for missing scene / missing entity; repoint via
  `scene_refs.repoint`; malformed file tolerated.
- **`audit.py` unit**: parse tolerance (garbage, non-dict, missing keys →
  empty); materialize gates one by one — unknown entity, out-of-scope
  entity, unsheeted entity, invalid sheet, unknown field, static field
  (`number`/`dots`/`text`), bad value (validation reject), resource `max`
  tamper rejected (only `current` from the payload), no-op dropped; happy
  round-trips for resource / track / list; XP-pool award accepted as a
  plain resource delta; `before`/`after` strings render from
  `payload.expect`/`payload.value`.
- **`sheets.set_field`**: happy path per type; single-field isolation (an
  unrelated field changed between materialize and apply survives the
  write); idempotent retry (live == value) is a silent no-op; conflict
  (live ≠ expect, live ≠ value) raises `SheetConflict`; **threaded race
  against `advance`** on the same campaign — both complete, neither's
  write is lost (lock sharing); validation reject leaves the file
  untouched.
- **`apply_edits`**: sheet branch happy path; `SheetConflict` → recorded
  in `conflicts`, other edits still applied; `SheetError` → skipped;
  sheet edits absent from changes.json.
- **Routes**: module-bound absorb fires exactly two LLM calls and returns
  merged edits + `mechanics_warnings`; module-less absorb fires one and
  returns `mechanics_warnings: []`; audit-call failure (LLMError, garbage)
  still returns a complete absorb; PUT applies an approved sheet edit and
  the sheet reads back changed; PUT with a conflicting edit returns it in
  `"conflicts"` and the live value survives; **double-save of the same
  panel** applies once and the second save no-ops.
- **Frontend**: warnings section renders and clears; sheet edit row is
  read-only (no textarea) and approve/reject round-trips through save;
  conflict notice renders from the PUT response; a test asserting the
  displayed after string matches the persisted sheet value after save.
- **Milestone check** (verify skill, mocked OpenRouter): scripted scene
  with a logged roll and narrated damage → absorb shows the warning +
  delta, save lands the delta on the sheet; re-running absorb on the same
  scene (baseline unchanged, current updated) proposes no second delta.

## Out of scope

Narration-driven stat raises (manual Advance only); deltas for sheeted
entities beyond present cast + current location (items need presence
tracking first); persisting warnings; typed in-row editing of sheet deltas
(reject-and-edit-the-sheet covers it); ingestion-time auditing
(`ingest_scene.py` runs no mechanics); auditing scenes retroactively
(the audit sees one scene at end-scene time, like absorb itself); a sheet
mutation ledger (see Decisions — rejected as disproportionate).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
