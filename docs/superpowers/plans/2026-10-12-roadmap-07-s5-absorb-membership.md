# 07-S5 Absorb membership implementation plan

> Execute inline with superpowers:executing-plans. The user authorized all slices,
> frequent coherent commits and independent brutal-review gates. Focused checks
> once per change; one combined full branch gate before completion.

**Goal:** Stage explicit cited joins/leaves through the existing Absorb call and
apply approved per-actor deltas without confusing an old review with a new group.

**Spec:** ../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md,
sections4.5,9,17.4 and17.9. Preparation review:
../validation/2026-10-12-spec-07-s5-preparation-review.md.

**Theory:** Group files remain the only membership authority. A host-generated
review token names the source identity before generation, and the commit's frozen
position targets retain that identity through recovery. A row changes one actor's
membership boolean; unrelated raw tokens and Leader remain outside its authority.
Serialization/schema models are plain Pydantic; schema adaptation is a separate
leaf and never converts raw null/omission into a clear instruction.

## Constraints and ownership

- Preserve every existing extraction section and citation/identity signal.
  No extra model call. Render/pass the same model-derived schema through the
  existing inference operations facade, retaining capability/refusal/retry/budget
  and metering ownership. Shared llm_fakes only; no inline model doubles.
- No Field, validators, ConfigDict, dataclasses or serialization methods inside
  model definitions. External v1/v2 adapters own schema/dump differences.
- Never trust a model/client filesystem path or allow a first saver to mint/adopt
  a submitted lifecycle token. Strict complete ledgers: absence alone is empty.
- World barrier precedes discovery and campaign/hold_all, under module-edit where
  applicable. Key it by host-resolved World/store roots; hold across registration
  and initial reservation, release before model generation. Existing inner helpers
  reuse outer holds. Capture and save recheck roots/binding after ordered holds.
- Historical captured sources belong in lifecycle discovery even when their
  campaign now binds elsewhere. Actual record mutation retains bound_dependents;
  inherited pending work is fenced separately for the historical-source union.
- Current owned groups survive unrelated World identity changes/rebinding. An
  inherited token survives a copy only through a trusted host receipt. Observed
  provenance mismatch retires durably; A->B->A does not restore authority.
- Use existing group_refs actor grammar, native alias proof and overlay copy
  boundary. No case folding, auxiliary membership facts, actor/card N+1 or
  frozen fixture regeneration. Public placeholder names only.
- Commit plan and each coherent implementation increment; do not stage incomplete
  unrelated work. Native plan gate precedes code; native code/final plus independent
  brutal review follow frozen code. Landed remains blank.

## File map and interfaces

Existing owners: store/commits.py for every reservation/checkpoint/finalization;
store/locks.py/proclock.py for process-scoped ordering; store/overlay.py,
store/reclassify.py, store/sync.py and campaigns/lifecycle.py for identity changes;
store/journal.py/undo.py for reversal lifetimes; store/absorb/{parse,prompt,
snapshots,materializer,conflicts,apply,publish,routing}.py for the existing review;
routes/scenes.py for pre-generation _Prepared and PUT /chronicle; templates/absorb;
frontend/components/review and their existing category/editor owners; evals graders,
cases and shared backend/tests/llm_fakes.py.

New leaves: store/group_proposals.py owns only strict host-provenance token-ledger
I/O and identity transitions; imports atomic/group_refs/locks/path owners, never
membership or overlay. store/absorb/models.py owns plain extraction models.
grimoire/model_schema.py owns generic Pydantic v1/v2 schema normalization outside
stdlib-only schemas.py. Higher snapshot/lifecycle/apply owners compose the leaves;
no function-body imports or cycles. Classify new campaign mutators accurately and
hold the campaign domain on every RMW; reasoned existing caller-held conventions
only where the caller actually owns the hold.

A proposal capture carries canonical host store/campaign roots, source layer,
World root for inherited records, group ref and opaque token. Store token records
at membership_review_tokens.json. Host capture produces a Pydantic value threaded
through _Prepared into staging, never parsed from model output. Initial commit
progress records membership_targets by ORIGINAL approved edit position. Progress
checkpoints preserve those targets and retirement/suspension fences.

Staged row: kind=membership, group ref groups:<id>, field=members:<actor>, payload
contains validated actor/change and host token, before/after are per-actor binary
status. Routing subject is actor. Membership is not MERGEABLE. Citation/provenance
and diff keys retain that exact actor field. Shape validation is shared by batch
preflight, first apply and resume, before any target read.

### Task 1: Strict commit-ledger foundation

- [ ] Add focused RED cases for corrupt JSON/UTF8/read denial, nonobject documents,
  malformed entries/epochs, unsupported scalar versions and legacy tokens named
  tokens/scenes/v. Keep every valid legacy sibling and existing progress.
- [ ] Replace commits._read's fail-soft boundary for ALL readers and RMW owners:
  lookup/reserve/_put/checkpoint/record/scene_epoch/retire_scene/repoint_scenes.
  FileNotFound is empty; everything else raises the shared identity-repair409.
  Validate legacy flat maps and explicit v2 without silently discarding entries.
