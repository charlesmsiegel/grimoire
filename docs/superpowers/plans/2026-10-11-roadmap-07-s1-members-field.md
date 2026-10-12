# 07-S1: Members field and safe campaign editing implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver 07-S1's typed Members field and editor with explicit actor-slot consent for inherited copies and reversible campaign hand edits.

**Architecture:** Group frontmatter remains the sole authority. A pure leaf owns actor-ref parsing, inherited filtering and the plain Pydantic one-group context; overlay owns identity reads and atomic prepared copies. The campaign lock joins revision checking, copy validation, mutation and journal sealing; lifecycle fences keep Undo attached to the original owned record.

**Tech Stack:** Python 3.11+, FastAPI, plain Pydantic BaseModel v1/v2, pytest; React/TypeScript and Vitest; existing Markdown/JSON store and atomic writers. No new dependency.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), especially 4.1-4.3, 4.5's S1 boundaries, 5.5, 6.1-6.4, 7.6-7.7, 13.1-13.3, 16, 17.1 and S1 cases of 17.3/17.8. Human and actual Codex spec approval: [review record](../validation/2026-10-11-spec-07-review.md), reviewed `c4dbde83b`, thread `01a12c98-d9dd-7dd1-bbfc-ed6e2f12123b`.

## Global Constraints

These requirements are copied from the spec; every task inherits them.

- "Membership is stored **on the group**, as `members`. The inverse ("Mara's groups") is derived, never stored."
- "No mining of prose" into members, at startup or anywhere automatic.
- "Writers edit the raw token list, never the parsed one."
- "World actor reads are confined to this one-group context and copy validation, not the inverse or its digest."
- "Invalid legacy tokens stay untouched."
- "Non-group requests still cannot submit binding consent".
- "Undo never dematerializes the record."
- "Plain BaseModel membership, API response and extraction models; no new dependency or desktop-path assumption."
- "The response models serialize through the existing route shim."
- "Hold `campaign_lock(cid)` across collision validation, manifest update and publication".
- "The lock does not coordinate external filesystem edits or other devices."

Also follow CLAUDE.md and CONTRIBUTING.md whole: store paths/atomic writers, module imports, no cycles, multiple campaign holds only through hold_all, and preserve issue-linked comments. Models use no dataclasses, Field, validators, ConfigDict or model_dump calls. Fixtures use existing Seraphine/Mara/Winifred/Realm/Saltmarch/Salt Circle examples. Read no private store or regenerate frozen home/snapshot.

**Scope:** One plan/PR for **07-S1 only**. S2 inverse/API/actor pages, S3 sync field rendering, S4 presence, S5 absorb proposals/full model-derived structured schema, S6 graph, S7 projections and S8 persistent cache remain separate. No prompt/schema change, proposal-token ledger, unfinished-commit metadata or inverse index here. Journal lifecycle predicates recognize both new tags, but S1 writes only group_members.

**Publication decision for plan approval:** Adopt Q4's recommended Leader check alongside Members in S1, including dormant fields after reclassification. This deliberately refuses push/promote that currently succeeds when Leader names no World actor or a detached campaign actor. Force bypasses neither. Name this behavior change in the eventual PR.

**Execution:** `.worktrees/spec-07-group-membership`, branch `feat/spec-07-group-membership`, rebased onto origin/main `f816921c8`. Commit docs now and coherent checked implementation within tasks; commit steps below are minimum checkpoints. Human plan review and execution-method choice follow the actual plan gate.

## Review Focus

1. Retained World refs can bind actors created later: Task 3 pins detached/missing-World, separate Members/Leader, unrelated-field and stale-context copy refusal.
2. Corrupt ledgers can lose unrelated markers: Task 2 pins strict reads, unchanged corrupt bytes, migration/reattach/repoint/image refusal before effects and shared-writer preservation.
3. Matching refs/bytes can name replacement groups or actors: Task 4 pins deletion/move/dematerialization/rebinding fences, owned scope and conservative identity-only Undo.
4. Actor birth or a second PUT can race copy/revision checking: Tasks 3/4 pin event-controlled same-lock interleavings and stale-revision refusal.
5. Missing/stale context or late replies can misapply consent: Task 5 pins foreign chips, blocked unsafe save, scope/record/reload/success reset and discarded async replies.

## File structure

Create store/group_refs.py as a pure leaf for shared parsing/filtering, the Pydantic context and conflict error. Create read-only store/membership.py for S1's three public helper exports; overlay never imports membership. entities.py supplies a pure text composer; overlay supplies same-bytes reads, strict ledgers and prepared publication. journal.py supplies lifecycle fencing without importing overlay/undo; undo.py interprets the new tag. Existing routes/editor supply typed exact-slot consent. Authoring docs accompany the field; store guarantees accompany changed preflight/lifetime behavior. No unrelated restructuring.

