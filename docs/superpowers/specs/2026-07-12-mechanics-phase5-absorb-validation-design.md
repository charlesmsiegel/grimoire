# Mechanics Phase 5 — narrated-event validation

Full design for Phase 5 of the Mechanics & Dice milestone (roadmap issue
#826), superseding `2026-07-12-mechanics-phase5-absorb-validation-draft.md`.
Depends on Phase 4 (landed). When a campaign resolves to a module, the
end-scene absorb pass also audits the transcript against the sheets and the
roll log: it flags narration that contradicts mechanics, and proposes sheet
deltas through the existing StagedEdit review flow.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Architecture | A second, focused LLM call after the main absorb call; best-effort (dossier pattern: any failure leaves absorb untouched) | The absorb system prompt is already one dense block with ~13 output keys; numeric bookkeeping against sheets + a roll log is a different cognitive task from narrative summarization. The route already fires extra best-effort calls (dossiers), so the pattern exists. Costs one extra call per end-scene, module-bound campaigns only. |
| Warnings | Ephemeral: rendered in the absorb panel, gone when the panel closes; never persisted | Warnings are advisory — the player reads them, maybe fixes something by hand, moves on. No bookkeeping. |
| XP / advancement | No exception to the mutable-only rule. XP *awards* are ordinary resource deltas (advancement pools are structurally `resource` fields — `sheets.advance` requires `{current, max}`); *raises* stay behind the manual Advance button | The rule stays clean with zero special cases; narration-driven stat raises were the questionable half and remain out of scope. |
| Apply semantics | Absolute overwrite — the approved `after` is exactly what lands, same contract as every other absorb edit kind | The review diff shows the guaranteed final value; the stale window is one open panel. Zero new machinery. |
| Sheet scope | Present sheeted cast + the sheeted current location — the same scope as Phase 4's `mechanics_sheets` context section. The model sees **full** compact sheet blocks (static stats included, so contradictions involving them are visible) with mutable fields explicitly marked as the only delta-eligible ones | Letting the model propose deltas against sheets it never saw is guesswork. Item sheets wait for item presence tracking (future); location wards are covered because the sheeted current location is in scope. |
| Delta granularity | One StagedEdit per (entity, field), independently approvable | Matches how the panel works; a rejected essence spend shouldn't drag down an approved XP award. |

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
  resource, damage that never landed on a sheet. Deltas cover mechanical
  state the scene visibly changed: damage, resource spends and recoveries,
  XP awards, list gains/losses. Never dispute a logged roll; never propose
  a change to a static field; never change a resource's `max`. `id` is the
  `kind:eid` header printed on the sheet block; `value` is the complete new
  field value (`{"current": n}` for resources, an int for tracks, the full
  new list for lists); `note` is one sentence of justification shown to the
  reviewer.
- **`sheet_blocks(cid, sid) -> list[str]`** — one compact block per present
  sheeted cast member plus the sheeted current location, rendered like
  Phase 4's sheet summaries (header `kind:eid — Type (Name)`; one line per
  field), with each mutable field (`resource`, `track`, `list`) marked
  delta-eligible and everything else marked static. Invalid sheets
  (non-empty `errors`) are skipped — absorb must not propose deltas against
  a sheet the engine itself can't read.
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
  "note": str}}`. Rendering: resources as `essence 6/10`, tracks as ints,
  lists one item per line — the diff a reviewer reads is the exact value
  that lands.
- **`absorb.apply_edits` gains a `"sheet"` branch**: read the live sheet,
  set the one field from `payload["value"]` (resources: `current` from the
  payload, `max` from the live field), write via `sheets.write` with the
  sheet's stored `sheet_type`. `SheetError` → the edit is skipped (existing
  best-effort posture). `"sheet"` is **not** added to `_BROWSABLE_KINDS` —
  changes.json tracks browsable prose records, not sheets.

## Route

`POST /api/campaigns/{cid}/scenes/{sid}/absorb`: after the main absorb call
(and its materialize), when `modules.resolve(cid)` is not `None` and the
scope is non-empty, build and fire the audit call, extend `edits` with its
materialized StagedEdits, and add `"mechanics_warnings": [str]` to the
response (empty list when the module is absent, the scope is empty, or the
audit call fails — an audit failure of any kind must never fail absorb, same
contract as dossiers). `PUT .../chronicle` is unchanged: approved `"sheet"`
edits flow through `apply_edits` like every other kind. Module-less
campaigns see zero extra calls and zero behavior change.

## Frontend

- `SceneAbsorb` gains `mechanics_warnings: string[]`; the absorb panel
  renders a warnings section (⚠ per line, informational) above the edits
  list when non-empty. Cleared with the panel; never sent to
  `PUT .../chronicle`.
- `"sheet"` edits render as ordinary approve/reject diff rows — before/after
  are pre-rendered strings, so no new interaction model. The `note` renders
  as the row's hint line.

## Testing

- **`audit.py` unit**: parse tolerance (garbage, non-dict, missing keys →
  empty); materialize gates one by one — unknown entity, out-of-scope
  entity, unsheeted entity, invalid sheet, unknown field, static field
  (`number`/`dots`/`text`), bad value (validation reject), resource `max`
  tamper rejected (only `current` from the payload), no-op dropped; happy
  round-trips for resource / track / list; XP-pool award accepted as a
  plain resource delta.
- **`apply_edits`**: sheet branch happy path; `SheetError` → skipped, other
  edits still applied; sheet edits absent from changes.json.
- **Routes**: module-bound absorb fires exactly two LLM calls and returns
  merged edits + `mechanics_warnings`; module-less absorb fires one and
  returns `mechanics_warnings: []`; audit-call failure (LLMError, garbage)
  still returns a complete absorb; PUT applies an approved sheet edit and
  the sheet reads back changed.
- **Frontend**: warnings section renders and clears; sheet edit row
  approve/reject round-trip through save.
- **Milestone check** (verify skill, mocked OpenRouter): scripted scene
  with a logged roll and narrated damage → absorb shows the warning +
  delta, save lands the delta on the sheet.

## Out of scope

Narration-driven stat raises (manual Advance only); deltas for sheeted
entities beyond present cast + current location (items need presence
tracking first); persisting warnings; ingestion-time auditing
(`ingest_scene.py` runs no mechanics); auditing scenes retroactively
(the audit sees one scene at end-scene time, like absorb itself).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