- [ ] Extend reserve with optional initial progress/targets; persist them with
  reservation before effects. Never let checkpoints, pruning or finalize clear
  lifecycle fences or replace a newer fenced progress with a stale worker copy.
- [ ] Run focused commits tests and ownership/import guards once; static ratchets.
  Commit the strict foundation independently.

### Task 2: Host token ledger, ordered barrier and lifetime transitions

- [ ] RED token absence/corruption, preserve-on-edit, revoke-before-delete/reclassify,
  inherited vs owned, same-slug recreation, root/binding mismatch and A->B->A.
  Add a two-process barrier/discovery-versus-registration test with bounded waits.
- [ ] Add strict token-ledger read/register/validate/retire/relocate operations.
  Registration records host provenance; validation refuses unknown/submitted tokens,
  unsupported/malformed entries and root changes. Retired identities cannot revive.
- [ ] Add a pinned process-scoped World lifecycle barrier using existing proclock
  and registry. Acquisition must precede campaign holds; expose held-state checks
  and reset registries after fork consistently with existing locks.
- [ ] Wrap World group delete/demote/reclassify and pre-generation/save capture entry
  points before dependent enumeration. Discover current dependents plus strict
  host-scanned historical token and unfinished-commit source claims. Under hold_all,
  recheck current bindings, preflight all identity ledgers, then fence token and
  journal/commit targets before mutation. Non-group reclassification suspends;
  delete permanently retires; actual suffixed destinations retarget originals.
- [ ] Extend owned group lifecycle and deferred commit publications using the same
  semantics. Already-applied results stay historical, while new active citation,
  diff and Undo offers are suppressed for removed/retired/suspended targets. Pending
  intents and unconfirmed writes remain distinguishable, never assumed applied.
- [ ] Focused token/lifecycle/lock checks, cross-process ordering and static guards;
  commit coherent token/barrier and lifecycle increments separately if useful.

### Task 3: Typed extraction and model-derived structured output

- [ ] RED exact raw section coverage, nested closed schema, citation nullability,
  single Literal enums, ref inlining, from_ -> wire from, v1/v2 parity and unsupported
  constraint refusal. Raw character/group-state null or omission keeps stored data;
  explicit empty strings clear. Keep existing string-only due semantics.
- [ ] Declare Citation and typed rows for timeline/facts/character_state/group_state,
  lore/authored/relationships/bonds/plot/commitments/new characters/locations/lore/
  weather plus MembershipChange(group,actor,Literal[join,leave],cite). AbsorbExtraction
  owns all existing sections plus membership_changes default empty. Group-state fields
  are goals/resources/focus/public_perception/secrets. No model boilerplate aliases.
- [ ] Normalize Pydantic schemas externally: inline refs/defs; remove title/default;
  objects closed and every property required; preserve nullable branches across
  v1/v2; single literal enum; explicit from_ wire rename. Reject unsupported constraints
  rather than silently dropping them. schemas.check verifies the resulting dialect.
- [ ] Render schema_json in absorb/system.j2 and pass that EXACT schema to the existing
  operations.generate(absorb) call. Keep tolerant extract_object/parse_output for old
  raw replies, discard malformed membership rows and preserve usable existing rows.
  Do not serialize typed defaults into raw input before presence/null decisions.
- [ ] Shared-fake structured mode and legacy raw regression cases plus actual Pydantic1
  schema parity checks; static model/import/usage guards; commit typed contract.

### Task 4: Capture before generation and durable initial reservation

- [ ] RED delete/recreate while generation is blocked, binding/store switches,
  malformed token/commit ledgers before LLM/effects, owned-copy receipt continuity,
  and resume with missing/retired initial targets.
- [ ] Capture one effective coupled group snapshot and cast affiliations under the
  ordered World barrier/campaign hold; register host tokens before generation.
  Convert _Prepared snapshot carrier to plain Pydantic and carry token/provenance
  values to materializer and pending review. Release locks before any LLM await.
- [ ] group_snapshot(cid,cast=()) uses one inverse, independent roles, public blocks
  and explicit narrator-framed secret blocks. Never include GM-only names/state in
  any model input. Cast intersection comes from the owning appearance roster.
- [ ] Reject an empty chronicle commit_token before target reads or ANY effects
  when the submitted save contains membership rows. Preserve supported tokenless
  non-membership saves. A group lifecycle token alone cannot supply durable
  per-position targets/receipts: commits.reserve/checkpoint deliberately do nothing
  for an empty commit token. Pin409 with no membership write or deferred publication.
- [ ] PUT /chronicle reacquires barrier before campaign hold, rechecks provenance,
  freezes approved positions and fingerprints, validates targets, and passes initial
  membership_targets to reserve before writes. Resume uses persisted identities and
  request positions; no current-slug adoption or fresh token mint. Preserve legacy
  non-membership saves and existing epoch/watermark fencing.