All paths below are relative to the worktree. Function anchors replace unstable line numbers. Run commands there with `PY=../../backend/.venv/Scripts/python.exe`; the main checkout owns the venv. New tests follow existing isolated-home fixtures/store APIs. Any LLM behavior uses backend/tests/llm_fakes.py, never inline new fakes.

### Task 1: Field, pure helpers and Pydantic context

**Files:** Create `backend/src/grimoire/store/group_refs.py`, `backend/src/grimoire/store/membership.py`, `backend/tests/test_membership.py`. Modify `backend/src/grimoire/store/entity_schema.py` (groups FIELDS), `backend/src/grimoire/store/entities.py` (rewrite_ref_fields doc), `backend/src/grimoire/store/__init__.py`, `backend/tests/store_api_baseline.json` (two deliberate module exports), `frontend/src/api/types.ts` (ENTITY_FIELDS/context/bindings/EntityDetail), `frontend/src/components/EntityEditor.test.tsx` (mock mirror), `.claude/skills/create-world/SKILL.md`, `.claude/skills/world-card-integration/references/house-formats.md`. Test `backend/tests/test_entity_schema.py`, `backend/tests/test_entities_store.py`, `backend/tests/test_create_world.py`, `backend/tests/test_store_api_baseline.py`.

**Interfaces:** Consumes existing entity_schema.parse_refs/referenceable/invalid_values. Produces in group_refs:

```python
ACTOR_KINDS = ("characters", "pcs")
def actor_ref(token: object) -> str | None: ...
def parse_members(meta: Mapping[str, object]) -> tuple[str, ...]: ...
def parse_leader(meta: Mapping[str, object]) -> str | None: ...
def edited_line(raw: object, actor: str, change: Literal["join", "leave"]) -> str: ...
def effective_refs(meta: Mapping[str, object], *, inherited: bool,
                   detached: set[str]) -> tuple[tuple[str, ...], str | None]: ...
def group_slots(meta: Mapping[str, object]) -> frozenset[tuple[str, str]]: ...
```

Plain GroupMembershipContext(BaseModel) fields exactly: layer Literal world/campaign; members tuple[str,...]; leader str|None; filtered_members tuple[str,...]; filtered_leader str|None; unresolved_members tuple[str,...]; unresolved_leader str|None. GroupIdentityConflict(RuntimeError) constructor `(message: str, *, slots: tuple[tuple[str, str], ...] = ())` exposes field/ref pairs; RuntimeError avoids existing ValueError-to-400 handlers. membership module-level aliases export parse_members/parse_leader/edited_line; no inverse yet.

- [ ] **Step 1: Write failing tests.** Add `test_members_field_accepts_only_multi_actor_refs`, `test_the_frontend_ships_the_same_field_table`, `test_parse_members_is_lenient`, `test_edited_line_preserves_unrelated_raw_tokens`, `test_inherited_filter_keeps_owned_and_dangling_refs`, `test_group_context_is_plain_pydantic`.

```python
assert entity_schema.invalid_values("groups", {"members": "characters:mara, pcs:winifred"}) == []
assert entity_schema.invalid_values("groups", {"members": "groups:salt-circle"}) == ["members"]
assert membership.parse_members({"members": "Mara, characters:mara, characters:mara, pcs:winifred"}) == ("characters:mara", "pcs:winifred")
assert membership.parse_members({"members": None}) == ()
assert membership.parse_leader({"leader": "Mara, pcs:winifred, characters:mara"}) == "pcs:winifred"
assert membership.edited_line("Mara, characters:mara, pcs:winifred", "characters:mara", "leave") == "Mara, pcs:winifred"
assert membership.edited_line("Mara, pcs:winifred", "characters:mara", "join") == "Mara, pcs:winifred, characters:mara"
```

Include non-string/legacy/non-actor/traversal/separator/comma/line-break ids, deduplication, idempotent join, duplicate-removing leave and preserved untouched spelling/order. Frontend table test parses controlled current TS literals, compares every kind's ordered keys/widget/kinds/multi/min/max/options/source, normalizes list/tuple only, explicitly rejects unsupported syntax, never eval/Node. Context is BaseModel and route _dump/HTTP JSON preserves fields.

- [ ] **Step 2: Run red.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe "TESTS=backend/tests/test_membership.py backend/tests/test_entity_schema.py backend/tests/test_entities_store.py"`. Expect missing declaration/helper/mirror failures, not fixture/path failures.
- [ ] **Step 3: Implement interfaces and field.** Add `{"key": "members", "label": "Members", "widget": REF, "kinds": ("characters", "pcs"), "multi": True}` after Leader, mirrored identically in TS. Reuse referenceable/parse_refs; first-seen valid actor refs, existence unchecked. Inherited effective filter drops only detached slash refs; owned refs remain authored. Leaf imports stdlib/Pydantic/entity_schema module only. Correct both stale rewrite_ref_fields claims (spec 1.3). Export only the two new modules in facade/baseline.
- [ ] **Step 4: Update authoring and pin creation.** Read writing-skills before editing create-world SKILL.md. Add Members alongside prose roster using current placeholders. In `test_create_world_resolves_members_names_to_actor_refs`, extend _plan's Salt Circle fields with members `["characters:Mara", "pcs:Winifred"]`; after _apply assert stored `characters:mara, pcs:winifred`. Existing create_world.py multi-ref resolver is sufficient; change it only if this test proves otherwise.
- [ ] **Step 5: Verify and commit.** Run Task 1 tests plus test_create_world.py, test_store_api_baseline.py, test_pydantic_guard.py and test_import_guard.py using test-py-fast. Expect all pass. Diff check, stage Task 1 files, commit `feat: declare group members and shared Pydantic context`. Commit checked authoring docs separately if ready earlier.

### Task 2: Strict ledgers before associated effects

**Files:** Modify `backend/src/grimoire/store/overlay.py` (all deleted/detached RMW, creation/reattach/repoint/delete), `backend/src/grimoire/store/sync.py` (advance/reattachment/promote/push/demote preflight), `backend/src/grimoire/store/reclassify.py`, `backend/src/grimoire/store/campaigns/lifecycle.py`, `backend/src/grimoire/store/campaign_images.py`, `backend/src/grimoire/store/world_images.py`, `backend/src/grimoire/store/locks.py` (changed mutator classifications), `backend/src/grimoire/routes/entities.py`, `backend/src/grimoire/routes/characters.py`, `backend/src/grimoire/routes/worlds.py`, `backend/src/grimoire/routes/greetings.py` (World delete wrappers), `docs/store-guarantees.md`. Create `backend/tests/test_group_membership_store.py`; test `backend/tests/test_overlay_tombstones.py`, `backend/tests/test_overlay.py`, `backend/tests/test_campaign_images_store.py`, `backend/tests/test_world_images_store.py`, `backend/tests/test_locks_store.py`.

**Interfaces:** Consumes Task 1 GroupIdentityConflict, existing safe_id/campaign_lock/hold_all/dependent_campaigns. Produces:

```python
def detached_strict(cid: str) -> set[str]: ...
def deleted_strict(cid: str) -> set[str]: ...
def require_identity_ledgers(cid: str) -> tuple[set[str], set[str]]: ...  # detached, deleted
@contextmanager
def world_identity_change(wroot: Path) -> Iterator[tuple[str, ...]]: ...
```

All are overlay functions. World wrapper enumerates current dependents, hold_all, strictly preflights complete ledgers, yields held ids through World mutation/sweep; this is S1, not S5's lifecycle barrier. Existing writer signatures stay intact.

- [ ] **Step 1: Write failing refusal/preservation tests.** Parameterize `test_strict_ledgers_refuse_corruption_before_effects` over truncated/wrong-outer/wrong-entry/invalid-ref/undecodable JSON and PermissionError. Only FileNotFoundError is empty. Detached accepts all known ledger kinds including plotmap/plotmap with safe_id (not stricter actor referenceable). Deleted preserves existing flat/asset/library spellings and duplicates/unrelated entries. For each actor birth, reattach, repoint, delete, image and migration operation assert:

```python
before = ledger_path.read_bytes()
with pytest.raises(group_refs.GroupIdentityConflict):
    operation()
assert ledger_path.read_bytes() == before
assert record_path.read_bytes() == original_record_bytes
assert manifest_path.read_bytes() == original_manifest_bytes
```

Select setup paths that exist and compare optional-file existence separately; failed birth creates no actor directory. `test_world_mutation_preflights_every_dependent` corrupts the second dependent and asserts first/World records, assets and manifests unchanged. Image refusal preserves bytes and descriptions. `test_repoint_and_reattach_preserve_unrelated_markers` covers both complete ledgers.

- [ ] **Step 2: Run red.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe "TESTS=backend/tests/test_group_membership_store.py backend/tests/test_overlay_tombstones.py"`. Expect fail-soft mutation failures; current tombstone locking remains passing.
- [ ] **Step 3: Implement strict full reads/RMW and preflight.** Atomic writes; catch genuine absence only; never repair corrupt data automatically. Retain existing add_deleted/_drop_deleted locks and correct stale add_detached comment claiming their gap. Convert every RMW and earlier selection (_undetach assets, reattachment, repoint) to strict inputs under the same campaign hold. Ordinary non-membership display readers remain fail-soft. Character/PC birth checks both before allocation/publication; movement/delete checks before effects. Migration checks before tombstone writes, manifest reservation, pruning or campaign.md. Sync reattachment rollback cannot snapshot fail-soft empty. Campaign image removal checks before bytes/sidecars. World routes use wrapper before actual removal/forget sweep. World image deletion holds affected campaigns before image_collection/name/sidecar locks; no campaign acquisition inside image or world_actor hold. Demote reuses its existing hold_all.
- [ ] **Step 4: Pin serialization and document guarantees.** Event-controlled `test_group_delete_and_library_restore_preserve_both_markers` pauses one locked writer and proves the other cannot reach its ledger write until release; both final markers survive. Do not invent a missing-lock fix. Add sorted hold_all-order assertion for new holder. Update store guarantees/classifications for actual changed behavior; no reasonless escape/growing UNREVIEWED. Preserve external-device/editor and multi-file transaction limits.
- [ ] **Step 5: Verify and commit.** Run all Task 2 test files plus test_atomic_guard.py, test_lock_domain_guard.py, test_lock_order_guard.py, test_paths_guard.py and test_import_guard.py via test-py-fast. Expect all pass. Diff check and commit `fix: preserve group identity ledgers before store effects`; checked strict-reader/RMW and lifecycle portions may be separate commits.