- [ ] Focused host flow tests with existing shared fakes and static usage/locks; commit.

### Task 5: Membership parsing, staging and routing

- [ ] RED valid joins/leaves, malformed/nonactor refs, unknown/GM group, duplicate
  pair first-wins, dangling join, same-reply new actors/groups and no-op drops.
- [ ] Resolve only existing visible groups by bare/slash/colon forms. Actor validation
  shares group_refs and actor_exists. No creation fallback and no model token authority.
  Use effective before values; no-op rows disappear. Stage host token and exact actor
  field with existing routing.review subjects=(actor,), evidence and independent bands.
- [ ] Extend conflicts.current_value/target_key/survey with per-actor status and strict
  shape preflight. Membership is binary CAS, never text MERGEABLE. For this kind only,
  current==after is a settled no-op, leaving all other conflict contracts unchanged.
- [ ] Focused parser/stager/routing/conflict cases and static checks; commit.

### Task 6: Per-actor apply, neutral first copy, recovery and Undo

- [ ] RED malformed hand-edited approvals before reads; concurrent other-actor edits;
  preserve invalid legacy tokens; joins require actor existence, dangling leaves clean;
  effective settled rows make no copy. Verify exact actor citation and diff keys.
- [ ] Freeze approved batch shape and capture current source under the held scope.
  Build a neutral FIRST-COPY baseline only for inherited groups: remove filtered
  Members tokens only for approved actors that begin effectively absent, never Leader,
  pending leave tokens or unrelated unresolved actors. No preparation-only write.
- [ ] Reuse overlay._materialize_flat prepare/source_text/reviewed_slots and original
  sync base discipline through a deliberate preparation hook on update_entity. Apply
  ONLY current row's edited_line delta and exact Members actor consent. Revalidate final
  proposed refs atomically under hold; owned groups keep authored metadata unchanged.
- [ ] When a current safe leave can remove a missing World actor token, select that row
  first in deterministic canonical order while maintaining original outcome positions.
  If no safe writer exists, fail those group rows without a copy. No batch-wide consent,
  implicit Leader authority, future join/citation receipt or guessed crash outcome.
- [ ] Thread selected writer through ordinary attempted/apply/checkpoint receipts and
  deferred publications. Resume uses original frozen targets/fingerprints; validate
  fences again before cited/journalled/recorded outputs, retaining historical results.
- [ ] Add per-actor bool Undo descriptor under existing scoped target/retire/suspend
  checks; edited_line restores ONLY that actor after CAS. Never dematerialize; preserve
  other actors, Leader, raw legacy tokens and the original World scope. Clear only that
  actor's provenance key; no foreign group recreation through Undo.
- [ ] Focused interrupt/resume/lifecycle/neutral-copy/Undo cases and owning guards;
  commit writer and recovery increments whenever coherent.

### Task 7: Review UI, eval contract and slice gates

- [ ] Membership row category alongside relationships; display before/after status as
  spans, retaining approval/rejection/citation/conflict controls. No bond textarea or
  raw metadata/internal token/path exposed. Add focused drawer tests.
- [ ] Update malformed [null] section counter and compliant no-summary/laundered rows
  with membership_changes []; add required Mara membership extraction needle. Preserve
  campaign_flow cassette and frozen home byte-for-byte; shared model fakes only.
- [ ] Run focused acceptance/model schema parity and UI/type/static once per change.
  Independent brutal-review on frozen S5, actual native /codex:review, then actual final
  /codex:adversarial-review against full originating spec4.5/9/17.4. Reproduce and fix
  only supported findings with coherent commits.
- [ ] Combined branch make check with explicit worktree Python and actual3.11/Pyd1
  target; isolated verify browser with mocked OpenRouter and synthetic store. Record
  exact evidence/limitations; check only passed roadmap stages, Landed remains blank.

## Adversarial acceptance anchors

1. Capture Salt Circle, delete/recreate it byte-identically during generation: old
   review and unfinished/deferred work cannot write, cite or offer Undo for replacement.
2. Capture inherited Realm source, bind Saltmarch, mutate Realm, bind Realm again:
   historical-source fencing refuses old token; independent owned original survives.
3. Inherited effective Mara + filtered Winifred, approved Mara leave/Winifred join:
   neutral copy applies first actor only; other actor cannot bind before its receipt.
4. Inherited missing non-detached Winifred + valid Mara, both leaves: safe Winifred
   cleanup can run first with original-position receipts; no safe writer means no copy.
5. Crash after actor write but before citation/diff/Undo; delete/reclassify then resume:
   durable progress fences suppress active stale publications while preserving history.
6. Raw state null/omitted keeps; explicit empty clears. Model schema defaults never
   erase that distinction. Structured-capable and legacy/refusal paths retain facade
   budgeting, metering and cancellation with exactly one extraction call.

7. A valid group lifecycle token with empty chronicle commit_token is refused
   before any authoritative write/deferred publication; legacy tokenless saves
   containing only non-membership edits retain their existing behavior.