### Task 3: Same-bytes context, prepared copies and publication

**Files:** Modify `backend/src/grimoire/store/entities.py` (pure composer), `backend/src/grimoire/store/overlay.py` (read/update/materialize), `backend/src/grimoire/store/sync.py` (push/promote/demote), `backend/src/grimoire/store/reclassify.py` (first-copy). Test `backend/tests/test_group_membership_store.py`, `backend/tests/test_overlay.py`, `backend/tests/test_library_move.py`, `backend/tests/test_reclassify_store.py`, `backend/tests/test_locks_store.py`.

**Interfaces:** Consumes Tasks 1/2 parsing/filter/context/strict ledgers. Produces:

```python
# entities.py; existing update_entity delegates composition here.
def updated_text(text: str, *, name: str | None = None, body: str | None = None,
                 keys: str | None = None, owners: str | None = None,
                 sd_prompt: str | None = None, fields: dict[str, str] | None = None,
                 secrecy: str | None = None) -> str: ...
```

Overlay signatures:

```python
def read_group_membership(cid: str, eid: str) -> tuple[dict, GroupMembershipContext]: ...
def group_unresolved(cid: str, meta: Mapping[str, object]) -> frozenset[tuple[str, str]]: ...
def require_group_materialization(cid: str, kind: str, eid: str, *,
                                  source_text: str | None = None) -> None: ...
def _materialize_flat(cid: str, kind: str, eid: str, *,
                      prepare: Callable[[str], str] | None = None,
                      source_text: str | None = None,
                      reviewed_slots: frozenset[tuple[str, str]] = frozenset()) -> bool: ...
```

Extend existing overlay.update_entity's keyword-only arguments with reviewed_slots of the same type/default. read_group_membership returns the ordinary entity payload including rev and a same-bytes context. Produces `sync._require_world_members(cid: str, wroot: Path, text: str, ref: str) -> None`: Members plus adopted Leader validation; missing/detached destination identity uses existing DanglingReferenceError, ledger failure uses GroupIdentityConflict.

- [ ] **Step 1: Write failing matrix tests.** Add `test_unrelated_save_cannot_bind_present_or_future_campaign_actor`, `test_exact_slot_consent_does_not_review_other_member_or_leader`, `test_clear_collision_publishes_only_final_bytes`, `test_dormant_group_refs_cannot_bypass_copy_guard`, `test_one_group_context_and_rev_share_source_bytes`, `test_publication_requires_world_actor_even_when_forced`. Pin inherited/owned, detached/missing World actor (campaign actor absent/present), Members/Leader/both, body-only/unrelated edit/clear/exact consent, materialize/reclassify/demote and group -> lore -> copy -> group.

```python
assert context.layer == "world"
assert context.unresolved_members == ("characters:mara",)
assert ("members", "characters:mara") in conflict.slots
assert not campaign_group_path.exists()  # refusal or injected pre-publication failure
assert world_group_path.read_bytes() == original_world_bytes
```

A safe clear/consented success records base hash of original World bytes and atomically publishes only final safe text. Spy on atomic writes and inject before publication: no unsafe intermediate own file. Owned dangling refs remain authored; nongroup with no dormant refs unchanged. Same-bytes test pauses source read and replaces World content, asserting context and rev describe one selected text. Publication refusal preserves World/base/detachment/assets for push/promote/force, Members/Leader and dormant lore fields; valid/no-ref regressions pass.

- [ ] **Step 2: Run red.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe "TESTS=backend/tests/test_group_membership_store.py backend/tests/test_library_move.py backend/tests/test_reclassify_store.py"`. Expect missing context/helpers or observed unsafe copy/publication.
- [ ] **Step 3: Implement same-bytes read and prepared copy.** Select effective source through overlay, parse/hash its bytes once for record/rev/context. Share effective_refs. Only inherited context reads strict ledgers and World actor resolvers; owned diagnostics empty. Under campaign_lock compare original valid slots with final retained intersection, requiring reviewed_slots for every unresolved retained slot. New field tokens are authored, not consent for retained ones. Prepare updated_text in memory before base reservation/publication; preflight/base/copy use identical source_text. Never copy unsafe World text then rewrite. Already-owned updates retain ordinary semantics and legacy tokens.
- [ ] **Step 4: Guard all copy/publication callers.** Default generic materialize/reclassification/copy-down grants no consent; include dormant actor fields on every flat kind. Demote freezes/preflights all affected actual source copies under existing hold_all before copying/assets/deletion. Push/promote guards run before World/base/detachment/asset effects, force included. Members consent does not approve Leader, context defaults/whole-field submission mint no slots. Resolve safe actor refs with existing World resolvers, no full index.
Define the existing post-move collateral-sweep failure contract explicitly:
`reclassify._sweep` also catches `GroupIdentityConflict` from collateral
`overlay.rewrite_owner_refs`/`rewrite_ref_fields`, logs the existing warning,
and lets independent sweep legs continue. These collateral copies grant no
reviewed slots; a refused referring group stays inherited and byte-identical.
The original move has already landed, so the route returns its normal success
and destination id; campaign revision middleware stamps that successful write.
Preserve existing partial-reference best-effort behavior rather than adding a
cross-record transaction. Strict ledger/copy/lifetime preflight and the
membership relocation begin/complete calls stay outside these best-effort legs;
this catch must never bypass a pre-move fence or clear a suspended target.
Add `test_reclassify_succeeds_when_collateral_group_copy_is_refused` in
`test_reclassify_routes.py`: reclassify an owned location to lore while an
inherited group names it in Owners and has unresolved Members. Assert HTTP 200,
new id/file present, old source absent, campaign revision changes, referring
group has no own copy and World bytes stay unchanged, warning captured, and
subsequent independent record-ref sweep still runs. Repeat the ref-field leg
by moving an owned location to groups while an inherited item has
`holder: locations:the-counting-house` and dormant unresolved Members: the
item holder permits both locations and groups. Assert the same success/stamp,
blocked-copy/unchanged-bytes and continued independent-sweep results; no
consent is inferred. Keep existing World follow/stamp regressions.

- [ ] **Step 5: Verify concurrency and commit.** Event-controlled `test_actor_birth_and_first_copy_share_campaign_hold` pauses copy after validation and proves birth cannot publish until release; reverse order and copy sees new marker/refuses. Run Task 3 tests plus test_entities_store.py, test_locks_store.py, test_overlay_guard.py, test_import_guard.py and test_pydantic_guard.py via test-py-fast. Expect all pass. Diff check and commit `fix: require exact actor consent before group copies`; composer/context and publication checks may be separate checked commits.

### Task 4: Locked API and original-record Undo/Redo

**Files:** Modify `backend/src/grimoire/routes/models.py`, `backend/src/grimoire/routes/entities.py`, `backend/src/grimoire/main.py`, `backend/src/grimoire/store/journal.py`, `backend/src/grimoire/store/undo.py`, `backend/src/grimoire/routes/campaigns.py` (_journal_row), `backend/src/grimoire/store/overlay.py`, `backend/src/grimoire/store/sync.py` (_advance direct unlink), `backend/src/grimoire/store/reclassify.py`, `backend/src/grimoire/store/campaigns/lifecycle.py`, `backend/src/grimoire/store/locks.py`, `docs/store-guarantees.md`. Create `backend/tests/test_group_membership_routes.py`. Test `backend/tests/test_journal_store.py`, `backend/tests/test_journal_routes.py`, `backend/tests/test_undo_store.py`, `backend/tests/test_library_routes.py`, `backend/tests/test_reclassify_store.py`, `backend/tests/test_reclassify_routes.py`, `backend/tests/test_locks_http.py`, `backend/tests/test_overlay.py`.

**Interfaces:** Consumes Tasks 1-3 and existing `revision.bump(cid: str) -> str` / `revision.current(cid: str) -> str`. Plain GroupRefBinding(BaseModel) has field Literal members/leader and actor str; EntityUpdate adds group_ref_bindings list[GroupRefBinding] = []. Produces `routes.entities._checked_group_bindings(kind: str, body: EntityUpdate) -> frozenset[tuple[str, str]]`: group-only, valid actor ref, corresponding fields key submitted; invalid combination HTTP 400, model-shape/literal errors may be 422.

```python
# undo.py; specialized before-read then owned-scope seal, route owns outer lock.
@contextmanager
def journalled_group_members(cid: str, eid: str, *,
                              bindings: frozenset[tuple[str, str]],
                              label: str) -> Iterator[None]: ...
# journal.py; strict lifecycle helpers, no overlay/undo import.
def require_readable(cid: str) -> None: ...
def retire_membership_targets(cid: str, kind: str, rid: str) -> None: ...
def begin_membership_relocation(cid: str, kind: str, rid: str,
                                new_kind: str) -> str | None: ...
def complete_membership_relocation(cid: str, token: str,
                                   new_kind: str, new_id: str) -> None: ...
```

Target JSON: w group_members, current kind/id, Members-only bindings list, original World raw actor refs inherited list, actor_world_root str|None, host-derived scope `{store_root: str, campaign_root: str, layer: "campaign"}`, and permanent retired/operation-specific relocating metadata when fenced. Derive original actor origin from locked before-source; seal actual owned scope only after successful owned write. Neither request metadata nor matching bytes asserts identity. read_value/restore JSON values exactly `{raw: str|None, effective: list[str], unresolved: list[str]}`, no persisted tuples.

- [ ] **Step 1: Write failing HTTP/journal/restore tests.** Add `test_group_get_serializes_same_bytes_context`, `test_bindings_require_actor_group_and_submitted_field`, `test_group_put_keeps_revision_check_through_journal_in_one_hold`, `test_members_edit_journals_json_native_effective_state`, `test_undo_revokes_identity_only_future_binding`, `test_restore_revalidates_original_world_actor`, `test_members_undo_preserves_legacy_and_other_fields`.

GET context is sibling of meta/body/tokens/rev, JSON arrays, owned diagnostics empty. Invalid combinations return 400 with no changes. Missing binding list grants none; duplicates grant no additional slots. GIC returns 409 detail `{kind: "group_identity_conflict", message, slots}` (field/actor entries) with repair/editor action; existing stale_rev stays separate. Fully unchanged membership does not journal; identity-only opt-in does. Persist/load CAS equality; later member edit refuses, unrelated body/Leader edit permits.

```python
# Missing-World non-detached Winifred: opt-in, Undo, then campaign actor birth.
assert before["effective"] == ["pcs:winifred"]
assert before["unresolved"] == ["pcs:winifred"]
assert after["raw"] == before["raw"]
assert after["unresolved"] == []
assert undo_value["effective"] == []
assert "pcs:winifred" not in (undo_value["raw"] or "")
```

Assert actor birth after Undo receives no recorded membership; Redo restores exact reviewed binding. Original resolved World actor later removed/detached/rebound needs original exact Members consent; pressing Undo is no consent. Corrupt current ledgers refuse before record/reversal/citation writes even with bindings. Newly authored/originally owned refs keep ordinary semantics. Conservative inverse preserves legacy tokens, Leader and unrelated fields.

- [ ] **Step 2: Run red.** `make test-py-fast PY=../../backend/.venv/Scripts/python.exe "TESTS=backend/tests/test_group_membership_routes.py backend/tests/test_undo_store.py backend/tests/test_journal_store.py"`. Expect missing API/tag/identity behavior, no path/setup failure.
- [ ] **Step 3: Implement locked route and whole-field journal.** GET uses read_group_membership and common._dump. Group PUT holds campaign_lock before rev checking through derived exact bindings, source capture, update, seal and append. Nest specialized Members context only when submitted; existing body/other field journaling remains independent. Capture ephemeral inherited before without requiring owned scope; after write seal owned target, never persist unsealed target. Preserve existing best-effort manual append after landed edit, but never swallow copy/ledger errors. Show readable "Recorded members", "Effective members", "Unresolved World references" rather than empty/JSON diffs.

Restore validates target structure/retirement/suspension/current kind groups/host store-campaign scope and owned file before CAS; no overlay World fallback. Build restored raw by edited_line leaves for valid tokens absent restore.effective OR in restore.unresolved. Revalidate remaining original inherited refs under strict ledgers/World resolvers; changed actor-world root makes them unresolved even with matching actor id. Compare root identifiers, never open a persisted arbitrary path. Require original Members binding for unresolved restores; convert refusal to UndoConflict before effects. Inverse entries keep origin/consent/scope/lifetime metadata.

- [ ] **Step 4: Pin and implement lifetime fences.** Add `test_members_undo_follows_original_suffixed_reclassification`, `test_failed_relocation_completion_never_targets_old_slug`, `test_removed_copy_permanently_retires_matching_offers`, `test_retirement_preflight_preserves_copy_and_manifest`, `test_retained_owned_copy_survives_world_rebind`.

Test campaign/World group -> lore, old slug replacement with equal Members, original return with occupied destination suffix; Undo/Redo refuse nongroup and follow only original. Preserve independent detached own copy during World move. Include delete after reclass, take-world same Members then bind another World with matching group, and migration pruning. Strict journal document/entry/decode/access and atomic fence-write errors refuse before record/manifest effects; genuine absence empty. World workflows preflight every affected journal before any effect.

Begin relocation durably suspends matching two tags using opaque operation/from-ref/intended-kind before move; complete actual landed kind/id and clear only that operation. Failure/crash leaves suspended, even with old-ref matching replacement. Generic repoint, inverse creation and slug reuse cannot clear unrelated suspension. Repoint display/target original, not actor ids/bindings/origin/scope. Recovery without verified durable receipt stays suspended or retires.

Failure-path revision contract: S1 group-write, restoration, reclassification and copy-removal owners track
which held campaigns may have changed during durable suspension/retirement,
record/manifest effects or completion. On an exception after an attempted effect,
call existing `revision.bump(cid) -> str` for each such campaign **inside the same
campaign/hold_all hold**, then propagate the error with suspension/retirement
intact. Set the conservative changed flag before an effect that can partially
land; strict validation/preflight refusal before any effect leaves it unset.
A stamp before the file move is insufficient: a completion failure after the
move must stamp again before releasing the hold. Successful campaign routes keep
normal middleware stamping; World-driven moves retain explicit affected-campaign
stamping, including failure before their usual per-campaign follow loop. Once a
World move may have landed, include every held dependent whose effective view
may have changed; after only partial suspension writes, include their campaigns.
Do not swallow a failure or clear a fence merely to obtain a 2xx stamp.
Add `test_failed_lifecycle_effects_invalidate_campaign_revision`: inject atomic
completion-write failure after destination publication, assert error response,
landed destination/old source absent, suspended Undo refusing replacement,
and `revision.current(cid) != old_revision`. The old revision must fail
`revision.require(cid, old_revision)` before advance/fork confirmation. Repeat
for World-driven move with two affected campaigns, partial suspension failure
before any move, and copy-removal failure after retirement. A strict preflight
failure before effects leaves revision and files unchanged. Reuse revision's
existing best-effort bump behavior rather than promising crash atomicity across
record/journal/revision files.
The same S1 failure-stamping rule applies to the new group PUT/copy writer and
`group_members` Undo/Redo, not just movement/removal. In `undo.undo`'s existing
campaign hold, track possible restoration effects before write_value; if later
journal.append or mark_undone fails, bump the revision before propagating.
Do not convert these failures to successful Undo or stamp the journal undone
without its actual completion. Preserve the landed Members and any already-
appended inverse entry; existing CAS/entry rules still govern retry.
Add `test_members_undo_failure_after_restore_invalidates_revision`, parameterized
for append and mark_undone failure and for Undo/Redo. Assert error response,
restored Members durable, revision changed, old revision.require refuses, and
no invented completed reversal. When append fails, no inverse was added; when
mark_undone fails after append, the landed inverse remains and the original
is not falsely marked undone. Cover group PUT/first-copy failure after final
publication but before a fallible base/metadata effect: bump inside its outer
campaign hold before the non-2xx return. Unchanged/preflight-only requests do
not stamp through this failure rule; normal 2xx still uses the middleware.

Retire before deletion, overlay.dematerialize, direct sync _advance flat unlink, and migration's first pruning manifest reservation. Keep same campaign hold. Equal World replacement bytes/new copy never reactivate old offers. Retire only disappearing originals; retained own/safe copy-down preserve offers. Missing owned file refuses restore even if external removal bypassed app retirement. `_journal_row` disables retired/suspended/nongroup membership reversibility with reason. No old writer overhaul or S5 ledgers.

- [ ] **Step 5: Verify races/routes/lifecycle and commit.** Event-controlled concurrent PUT test uses same rev: first pauses after locked read, second cannot check until release; one success/one stale_rev/no duplicate journal. Assert write/seal/append sees holds_campaign. Run every Task 4 test file plus Task 3 store cases and atomic/lock-domain/lock-order/overlay/import/Pydantic guards through test-py-fast. Expect all pass. Diff check and commit `feat: journal group members with identity-safe undo`; coherent API and lifetime portions may be separate checked commits.

### Task 5: Editor consent and final verification

**Files:** Modify `frontend/src/components/EntityEditor.tsx`, `frontend/src/components/EntityEditor.test.tsx`, `frontend/src/api/client.ts` (patch type), `frontend/src/components/IncomingReview.tsx`, `frontend/src/components/IncomingReview.test.tsx`, `ROADMAP-CHECKLIST.md`, `docs/store-guarantees.md`. Create `docs/superpowers/validation/2026-10-11-spec-07-s1-implementation.md` for verification/review evidence; screenshots stay ignored.

**Interfaces:** Consumes optional EntityDetail.membership_context, typed GroupRefBinding, API updateEntity patch extended with optional group_ref_bindings array. Produces editor context and opted-in exact slots scoped to loaded record/rev, reusing existing RefField/SidebarSection. No inverse endpoint or actor affiliation page.

- [ ] **Step 1: Write failing UI tests.** Add `shows_members_chips_and_saves_only_changed_fields`, `marks_unresolved_world_refs_even_when_campaign_actor_option_matches`, `requires_separate_members_and_leader_opt_ins`, `includes_unchanged_opted_field_in_request`, `missing_campaign_group_context_blocks_unsafe_save`, `resets_bindings_after_record_scope_reload_or_save`, `ignores_late_context_and_actor_option_replies`, `identity_409_never_retries_with_implicit_consent`. Assert request contains:

```typescript
expect(patch.fields).toEqual({ members: 'characters:mara' });
expect(patch.group_ref_bindings).toEqual([{ field: 'members', actor: 'characters:mara' }]);
```

Read patch from the actual existing mock argument order. Body-only has no consent; untouched legacy Members not submitted. Foreign chips show World-reference label instead of campaign friendly name/navigation; present/future binding warning visible; checkboxes start unchecked and Members/Leader distinct. Clearing/re-adding a slot discards opt-in. Actor-options failure cannot invent identity. Missing context on existing campaign group blocks save with reload action; World/new authored groups normal. Scope/record/reload/success reset; late replies never restore old context/consent. GIC 409 shows action, no auto-consent/retry or stale_rev overwrite path.

IncomingReview test pins temporary hint "keys, owners, secrecy, or typed fields such as its members" when otherwise invisible World change exists. Typed diff rows remain S3.

- [ ] **Step 2: Run red.** `npm --prefix frontend ci` once if needed, then `npm --prefix frontend test -- src/components/EntityEditor.test.tsx src/components/IncomingReview.test.tsx`. Expect missing controls/request/reset failures.
- [ ] **Step 3: Implement editor/client/hint.** Use received unresolved slots to override chip candidate resolution; no context inference from options. Separate unchecked opt-ins send only exact slots and include their unchanged raw field. Preserve loaded legacy/no-change behavior. Bind context/opt-ins to record/rev through existing async request guards; reset on all tested transitions and slot removal/re-add. Existing campaign missing-context is an actionable disabled save/reload state. Add typed client patch and exact temporary hint.
- [ ] **Step 4: Verify UI and commit.** Task 5 UI command plus `npm --prefix frontend run typecheck`; expect all pass. Diff check and commit `feat: require explicit group reference bindings in the editor`. Commit any coherent passing part earlier.
- [ ] **Step 5: Full repository gate.** `make check PY=../../backend/.venv/Scripts/python.exe`; expect exit 0 for ratcheted lint/mypy/eslint, backend coverage, web coverage/typecheck, templates and Pydantic 1 suite. No Android packaging change/APK run. Real lint reductions require `make baseline PY=../../backend/.venv/Scripts/python.exe`, smaller baselines reviewed/committed with fix; never ACCEPT=1 to hide regressions. Frozen campaign/lore golden stay passing unchanged; never regenerate home/snapshot. Repeat/broaden only for new edits or unresolved failures.
- [ ] **Step 6: Isolated actual browser verification.** Apply `.claude/skills/verify/SKILL.md` and browser-control skill. Scratch GRIMOIRE_HOME before imports, existing campaign_flow cassette/mock, OpenRouter API_URL/MODELS_URL/KEY_URL overridden, ports 8199/5199 (user 8173/5173 untouched), Windows background Start-Process -WindowStyle Hidden. Confirm initial /api/worlds empty; seed placeholder World/campaign via API. Drive normal multi-members, foreign present/future chips, body-only copy refusal, exact separate opt-ins, safe clear, identity-only journal Undo/Redo, stale actor-context 409, temporary incoming hint. Isolated screenshots under ignored .playwright-mcp; record observed results/commands, stop only our helpers.
- [ ] **Step 7: Actual review gates.** Follow CLAUDE.md: `/codex:review` actual diff origin/main...HEAD, resolve supported findings, then actual `/codex:adversarial-review` diff plus originating spec specifically **07-S1 coverage** (field/copy/editor/journal/publication/lifecycle). Installed runtime/public snapshot workaround is allowed if helper still absent; record actual commit/thread/verdict. Substitute is not an actual pass. Apply verification-before-completion before any success claim.
- [ ] **Step 8: Record achievements and commit.** Record full-gate/browser/review evidence. Mark S1 Code/Review/Final gate only when passed, leave Landed until integration, S2-S8 unchanged. Commit docs/checklist separately; keep branch linear and ready for squash. No unsolicited push/PR/merge.

## Self-review

S1 field/raw helpers/mirror/authoring -> Task 1; strict Off/Gone RMW and associated-effect preflight -> Task 2; same-bytes context/dormant first-copy/World publication -> Task 3; locked route, JSON-native whole-field journal, conservative Undo, owned scope/removal/move fences -> Task 4; exact editor consent/reset/missing-context, temporary hint and final gates -> Task 5. Each Review Focus has named owning tests. No later slice pulled in.

Shared types agree: tuple collections in Pydantic context, exact field/actor frozenset for store consent, explicit JSON lists for durable CAS. Existing update keywords retained. Leaf imports avoid cycles; overlay never imports membership, journal never imports overlay/undo. The plan specifies seams and assertions, leaving determined function bodies to implementation. Commit checkpoints are minimums.

**Plan status:** Self-reviewed; actual Codex plan gate approved at `51f4cc950`, thread `01a12cb7-3bf7-79d2-8355-308cceed7f64` ([review record](../validation/2026-10-11-spec-07-s1-plan-review.md)). Human approved execution on 2026-10-11: implement all slices inline, with brutal-review on an independent agent as a slice gate. S1 implementation started.
